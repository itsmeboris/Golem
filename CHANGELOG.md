# Changelog

All notable changes to Golem will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
