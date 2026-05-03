"""Per-event hook dispatch. Mutates state, builds responses.

PreToolUse blocks on PendingPrompt.event until the device responds or timeout.
All other handlers return immediately.
"""
from __future__ import annotations

import logging
import threading

from . import protocol
from .config import Config
from .state import State

log = logging.getLogger(__name__)


class HookHandler:
    """Dispatches a parsed hook payload by `hook_event_name`."""

    def __init__(self, state: State, cfg: Config, flush_event: threading.Event):
        self.state = state
        self.cfg = cfg
        self.flush = flush_event  # wake the heartbeat scheduler ASAP after a state change

    def dispatch(self, payload: dict) -> dict:
        event = str(payload.get("hook_event_name", ""))
        method = getattr(self, f"handle_{event.lower()}", None)
        if method is None:
            log.debug("ignoring unknown hook event %r", event)
            return {}
        try:
            return method(payload) or {}
        except Exception:
            log.exception("hook handler crashed for %s", event)
            return {}

    # ---- per-event handlers --------------------------------------------

    def handle_sessionstart(self, p: dict) -> dict:
        sid = str(p.get("session_id", ""))
        cwd = str(p.get("cwd", "") or "")
        self.state.session_start(sid, cwd)
        self.state.add_entry(protocol.make_entry("start", sid[:4]))
        self.flush.set()
        return {}

    def handle_userpromptsubmit(self, p: dict) -> dict:
        sid = str(p.get("session_id", ""))
        self.state.session_running(sid)
        self.state.add_entry(protocol.make_entry("ask", sid[:4]))
        self.flush.set()
        return {}

    def handle_pretooluse(self, p: dict) -> dict:
        sid = str(p.get("session_id", ""))
        tool = str(p.get("tool_name", ""))
        tool_input = p.get("tool_input") or {}
        mode = p.get("permission_mode")

        if mode:
            self.state.set_permission_mode(sid, mode)

        # Bypass short-circuit: don't bother the device.
        if mode == "bypassPermissions" or self.state.is_bypass(sid):
            return self._perm("allow", "bypassPermissions")

        self.state.session_running(sid)
        self.state.set_last_tool(tool)

        prompt_id = self.state.next_prompt_id()
        hint = protocol.make_hint(tool, tool_input if isinstance(tool_input, dict) else None)
        pending = self.state.enqueue_prompt(prompt_id, sid, tool, hint)
        self.state.add_entry(protocol.make_entry("ask", f"{tool} {sid[:4]}"))
        self.flush.set()

        try:
            woke = pending.event.wait(timeout=self.cfg.decision_timeout_s)
            decision = pending.decision if woke else None
        finally:
            self.state.dequeue_prompt(prompt_id)
            self.flush.set()

        if decision == "allow":
            return self._perm("allow", "buddy: A button")
        if decision == "deny":
            return self._perm("deny", "buddy: B button")
        # timeout → ask: fall back to Claude Code's native dialog.
        return self._perm("ask", "buddy: timeout")

    def handle_posttooluse(self, p: dict) -> dict:
        sid = str(p.get("session_id", ""))
        tool = str(p.get("tool_name", ""))
        self.state.set_last_tool(tool)
        self.state.session_touch(sid)
        self.state.add_entry(protocol.make_entry("done", tool))
        self.flush.set()
        return {}

    def handle_notification(self, p: dict) -> dict:
        sid = str(p.get("session_id", ""))
        self.state.session_touch(sid)
        return {}

    def handle_stop(self, p: dict) -> dict:
        sid = str(p.get("session_id", ""))
        self.state.session_stop(sid)
        self.state.add_entry(protocol.make_entry("stop", sid[:4]))
        self.flush.set()
        return {}

    @staticmethod
    def _perm(decision: str, reason: str) -> dict:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        }
