---
name: golem-workspace
description: Domain knowledge for the Golem agent codebase. Use when making code changes, implementing features, fixing bugs, refactoring, or modifying any file in the golem/ package. Provides module layout, conventions, error taxonomy, and verification commands.
---

# Golem Workspace

Domain-specific intelligence for the `golem` Python package (`golem-agent` on PyPI).

## Package Layout

```
golem/
├── core/                    # Infrastructure & services
│   ├── cli_wrapper.py       # Claude CLI subprocess invocation
│   ├── config.py            # YAML config loading, Config dataclass
│   ├── control_api.py       # HTTP API (submit/batch/cancel)
│   ├── dashboard.py         # Web dashboard + WebSocket state push
│   ├── flow_base.py         # Shared flow/orchestration helpers
│   ├── stream_printer.py    # Real-time CLI output parsing
│   ├── report.py            # Markdown run reports
│   ├── commit_format.py     # Commit message formatting
│   ├── slack.py / teams.py  # Notification adapters (Block Kit / Adaptive Cards)
│   ├── live_state.py        # In-memory state for dashboard
│   ├── log_context.py       # Structured logging context
│   ├── run_log.py           # Session run log persistence
│   └── json_extract.py      # JSON extraction from LLM output
│
├── orchestrator.py          # Per-session lifecycle (detect → run → validate → merge)
├── supervisor_v2_subagent.py  # Single-session subagent orchestrator
├── flow.py                  # Detection loop, PriorityGate scheduling
├── validation.py            # Post-execution validation agent
├── merge_review.py          # Conflict resolution & reconciliation agents
├── merge_queue.py           # Sequential MergeQueue (rebase-then-merge)
├── worktree_manager.py      # Git worktree create/merge/cleanup
├── committer.py             # Git add/commit in worktrees
├── priority_gate.py         # Concurrency-limited priority scheduler
├── profile.py               # Pluggable profiles (local, redmine)
├── poller.py                # Task source polling
├── notifications.py         # Pluggable notifier (Slack/Teams)
├── mcp_scope.py             # MCP server filtering per task
├── interfaces.py            # Protocol definitions
├── errors.py                # Error taxonomy
├── prompts.py               # Template loader (str.format_map)
├── prompts/                 # .txt template files
│
├── tests/                   # Mirrors source: test_<module>.py
│   ├── test_orchestrator_v2.py
│   ├── test_supervisor_v2_subagent.py
│   ├── test_cli_wrapper*.py
│   ├── test_task_agent.py
│   └── ...
│
└── cli.py / __main__.py     # CLI entry point
```

## Error Taxonomy

| Error | Retryable | When |
|---|---|---|
| `InfrastructureError` | Yes | Worktree, permission, CWD, event loop |
| `TaskExecutionError` | No | Agent failed its task |
| `ValidationError` | Yes | Validator couldn't produce verdict |
| `TaskNotFoundError` | No | Task ID doesn't exist |
| `TaskNotCancelableError` | No | Task in non-cancelable state |

Infrastructure errors auto-retry without consuming the task retry budget.

## Key Patterns

- **Async orchestration** — `asyncio.Task` per session, `PriorityGate` for concurrency
- **CLI invocation** — `CLIConfig` + `invoke_cli_monitored` wraps Claude CLI as subprocess
- **Worktree isolation** — each session gets its own git worktree; `merge_and_cleanup` merges back
- **Sequential merge** — `MergeQueue` rebases onto HEAD before fast-forward merge
- **Pluggable profiles** — `local` (prompt-based) and `redmine` (issue tracker) customize behavior
- **Template prompts** — `prompts.py` loads `.txt` files from `prompts/`, fills `{placeholders}` via `str.format_map`

## Verification Commands

Pre-push hook chain — run all three:

```bash
black --check .
pylint --errors-only golem/
pytest --cov=golem --cov-fail-under=100
```

100% test coverage is mandatory.

## Code Style

- Black-formatted (line length default 88)
- No unnecessary comments — comments only for non-obvious logic
- No organization-specific references in shared code
- Lazy logging: `logger.info("msg %s", val)` not f-strings
- Tests: `golem/tests/test_<module>.py`, class-based (`class TestFeature:`)

## Worktree Awareness

You are running in an isolated git worktree. The orchestrator manages the git lifecycle:

- Do NOT commit — leave files as uncommitted changes
- Do NOT push to any remote
- Do NOT create or switch branches
- Focus only on your assigned task
