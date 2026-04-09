---
description: View and edit Golem configuration values
argument-hint: '[get|set|list] [field] [value]'
disable-model-invocation: true
allowed-tools: Bash(python3:*), Bash(golem:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/golem-companion.py" config $ARGUMENTS --json`

Present the output to the user. For `list`, format as a table. For `get`, show the value. For `set`, confirm the change.
