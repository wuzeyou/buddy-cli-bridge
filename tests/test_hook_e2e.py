"""End-to-end test of the daemon's HTTP + state + handler stack.

No BLE — verifies the hook → registry → block-on-event → resolve → return-JSON
cycle on a real socket. Uses `decision_timeout_s` overrides to keep tests fast.
"""
from __future__ import annotations

import http.client
import json
import threading
import time
import unittest

from daemon.config import Config
from daemon.hook_handler import HookHandler
from daemon.http_server import make_server
from daemon.state import State


def _post(port: int, body: dict, timeout: float = 10.0) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        raw = json.dumps(body).encode("utf-8")
        conn.request("POST", "/hook", body=raw, headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
        })
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, (json.loads(data) if data else {})
    finally:
        conn.close()


def _get(port: int, path: str = "/healthz") -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read()
        return resp.status, (json.loads(data) if data else {})
    finally:
        conn.close()


class _ServerFixture:
    def __init__(self, decision_timeout_s: float = 2.0):
        self.state = State()
        self.cfg = Config(decision_timeout_s=decision_timeout_s)
        self.flush = threading.Event()
        self.handler = HookHandler(self.state, self.cfg, self.flush)
        self.server = make_server(
            "127.0.0.1", 0, self.handler,
            healthz=lambda: {"ok": True, "ble": "disconnected"},
        )
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=2.0)


class TestHookE2E(unittest.TestCase):
    def test_session_start_increments_total(self):
        with _ServerFixture() as srv:
            status, body = _post(srv.port, {
                "hook_event_name": "SessionStart",
                "session_id": "sess1", "cwd": "/tmp",
            })
            self.assertEqual(status, 200)
            self.assertEqual(body, {})
            self.assertEqual(srv.state.snapshot()["total"], 1)

    def test_user_prompt_submit_increments_running(self):
        with _ServerFixture() as srv:
            _post(srv.port, {"hook_event_name": "SessionStart", "session_id": "s"})
            _post(srv.port, {"hook_event_name": "UserPromptSubmit", "session_id": "s"})
            self.assertEqual(srv.state.snapshot()["running"], 1)

    def test_pre_tool_use_blocks_until_decision(self):
        with _ServerFixture(decision_timeout_s=5.0) as srv:
            _post(srv.port, {"hook_event_name": "SessionStart", "session_id": "s"})
            results: dict = {}

            def call_pretool():
                status, body = _post(srv.port, {
                    "hook_event_name": "PreToolUse",
                    "session_id": "s",
                    "tool_name": "Bash",
                    "tool_input": {"command": "ls"},
                }, timeout=10)
                results["status"] = status
                results["body"] = body

            t = threading.Thread(target=call_pretool)
            t.start()

            # Wait for the handler to enqueue the prompt
            deadline = time.time() + 2.0
            head = None
            while time.time() < deadline:
                head = srv.state.head_prompt()
                if head is not None:
                    break
                time.sleep(0.02)
            self.assertIsNotNone(head, "prompt was never enqueued")

            self.assertTrue(srv.state.resolve_prompt(head.prompt_id, "allow"))
            t.join(timeout=5.0)
            self.assertEqual(results["status"], 200)
            self.assertEqual(
                results["body"]["hookSpecificOutput"]["permissionDecision"],
                "allow",
            )

    def test_pre_tool_use_deny(self):
        with _ServerFixture(decision_timeout_s=5.0) as srv:
            _post(srv.port, {"hook_event_name": "SessionStart", "session_id": "s"})
            results: dict = {}

            def call_pretool():
                status, body = _post(srv.port, {
                    "hook_event_name": "PreToolUse",
                    "session_id": "s", "tool_name": "Bash",
                    "tool_input": {"command": "rm -rf /"},
                }, timeout=10)
                results["status"] = status
                results["body"] = body

            t = threading.Thread(target=call_pretool)
            t.start()

            deadline = time.time() + 2.0
            head = None
            while time.time() < deadline:
                head = srv.state.head_prompt()
                if head is not None:
                    break
                time.sleep(0.02)

            srv.state.resolve_prompt(head.prompt_id, "deny")
            t.join(timeout=5.0)
            self.assertEqual(
                results["body"]["hookSpecificOutput"]["permissionDecision"],
                "deny",
            )

    def test_pre_tool_use_timeout_returns_ask(self):
        with _ServerFixture(decision_timeout_s=0.2) as srv:
            _post(srv.port, {"hook_event_name": "SessionStart", "session_id": "s"})
            status, body = _post(srv.port, {
                "hook_event_name": "PreToolUse",
                "session_id": "s",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            }, timeout=5)
            self.assertEqual(status, 200)
            self.assertEqual(
                body["hookSpecificOutput"]["permissionDecision"],
                "ask",
            )

    def test_bypass_permissions_short_circuits(self):
        with _ServerFixture(decision_timeout_s=10.0) as srv:
            _post(srv.port, {"hook_event_name": "SessionStart", "session_id": "s"})
            t0 = time.time()
            status, body = _post(srv.port, {
                "hook_event_name": "PreToolUse",
                "session_id": "s",
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "permission_mode": "bypassPermissions",
            }, timeout=5)
            elapsed = time.time() - t0
            self.assertEqual(status, 200)
            self.assertEqual(
                body["hookSpecificOutput"]["permissionDecision"],
                "allow",
            )
            self.assertLess(elapsed, 1.0, "bypass should not block on device")

    def test_stop_opens_completed_window(self):
        with _ServerFixture() as srv:
            _post(srv.port, {"hook_event_name": "SessionStart", "session_id": "s"})
            _post(srv.port, {"hook_event_name": "Stop", "session_id": "s"})
            self.assertTrue(srv.state.snapshot()["completed"])

    def test_unknown_event_is_noop(self):
        with _ServerFixture() as srv:
            status, body = _post(srv.port, {"hook_event_name": "MysteryEvent"})
            self.assertEqual(status, 200)
            self.assertEqual(body, {})

    def test_healthz(self):
        with _ServerFixture() as srv:
            status, body = _get(srv.port, "/healthz")
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])

    def test_404_for_unknown_route(self):
        with _ServerFixture() as srv:
            conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=2)
            try:
                conn.request("GET", "/")
                resp = conn.getresponse()
                self.assertEqual(resp.status, 404)
            finally:
                conn.close()

    def test_post_invalid_json_returns_400(self):
        with _ServerFixture() as srv:
            conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=2)
            try:
                conn.request("POST", "/hook", body=b"{not json", headers={
                    "Content-Type": "application/json",
                    "Content-Length": "9",
                })
                resp = conn.getresponse()
                self.assertEqual(resp.status, 400)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
