#!/bin/bash
# Sourced by other scripts. Defines paths from Claude Code's injected env vars
# (CLAUDE_PLUGIN_ROOT, CLAUDE_PLUGIN_DATA), with fallbacks for direct invocation
# from a checkout (e.g. running scripts/start.sh from inside the plugin dir).

set -euo pipefail

if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ]; then
  # plugin/scripts/_common.sh → plugin root is the parent of scripts/
  CLAUDE_PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

if [ -z "${CLAUDE_PLUGIN_DATA:-}" ]; then
  # Dev fallback: keep state next to the source tree, not under $HOME.
  # Mirrors the python-side resolution in daemon/config.py.
  CLAUDE_PLUGIN_DATA="$(cd "${CLAUDE_PLUGIN_ROOT}/.." && pwd)/.dev-state"
fi

VENV_DIR="${CLAUDE_PLUGIN_DATA}/venv"
PID_FILE="${CLAUDE_PLUGIN_DATA}/run/daemon.pid"
LOG_FILE="${CLAUDE_PLUGIN_DATA}/logs/daemon.log"
PORT="${BUDDY_CLI_PORT:-9876}"

is_alive() {
  [ -f "$1" ] || return 1
  local pid
  pid=$(cat "$1" 2>/dev/null || true)
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null
}
