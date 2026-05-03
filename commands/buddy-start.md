---
description: Start the Hardware Buddy CLI bridge daemon. Use when the user wants to launch the buddy.
disable-model-invocation: true
allowed-tools: Bash
---

```!
export CLAUDE_PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT}"
export CLAUDE_PLUGIN_DATA="${CLAUDE_PLUGIN_DATA}"
"${CLAUDE_PLUGIN_ROOT}/scripts/start.sh"
```

Print the start script's output above to the user verbatim. Do not add commentary.
