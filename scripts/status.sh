#!/bin/bash
# Print daemon liveness and BLE connection state. No external deps (no jq).

source "$(dirname "$0")/_common.sh"

if ! is_alive "${PID_FILE}"; then
  echo "daemon: not running"
  exit 0
fi

PID=$(cat "${PID_FILE}")
echo "daemon: running (pid ${PID})"

HEALTHZ=$(curl -sf "http://127.0.0.1:${PORT}/healthz" 2>/dev/null || echo "")
if [ -z "${HEALTHZ}" ]; then
  echo "  /healthz: unreachable on port ${PORT}"
  exit 0
fi

echo "${HEALTHZ}" | python3 "$(dirname "$0")/_format_status.py"
echo "  log:       ${LOG_FILE}"
