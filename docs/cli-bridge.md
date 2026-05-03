# CLI bridge architecture

Architecture reference for `buddy-cli-bridge`: a Python daemon that bridges Claude Code CLI hook events to a Hardware Buddy device over BLE Nordic UART Service. The firmware and wire protocol live in [`anthropics/claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy) — this plugin strictly speaks the protocol documented in [`REFERENCE.md`](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md), no firmware changes required.

## Why this exists

The macOS Claude.app desktop application has a built-in BLE bridge (see `Developer → Open Hardware Buddy…`). It pushes heartbeats and routes permission prompts to the device. **Claude Code CLI does not** — its permission prompts are stdin/stdout interactions in the terminal, completely outside the desktop App's process.

This plugin closes that gap with no firmware changes.

## Deployment model

The daemon **replaces** the desktop App's BLE link rather than running alongside it. The firmware accepts a single BLE central connection at a time, so the user picks one bridge:

```
Claude.app (desktop) ──BLE──► device      (default; nothing to install)
                       OR
buddy-cli-bridge daemon ──BLE──► device   (when /buddy-start is running)
```

Switching is a manual `Disconnect` in Claude.app's Hardware Buddy window then `/buddy-start`, or `/buddy-stop` followed by `Connect`.

## Component map

```
~/.claude/plugins/cache/buddy-cli-bridge-*/    ← plugin install cache
├── .claude-plugin/plugin.json                  ← manifest
├── commands/buddy-{install,start,stop,status,uninstall}.md
├── scripts/{install,start,stop,status,uninstall,_common}.sh
├── hooks/
│   ├── hooks.json                              ← 6 hook entries auto-merged by Claude Code
│   └── hook_client.py                          ← stdin → POST /hook → stdout
└── daemon/
    ├── daemon.py                               ← entrypoint: HTTP + heartbeat scheduler
    ├── http_server.py                          ← ThreadingHTTPServer, POST /hook + GET /healthz
    ├── hook_handler.py                         ← per-event dispatch; PreToolUse blocks on threading.Event
    ├── state.py                                ← SessionRegistry + PromptQueue
    ├── protocol.py                             ← pure helpers: build_heartbeat / parse_device_line / ...
    ├── ble_link.py                             ← bleak asyncio supervisor + reconnect backoff
    ├── config.py                               ← STATE_DIR fallback chain, env overrides
    └── logging_setup.py                        ← rotating file + stderr

~/.claude/plugins/data/buddy-cli-bridge/        ← persistent state (${CLAUDE_PLUGIN_DATA})
├── venv/                                       ← isolated bleak install
├── logs/daemon.log                             ← 1MB × 3 rotation
├── run/daemon.pid                              ← process id, removed on graceful exit
└── config.json                                 ← optional user overrides
```

## Data flow

```
                          ┌──────────────────────┐
   /buddy-start ──spawns──▶│  daemon process     │
                          │                      │
                          │  HTTP on :9876       │
                          │  ╲                   │
                          │   POST /hook ◀───── hook_client.py ◀─┐
                          │                      │                │
   Claude Code CLI       │   HookHandler        │                │
   tool-call gate ──────────────────────┐       │                │
                          │             ▼       │                │
                          │   SessionRegistry   │                │
                          │   PromptQueue       │                │
                          │             │       │                │
                          │             ▼       │                │
                          │   HeartbeatScheduler (1 Hz tick)     │
                          │             │       │                │
                          │             ▼       │                │
                          │     BleLink (bleak) │                │
                          │             │       │                │
                          │             ▼       │                │
                          │     NUS RX char     │                │
                          └──────┬───────────────┘                │
                                 │                                │
                            BLE Notifications                     │
                                 │                                │
                                 ▼                                │
                          ┌──────────────┐                        │
                          │   M5StickC   │                        │
                          │   Plus       │                        │
                          │              │                        │
                          │   A/B button─┼──{cmd:permission,id…}──┤
                          │              │   over NUS TX char     │
                          │   render     │                        │
                          │   PersonaState                        │
                          └──────────────┘                        │
                                                                  │
                          (HTTP response                          │
                           with hookSpecificOutput)               │
                          ────────────────────────────────────────┘
```

## Protocol mapping

The daemon only emits fields documented in [`REFERENCE.md`](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md), with one acknowledged extension:

| Hook event           | Daemon mutation                                    | Heartbeat field changes                                      | hookSpecificOutput              |
| :------------------- | :------------------------------------------------- | :----------------------------------------------------------- | :------------------------------ |
| `SessionStart`       | `sessions[sid] = ...`                              | `total++`, entries log line                                  | `{}`                            |
| `UserPromptSubmit`   | `running.add(sid)`                                 | `running++`, entries log                                     | `{}`                            |
| `PreToolUse`         | enqueue PendingPrompt, `waiting.add(sid)`          | `waiting++`, `prompt={id,tool,hint}`, `msg="approve: <tool>"` | **block** until decision/timeout |
| (device A button)    | `pending.decision="allow"`, event.set()            | next tick: `prompt` absent (firmware clears), `waiting--`    | `permissionDecision: allow`     |
| (device B button)    | `pending.decision="deny"`                          | same                                                         | `permissionDecision: deny`      |
| 30 s timeout         | event.wait expired                                 | same                                                         | `permissionDecision: ask` (CLI falls back to native dialog) |
| `PostToolUse`        | `last_tool` updated, entries log                   | `msg="running: <tool>"`                                      | `{}`                            |
| `Stop`               | `running.discard(sid)`, completed window 4 s       | next 4 s: `completed: true` → device shows celebrate         | `{}`                            |
| `Stop` + 60 s GC     | `sessions.pop(sid)`                                | `total--`                                                    | n/a                             |
| `bypassPermissions`  | (skip enqueue)                                     | unchanged                                                    | `permissionDecision: allow` (immediate, no device round-trip) |

### About the `completed` flag

`REFERENCE.md` does not document a `completed` field, but the upstream firmware's `_applyJson` ([`src/data.h:96`](https://github.com/anthropics/claude-desktop-buddy/blob/main/src/data.h#L96)) accepts and uses it: `out->recentlyCompleted = doc["completed"] | false;`. We send it for 4 s after each `Stop` to trigger the celebrate animation. Other downstream devices that strictly follow `REFERENCE.md` simply ignore the unknown field, so the daemon stays compatible with non-firmware-bundled hardware.

### Field length caps (from upstream `src/data.h`'s `TamaState` struct)

| JSON field    | Max bytes | Daemon's truncation rule                                |
| :------------ | :-------- | :------------------------------------------------------ |
| `prompt.id`   | 39        | `f"p{counter:04d}"` — counter wraps fine within 39 chars |
| `prompt.tool` | 19        | tool_name truncated, ASCII only                         |
| `prompt.hint` | 43        | per-tool heuristic (Bash → command, Edit → basename)    |
| `entries[i]`  | 91        | `f"{HH:MM} {verb} {target}"`, ASCII only                |
| `msg`         | 23        | priority ladder: prompt > done > running > sessions > "ready" |

ASCII enforcement: non-ASCII characters get replaced with `?` before the byte-cap truncation, so a multi-byte UTF-8 sequence never gets cut mid-codepoint and the firmware never sees garbage.

## Concurrency model

- **HTTP server**: `ThreadingHTTPServer` from stdlib, one thread per request.
- **HookHandler.handle_pretooluse**: blocks the request thread on a `threading.Event` until `BleLink.on_line` resolves it (or `cfg.decision_timeout_s` elapses).
- **HeartbeatScheduler**: own thread, 1 Hz tick; wakes early on `flush_event` set by hook handlers; rate-limited by `cfg.inter_write_gap_s` (200 ms) so rapid hook bursts don't feed the ESP32 watchdog.
- **BleLink**: own asyncio event loop on a dedicated thread. Supervisor coroutine runs scan → connect → notify → write loop with exponential backoff on disconnect (1 s → 30 s capped).
- **State**: single `threading.RLock` guards `SessionRegistry` + `PromptQueue`. Snapshot is the only read path used by the heartbeat scheduler.

## Resilience properties

- **Daemon down**: `hook_client.py` fail-opens (`{}`) on connection refused. CLI uses native dialog. Nothing blocks the user.
- **Device off / out of range**: BLE supervisor enters scan loop, `/healthz` reflects `ble: disconnected`. Heartbeats queue (drop-oldest at 50). Hooks still run — PreToolUse times out at 30 s and returns `ask`.
- **Device reconnect**: BLE supervisor re-claims the link within ~2 s of the device coming back; queued prompts (if any) flush in their original order.
- **Daemon SIGTERM**: graceful shutdown cancels pending asyncio tasks, awaits their teardown, releases BLE. Verified exit code 0 (no SIGSEGV).

## Why this is a separate repo

The upstream `anthropics/claude-desktop-buddy` is a reference implementation with a frozen contribution policy ("new features... we won't take" — see its `CONTRIBUTING.md`). This plugin is an additive runtime that doesn't touch the firmware or the protocol; it's deliberately distributed standalone so:

- Any device that speaks `REFERENCE.md` (not just the M5StickC Plus reference firmware) can use this plugin
- Plugin updates ship independently of firmware revisions
- License and dependency surface stay scoped to what the daemon needs (just `bleak`)
