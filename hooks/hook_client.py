#!/usr/bin/env python3
"""Stdin → POST http://127.0.0.1:$BUDDY_CLI_PORT/hook → stdout.

Stdlib only. On any failure (daemon down, timeout, malformed reply, anything)
this prints `{}` and exits 0 so Claude Code is never blocked by us.
"""
from __future__ import annotations

import http.client
import json
import os
import sys

HOST = "127.0.0.1"
PORT = int(os.environ.get("BUDDY_CLI_PORT", "9876"))
# Slightly less than the longest hook timeout (PreToolUse=40s in hooks.json);
# daemon-side device-decision window is 30s by default, so ~35s is a safe ceiling.
TIMEOUT = float(os.environ.get("BUDDY_CLI_HOOK_HTTP_TIMEOUT", "35"))


def main() -> int:
    body = sys.stdin.buffer.read()
    try:
        conn = http.client.HTTPConnection(HOST, PORT, timeout=TIMEOUT)
        try:
            conn.request("POST", "/hook", body=body, headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            })
            resp = conn.getresponse()
            data = resp.read()
            status = resp.status
        finally:
            conn.close()
    except Exception:
        sys.stdout.write("{}")
        return 0

    if status != 200:
        sys.stdout.write("{}")
        return 0

    # Only echo if it parses as JSON. Anything else → fail-open `{}`.
    try:
        json.loads(data)
    except (json.JSONDecodeError, ValueError):
        sys.stdout.write("{}")
        return 0
    sys.stdout.write(data.decode("utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
