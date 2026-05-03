"""Daemon's in-memory state: sessions, prompts, transcript.

A single RLock guards everything. Hook handlers and the heartbeat builder both
take the lock; we only expect contention on PreToolUse spikes which is fine.

The shape is shaped around what `protocol.build_heartbeat` consumes — call
`State.snapshot()` to get an immutable view, never reach into the fields.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Optional

# A Stop hook keeps `completed=True` in heartbeats for this long, to give the
# firmware enough ticks to render the celebrate animation.
COMPLETED_WINDOW_MS = 4000

# Sessions whose last hook is older than this get garbage-collected.
SESSION_GC_AGE_S = 300

# How many transcript lines we keep ready for `entries[]`.
ENTRIES_DEQUE_LEN = 8


@dataclass
class SessionMeta:
    sid: str
    started_at: float
    cwd: str = ""
    last_hook_at: float = 0.0
    last_tool: Optional[str] = None
    permission_mode: Optional[str] = None


@dataclass
class PendingPrompt:
    prompt_id: str
    sid: str
    tool: str
    hint: str
    event: threading.Event = field(default_factory=threading.Event)
    decision: Optional[str] = None  # "allow" / "deny", or None on timeout
    created_at: float = field(default_factory=time.time)


class State:
    """Thread-safe daemon state."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.sessions: dict[str, SessionMeta] = {}
        self.running: set[str] = set()
        self.waiting: set[str] = set()
        self.pending: "OrderedDict[str, PendingPrompt]" = OrderedDict()
        self.entries: "deque[str]" = deque(maxlen=ENTRIES_DEQUE_LEN)
        self.last_tool: Optional[str] = None
        self._last_completed_until_ms: int = 0
        self._prompt_counter: int = 0

    # ---- session lifecycle ------------------------------------------------

    def session_start(self, sid: str, cwd: str = "", *, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            s = self.sessions.get(sid)
            if s is None:
                self.sessions[sid] = SessionMeta(sid=sid, started_at=now, cwd=cwd, last_hook_at=now)
            else:
                s.last_hook_at = now

    def session_running(self, sid: str, *, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            if sid not in self.sessions:
                self.sessions[sid] = SessionMeta(sid=sid, started_at=now, last_hook_at=now)
            else:
                self.sessions[sid].last_hook_at = now
            self.running.add(sid)

    def session_stop(self, sid: str, *, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            self.running.discard(sid)
            if sid in self.sessions:
                self.sessions[sid].last_hook_at = now
            self._last_completed_until_ms = int(now * 1000) + COMPLETED_WINDOW_MS

    def session_touch(self, sid: str, *, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        with self._lock:
            if sid in self.sessions:
                self.sessions[sid].last_hook_at = now

    def set_permission_mode(self, sid: str, mode: Optional[str]) -> None:
        with self._lock:
            if sid in self.sessions:
                self.sessions[sid].permission_mode = mode

    def gc(self, *, now: Optional[float] = None) -> int:
        """Drop sessions whose last hook event is older than SESSION_GC_AGE_S.
        Returns the number dropped."""
        now = now if now is not None else time.time()
        with self._lock:
            stale = [sid for sid, s in self.sessions.items()
                     if (now - s.last_hook_at) > SESSION_GC_AGE_S]
            for sid in stale:
                self.sessions.pop(sid, None)
                self.running.discard(sid)
                self.waiting.discard(sid)
            return len(stale)

    # ---- prompts ---------------------------------------------------------

    def next_prompt_id(self) -> str:
        with self._lock:
            self._prompt_counter += 1
            return f"p{self._prompt_counter:04d}"

    def enqueue_prompt(self, prompt_id: str, sid: str, tool: str, hint: str) -> PendingPrompt:
        p = PendingPrompt(prompt_id=prompt_id, sid=sid, tool=tool, hint=hint)
        with self._lock:
            self.pending[prompt_id] = p
            self.waiting.add(sid)
        return p

    def dequeue_prompt(self, prompt_id: str) -> Optional[PendingPrompt]:
        """Pop the prompt and clear `waiting` for its sid only if no other
        pending prompts share that sid (concurrent CLI windows from same session)."""
        with self._lock:
            p = self.pending.pop(prompt_id, None)
            if p is not None:
                still_waiting = any(other.sid == p.sid for other in self.pending.values())
                if not still_waiting:
                    self.waiting.discard(p.sid)
            return p

    def head_prompt(self) -> Optional[PendingPrompt]:
        with self._lock:
            return next(iter(self.pending.values()), None)

    def resolve_prompt(self, prompt_id: str, decision: str) -> bool:
        """Set decision on the matching prompt and wake its event.
        Returns True iff the prompt was still pending."""
        with self._lock:
            p = self.pending.get(prompt_id)
            if p is None:
                return False
            p.decision = decision
            p.event.set()
            return True

    # ---- transcript ------------------------------------------------------

    def add_entry(self, line: str) -> None:
        with self._lock:
            # newest first — firmware expects entries[0] = most recent
            self.entries.appendleft(line)

    def set_last_tool(self, tool: Optional[str]) -> None:
        with self._lock:
            self.last_tool = tool

    # ---- snapshot --------------------------------------------------------

    def snapshot(self, *, now: Optional[float] = None) -> dict:
        """Immutable view fit for `protocol.build_heartbeat()`."""
        now = now if now is not None else time.time()
        with self._lock:
            head = next(iter(self.pending.values()), None)
            completed = int(now * 1000) < self._last_completed_until_ms
            return {
                "total": len(self.sessions),
                "running": len(self.running),
                "waiting": len(self.waiting),
                "entries": list(self.entries),
                "last_tool": self.last_tool,
                "completed": completed,
                "head_prompt": (
                    {"id": head.prompt_id, "tool": head.tool, "hint": head.hint}
                    if head is not None else None
                ),
                "active_prompt_tool": head.tool if head is not None else None,
            }

    # ---- bypass-permissions short-circuit -------------------------------

    def is_bypass(self, sid: str) -> bool:
        with self._lock:
            s = self.sessions.get(sid)
            return s is not None and s.permission_mode == "bypassPermissions"
