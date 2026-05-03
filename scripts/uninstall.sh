#!/bin/bash
# Stops daemon and removes the runtime data dir.
# This script does NOT touch hooks/settings.json — that's `claude plugin uninstall`'s job.

source "$(dirname "$0")/_common.sh"

if is_alive "${PID_FILE}"; then
  echo "→ stopping daemon first"
  bash "$(dirname "$0")/stop.sh"
fi

if [ -d "${CLAUDE_PLUGIN_DATA}" ]; then
  echo "→ removing ${CLAUDE_PLUGIN_DATA}"
  rm -rf "${CLAUDE_PLUGIN_DATA}"
  echo "✓ runtime data removed"
else
  echo "(no runtime data to remove at ${CLAUDE_PLUGIN_DATA})"
fi

echo ""
echo "→ to also unregister hooks and commands: claude plugin uninstall buddy-cli-bridge"
