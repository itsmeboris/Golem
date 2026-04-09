# Troubleshooting

Practical diagnosis and recovery guides for common Golem issues.

---

### Task Stuck in RUNNING

**Symptoms:** `golem status` shows a task in RUNNING state for much longer than expected. Cost keeps climbing. No visible progress in the dashboard timeline.

**Diagnosis steps:**

1. Check elapsed time and cost in `golem status` to confirm the task is truly stuck (not just a complex task)
2. Check `task_timeout_seconds` in [[Configuration]] — the default is 3600 (1 hour). The task may not have timed out yet
3. Open the dashboard Task Detail tab to inspect the current phase — is the agent looping on REVIEW?
4. Check daemon logs for subprocess errors:
   ```bash
   golem logs -n 100
   # or via API
   curl http://localhost:8081/api/logs
   ```
5. Check the parsed trace for the last tool call:
   ```bash
   curl http://localhost:8081/api/trace-parsed/{task_id}
   ```

**Solution:**

Cancel the task and resubmit:

```bash
# Cancel via CLI
golem cancel 1042

# Or via API
curl -X POST http://localhost:8081/api/cancel/1042
```

If the task consistently gets stuck at the same phase, reduce the per-task budget to force an earlier stop, or reduce `inner_retry_max` (default: 3) to prevent the orchestrator from looping on repeated REVIEW failures.

---

### Verification Keeps Failing

**Symptoms:** Tasks reach VERIFYING state but keep failing, triggering retries. Dashboard shows RETRYING in a loop. Cost grows with each retry.

**Diagnosis steps:**

1. Open the dashboard Task Detail tab and navigate to the VERIFY phase
2. Or fetch the parsed trace to see the verification output:
   ```bash
   curl http://localhost:8081/api/trace-parsed/{task_id}
   ```
3. Look for the specific failure category:

| Category | Typical cause |
|----------|--------------|
| `black` failures | Code formatting violations — missing newlines, trailing whitespace, line length |
| `pylint` errors | Import errors (missing dependency), undefined names, syntax errors |
| `pytest` failures | Test failures, import errors in test files, fixture issues |
| Coverage below 100% | New code not covered by any test |

4. If the issue is a **missing dependency**, check whether the package is installed in the environment the daemon runs in — agents run in the same Python environment as the daemon
5. If **coverage is below 100%**, the agent needs to write tests for all new code paths — this is a project requirement

**Solution:**

For persistent failures, view the structured verification output:

```bash
curl "http://localhost:8081/api/trace-parsed/{task_id}" | python3 -m json.tool
```

Look for the `VERIFY` phase events. The error output from the verification commands (see .golem/verify.yaml) is captured in the trace.

You can also run the checks manually in the worktree to reproduce the error:

```bash
# Find the worktree for the task
ls data/agent/worktrees/

# Run checks manually
cd data/agent/worktrees/1042
black --check .
pylint --errors-only golem/
pylint --disable=all --enable=W0611,W0612,W0101 golem/
pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100
```

---

### Merge Queue Blocked

**Symptoms:** Dashboard Merge Queue tab shows entries stuck in Deferred. Health monitor fires `ALERT_MERGE_QUEUE_BLOCKED`. Tasks complete validation but never get committed.

**Diagnosis steps:**

1. Check the merge queue via the dashboard Merge Queue tab or API:
   ```bash
   curl http://localhost:8081/api/merge-queue | python3 -m json.tool
   ```
2. Look at deferred entry details — the reason field will show either:
   - `dirty working tree` — you (or another process) have uncommitted changes in the main repo
   - `rebase conflict` — the validated branch conflicts with recent changes on `master`

3. Check for uncommitted changes:
   ```bash
   git status
   git diff --stat
   ```

**Solution:**

For a **dirty working tree** (human edited files while daemon runs):

```bash
# Option 1: Commit your changes
git add -p && git commit -m "WIP: manual changes"

# Option 2: Stash your changes
git stash

# Then retry the deferred merge
curl -X POST http://localhost:8081/api/merge-queue/retry/1042
# Or click "Retry" in the dashboard
```

For **persistent rebase conflicts** (the validated branch has become incompatible with `master`):

1. The merge agent will attempt automatic conflict resolution
2. If it fails, the entry moves to CONFLICTS state in the merge queue
3. Resolve manually: check out the branch from the worktree, resolve conflicts, and push
4. Or cancel and resubmit the task from the current HEAD

---

### Daemon Won't Start

**Symptoms:** `golem daemon` exits immediately. `golem run -p "..."` reports the daemon did not start. `golem status` shows no daemon running.

**Diagnosis steps:**

1. **Stale PID file** — if the daemon crashed previously, its PID file may be left behind:
   ```bash
   cat ~/.golem/data/daemon.pid
   ps aux | grep golem   # check if that PID is actually running
   ```
   If the PID is not running, remove the stale file:
   ```bash
   rm ~/.golem/data/daemon.pid
   ```

2. **Port already in use** — another process may be listening on port 8081:
   ```bash
   lsof -i :8081
   # or
   ss -tlnp | grep 8081
   ```
   Either stop the conflicting process or change `dashboard.port` in `config.yaml`.

3. **Config parse errors** — a syntax error in `config.yaml` will prevent startup:
   ```bash
   python3 -c "import yaml; yaml.safe_load(open('config.yaml'))"
   ```
   Fix any YAML syntax errors (indentation, unquoted special characters, etc.).

4. **Missing dependencies** — if you installed without the `dashboard` extra:
   ```bash
   pip install -e ".[dashboard]"
   ```

**Solution:**

Start in foreground to see the full error output:

```bash
golem daemon --foreground
```

This keeps all output attached to the terminal and prevents the background fork, making errors visible immediately.

---

### Heartbeat Not Picking Up Work

**Symptoms:** Daemon is idle (no tasks running) but the heartbeat never submits any self-directed work. `golem status` shows queue empty and no activity.

**Diagnosis steps:**

1. Verify heartbeat is enabled:
   ```bash
   golem config get heartbeat_enabled
   ```
   If `false`, enable it:
   ```bash
   golem config set heartbeat_enabled true
   ```

2. Check the idle threshold — the heartbeat only activates after this many seconds of inactivity:
   ```bash
   golem config get heartbeat_idle_threshold_seconds
   # default: 900 (15 minutes)
   ```

3. Check daily budget — the heartbeat stops submitting when the daily budget is exhausted:
   ```bash
   curl http://localhost:8081/api/live | python3 -m json.tool
   # Look for heartbeat.daily_spent_usd vs heartbeat_daily_budget_usd
   ```

4. For Tier 1 (GitHub issue triage), the `profile` must support `poll_untagged_tasks`. The `github` profile supports this; the `local` profile does not. Tier 2 (self-improvement) works with any profile.

5. Check daemon logs for heartbeat tick errors:
   ```bash
   golem logs -n 50
   ```

**Solution:**

If the budget is exhausted, reset it by restarting the daemon (the daily budget counter resets) or increase `heartbeat_daily_budget_usd`:

```bash
golem config set heartbeat_daily_budget_usd 5.0
```

The heartbeat state (including daily spend) persists to `data/heartbeat_state.json` across restarts.

---

### Cost Higher Than Expected

**Symptoms:** Task costs significantly more than anticipated. Total daily spend is climbing unexpectedly.

**Diagnosis steps:**

1. Check per-task cost in `golem status` or the dashboard Overview tab
2. Fetch cost analytics:
   ```bash
   curl http://localhost:8081/api/cost-analytics | python3 -m json.tool
   ```
3. Check whether retries are happening — each retry runs the full agent pipeline again:
   ```bash
   curl http://localhost:8081/api/analytics | python3 -m json.tool
   # Look at retry_rate and avg_retries_per_task
   ```

**Key cost multipliers:**

| Factor | Effect |
|--------|--------|
| `max_retries: 2` | Each PARTIAL verdict triggers another full agent run |
| `ensemble_on_second_retry: true` | Doubles cost on second retry (runs 2 candidates in parallel) |
| `validation_model: opus` | Validation agent uses Opus — more expensive than Sonnet |
| `orchestrate_model: opus` | Orchestrator uses Opus — most tokens in multi-phase tasks |
| Heartbeat tasks | Heartbeat respects `heartbeat_daily_budget_usd` separately from task budget |

**Solution:**

Reduce costs by:

```bash
# Lower the per-task budget cap
golem config set budget_per_task_usd 5.0

# Reduce retries
golem config set max_retries 1

# Disable ensemble retry
golem config set ensemble_on_second_retry false

# Switch orchestrator to sonnet for simpler tasks
golem config set orchestrate_model sonnet
```

---

### Corrupt Checkpoint File

**Symptoms:** A task that was in progress when the daemon was killed does not
resume cleanly on restart. Daemon logs show `Checkpoint for #NNN is corrupt`.

**What happened:** The checkpoint file contains invalid JSON, most likely from
an interrupted write. The file has been automatically renamed to
`~/.golem/data/state/checkpoints/<issue_id>/checkpoint.json.corrupt` so the data is
preserved for inspection.

**Solution:**

The affected task is treated as if no checkpoint exists and will restart from
its last known phase. If you need to inspect the corrupt file:

```bash
cat ~/.golem/data/state/checkpoints/<issue_id>/checkpoint.json.corrupt
```

To force a clean restart of the task, remove the corrupt backup and resubmit:

```bash
rm -rf ~/.golem/data/state/checkpoints/<issue_id>/
golem run -p "..." # resubmit the original prompt
```

---

### Health Monitor Pausing Detection

**Symptoms:** The daemon is running but not picking up new tasks from the
issue tracker. Dashboard shows tasks pending in the tracker but none start.
Logs contain `Health status UNHEALTHY — pausing new task detection`.

**Diagnosis steps:**

Check the current health status and active alerts:

```bash
curl http://localhost:8081/api/live | python3 -m json.tool
# Look for health.status and health.active_alerts
```

The detection loop pauses when status is `unhealthy`. This is triggered by
severe alerts: `consecutive_failures`, `stale_daemon`, `disk_usage`, or
`merge_queue_blocked`.

**Solution:**

Resolve the underlying alert (e.g., fix the failing tasks, clear the merge
queue backlog, free disk space). The detection loop resumes automatically on
the next health check interval once alerts clear. To force an immediate check,
send SIGHUP:

```bash
kill -HUP $(cat ~/.golem/data/daemon.pid)
```

---

### Where to Find Logs

| Log source | Location / Command |
|------------|-------------------|
| Daemon log files | `~/.golem/data/logs/` — one file per start, named `agent_YYYYMMDD_HHMMSS.log` (background start) or `daemon_YYYYMMDD_HHMMSS.log` (foreground) |
| Latest log symlink | `~/.golem/data/logs/daemon_latest.log` |
| Tail via CLI | `golem logs -n 100` |
| Tail via API | `curl http://localhost:8081/api/logs` |
| Agent traces (JSONL) | Stored when `store_agent_traces: true` in config; viewable in dashboard Task Detail tab |
| Structured trace | `curl http://localhost:8081/api/trace-parsed/{task_id}` |
| Raw trace | `curl http://localhost:8081/api/trace/{task_id}` |

Set `log_level: DEBUG` in [[Configuration]] to get verbose output from all components:

```bash
golem config set log_level DEBUG
```

Agent traces (`store_agent_traces: true`) capture every tool call, message, and event from Claude sessions. These are the most useful for diagnosing unexpected agent behavior. View them in the dashboard Task Detail tab or via the `/api/trace-parsed/{task_id}` endpoint.
