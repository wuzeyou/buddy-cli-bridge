---
description: Stop the Hardware Buddy daemon and remove runtime data (venv, logs, pid). Plugin uninstall (claude plugin uninstall) handles hooks unregistration separately.
disable-model-invocation: true
allowed-tools: Bash
---

```!
export CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}"
"${CLAUDE_PLUGIN_ROOT}/scripts/uninstall.sh"
```

Print the uninstall script's output above to the user verbatim. Do not add commentary.
