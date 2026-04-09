---
description: Cancel a running Golem task
argument-hint: '<task-id>'
disable-model-invocation: true
allowed-tools: Bash(python3:*), Bash(golem:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/golem-companion.py" cancel $ARGUMENTS --json`

Present the result to the user.
