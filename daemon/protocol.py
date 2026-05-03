"""Pure helpers that build heartbeat / cmd JSON and parse device replies.

All field length caps come from src/data.h's TamaState (the firmware's char[N] buffers),
minus 1 for the trailing NUL. Going over silently truncates on the device side, so we
truncate here to keep things observable.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Iterable, Optional

PROMPT_ID_MAX = 39      # promptId   char[40]
PROMPT_TOOL_MAX = 19    # promptTool char[20]
PROMPT_HINT_MAX = 43    # promptHint char[44]
ENTRY_MAX_BYTES = 91    # lines      char[8][92]
ENTRIES_MAX_LINES = 8
MSG_MAX = 23            # msg        char[24]
OWNER_MAX = 32


def _ascii_only(s: str, replacement: str = "?") -> str:
    return "".join(c if ord(c) < 128 else replacement for c in s)


def _truncate_ascii(s: str, max_bytes: int) -> str:
    return _ascii_only(s)[:max_bytes]


def make_hint(tool_name: str, tool_input: Optional[dict]) -> str:
    """≤43 ASCII chars summarizing what the tool will do."""
    tool_input = tool_input or {}
    if tool_name == "Bash":
        raw = str(tool_input.get("command", ""))
    elif tool_name in ("Edit", "Write", "Read", "NotebookEdit"):
        raw = os.path.basename(str(tool_input.get("file_path", "")))
    elif tool_name == "WebFetch":
        url = str(tool_input.get("url", ""))
        raw = url.split("://", 1)[1].split("/", 1)[0] if "://" in url else url
    else:
        try:
            raw = json.dumps(tool_input, ensure_ascii=True)
        except (TypeError, ValueError):
            raw = str(tool_input)
    return _truncate_ascii(raw, PROMPT_HINT_MAX)


def make_msg(
    *,
    active_prompt_tool: Optional[str] = None,
    completed: bool = False,
    last_tool: Optional[str] = None,
    total: int = 0,
) -> str:
    """≤23 ASCII chars one-liner. Priority: prompt > done > running > sessions > ready."""
    if active_prompt_tool:
        s = f"approve: {active_prompt_tool}"
    elif completed and last_tool:
        s = f"done: {last_tool}"
    elif last_tool:
        s = f"running: {last_tool}"
    elif total > 0:
        s = f"{total} session{'' if total == 1 else 's'}"
    else:
        s = "ready"
    return _truncate_ascii(s, MSG_MAX)


def make_entry(verb: str, target: str = "", now: Optional[float] = None) -> str:
    """Transcript line `HH:MM verb [target]`, ≤91 ASCII bytes."""
    when = time.localtime(now if now is not None else time.time())
    ts = f"{when.tm_hour:02d}:{when.tm_min:02d}"
    line = f"{ts} {verb}" + (f" {target}" if target else "")
    return _truncate_ascii(line, ENTRY_MAX_BYTES)


def build_heartbeat(
    *,
    total: int,
    running: int,
    waiting: int,
    msg: str,
    entries: Iterable[str],
    prompt: Optional[dict] = None,
    completed: bool = False,
) -> dict:
    """Build a heartbeat JSON object.

    Absent `prompt` clears the device's pending prompt (firmware full-replace at
    src/data.h:122-123). Set `completed=True` for one frame after a session Stop
    to trigger the celebrate animation.
    """
    out: dict = {
        "total": int(total),
        "running": int(running),
        "waiting": int(waiting),
        "msg": _truncate_ascii(msg, MSG_MAX),
        "entries": [_truncate_ascii(e, ENTRY_MAX_BYTES) for e in list(entries)[:ENTRIES_MAX_LINES]],
    }
    if prompt is not None:
        out["prompt"] = {
            "id": _truncate_ascii(str(prompt["id"]), PROMPT_ID_MAX),
            "tool": _truncate_ascii(str(prompt.get("tool", "")), PROMPT_TOOL_MAX),
            "hint": _truncate_ascii(str(prompt.get("hint", "")), PROMPT_HINT_MAX),
        }
    if completed:
        out["completed"] = True
    return out


def build_status_query() -> dict:
    return {"cmd": "status"}


def build_time(now: Optional[float] = None) -> dict:
    """One-shot time sync. tz_offset is the local UTC offset in seconds (DST-aware)."""
    t = now if now is not None else time.time()
    offset = int(datetime.fromtimestamp(t).astimezone().utcoffset().total_seconds())
    return {"time": [int(t), offset]}


def build_owner(name: str) -> dict:
    return {"cmd": "owner", "name": _truncate_ascii(name, OWNER_MAX)}


def parse_device_line(line: str) -> Optional[dict]:
    """Parse one line of JSON from the device. Returns None on anything malformed."""
    s = line.strip()
    if not s or not s.startswith("{"):
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None
