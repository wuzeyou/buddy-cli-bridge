"""Daemon entrypoint: wires components, runs HTTP + heartbeat scheduler over BLE.

Set BUDDY_CLI_TRANSPORT=stub to fall back to log-only sender (useful for
local dev without hardware nearby).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from . import protocol
from .ble_link import BleLink
from .config import LOG_PATH, PID_PATH, ensure_dirs, load
from .hook_handler import HookHandler
from .http_server import make_server
from .logging_setup import configure as configure_logging
from .state import State

log = logging.getLogger(__name__)


class HeartbeatScheduler(threading.Thread):
    """1 Hz heartbeat. Wakes early on flush events but never sends twice within
    `inter_write_gap_s` (avoids feeding the ESP32 WDT)."""

    def __init__(self, state: State, cfg, flush_event: threading.Event, sender):
        super().__init__(daemon=True, name="heartbeat")
        self.state = state
        self.cfg = cfg
        self.flush = flush_event
        self.sender = sender   # callable(line: str) -> None
        self._stop = threading.Event()
        self._last_send_at = 0.0

    def stop(self) -> None:
        self._stop.set()
        self.flush.set()

    def run(self) -> None:
        while not self._stop.is_set():
            self.flush.wait(timeout=self.cfg.heartbeat_interval_s)
            self.flush.clear()
            if self._stop.is_set():
                break
            now = time.time()
            if (now - self._last_send_at) < self.cfg.inter_write_gap_s:
                continue
            try:
                self.state.gc(now=now)
                snap = self.state.snapshot(now=now)
                msg = protocol.make_msg(
                    active_prompt_tool=snap["active_prompt_tool"],
                    completed=snap["completed"],
                    last_tool=snap["last_tool"],
                    total=snap["total"],
                )
                hb = protocol.build_heartbeat(
                    total=snap["total"],
                    running=snap["running"],
                    waiting=snap["waiting"],
                    msg=msg,
                    entries=snap["entries"],
                    prompt=snap["head_prompt"],
                    completed=snap["completed"],
                )
                line = json.dumps(hb, separators=(",", ":"))
                self.sender(line)
                self._last_send_at = now
            except Exception:
                log.exception("heartbeat tick failed")


class StubSender:
    """Log-only fallback: enabled via BUDDY_CLI_TRANSPORT=stub. Useful when
    iterating without hardware (or when bleak isn't installed)."""

    def __init__(self) -> None:
        self._log = logging.getLogger("heartbeat")

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def __call__(self, line: str) -> None:
        self._log.info("HB %s", line)

    def is_connected(self) -> bool:
        return False

    def last_error(self) -> Optional[str]:
        return "stub transport"


def _resolve_owner_name(cfg) -> str:
    """Pick an owner name: cfg.owner_name → git config user.name (first token) → $USER."""
    if cfg.owner_name:
        return cfg.owner_name
    try:
        out = subprocess.check_output(
            ["git", "config", "--global", "user.name"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode("utf-8", errors="replace").strip()
        if out:
            return out.split()[0]
    except Exception:
        pass
    return os.environ.get("USER", "")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="claude-desktop-buddy CLI bridge daemon")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--log-level", default=None)
    return p.parse_args()


def write_pid(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()))


def remove_pid(path: Path) -> None:
    try:
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


def main() -> int:
    args = parse_args()
    cfg = load()
    if args.port is not None:
        cfg.http_port = args.port
    if args.log_level is not None:
        cfg.log_level = args.log_level

    ensure_dirs()
    configure_logging(LOG_PATH, cfg.log_level)
    write_pid(PID_PATH)
    log.info("daemon starting on %s:%s (pid %d)", args.host, cfg.http_port, os.getpid())

    state = State()
    flush_event = threading.Event()
    handler = HookHandler(state, cfg, flush_event)

    transport = os.environ.get("BUDDY_CLI_TRANSPORT", "ble").lower()
    sender: object
    if transport == "stub":
        log.info("BUDDY_CLI_TRANSPORT=stub — using log-only sender")
        sender = StubSender()
    else:
        owner = _resolve_owner_name(cfg)

        def _on_device_line(obj: dict) -> None:
            cmd = obj.get("cmd")
            if cmd == "permission":
                pid = str(obj.get("id", ""))
                decision = str(obj.get("decision", ""))
                # Firmware uses "once" (per REFERENCE.md); we normalize to "allow".
                norm = "allow" if decision == "once" else decision
                if norm in ("allow", "deny"):
                    if state.resolve_prompt(pid, norm):
                        log.info("device decision %s for %s", norm, pid)
                        flush_event.set()
                    else:
                        log.warning("device decision for unknown prompt %s", pid)
                else:
                    log.warning("unknown decision %r", decision)
            else:
                log.debug("device → %s", obj)

        ble = BleLink(
            inter_write_gap_s=cfg.inter_write_gap_s,
            max_queue=cfg.ble_max_queue,
            reconnect_min_s=cfg.ble_reconnect_min_s,
            reconnect_max_s=cfg.ble_reconnect_max_s,
            on_line=_on_device_line,
        )

        def _on_ble_connect() -> None:
            ble.send(json.dumps(protocol.build_time(), separators=(",", ":")))
            if owner:
                ble.send(json.dumps(protocol.build_owner(owner), separators=(",", ":")))
            flush_event.set()

        ble.on_connect = _on_ble_connect
        sender = ble

    hb = HeartbeatScheduler(state, cfg, flush_event, sender)

    def healthz() -> dict:
        snap = state.snapshot()
        return {
            "ok": True,
            "ble": "connected" if sender.is_connected() else "disconnected",
            "ble_status": sender.last_error(),
            "sessions": snap["total"],
            "waiting": snap["waiting"],
            "running": snap["running"],
            "pid": os.getpid(),
            "transport": transport,
        }

    server = make_server(args.host, cfg.http_port, handler, healthz)

    def _shutdown(signum, _frame):
        log.info("signal %d received, stopping", signum)
        hb.stop()
        if hasattr(sender, "stop"):
            sender.stop()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if hasattr(sender, "start"):
        sender.start()
    hb.start()
    try:
        server.serve_forever()
    finally:
        log.info("server stopped, cleaning up")
        hb.stop()
        hb.join(timeout=2.0)
        if hasattr(sender, "stop"):
            sender.stop()
        remove_pid(PID_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
