---
description: Install the Hardware Buddy CLI bridge runtime dependencies (Python venv + bleak). Idempotent — safe to re-run.
disable-model-invocation: true
allowed-tools: Bash
---

```!
export CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}"
"${CLAUDE_PLUGIN_ROOT}/scripts/install.sh"
```

Print the install script's output above to the user verbatim. Do not add commentary.
