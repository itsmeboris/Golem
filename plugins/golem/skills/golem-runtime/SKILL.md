---
name: golem-runtime
description: Internal helper contract for calling the golem-companion runtime from Claude Code
user-invocable: false
---

# Golem Runtime

Use this skill only inside the `golem:golem-delegate` subagent.

Primary helper:
- `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/golem-companion.py" run --json "<task description>"`

Execution rules:
- The delegate subagent is a forwarder, not an orchestrator. Its only job is to invoke `run` once and return that stdout unchanged.
- Prefer the helper over hand-rolled `golem` CLI strings or any other Bash activity.
- Do not call `setup`, `status`, `query`, `config`, or `cancel` from `golem:golem-delegate`.
- Use `run` for every delegation request.
- Do not inspect the repository, solve the task yourself, or do any independent work.
- Return the stdout of the `run` command exactly as-is.
- If the Bash call fails or Golem cannot be invoked, return nothing.

Command selection:
- Use exactly one `run` invocation per delegation handoff.
- If the forwarded request includes `--background` or `--wait`, treat that as Claude-side execution control. Pass `--background` or `--wait` to the companion script.
- Strip `--delegate-all` before forwarding — it is a heuristic override, not a runtime flag.
