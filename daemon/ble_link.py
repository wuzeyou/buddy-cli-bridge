"""BLE Nordic UART Service link to a Claude-* peripheral.

Owns its own asyncio event loop on a daemon thread. Exposes a thread-safe
`send(line)` (also reachable via `__call__`) for the heartbeat scheduler, and
forwards inbound JSON lines to `on_line(obj)`.

Design notes:
- One supervisor coroutine handles scan → connect → notify → write loop, with
  exponential backoff on disconnect (cfg.ble_reconnect_min_s → ble_reconnect_max_s).
- Outbound queue is drop-oldest (cap = ble_max_queue). Heartbeats are idempotent
  full-state snapshots, so dropping stale ones is fine.
- Inter-write gap (cfg.inter_write_gap_s, default 200ms) prevents feeding the
  ESP32 watchdog, the same trap m5-paper-buddy hit on early high-frequency hooks.
- macOS bond reuse is transparent: bleak uses CoreBluetooth which silently reads
  the LTK from the system keychain (the same one Claude.app populated during
  initial pairing — see CLAUDE.md device acceptance).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Callable, Optional

from . import protocol

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"   # client → device (write)
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"   # device → client (notify)

log = logging.getLogger(__name__)


class BleLink:
    """Thread-safe BLE NUS bridge. Heartbeat scheduler treats this as a callable."""

    def __init__(
        self,
        *,
        device_name_prefix: str = "Claude-",
        inter_write_gap_s: float = 0.2,
        max_queue: int = 50,
        reconnect_min_s: float = 1.0,
        reconnect_max_s: float = 30.0,
        on_line: Optional[Callable[[dict], None]] = None,
        on_connect: Optional[Callable[[], None]] = None,
    ) -> None:
        self.prefix = device_name_prefix
        self.inter_write_gap_s = inter_write_gap_s
        self.max_queue = max_queue
        self.reconnect_min_s = reconnect_min_s
        self.reconnect_max_s = reconnect_max_s
        self.on_line = on_line or (lambda _: None)
        self.on_connect = on_connect or (lambda: None)

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread = threading.Thread(target=self._run_loop, name="ble-loop", daemon=True)
        self._loop_ready = threading.Event()
        self._stop = False

        self._out_lines: "deque[str]" = deque()
        self._out_cv: Optional[asyncio.Condition] = None
        self._connected = threading.Event()
        self._last_error: Optional[str] = "starting"

    # ---- public, thread-safe API ---------------------------------------

    def start(self) -> None:
        self._thread.start()
        self._loop_ready.wait(timeout=2.0)
        if self._loop is None:
            raise RuntimeError("BLE event loop did not start")
        asyncio.run_coroutine_threadsafe(self._supervisor(), self._loop)

    def stop(self) -> None:
        """Graceful shutdown: cancel running tasks, await their teardown, then close loop.
        Avoids the SIGSEGV that comes from `loop.stop()` while the BleakClient context
        manager is still pending."""
        self._stop = True
        if self._loop is None or not self._loop.is_running():
            return

        async def _shutdown() -> None:
            assert self._out_cv is not None
            # 1. wake the writer so it can observe _stop
            async with self._out_cv:
                self._out_cv.notify_all()
            # 2. cancel any other pending tasks (the supervisor, and the BleakClient
            #    context manager nested in it — bleak handles cancel cleanly).
            current = asyncio.current_task()
            tasks = [t for t in asyncio.all_tasks() if t is not current]
            for t in tasks:
                t.cancel()
            # 3. give them a chance to unwind. bleak's disconnect may take a moment.
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), self._loop).result(timeout=5.0)
        except Exception as e:
            log.warning("ble graceful shutdown failed: %s", e)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3.0)

    def send(self, line: str) -> None:
        """Enqueue a single JSON line (no trailing newline). Drops oldest if full."""
        if not line:
            return
        if self._loop is None or self._out_cv is None:
            return

        async def _push() -> None:
            assert self._out_cv is not None
            async with self._out_cv:
                if len(self._out_lines) >= self.max_queue:
                    self._out_lines.popleft()
                self._out_lines.append(line)
                self._out_cv.notify()

        asyncio.run_coroutine_threadsafe(_push(), self._loop)

    __call__ = send  # heartbeat scheduler calls sender(line)

    def is_connected(self) -> bool:
        return self._connected.is_set()

    def last_error(self) -> Optional[str]:
        return self._last_error

    # ---- internals ------------------------------------------------------

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._out_cv = asyncio.Condition()
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _supervisor(self) -> None:
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError as e:
            log.error("bleak not installed; BLE disabled (%s)", e)
            self._last_error = f"bleak not installed: {e}"
            return

        backoff = self.reconnect_min_s
        while not self._stop:
            self._last_error = "scanning"
            log.info("scanning for %r", self.prefix)
            try:
                device = await BleakScanner.find_device_by_filter(
                    lambda d, _ad: bool(d.name and d.name.startswith(self.prefix)),
                    timeout=10.0,
                )
            except Exception as e:
                log.warning("scan failed: %s", e)
                self._last_error = f"scan failed: {e}"
                device = None

            if self._stop:
                return
            if device is None:
                self._last_error = "no device found"
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.reconnect_max_s)
                continue

            log.info("connecting to %s (%s)", device.name, device.address)
            self._last_error = "connecting"
            try:
                async with BleakClient(device) as client:
                    await self._handle_connection(client)
            except Exception as e:
                log.warning("connection lost / failed: %s", e)
                self._last_error = str(e)
            finally:
                self._connected.clear()

            if self._stop:
                return
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.reconnect_max_s)

    async def _handle_connection(self, client) -> None:
        inbox = bytearray()

        def _on_notify(_handle, data: bytearray) -> None:
            inbox.extend(data)
            while True:
                nl = inbox.find(b"\n")
                if nl < 0:
                    break
                raw = bytes(inbox[:nl]).rstrip(b"\r")
                del inbox[: nl + 1]
                if not raw:
                    continue
                obj = protocol.parse_device_line(raw.decode("utf-8", errors="replace"))
                if obj is None:
                    continue
                try:
                    self.on_line(obj)
                except Exception:
                    log.exception("on_line callback failed")

        await client.start_notify(NUS_TX, _on_notify)
        self._connected.set()
        self._last_error = None
        log.info("connected")

        # Reset backoff via on_connect callback (also delivers time/owner)
        try:
            self.on_connect()
        except Exception:
            log.exception("on_connect callback failed")

        try:
            await self._writer_loop(client)
        finally:
            try:
                await client.stop_notify(NUS_TX)
            except Exception:
                pass

    async def _writer_loop(self, client) -> None:
        assert self._out_cv is not None
        loop = asyncio.get_running_loop()
        last_write = 0.0
        while not self._stop and client.is_connected:
            async with self._out_cv:
                while not self._out_lines and not self._stop and client.is_connected:
                    try:
                        await asyncio.wait_for(self._out_cv.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        pass  # re-check is_connected
                if self._stop or not client.is_connected:
                    return
                line = self._out_lines.popleft()

            now = loop.time()
            wait_s = self.inter_write_gap_s - (now - last_write)
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            payload = (line + "\n").encode("utf-8")
            await client.write_gatt_char(NUS_RX, payload, response=False)
            log.debug("→ %s", line)
            last_write = loop.time()
