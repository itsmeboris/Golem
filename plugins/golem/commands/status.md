---
description: Show Golem daemon health, active tasks, and recent task history
argument-hint: '[task-id] [--watch [seconds]] [--hours N]'
disable-model-invocation: true
allowed-tools: Bash(python3:*), Bash(golem:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/golem-companion.py" status $ARGUMENTS --json`

Present the output as a Markdown table. Include both Golem's daemon status and any session-local jobs.

If `--watch` was passed, the companion script handles auto-refresh internally.
