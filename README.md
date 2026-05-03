# buddy-cli-bridge

A Claude Code plugin that drives the **official Claude Hardware Buddy** device from your **terminal CLI** — taking over the BLE bridge that Claude.app's desktop app normally provides.

> 🛠️ **Hardware**: this plugin works with any device that speaks the protocol documented in [`anthropics/claude-desktop-buddy/REFERENCE.md`](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md). The reference firmware targets an M5StickC Plus, but the plugin doesn't care about hardware specifics.

## Why this exists

Claude.app (the macOS / Windows desktop app) ships a built-in BLE bridge: open **Developer → Open Hardware Buddy…** and your buddy lights up when the desktop app needs your approval. **Claude Code CLI does not get this** — its permission prompts are stdin/stdout interactions in the terminal, completely outside the desktop app's process.

`buddy-cli-bridge` closes that gap by running a small Python daemon that:
- Listens on Claude Code's hooks (`PreToolUse`, `Stop`, etc.)
- Pushes heartbeat snapshots and permission prompts over BLE to the device
- Routes the device's A/B button presses back to the CLI

No firmware changes. No protocol changes. The daemon is wire-compatible with the desktop app — just runs in its place.

## What it does

- 🔐 **Hardware approval**: when a CLI tool call needs permission, the device shows the tool name + a hint. Press **A** to approve, **B** to deny.
- 🔄 **Session lifecycle**: `SessionStart` / `Stop` / `UserPromptSubmit` / `PostToolUse` drive the buddy's `idle` / `running` / `celebrate` states.
- ⚡ **Fail-open by design**: if the daemon is down, the device is off, or 30 s elapses with no button press, Claude Code falls back to its native terminal dialog automatically. Nothing blocks you.
- 🚦 **Mutually exclusive with the desktop app**: BLE is single-link. Run one bridge or the other — `/buddy-stop` to hand back to the desktop app, `/buddy-start` to take over.

## Requirements

- macOS or Linux (BLE via [bleak](https://github.com/hbldh/bleak); CoreBluetooth on Mac, BlueZ on Linux)
- Python ≥ 3.10
- A Hardware Buddy device speaking the [reference protocol](https://github.com/anthropics/claude-desktop-buddy/blob/main/REFERENCE.md), already paired with macOS once via Claude.app's Hardware Buddy window (so the system keychain has the LTK)

## Install

```bash
# 1. add this repo as a marketplace (one-time)
/plugin marketplace add wuzeyou/buddy-cli-bridge

# 2. install the plugin
/plugin install buddy-cli-bridge@buddy-cli-bridge

# 3. install runtime deps (creates venv, installs bleak)
/buddy-install
```

## Daily use

```bash
# 1. in Claude.app: Developer → Open Hardware Buddy → Disconnect
#    (BLE is single-link; the daemon needs the device to itself)

/buddy-start           # launches daemon, auto-scans for Claude-* device
/buddy-status          # shows daemon + BLE state
/buddy-stop            # releases BLE so Claude.app can reconnect
```

After `/buddy-start`, every CLI tool call that requires approval is mirrored to the device's screen and gated on the A/B buttons.

## Slash commands

| Command            | What it does                                          |
| :----------------- | :---------------------------------------------------- |
| `/buddy-install`   | Create venv at `${CLAUDE_PLUGIN_DATA}/venv` + install bleak |
| `/buddy-start`     | Launch the daemon (1 Hz heartbeat scheduler + HTTP `/hook` server) |
| `/buddy-stop`      | SIGTERM with 5 s grace, then SIGKILL                  |
| `/buddy-status`    | Daemon pid + BLE state + session counts               |
| `/buddy-uninstall` | Stop daemon + remove `${CLAUDE_PLUGIN_DATA}` (run `claude plugin uninstall buddy-cli-bridge` to also unregister hooks) |

## Limitations

- **CLI ↔ desktop App is mutually exclusive.** Run one or the other, not both.
- **No `tokens` / `tokens_today` reporting.** Hooks don't expose token counts, so the device's level/XP only progresses while the desktop App is connected.
- **Hint text is ASCII-only.** Non-ASCII characters in commands or filenames are stripped (the firmware's `prompt.hint` buffer is a 44-byte char array).
- **`celebrate` animation has no sound.** The reference firmware ties beep to its level-up event (every 50 K cumulative tokens), not to the `completed` flag the daemon sends on session Stop.

## Troubleshooting

### `daemon did not respond to /healthz within 5s`

Check `~/.claude/plugins/data/buddy-cli-bridge/logs/daemon.log` (or `.dev-state/logs/` in dev mode). Most likely either:
- Port 9876 already in use → set `BUDDY_CLI_PORT=<other>` in your environment
- bleak import failed → re-run `/buddy-install`

### `ble: disconnected, ble_status: no device found`

Either:
- The device is off / out of BLE range
- Claude.app is still connected to it — open the Hardware Buddy window and click Disconnect

### Daemon won't release BLE

Force kill: `kill -9 $(cat ~/.claude/plugins/data/buddy-cli-bridge/run/daemon.pid)`. The macOS BLE stack reclaims the link within a second.

## Architecture

See [`docs/cli-bridge.md`](docs/cli-bridge.md) for the full design — HTTP server, FIFO prompt queue, BLE asyncio supervisor, protocol mapping, concurrency model.

## Environment variables

| Variable                       | Default | Purpose                                                                 |
| :----------------------------- | :------ | :---------------------------------------------------------------------- |
| `BUDDY_CLI_PORT`               | `9876`  | HTTP server port                                                        |
| `BUDDY_CLI_DECISION_TIMEOUT`   | `30`    | Seconds to wait for device button before falling back to native dialog  |
| `BUDDY_CLI_OWNER`              | (auto)  | Owner name shown on device. Defaults to `git config user.name` or `$USER` |
| `BUDDY_CLI_LOG_LEVEL`          | `INFO`  | `DEBUG` / `INFO` / `WARNING`                                            |
| `BUDDY_CLI_TRANSPORT`          | `ble`   | Set to `stub` for log-only sender (dev without hardware)                |
| `BUDDY_CLI_HOME`               | (unset) | Override state directory; takes precedence over `CLAUDE_PLUGIN_DATA`    |

## Development

Hot-iterate without going through `/plugin marketplace update`:

```bash
git clone https://github.com/wuzeyou/buddy-cli-bridge.git
cd buddy-cli-bridge
python3 -m venv .dev-state/venv
PIP_USER=0 .dev-state/venv/bin/pip install bleak

# run the daemon directly
.dev-state/venv/bin/python -m daemon.daemon

# or with the helper scripts (state goes to ./.dev-state/)
BUDDY_CLI_TRANSPORT=stub scripts/start.sh
scripts/status.sh
scripts/stop.sh

# run the test suite (68 tests, no hardware needed)
python3 -m unittest discover tests
```

## License

MIT — see [LICENSE](LICENSE).

## Acknowledgements

Hardware protocol, reference firmware, and the original desktop-app integration are from [`anthropics/claude-desktop-buddy`](https://github.com/anthropics/claude-desktop-buddy). This plugin is an unofficial extension built on top of that protocol; it isn't endorsed or maintained by Anthropic.

The daemon's overall shape (HTTP-server-as-hook-bridge, FIFO prompt queue, 1 Hz heartbeat with rate limiting) is a deliberate borrow from [`op7418/m5-paper-buddy`](https://github.com/op7418/m5-paper-buddy), an analogous project for the M5Paper e-ink hardware. That project's `docs/ARCHITECTURE.md` was an invaluable reference; the WDT rate-limiting and `bypassPermissions` short-circuit are direct lessons from there.
