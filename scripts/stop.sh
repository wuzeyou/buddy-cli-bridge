#!/bin/bash
# SIGTERM with up to 5s grace, then SIGKILL.

source "$(dirname "$0")/_common.sh"

if ! is_alive "${PID_FILE}"; then
  echo "daemon not running"
  rm -f "${PID_FILE}"
  exit 0
fi

PID=$(cat "${PID_FILE}")
echo "→ sending SIGTERM to pid ${PID}"
kill -TERM "${PID}"

for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.5
  if ! kill -0 "${PID}" 2>/dev/null; then
    echo "✓ daemon stopped"
    rm -f "${PID_FILE}"
    exit 0
  fi
done

echo "→ daemon still alive after 5s, escalating to SIGKILL"
kill -9 "${PID}" 2>/dev/null || true
sleep 0.5
rm -f "${PID_FILE}"
echo "✓ daemon force-killed"
