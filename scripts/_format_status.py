#!/usr/bin/env python3
"""Pretty-print the /healthz JSON read from stdin. Stdlib only."""
import json
import sys

try:
    d = json.load(sys.stdin)
except Exception:
    print("  (could not parse /healthz response)")
    sys.exit(0)

print(f"  ble:       {d.get('ble', '?')}")
if d.get("ble_status"):
    print(f"  ble_state: {d['ble_status']}")
print(f"  transport: {d.get('transport', '?')}")
print(
    f"  sessions:  total={d.get('sessions', 0)}"
    f" running={d.get('running', 0)}"
    f" waiting={d.get('waiting', 0)}"
)
