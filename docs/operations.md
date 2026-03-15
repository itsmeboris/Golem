# Operations Guide

Detailed reference for Golem's autonomous operational features: heartbeat,
self-update, health monitoring, config management, and SIGHUP reload.

For architecture and agent internals, see [architecture.md](architecture.md).
For quick start and overview, see the [README](../README.md).

---

## Heartbeat — Self-Directed Work

When the daemon is idle (no external tasks for 15 minutes by default), Golem
starts looking for work on its own.

```mermaid
flowchart LR
    idle["Idle Daemon"] --> t1["Tier 1: Triage<br/>(haiku scans issues)"]
    idle --> t2["Tier 2: Self-Improve<br/>(TODOs, coverage gaps,<br/>antipatterns)"]
    t1 --> submit["Submit Task"]
    t2 --> submit
    submit --> agent["Normal Agent Pipeline"]
```

### Tier 1 — Issue Triage

Scans untagged issues from your task source, runs each through Haiku to assess
automatability, confidence, and complexity. Candidates below the confidence
threshold are skipped.

### Tier 2 — Self-Improvement

Scans the codebase for:
- TODOs/FIXMEs in recent git history
- Modules below 100% coverage
- Recurring antipatterns from `AGENTS.md`

### Deduplication

Candidates are deduplicated with a configurable TTL (default 30 days). The
dedup memory, inflight task IDs, and daily spend are persisted to
`data/heartbeat_state.json` for recovery across restarts.

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `heartbeat_enabled` | `false` | Enable self-directed work |
| `heartbeat_interval_seconds` | `300` | Scan frequency (5 min) |
| `heartbeat_idle_threshold_seconds` | `900` | Idle time before activation (15 min) |
| `heartbeat_daily_budget_usd` | `1.0` | Daily spend cap for heartbeat tasks |
| `heartbeat_max_inflight` | `1` | Max concurrent heartbeat tasks |
| `heartbeat_candidate_limit` | `5` | Max candidates per scan |
| `heartbeat_dedup_ttl_days` | `30` | Deduplication memory TTL |

---

## Self-Update — Zero-Downtime Upgrades

The daemon monitors its own Git repository for upstream changes and applies
them automatically with review, verification, and crash-loop protection.

```mermaid
flowchart LR
    poll["Poll Remote"] --> diff["Review Diff<br/>(Claude)"]
    diff -- ACCEPT --> verify["Verify in Worktree<br/>(black + pylint + pytest)"]
    diff -- REJECT --> wait["Wait for Next Poll"]
    verify -- pass --> stage["Stage Update"]
    verify -- fail --> wait
    stage -- "SIGHUP" --> reload["Drain → Merge → Restart<br/>(os.execv)"]
    reload -. "crash < 60s" .-> rollback["Rollback"]
```

### Update Pipeline

1. Polls the configured remote branch at a configurable interval
2. Reviews the diff with Claude — verdict is ACCEPT or REJECT
3. Runs full verification (`black`, `pylint`, `pytest`) in a temporary worktree
4. On next `SIGHUP`: drains active sessions (up to `drain_timeout_seconds`),
   merges the verified commit, and restarts via `os.execv`
5. If the daemon crashes within 60 seconds of an update twice, it rolls back
   automatically to the pre-update SHA

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `self_update_enabled` | `false` | Enable self-update monitoring |
| `self_update_branch` | `master` | Remote branch to watch |
| `self_update_interval_seconds` | `600` | Poll frequency (10 min) |
| `self_update_strategy` | `merged_only` | `merged_only` (fast-forward) or `any_commit` (hard reset) |

State persists to `data/self_update_state.json` including update history (last
50 entries) and pre-update SHA for rollback.

### API

`GET /api/self-update` — returns status snapshot with enabled state, branch,
strategy, last check/update timestamps, review verdict, and update history.

---

## SIGHUP Reload

The daemon handles `SIGHUP` gracefully:

1. Stops the tick loop
2. Waits up to `drain_timeout_seconds` (default 300s) for active sessions to
   complete
3. Applies any pending self-update (if staged)
4. Restarts the process via `os.execv()` — picks up fresh config automatically

### Triggering a Reload

```bash
# Automatic — golem config set sends SIGHUP to the running daemon
golem config set heartbeat_enabled true

# Manual
kill -HUP $(cat data/golem.pid)
```

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `daemon.drain_timeout_seconds` | `300` | Grace period for active sessions before forced restart |

---

## Health Monitoring

Real-time daemon health tracking with threshold-based alerting. Fires
notifications through the configured notifier (Slack, Teams, or stdout).

### Monitored Metrics

| Metric | Config Key | Default |
|--------|-----------|---------|
| Consecutive failures | `consecutive_failure_threshold` | `3` |
| Error rate (rolling window) | `error_rate_threshold` | `0.5` (50%) |
| Queue depth | `queue_depth_threshold` | `10` |
| Daemon inactivity | `stale_seconds` | `3600` (1 hour) |
| Disk usage | `disk_usage_threshold_gb` | `0` (disabled) |

### Status Tiers

- **healthy** — all metrics within thresholds
- **degraded** — one or more warnings
- **unhealthy** — critical thresholds breached

### Alert Behavior

Alerts fire through the configured notifier with a 15-minute cooldown
(`alert_cooldown_seconds: 900`) to prevent spam. The `/api/health` endpoint
includes active alerts and current metrics.

### Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `health.enabled` | `true` | Enable health monitoring |
| `health.check_interval_seconds` | `60` | Check frequency |
| `health.error_rate_window_seconds` | `900` | Rolling window for error rate (15 min) |
| `health.error_rate_min_tasks` | `4` | Min tasks in window before evaluating rate |
| `health.alert_cooldown_seconds` | `900` | Cooldown between repeated alerts |

---

## Config Management

### CLI

```bash
golem config                        # interactive TUI editor
golem config get <field>            # read a single value
golem config set <field> <value>    # update + trigger daemon reload
golem config list                   # list all fields (sensitive values masked)
```

### Interactive TUI

Full-screen editor with:
- Category-based navigation (profile, budget, models, heartbeat, self-update,
  health, integrations, dashboard, daemon, logging, polling)
- Inline editing, choice cycling, boolean toggles
- Unsaved changes tracking
- Live status messages

### Dashboard Config Tab

The web dashboard includes a Config tab with the same category-based layout.
Changes are validated and optionally trigger a daemon reload on save.

### API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Current config grouped by category with field metadata |
| `/api/config/update` | POST | Validate and apply updates; triggers reload |

### Atomic Writes

Config changes use temp file + rename to prevent corruption. The daemon is
notified via `SIGHUP` to reload without restart.

---

## Pre-Flight Verification

Before spending budget on a task, the supervisor runs `black`, `pylint`, and
`pytest` on the base branch in the worktree.

### Verified Ref Fallback

The daemon tracks the last commit SHA that passed pre-flight. If HEAD fails
verification (e.g., because commits were pushed to master while the daemon is
running), the supervisor falls back to the last-known-good commit instead of
aborting the task.

Flow:
1. Create worktree from HEAD
2. Run pre-flight verification
3. **Pass** → record HEAD SHA as verified, proceed with agent
4. **Fail + verified ref exists** → clean up worktree, recreate from verified
   ref, proceed with warning
5. **Fail + no verified ref** → abort task (same as before)

This prevents cascading failures when the base branch is temporarily broken.
