# Configuration

Complete reference for all Golem configuration settings.

---

## Config Methods

Golem supports five ways to configure it, all writing to the same `config.yaml`:

| Method | Command / Location | Notes |
|--------|-------------------|-------|
| **YAML file** | `config.yaml` in project root | Direct edit; reload with `kill -HUP $(cat data/daemon.pid)` |
| **CLI set** | `golem config set <field> <value>` | Writes atomically and sends SIGHUP to reload |
| **Interactive TUI** | `golem config` (no subcommand) | Full-screen editor with category navigation |
| **Dashboard Config tab** | `http://localhost:8081/dashboard` | Browser-based; optional daemon reload on save |
| **HTTP API** | `POST /api/config/update` | Programmatic updates; see [[Dashboard]] for API reference |

Changes made via `golem config set` or the dashboard are written atomically (temp file + rename) and the daemon receives `SIGHUP` to pick them up without restarting active tasks.

---

## Flow Settings

Settings under `flows.golem.*` in `config.yaml`.

| Setting | Default | Description |
|---------|---------|-------------|
| `profile` | `local` | Backend profile: `local`, `github`, `redmine`, or a custom profile name |
| `projects` | `["my-project"]` | Project identifiers to poll (e.g. `owner/repo` for GitHub) |
| `detection_tag` | `"[AGENT]"` | Label on issues Golem should pick up |
| `poll_interval` | `120` | Seconds between issue tracker polls in daemon mode |
| `tick_interval` | `30` | Seconds between orchestrator ticks |
| `task_model` | `sonnet` | Claude model for task execution and Builder subagents |
| `task_timeout_seconds` | `3600` | Max time per task (0 = unlimited) |
| `budget_per_task_usd` | `10.0` | Max spend per task (0 = unlimited) |
| `max_active_sessions` | `3` | Maximum concurrent tasks |
| `validation_model` | `opus` | Model for the validation agent review |
| `max_retries` | `1` | Retry attempts on PARTIAL validation verdict |
| `supervisor_mode` | `true` | Enable 5-phase orchestration with subagent delegation |
| `orchestrate_model` | `opus` | Model for the orchestrator (needs strong planning) |
| `use_worktrees` | `true` | Isolate each task in its own git worktree |
| `ast_analysis` | `true` | Run ast-grep structural rules during validation (requires `sg` binary) |
| `context_injection` | `true` | Inject `AGENTS.md` + `CLAUDE.md` into agent sessions |
| `enable_simplify_pass` | `true` | Run a code-cleanup pass between BUILD and REVIEW phases |
| `verification_timeout_seconds` | `300` | Timeout applied to each configured verification command (from .golem/verify.yaml or Python fallback) across pre-flight, post-merge, and validation runs |
| `ensemble_on_second_retry` | `false` | Spawn parallel candidates with different strategies on second retry |
| `ensemble_candidates` | `2` | Number of parallel candidates for ensemble retry |
| `auto_commit` | `true` | Commit on PASS validation verdict |
| `context_budget_tokens` | `4000` | Max token budget for system prompt context injection |
| `sandbox_enabled` | `true` | Apply OS-level resource limits to all subprocess calls |
| `sandbox_cpu_seconds` | `3600` | CPU time limit for sandboxed subprocesses (1 hour) |
| `sandbox_memory_gb` | `4` | Virtual memory limit for sandboxed subprocesses |
| `otel_enabled` | `false` | Enable OpenTelemetry tracing (requires `opentelemetry-sdk`) |
| `otel_endpoint` | `""` | OTLP exporter endpoint; empty = console export only |
| `otel_console_export` | `false` | Also export spans to console alongside OTLP |
| `json_logging` | `false` | Activate JSON log formatter with task_id/phase context |
| `prompt_evaluation_enabled` | `false` | Enable periodic prompt evaluation in detection loop |

---

## Claude Settings

Settings under `claude.*` in `config.yaml`.

| Setting | Default | Description |
|---------|---------|-------------|
| `cli_type` | `claude` | CLI backend: `"claude"` (Claude Code) or `"agent"` |
| `model` | `sonnet` | Default model (overridden per-task by `task_model`) |
| `timeout_seconds` | `3600` | Subprocess timeout for each Claude invocation |
| `max_concurrent` | `5` | Max simultaneous Claude subprocess invocations |

---

## Dashboard Settings

Settings under `dashboard.*` in `config.yaml`.

| Setting | Default | Description |
|---------|---------|-------------|
| `port` | `8081` | Port for the web dashboard and REST API |
| `admin_token` | `""` | Token required in the `Authorization` header for admin endpoints (empty = open access) |
| `api_key` | `""` | API key protecting `/api/submit` endpoints (empty = open access) |

---

## Health Settings

Settings under `health.*` in `config.yaml`.

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `true` | Enable health monitoring with threshold-based alerts |
| `check_interval_seconds` | `60` | How often to evaluate health metrics |
| `consecutive_failure_threshold` | `3` | Alert after this many consecutive task failures |
| `error_rate_threshold` | `0.5` | Alert when error rate exceeds this fraction (50%) in the rolling window |
| `queue_depth_threshold` | `10` | Alert when the queue depth exceeds this value |
| `stale_seconds` | `3600` | Alert when the daemon has been inactive for this long |
| `alert_cooldown_seconds` | `900` | Minimum time between repeated alerts of the same type (15 min) |
| `merge_deferred_threshold` | `5` | Alert when deferred (blocked) merges exceed this count |

When the computed status is `unhealthy` (triggered by `consecutive_failures`,
`stale_daemon`, `disk_usage`, or `merge_queue_blocked` alerts), the detection
loop pauses automatically until the status clears. The current status is
available via the `GolemFlow.health_status` property and the
`GolemFlow.last_health_alerts` list.

---

## Heartbeat Settings

Settings under `flows.golem.*` in `config.yaml`. The heartbeat activates when the daemon is idle and picks up self-directed work.

| Setting | Default | Description |
|---------|---------|-------------|
| `heartbeat_enabled` | `false` | Enable self-directed work when idle |
| `heartbeat_interval_seconds` | `300` | Scan frequency (5 min) |
| `heartbeat_idle_threshold_seconds` | `900` | Idle time before heartbeat activates (15 min) |
| `heartbeat_daily_budget_usd` | `1.0` | Daily spend cap for heartbeat-spawned tasks |
| `heartbeat_max_inflight` | `1` | Max concurrent heartbeat tasks |
| `heartbeat_batch_size` | `5` | Max Tier 2 candidates per batch submission |
| `heartbeat_tier1_every_n` | `3` | Force a GitHub issue task after N Tier 2 self-improvement completions |
| `heartbeat_dedup_ttl_days` | `30` | Deduplication memory TTL (days) |

---

## Self-Update Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `self_update_enabled` | `false` | Monitor own Git repo for upstream changes |
| `self_update_branch` | `master` | Remote branch to watch |
| `self_update_interval_seconds` | `600` | Poll frequency (10 min) |
| `self_update_strategy` | `merged_only` | `merged_only` (fast-forward) or `any_commit` (hard reset) |

---

## Notification Settings

Golem supports Slack and Teams notifications via incoming webhooks. Slack takes priority if both are enabled. Both notifiers retry failed sends up to 2 times with 1-second backoff; final failures are logged at ERROR level but never block the pipeline.

```yaml
# Slack (Block Kit messages via incoming webhook)
slack:
  enabled: true
  webhooks:
    golem: ${SLACK_GOLEM_WEBHOOK_URL}

# Teams (Adaptive Cards via incoming webhook)
teams:
  enabled: false
  webhooks:
    golem: ${TEAMS_GOLEM_WEBHOOK_URL}
```

---

## Logging Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `log_level` | `INFO` | Python log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `store_agent_traces` | `true` | Persist raw JSONL event traces from Claude sessions |
| `store_thinking` | `false` | Include extended thinking blocks in stored traces |

---

## Environment Variables

Sensitive values can be referenced in `config.yaml` as `${VAR_NAME}` and set in the environment:

| Variable | Description |
|----------|-------------|
| `REDMINE_URL` | Redmine base URL (e.g. `https://redmine.example.com`) |
| `REDMINE_API_KEY` | Redmine REST API key |
| `TEAMS_GOLEM_WEBHOOK_URL` | Microsoft Teams incoming webhook URL |
| `SLACK_GOLEM_WEBHOOK_URL` | Slack incoming webhook URL |
| `DASHBOARD_ADMIN_TOKEN` | Admin token for protected dashboard endpoints |

---

## Example Configs

### Minimal Local Config

For prompt-only or file-drop usage with no external integrations:

```yaml
flows:
  golem:
    enabled: true
    profile: local
    task_model: sonnet
    budget_per_task_usd: 5.0
    task_timeout_seconds: 1800
    max_active_sessions: 2

claude:
  model: sonnet
  timeout_seconds: 1800

dashboard:
  port: 8081

logging:
  log_level: INFO
  store_agent_traces: true
```

### Production GitHub Config

For a team setup polling GitHub Issues with Slack notifications and heartbeat enabled:

```yaml
flows:
  golem:
    enabled: true
    profile: github
    projects:
      - myorg/myrepo
    detection_tag: golem
    poll_interval: 60
    task_model: sonnet
    orchestrate_model: opus
    budget_per_task_usd: 10.0
    task_timeout_seconds: 3600
    max_active_sessions: 5
    validation_model: opus
    max_retries: 2
    use_worktrees: true
    context_injection: true
    heartbeat_enabled: true
    heartbeat_daily_budget_usd: 5.0

claude:
  model: sonnet
  timeout_seconds: 3600
  max_concurrent: 8

dashboard:
  port: 8081
  admin_token: ${DASHBOARD_ADMIN_TOKEN}

slack:
  enabled: true
  webhooks:
    golem: ${SLACK_GOLEM_WEBHOOK_URL}

health:
  enabled: true
  check_interval_seconds: 60
  error_rate_threshold: 0.4

logging:
  log_level: INFO
  store_agent_traces: true
```

For the full `config.yaml.example` with all available options, see the [project repository](https://github.com/itsmeboris/golem/blob/master/config.yaml.example).

---

## Plugin Settings

The Claude Code plugin uses the daemon's existing HTTP API and configuration. No additional config keys are needed.

- **Install path:** `~/.claude/plugins/golem/` (auto-detected by `golem install-plugins`; override with `--plugin-dir`)
- **API auth:** The plugin reads `dashboard.api_key` from `config.yaml` for `/golem:query` API calls
- **`golem.md`:** Generated per-repo by `/golem:setup`. Machine-owned, added to `.gitignore` automatically
- **`verify.yaml`:** Derived from `golem.md` during setup. Authoritative between setups.
