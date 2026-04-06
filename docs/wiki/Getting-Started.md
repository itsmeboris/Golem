# Getting Started

This guide walks you from zero to your first completed autonomous task.

---

## Prerequisites

Before installing Golem, you need:

- **Python 3.11+** — check with `python --version`
- **[Claude CLI](https://docs.anthropic.com/en/docs/claude-code)** — Golem wraps Claude Code as a subprocess. Install it first and verify `claude --version` works. A paid Anthropic plan (Claude Pro or API access) is required.
- **Git** — for worktree isolation and merge operations

---

## Installation

```bash
git clone https://github.com/itsmeboris/golem.git && cd golem
pip install -e ".[dashboard,tui]"
```

The `dashboard` extra installs FastAPI and uvicorn for the web UI. The `tui` extra installs `prompt_toolkit` for the interactive config editor. You can install only `pip install -e .` if you prefer a minimal setup.

Verify the install:

```bash
golem --help
```

---

## First-Run Wizard

Run `golem init` to generate a `config.yaml` tailored to your environment:

```bash
golem init
```

The wizard walks you through:

1. **Profile selection** — `local` (file-based, no external services), `github` (GitHub Issues via `gh` CLI), or `redmine` (Redmine issue tracker)
2. **Project config** — for GitHub, your `owner/repo`; for Redmine, your base URL and API key
3. **Budget settings** — per-task dollar cap (default `$10.00`) and timeout
4. **Model selection** — task model (default: `sonnet`) and orchestrator model (default: `opus`)

The wizard writes `config.yaml` in the current directory. You can re-run it at any time or edit the file directly. See [[Configuration]] for a complete reference.

---

## Running Your First Task

Submit a prompt directly — the daemon starts automatically if it is not already running:

```bash
golem run -p "Refactor the logging module to use structured JSON"
```

The CLI prints a task ID and returns immediately:

```
  Starting daemon in background...
  Daemon started (log: data/logs/agent_20260328_143022.log)

  Submitted task #1
  Track with: golem status
```

### Checking Progress

```bash
golem status
```

Example output while the task is running:

```
=== Golem Status (last 24h — golem) ===
  Daemon:       running (PID 48201)

  Uptime:       1h 23m 0s

  ACTIVE:
    # 1001  Refactor the logging module to use structured JSON
           Phase: orchestrating  Model: opus  Elapsed: 4m 12s  Cost: $1.24

  Queue:        0 waiting

  RECENT:
    [OK  ]  2m 0s ago  #  998  Fix login bug                     $0.82  1m 45s
    [FAIL]  18m 0s ago #  997  Add retry logic                   $2.10  5m 30s

  HISTORY:
    Total: 47  Success: 89.4%  Avg: 2m 22s  Cost: $15.82
```

Use `golem status --watch` to auto-refresh every 2 seconds. Use `golem status --task 1001` to see the full phase-by-phase breakdown for a specific task.

---

## What Just Happened?

When you ran `golem run -p "..."`, Golem:

1. Started the daemon in the background (if not already running)
2. Submitted your prompt as a task via the HTTP API
3. Created an isolated git worktree for the task
4. Ran the task through a **5-phase agent pipeline**:

| Phase | What happens |
|-------|-------------|
| **UNDERSTAND** | Reads 3–5 key files, assesses complexity |
| **PLAN** | Writes specification statements (SPEC-1, SPEC-2, ...) |
| **BUILD** | Dispatches builder subagents with context chaining |
| **REVIEW** | Spec compliance check, then code quality review |
| **VERIFY** | Full-suite verification (.golem/verify.yaml commands or Python fallback) |

5. After VERIFY passes, a separate **validation agent** reviews the evidence and returns a PASS / PARTIAL / FAIL verdict
6. On PASS, the result is committed via the **merge queue** (rebase onto HEAD, fast-forward merge)
7. A notification is sent (Slack, Teams, or stdout depending on config)

For the complete lifecycle including retry logic and the merge queue, see [[Task Lifecycle|Task-Lifecycle]].

---

## Typical Task Costs

| Task type | Typical cost | Typical time |
|-----------|-------------|-------------|
| Simple bug fix | $0.50–$1.00 | 1–3 min |
| New feature / endpoint | $1.00–$3.00 | 2–5 min |
| Complex refactor | $3.00–$8.00 | 5–15 min |

The `budget_per_task_usd` setting (default: `$10.00`) caps spend per task. Retries consume additional budget — see [[Configuration]] for tuning options.

---

## Next Steps

- **[[Configuration]]** — full reference for all settings, profiles, and environment variables
- **[[Dashboard]]** — web UI for live monitoring, trace inspection, and merge queue management
- **[[CLI Reference|CLI-Reference]]** — every command and flag documented with examples
- **[[Troubleshooting]]** — diagnosis and recovery guides for common issues
