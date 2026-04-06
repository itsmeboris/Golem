# CLI Reference

Complete reference for all `golem` commands and flags.

Global flags that apply to every command:

| Flag | Default | Description |
|------|---------|-------------|
| `-c, --config PATH` | `config.yaml` | Path to configuration file |
| `-v, --verbose` | off | Enable verbose (DEBUG) logging |

---

### `golem run`

Execute a task. Submits to the running daemon (auto-starting it if needed).

```
golem run [TASK_ID] [-p PROMPT] [-f FILE] [-s SUBJECT] [-C WORK_DIR] [--dry] [--mcp | --no-mcp]
```

| Flag | Description |
|------|-------------|
| `TASK_ID` | Optional issue ID to run directly (for issue-tracker profiles) |
| `-p, --prompt TEXT` | Submit an inline prompt to the daemon |
| `-f, --file PATH` | Read the prompt from a file and submit it |
| `-s, --subject TEXT` | Override the task subject/title |
| `-C, --cwd PATH` | Override the working directory for this task |
| `--dry` | Preview what would run without executing |
| `--mcp` | Enable MCP servers (keyword-scoped from task subject) |
| `--no-mcp` | Disable all MCP servers for this task |

**Examples:**

```bash
# Submit an inline prompt
golem run -p "Add retry logic to the HTTP client with exponential backoff"

# Submit from a plan file
golem run -f tasks/refactor-plan.md

# Submit with an explicit subject and working directory override
golem run -p "Fix the import cycle in utils.py" --subject "Import fix" --cwd /path/to/project

# Preview what would run (no execution)
golem run -p "Add tests for the auth module" --dry

# Run a specific GitHub issue by ID
golem run 1042
```

The `--prompt` and `--file` flags are mutually exclusive. When using either, the daemon is auto-started if not running.

---

### `golem status`

Show daemon status and recent task history.

```
golem status [--hours N] [--watch [SECS]] [--task ID]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--hours N` | `24` | Look-back window in hours |
| `--watch [SECS]` | off | Auto-refresh every SECS seconds (default: 2) |
| `--task ID` | — | Show phase-by-phase detail for a specific task ID |

**Example output:**

```
=== Golem Status (last 24h — golem) ===
  Daemon:       running (PID 48201)

  Uptime:       1h 23m 0s

  ACTIVE:
    # 1001  First-run config wizard (golem init)
           Phase: orchestrating  Model: opus  Elapsed: 4m 12s  Cost: $1.24

  Queue:        0 waiting

  RECENT:
    [OK  ]  2m 0s ago  #  998  Fix login bug                     $0.82  1m 45s
    [FAIL]  18m 0s ago #  997  Add retry logic                   $2.10  5m 30s

  HISTORY:
    Total: 47  Success: 89.4%  Avg: 2m 22s  Cost: $15.82
```

The status display shows:
- **Daemon** — whether it's running and its PID
- **Uptime** — how long the daemon has been running
- **ACTIVE** — tasks currently running, with phase, model, elapsed time, and cost
- **Queue** — number of tasks waiting to start
- **RECENT** — last few completed tasks with outcome, timing, and cost
- **HISTORY** — aggregate stats over the look-back window

---

### `golem daemon`

Manage the Golem daemon directly. When you use `golem run -p`, the daemon is started automatically — use this command when you want explicit control.

```
golem daemon [--foreground] [--log-dir PATH] [--pid-file PATH] [--port N]
```

| Flag | Description |
|------|-------------|
| `--foreground` | Stay attached to the terminal (no background fork) |
| `--log-dir PATH` | Directory for log files (default: `data/logs/`) |
| `--pid-file PATH` | PID file path (default: `data/daemon.pid`) |
| `--port N` | Dashboard port (overrides config) |

**Starting the daemon:**

```bash
# Start in background (default)
golem daemon

# Start in foreground (useful for debugging)
golem daemon --foreground
```

**Stopping the daemon:**

```bash
golem stop
```

**Restarting (applies config changes without dropping active tasks):**

```bash
kill -HUP $(cat data/daemon.pid)
# Or, after golem config set:
golem config set task_model opus   # automatically sends SIGHUP
```

**Tailing logs:**

```bash
# Last 50 lines from the daemon log
golem daemon logs 50

# Or using the REST API
curl http://localhost:8081/api/logs
```

---

### `golem dashboard`

Launch the standalone web dashboard UI.

```
golem dashboard [--port N]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port N` | `8081` (from config) | Port to serve the dashboard on |

```bash
# Start on default port 8081
golem dashboard

# Start on a custom port
golem dashboard --port 9000
```

The dashboard is also auto-started alongside the daemon when you run `golem daemon`. You do not need to run this command separately in normal operation. See [[Dashboard]] for a full guide to the UI.

---

### `golem config`

View and edit configuration.

```
golem config [get FIELD | set FIELD VALUE | list]
```

| Subcommand | Description |
|------------|-------------|
| (none) | Launch the interactive full-screen TUI editor |
| `get FIELD` | Print the current value of a config field |
| `set FIELD VALUE` | Update a field and trigger daemon reload via SIGHUP |
| `list` | List all fields and values (sensitive values masked as `***`) |

**Examples:**

```bash
# Open interactive TUI config editor
golem config

# Read a single value
golem config get task_model

# Update a value (daemon reload triggered automatically)
golem config set budget_per_task_usd 15.0
golem config set heartbeat_enabled true

# List all config keys
golem config list
```

The interactive TUI (`golem config` with no subcommand) requires `prompt_toolkit`. Install it with `pip install prompt_toolkit` or `pip install -e ".[tui]"`.

---

### `golem init`

First-run wizard that generates `config.yaml`.

```
golem init [-o OUTPUT] [--defaults]
```

| Flag | Default | Description |
|------|---------|-------------|
| `-o, --output PATH` | `config.yaml` | Output file path |
| `--defaults` | off | Use default values without prompting (non-interactive) |

```bash
# Interactive setup
golem init

# Write defaults to a custom path without prompting
golem init --defaults -o /etc/golem/config.yaml
```

The wizard prompts for:
1. Profile selection (`local`, `github`, or `redmine`)
2. Project identifiers
3. Budget per task
4. Task and orchestrator model selection

See [[Getting-Started]] for the full walkthrough.

---

### `golem cancel`

Cancel a running task.

```
golem cancel TASK_ID
```

```bash
golem cancel 1001
```

Equivalent to `curl -X POST http://localhost:8081/api/cancel/1001`.

---

### `golem stop`

Stop the running daemon.

```
golem stop [--force] [--dashboard]
```

| Flag | Description |
|------|-------------|
| `--force` | Send SIGKILL instead of SIGTERM |
| `--dashboard` | Stop the standalone dashboard instead of the main daemon |

---

### `golem batch`

Submit and query task batches.

```
golem batch submit FILE
golem batch status GROUP_ID
golem batch list
```

| Subcommand | Description |
|------------|-------------|
| `submit FILE` | Submit a batch from a JSON or YAML file |
| `status GROUP_ID` | Show status of a submitted batch by group ID |
| `list` | List all known batches |

See the [architecture docs](https://github.com/itsmeboris/golem/blob/master/docs/architecture.md) for the batch file format and `depends_on` ordering.
