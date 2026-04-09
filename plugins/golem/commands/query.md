---
description: Retrieve results from a completed Golem task, including verification status and phase trace
argument-hint: '<task-id> [--raw]'
allowed-tools: Bash(python3:*), Bash(golem:*)
---

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/golem-companion.py" query $ARGUMENTS --json
```

Present the results following the `golem-result-handling` skill:

**Default mode:**
1. Show verdict (PASSED/FAILED/RUNNING based on state + verification_result)
2. Summarize what Golem did in 1-3 sentences
3. List files changed (if commit_sha is available, run `git diff --name-only <sha>~1 <sha>`)
4. Show verification status: test count, lint status, coverage
5. Suggest next steps

**Raw mode (`--raw`):**
Present the full JSON output without summarization.

If the daemon is not running or the task is not found, tell the user and suggest `/golem:status` to check what is available.
