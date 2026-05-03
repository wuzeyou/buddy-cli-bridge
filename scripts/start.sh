#!/bin/bash
# Launch daemon in the background and wait briefly for /healthz to confirm.

source "$(dirname "$0")/_common.sh"

if is_alive "${PID_FILE}"; then
  echo "✓ daemon already running (pid $(cat "${PID_FILE}"))"
  exit 0
fi

if [ ! -x "${VENV_DIR}/bin/python" ]; then
  echo "ERROR: venv not found at ${VENV_DIR}. Run /buddy-install first." >&2
  exit 1
fi

mkdir -p "${CLAUDE_PLUGIN_DATA}/logs" "${CLAUDE_PLUGIN_DATA}/run"

# `python -m daemon.daemon` needs the plugin root on sys.path, which `cd` provides.
cd "${CLAUDE_PLUGIN_ROOT}"

nohup "${VENV_DIR}/bin/python" -m daemon.daemon \
  >> "${LOG_FILE}" 2>&1 &
disown

# Daemon writes its own pid file; poll /healthz to confirm it actually came up.
for i in 1 2 3 4 5 6 7 8 9 10; do
  sleep 0.5
  if curl -sf "http://127.0.0.1:${PORT}/healthz" > /dev/null 2>&1; then
    PID=$(cat "${PID_FILE}" 2>/dev/null || echo "?")
    echo "✓ daemon started (pid ${PID})"
    echo "  log: ${LOG_FILE}"
    echo ""
    echo "→ in Claude.app, Developer → Open Hardware Buddy… → Disconnect (if connected)"
    echo "  the daemon will auto-scan and connect within ~10s after that"
    exit 0
  fi
done

echo "ERROR: daemon did not respond to /healthz within 5s. tail of ${LOG_FILE}:" >&2
tail -20 "${LOG_FILE}" >&2 || true
exit 1
