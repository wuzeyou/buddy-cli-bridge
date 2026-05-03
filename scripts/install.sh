#!/bin/bash
# Idempotent: creates venv at $CLAUDE_PLUGIN_DATA/venv and installs bleak.

source "$(dirname "$0")/_common.sh"

mkdir -p "${CLAUDE_PLUGIN_DATA}"

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
  echo "ERROR: python3 >= 3.10 is required" >&2
  python3 --version >&2
  exit 1
fi
PY_VER=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')
echo "✓ python3 ${PY_VER}"

if [ ! -x "${VENV_DIR}/bin/python" ]; then
  echo "→ creating venv at ${VENV_DIR}"
  python3 -m venv "${VENV_DIR}"
fi

# PIP_USER=0 sidesteps user-level pip.conf settings that conflict with `pip install -t`
# inside venvs (a common Python 3.14 + PEP 668 footgun).
if ! "${VENV_DIR}/bin/python" -c "import bleak" 2>/dev/null; then
  echo "→ installing bleak"
  PIP_USER=0 "${VENV_DIR}/bin/pip" install --quiet --upgrade pip
  PIP_USER=0 "${VENV_DIR}/bin/pip" install --quiet bleak
fi

BLEAK_VERSION=$("${VENV_DIR}/bin/python" -c "import importlib.metadata as m; print(m.version('bleak'))")
echo "✓ bleak ${BLEAK_VERSION} installed at ${VENV_DIR}"
echo ""
echo "→ next:  in Claude.app, Developer → Open Hardware Buddy… → Disconnect"
echo "         (BLE is single-link; daemon needs the device to itself)"
echo "→ then:  /buddy-start"
