# Changelog

All notable changes to Golem will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] — 2026-03-28

### Added
- **Multi-repo support** — `golem attach` / `golem detach` registers directories with the daemon; heartbeat scans all attached repos with fair round-robin scheduling
- **Global home directory** — all state under `~/.golem/` (config, registry, data, heartbeat); auto-created with defaults on first run
- **Ad-hoc cwd default** — `golem run -p "..."` defaults `work_dir` to the caller's current directory
- **Git remote auto-detection** — detects `owner/repo` from git origin for Tier 1 issue triage
- **Graceful degradation** — non-git directories skip git-dependent scans but still support coverage, pitfall scanning, and ad-hoc tasks

### Changed
- **HeartbeatManager** refactored to a thin scheduler (1400 → 646 lines); per-repo scan logic extracted into `HeartbeatWorker`
- **Config search** prioritizes `~/.golem/config.yaml` over cwd
- **`golem init`** writes to `~/.golem/config.yaml` by default
- **State persistence** split into global (`heartbeat_state.json`) and per-worker (`heartbeat/<hash>.json`) files under `~/.golem/data/`

### Removed
- Single-repo fallback code from HeartbeatManager
- `GOLEM_DATA_DIR` env var seeding in `__main__.py` (DATA_DIR now derives from GOLEM_HOME)

---

## [0.1.0] — 2026-03-12

### Added
- Daemon-centric architecture — all task execution flows through the daemon
- CLI (`golem run`, `golem status`, `golem daemon`, `golem dashboard`, `golem init`)
- Parallel task execution in isolated git worktrees
- Deterministic verification pipeline (black + pylint + pytest + AST analysis + coverage delta)
- Validation agent review with structured feedback and retry
- Subagent orchestration (5-phase: Understand → Plan → Build → Review → Verify)
- Pluggable profile system (local, redmine, github)
- Batch task submission with dependency ordering
- Web dashboard with task list, detail view, and phase-aware trace timeline
- Cost analytics and budget insights
- Health monitoring and alerting (Slack/Teams)
- Checkpoint-based crash recovery
- Human feedback loop (re-attempt failed tasks with reviewer guidance)
- First-run config wizard (`golem init`)
- GitHub Issues backend profile
- Budget guardrails (`budget_per_task_usd`)
- Agent context injection (AGENTS.md + CLAUDE.md)
