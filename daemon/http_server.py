"""ThreadingHTTPServer with two routes:
  POST /hook     — Claude Code hook payload, body forwarded to HookHandler
  GET  /healthz  — daemon liveness + BLE state, used by status.sh and the slash command
"""
from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .hook_handler import HookHandler

log = logging.getLogger(__name__)


def make_server(
    host: str,
    port: int,
    handler: HookHandler,
    healthz: Callable[[], dict],
) -> ThreadingHTTPServer:
    """`healthz` is a no-arg callable returning a dict for /healthz responses."""

    class _Req(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug("http: " + (fmt % args))

        def _write_json(self, status: int, body: dict) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/healthz":
                self._write_json(200, healthz())
            else:
                self._write_json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/hook":
                self._write_json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length > 0 else b""
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (ValueError, json.JSONDecodeError):
                self._write_json(400, {"error": "invalid json"})
                return
            if not isinstance(payload, dict):
                self._write_json(400, {"error": "expected object"})
                return
            response = handler.dispatch(payload)
            self._write_json(200, response)

    return ThreadingHTTPServer((host, port), _Req)
