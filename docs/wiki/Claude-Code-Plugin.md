# Claude Code Plugin

Golem ships a Claude Code plugin that lets AI agents delegate complex tasks to Golem's autonomous pipeline. Instead of handling everything inline, agents can use `/golem:run` to route work through Golem's full UNDERSTAND → PLAN → BUILD → REVIEW → VERIFY cycle.

## Installation

### Prerequisites

- Golem installed (`pip install golem-agent`)
- Claude Code installed

### Install the Plugin

Run:

```bash
golem install-plugins
```

This detects your Claude Code installation (checks `~/.claude/`) and copies the plugin to `~/.claude/plugins/golem/`. The install is atomic — the existing plugin is preserved if the copy fails.

To update the plugin after upgrading Golem, run the same command again — it always overwrites with the latest version:

```bash
golem install-plugins
```

To install to a non-standard location:

```bash
golem install-plugins --plugin-dir /path/to/plugins/golem
```

**WSL note:** On WSL, Claude Code may be installed in your Windows home directory (e.g., `/mnt/c/Users/<name>/.claude/`). The installer checks both the Linux home directory and common Windows home paths under `/mnt/c/Users/` automatically. Use `--plugin-dir` if detection does not find the right location.

### Verify Installation

Start a new Claude Code session. You should see a startup message like:

```
[Golem] Daemon not running. Use /golem:setup to bootstrap.
```

or, if already set up:

```
[Golem] Daemon active. Repo attached. Use /golem:run to delegate tasks or /golem:status to check running tasks.
```

Type `/golem:` and the command list should appear with `setup`, `run`, `status`, `query`, `config`, and `cancel`.

---

## Quick Start

### 1. Bootstrap a Repo

From a Claude Code session in your repo, run:

```
/golem:setup
```

This does the following in order:

1. Checks that `golem` is in PATH (offers to install via `pip install golem-agent` if not found).
2. Starts the Golem daemon if it is not already running.
3. Attaches the current repo to Golem.
4. Explores the repo (stack, test commands, lint/format, CI config) and generates `golem.md` at the repo root.
5. Verifies each command listed in the `verify` section of `golem.md` actually passes (up to 2 retries per command).
6. Derives `.golem/verify.yaml` from the verified commands and adds `golem.md` to `.gitignore`.

**What is `golem.md`?**

`golem.md` is a machine-owned context file that tells Golem how to work on your repo: stack, test/lint/format commands, architecture notes, and coding conventions. It is gitignored and fully regenerated on each setup run — do not manually edit it. Put your own conventions in `CLAUDE.md` or `AGENTS.md` instead (which are human-curated and checked in).

**What is `verify.yaml`?**

`.golem/verify.yaml` is the structured verification config consumed by Golem's verifier. It is derived from the `verify` section of `golem.md` during finalize. Between setup runs, `verify.yaml` is the authoritative config — manual edits to it are preserved until the next `/golem:setup`.

### 2. Delegate a Task

```
/golem:run Add pagination to the user listing endpoint — update the API handler, tests, and OpenAPI schema
```

For large refactors, run in the background so Claude Code stays free:

```
/golem:run --background Rename UserProfile to UserAccount across all modules and update all references
```

To skip the delegation heuristic and always delegate:

```
/golem:run --delegate-all Fix the typo in config.py line 42
```

### 3. Check Progress

```
/golem:status
```

To watch live:

```
/golem:status --watch
```

To retrieve a completed task's results:

```
/golem:query 123
```

---

## Commands Reference

### /golem:setup

Bootstrap Golem for the current repository.

```
/golem:setup [--regenerate] [--update] [--skip-verify]
```

**Flags:**

| Flag | Effect |
|---|---|
| _(none)_ | Generate `golem.md` from scratch, verify commands, write `verify.yaml` |
| `--update` | Re-generate `golem.md` using the existing file as context/seed (full regeneration, not patching) |
| `--regenerate` | Ignore any existing `golem.md` and generate from scratch |
| `--skip-verify` | Write `golem.md` but skip command verification. `verify.yaml` is NOT generated. |

**What it generates:**

- `golem.md` at the repo root (gitignored, machine-owned)
- `.golem/verify.yaml` — structured test/lint/format commands (unless `--skip-verify` is used)

**Verification flow:** The setup command runs `golem-companion.py setup --verify --json`, which executes all verify commands internally with per-command timeouts, collects only the failures, and returns them as structured JSON. The AI never runs verify commands via individual Bash calls. If a command fails, the AI revises the entry and retries up to twice. Commands that cannot be made to pass are removed and the user is warned.

**Python auto-detection:** The companion auto-rewrites `python`, `python3`, and `python3.X` interpreter references to `sys.executable`, so verify commands always use the correct interpreter regardless of how the system PATH is configured.

**Command pre-check:** Before running each verify command, the companion checks whether the executable exists in PATH via `shutil.which`. Missing commands produce a clear error (`'<tool>' not found in PATH`) immediately, without wasting time attempting execution.

If you use `--skip-verify`, `golem.md` is written but `verify.yaml` is not generated. Run `/golem:setup` without the flag to produce a verified config.

---

### /golem:run

Smart task delegation to Golem's autonomous pipeline.

```
/golem:run [--background|--wait] [--delegate-all] <task description>
```

**Flags:**

| Flag | Effect |
|---|---|
| `--wait` | Foreground — poll until task completes and return a result summary |
| `--background` | Background — submit and return immediately. Check `/golem:status` for progress |
| `--delegate-all` | Bypass the delegation heuristic and always delegate |
| _(none)_ | Command picks a mode based on estimated complexity, then confirms with you |

**Delegation heuristic:** Before delegating, the command evaluates whether the task is large enough to warrant Golem. See [The Heuristic](#the-heuristic) below. Use `--delegate-all` to override.

**Prompt shaping:** If `golem.md` exists, the command reads it and enriches the task prompt with relevant conventions, test commands, and architectural notes before forwarding to Golem.

**Execution flow:** `/golem:run` routes to a lightweight `golem-delegate` subagent, which calls `golem run` via the companion script and returns structured output.

---

### /golem:status

Show Golem daemon health, active tasks, and recent task history.

```
/golem:status [task-id] [--watch [seconds]] [--hours N]
```

| Argument | Effect |
|---|---|
| _(none)_ | Show daemon state + last 24 hours of tasks |
| `task-id` | Show status for a specific task |
| `--watch` | Auto-refresh every 5 seconds (default); pass a number for custom interval |
| `--hours N` | Look back N hours instead of 24 |

Output is presented as a Markdown table showing both Golem's daemon status and any tasks delegated in the current session.

---

### /golem:query

Retrieve results from a completed Golem task.

```
/golem:query <task-id> [--raw]
```

**Default mode (summary):**

1. Verdict — PASSED, FAILED, or RUNNING (derived from task state and verification result)
2. Summary — 1-3 sentences describing what Golem did
3. Files changed — list derived from `git diff` against the commit SHA
4. Verification status — test count, lint status, coverage
5. Suggested next steps

**Raw mode (`--raw`):** Returns the full session dict from Golem's control API plus raw trace file contents (all 5 phases, tool calls, timing). Use this for debugging.

The companion script queries `http://localhost:<port>/api/sessions/<task-id>` using the API key from `~/.golem/config.yaml`.

---

### /golem:config

View and edit Golem configuration values.

```
/golem:config [get|set|list] [field] [value]
```

Thin wrapper around `golem config`. Use `list` to see all settings, `get <field>` to read one, and `set <field> <value>` to change one.

---

### /golem:cancel

Cancel a running Golem task.

```
/golem:cancel <task-id>
```

Requires the numeric task ID. Use `/golem:status` to find the ID of a running task.

---

## How Delegation Works

### The Heuristic

`/golem:run` evaluates the task description against delegation signals before forwarding to Golem. This is a judgment call, not a scored formula.

**Positive signals (delegate to Golem):**

| Signal | Weight | What to look for |
|---|---|---|
| File scope >3 files | High | Multiple files, directories, or "across the codebase" |
| Cross-cutting change | High | Refactor, rename, migration, API change across modules |
| Needs verification pipeline | Medium | Task requires writing tests, fixing lint, multi-step validation |
| Multi-step implementation | Medium | Task involves plan → build → verify cycle |
| Independence | Medium | No dependency on the current conversation state |

**Negative signals (keep inline):**

| Signal | Weight | What to look for |
|---|---|---|
| Single-file fix | High | Simple bug fix, typo, config change in one file |
| Conversational | High | Needs back-and-forth, clarification, design decisions |
| Depends on dirty local state | High | Uncommitted changes, unsaved buffers that Golem cannot see |
| Needs secrets/env not in config | High | Task requires credentials Golem does not have access to |
| Interactive diagnosis | Medium | Debugging that requires real-time observation |
| Current-context dependent | Medium | Task references "this file" or "what we just discussed" |

**Decision outcomes:**

1. **Delegate** — positive signals dominate. The command tells you what Golem will do and which execution mode it recommends.
2. **Too small** — negative signals dominate. The command says why and suggests handling inline. Mentions `--delegate-all` as override.
3. **Uncertain** — mixed signals. The command asks you: `Delegate to Golem (Recommended)` / `Handle inline`.

Use `--delegate-all` to bypass all heuristics.

### Execution Flow

When you run `/golem:run`:

1. **Parse arguments** — extract `--background`, `--wait`, `--delegate-all`, and the task description.
2. **Heuristic evaluation** — unless `--delegate-all`, evaluate the task description against the delegation signals. Stop here if the task is too small (unless you confirm).
3. **Prompt shaping** — if `golem.md` exists, read it and enrich the task prompt with repo context.
4. **Mode selection** — if neither `--wait` nor `--background` was passed, estimate complexity and suggest a mode. Multi-file tasks default to background; focused tasks to foreground.
5. **Delegate** — route to the `golem-delegate` subagent. The subagent calls `python3 golem-companion.py run --json <prompt>` and returns the result. `--delegate-all` is stripped and not forwarded.
6. **Result presentation** — foreground tasks show a structured summary (verdict, files changed, verification status). Background tasks tell you to check `/golem:status`.

The `golem-delegate` subagent is a thin forwarder — it does not inspect the repo, evaluate heuristics, or attempt the task itself. If Golem cannot be invoked, it reports the failure and stops.

**Note:** Always use `/golem:run` for delegation — do not call the `golem-delegate` agent directly, as it skips heuristic evaluation and prompt shaping.

### golem.md — Project Context

`golem.md` is the bridge between your repo's conventions and Golem's autonomous agents. It is generated by `/golem:setup` via repo exploration, not by reading existing config files alone.

Structure:

```
# Project: <name>

## Stack
- Language: <language and version>
- Framework: <framework(s)>
- Package manager: <tool> (<config file>)
- CI: <CI system>

## Commands

### verify
- **role:** `test` | **cmd:** `["pytest", "tests/", "-x", "-q"]` | **timeout:** 300
- **role:** `lint` | **cmd:** `["pylint", "--errors-only", "src/"]` | **timeout:** 30
- **role:** `format` | **cmd:** `["black", "--check", "src/"]` | **timeout:** 30

### build
- **install:** `pip install -e .`

### serve
- **dev-server:** `uvicorn src.main:app --reload`

## Architecture
<key modules and their roles>

## Conventions
<coding style, commit conventions, branch strategy>

## Notes
<anything unusual the AI noticed>
```

Only `verify` commands are tested during setup and written to `.golem/verify.yaml`. The `build` and `serve` sections are informational only and are never run automatically.

**Machine-owned:** `golem.md` is gitignored and fully regenerated on each `/golem:setup` run. Do not manually edit it. Put your own conventions in `CLAUDE.md` or `AGENTS.md`.

**Relationship to `verify.yaml`:** `golem.md` is the source from which `verify.yaml` is derived during setup finalization. Between setups, `verify.yaml` is the authoritative verification config. Manual edits to `verify.yaml` are preserved until the next `/golem:setup`.

**`--update` vs `--regenerate`:**
- `--update` — re-generates `golem.md` with the existing file as context/seed (full regeneration, not surgical patching)
- `--regenerate` — discards the existing `golem.md` entirely and generates from scratch

---

## Architecture

### Plugin Structure

```
plugins/golem/
├── .claude-plugin/
│   └── plugin.json              # Plugin manifest (name, version, description)
├── commands/
│   ├── setup.md                 # /golem:setup
│   ├── run.md                   # /golem:run
│   ├── status.md                # /golem:status
│   ├── query.md                 # /golem:query
│   ├── config.md                # /golem:config
│   └── cancel.md                # /golem:cancel
├── agents/
│   └── golem-delegate.md        # Thin delegation subagent (sonnet)
├── skills/
│   ├── golem-runtime/           # Internal: CLI invocation contract
│   ├── delegation-heuristics/   # Internal: when to delegate vs inline
│   └── golem-result-handling/   # Internal: presenting Golem output
├── hooks/
│   └── hooks.json               # SessionStart/SessionEnd lifecycle hooks
├── scripts/
│   ├── golem-companion.py       # Main companion script entry point
│   └── lib/
│       ├── daemon.py            # Daemon lifecycle: ensure_running, attach_repo
│       ├── setup_flow.py        # Repo signal collection; finalize_setup
│       ├── delegation.py        # Task metadata structuring
│       └── state.py             # Session-local job tracking and stats
└── prompts/
    └── golem-md-template.md     # Template for golem.md generation
```

The plugin source lives at `plugins/golem/` in the Golem repo. `golem install-plugins` copies it to `~/.claude/plugins/golem/`.

### Companion Script

`golem-companion.py` is the single Python entry point for all plugin-to-Golem communication, mirroring the Codex pattern:

```bash
python3 golem-companion.py <subcommand> [args] [--json]
```

All subcommands accept `--json` for machine-readable output. Human-readable by default.

| Subcommand | Purpose |
|---|---|
| `setup` | Check golem CLI, start daemon, attach repo, return repo signals |
| `setup --finalize` | Derive `verify.yaml` from `golem.md`, update `.gitignore` |
| `run` | Wrap `golem run`, add session job tracking |
| `status` | Wrap `golem status`, enrich with session-local job info |
| `query` | Query Golem's HTTP control API for task results |
| `config` | Wrap `golem config get/set/list` |
| `cancel` | Wrap `golem cancel`, update session job state |
| `session-start` | Hook handler — daemon/repo health check, stdout context injection |
| `session-end` | Hook handler — flush session stats |

**`scripts/lib/` modules:**
- `daemon.py` — `is_golem_installed`, `is_daemon_running`, `ensure_running`, `attach_repo`, `is_repo_attached`
- `setup_flow.py` — `collect_repo_signals` (detected config files for the AI agent), `finalize_setup` (parse `golem.md` verify section → write `verify.yaml`)
- `delegation.py` — `structure_task_metadata` (structures task keywords and file references as JSON)
- `state.py` — `record_delegation`, `update_job_status`, `get_session_jobs`, `get_session_stats`, `flush_stats_to_global`

### Lifecycle Hooks

The plugin registers two lifecycle hooks in `hooks.json`:

**SessionStart** — runs when a Claude Code session opens:
- Calls `golem-companion.py session-start` (5 second timeout).
- If Golem is not installed: exits silently (exit 0, no output).
- If the daemon is running and repo is attached: prints:
  ```
  [Golem] Daemon active. Repo attached.
  IMPORTANT: For complex tasks (multi-file changes, refactors, features needing tests), use /golem:run to delegate to Golem's autonomous pipeline instead of handling inline. Golem runs UNDERSTAND → PLAN → BUILD → REVIEW → VERIFY with full test/lint verification.
  Use /golem:status to check running tasks.
  ```
- If the daemon is running but repo is not attached: prints `[Golem] Daemon active but repo not attached. Use /golem:setup to bootstrap.`
- If the daemon is not running: prints `[Golem] Daemon not running. Use /golem:setup to bootstrap.`

Hook stdout appears as context in the conversation. A nonzero exit code is logged but does not suppress the plugin.

**SessionEnd** — runs when a Claude Code session closes:
- Calls `golem-companion.py session-end` (5 second timeout).
- Flushes session delegation stats to `~/.golem/data/plugin-stats.json`.
- Always exits 0 — never blocks session teardown.

---

## Troubleshooting

### "Golem not found" on /golem:setup

The `golem` CLI is not in PATH. Install it:

```bash
pip install golem-agent
```

Then run `/golem:setup` again. The command will also offer to install it for you via `AskUserQuestion`.

### "Daemon not running"

The SessionStart hook or `/golem:status` shows the daemon is down. Start it:

```bash
golem daemon
```

Or run `/golem:setup` — it starts the daemon as part of bootstrapping.

### Plugin not showing up in Claude Code

Check that the plugin was installed:

```bash
ls ~/.claude/plugins/golem/
```

If the directory is missing, run:

```bash
golem install-plugins
```

Then start a new Claude Code session. On WSL, also check `/mnt/c/Users/<your-windows-username>/.claude/plugins/golem/`.

### /golem:run says "too small"

The delegation heuristic determined the task is better handled inline. The command tells you the specific reason. To override and delegate anyway:

```
/golem:run --delegate-all <task description>
```

### verify.yaml not generated

This happens when `/golem:setup --skip-verify` was used. `golem.md` is written but `verify.yaml` is not generated because commands were not verified.

Run setup again without the flag to generate a verified config:

```
/golem:setup
```

This will re-verify the existing commands and write `verify.yaml`. If the commands in `golem.md` cannot be made to pass, revise the file before re-running.

### /golem:query cannot reach daemon

`/golem:query` connects to Golem's HTTP control API at `localhost:<port>`. If the daemon is not running, start it with `golem daemon` or `/golem:setup`. The port and API key are read from `~/.golem/config.yaml` (`dashboard.port` and `dashboard.api_key`).
