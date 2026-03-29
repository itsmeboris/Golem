# Changelog

All notable changes to Golem will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.1] — 2026-03-29

### Security
- Sandbox config wired through CLIConfig to `make_sandbox_preexec()` — `sandbox_cpu_seconds`/`sandbox_memory_gb` now flow from config (SEC-007b)
- Sandbox preexec applied to ~38 subprocess.run calls across all modules — verifier, flow, heartbeat, validation, self-update, worktree manager, committer, ast analysis, git utils, merge review, github backend, init wizard (SEC-007c, SEC-007d)
- Sandbox `setrlimit()` failure logging upgraded from DEBUG to WARNING with resource ID context (INFRA-018)
- XSS fix: `esc()` and `_esc()` now escape single quotes (`'` → `&#39;`) and double quotes (`"` → `&quot;`) to prevent injection in inline onclick handlers (SEC-012)

### Added
- **Toast notifications** — replaced 9 `alert()` calls with styled toast/snackbar; success/error/info variants; auto-dismiss after 4s (UX-006, UX-006b)
- **Loading states** — CSS loading-spinner and loading-overlay classes; skeleton cards on initial load; spinners on fetch calls with cache guard (UX-007)
- **Keyboard shortcuts** — Escape (close), ArrowUp/Down (navigate tasks), Ctrl/Cmd+K (focus search), 1-5 (switch tabs) (UX-008)
- **Mobile-responsive layout** — `@media` queries at 1024px and 600px breakpoints; 44px touch targets; vertical stacking; scrollable tab bars (UX-008)
- **Copy-to-clipboard** — click-to-copy for task IDs, prompt hashes, commit SHAs, and error text with toast feedback (UX-010)
- **Deep linking** — hash-based URL routing (#overview, #merge-queue, #prompts, #task/<id>); supports browser back/forward and bookmarks (UX-011)
- **Data visualizations** — sparkline() for inline SVG trend lines; barChart() for horizontal CSS bar charts; overview stats panel with success rate, cost-by-model, and phase duration charts (UX-009)
- **Knowledge graph wiring** — `subject` now passed from session to `build_system_prompt()`; query-side punctuation stripping matches index-side (FEAT-002c)
- **Structured logging installation** — `setup_logging()` installs `TaskContextFilter` on root logger; `json_logging` config option activates `JsonFormatter`; idempotent (FEAT-007b, FEAT-007c)
- **Prompt evaluation loop** — periodic `_run_prompt_evaluation()` in detection loop; opt-in via `prompt_evaluation_enabled` config (FEAT-005b)
- **OTel spans** — instrumented orchestrator tick/BUILD/VERIFY/REVIEW and flow detection/session lifecycle; GenAI semantic convention attributes for token accounting (FEAT-006b)
- **Error handling rule** — `.claude/rules/error-handling.md` with retry, circuit breaker, fallback, and asyncio patterns (INFRA-012)
- **Security patterns rule** — `.claude/rules/security.md` with path traversal, subprocess, XSS, secret handling, CORS patterns (INFRA-016)
- **Git workflow rule** — `.claude/rules/git-workflow.md` with branch naming, commit format, FF-only merge, pre-push checks (INFRA-015)
- **Error recovery skill** — `.claude/skills/error-recovery/` with failure classification, recovery protocol, phase-specific guidance (INFRA-013)
- **Phase transition criteria** — exit criteria table and transient vs deterministic failure guidance in `orchestrate_task.txt` (INFRA-017)
- **CLAUDE.md sections** — data models (TaskSession, Milestone, VerificationResult), state persistence table, dependency notes (INFRA-014b)

### Fixed
- Test quality audit — fixed 3 zero-assertion tests in test_flow; improved shallow assertions in test_heartbeat_worker (TEST-007)
- `setup_logging()` idempotency — duplicate filter guard; `_tick_human_review` now sets task context (FEAT-007c)

---

## [0.3.0] — 2026-03-29

### Security
- API key authentication on all `/api/*` endpoints except health probe (SEC-005, SEC-010, SEC-011)
- CORS middleware restricted to localhost/127.0.0.1 origins (SEC-003)
- In-memory sliding-window rate limiter (10 req/min) on mutation endpoints (SEC-004)
- `O_NOFOLLOW` atomic file open in `/api/submit` to prevent symlink TOCTOU race (SEC-009)
- Path traversal fix in `/api/submit` — removed untrusted `work_dir` from allowed bases (SEC-001)
- Path traversal fix in dashboard trace/prompt/report resolution (SEC-002)
- Path traversal fix in merge_review `_read_file_content` (SEC-008)

### Added
- **A-Mem knowledge graph** — `KnowledgeGraph` with keyword/file-ref indexing, relevance-scored queries, and selective pitfall injection into agent context (FEAT-002)
- **CLI `logs` command** — `golem logs -n 50 --follow` with tail and follow modes, most-recent log file selection by mtime (FEAT-004)
- **Evaluator-optimizer loop** — `PromptEvaluator` tracks per-prompt pass/fail rates; `PromptOptimizer` detects underperforming prompts and generates optimization suggestions (FEAT-005)
- **OpenTelemetry tracing** — optional OTel integration with `init_tracing()`, `get_tracer()`, `trace_span()`; NoOp fallback when OTel SDK is absent; `otel_enabled`/`otel_endpoint`/`otel_console_export` config fields (FEAT-006)
- **Structured logging** — `TaskContextFilter` injects `task_id`/`phase` via contextvars; `JsonFormatter` for machine-parseable logs; `json_logging` config option (FEAT-007)
- **MCP tool schema validation** — `validate_tool_schema()` checks required fields, name format, description length, input schema, and prompt injection patterns; `validate_and_filter_tools()` in `KeywordToolProvider` (SEC-006)
- **Subprocess sandboxing** — `SandboxLimits` + `make_sandbox_preexec()` using `resource.setrlimit` for CPU, memory, file size, process count, and open files; wired into `cli_wrapper.py` Popen calls; `sandbox_enabled`/`sandbox_cpu_seconds`/`sandbox_memory_gb` config (SEC-007)
- **Mutation testing** — mutmut configured in `pyproject.toml`; `make mutation` and `make mutation-report` targets (TEST-002)
- **Context budget system** — `ContextBudget` with token estimation, priority-based section fitting, and `context_budget_tokens` config field (FEAT-001)
- **Dashboard prompt comparison** — Prompts tab with per-prompt analytics table showing hash, runs, success rate bar, cost, duration (FEAT-003)
- **Dashboard empty states** — first-time guidance when no tasks; no-match feedback when filters return nothing (UX-005)
- **Dashboard pagination and search** — client-side search by ID/subject/state, state dropdown filter, 25-per-page pagination (UX-002)
- **Error handling rule** — `.claude/rules/error-handling.md` (INFRA-012)
- **Security patterns rule** — `.claude/rules/security.md` (INFRA-016)
- **Git workflow rule** — `.claude/rules/git-workflow.md` (INFRA-015)
- **Error recovery skill** — `.claude/skills/error-recovery/` (INFRA-013)
- **Phase transition criteria** — exit criteria and transient vs deterministic failure guidance in `orchestrate_task.txt` (INFRA-017)
- **Expanded CLAUDE.md** — architecture overview, concurrency model, common pitfalls, async patterns, git workflow (INFRA-014)
- State management and contract lint wired into pre-push hook as non-blocking warnings (INFRA-001, INFRA-002)
- Graceful shutdown with state save, task drain, and `finally` block awaiting (REL-009, REL-011)
- Notifier retry logic — 2 retries with 1s backoff on Slack/Teams (REL-001)
- Subprocess timeouts — `_detect_base_branch` 30s, rsync 120s, GitHub CLI 60s (REL-002, REL-010)
- Ensemble cost budget guard before spawning candidates (REL-003)
- Validation loop cost overflow guard (REL-005)
- Two-phase atomic session + batch state save (REL-007)
- Corrupt `batch_monitor` JSON handling — preserve in-memory state on disk corruption (REL-008)
- Startup dependency validation — checks git/claude in PATH before entering main loop (INFRA-011)
- Orphaned worktree cleanup on daemon restart (INFRA-009)
- Trace/checkpoint data retention — auto-prune files older than 30 days (INFRA-010)
- Configurable `verification_timeout_seconds` config field (INFRA-007)
- AGENTS.md growth bound — age out high-seen pitfall entries after 90 days (INFRA-004)
- Dashboard accessibility — ARIA roles/labels, focus-visible outline, WCAG AA contrast (UX-001)
- Confirm dialogs on destructive dashboard actions (UX-003)
- `AbortSignal.timeout` on all frontend fetch calls — 10s GET, 30s POST (UX-004)

### Fixed
- Merge queue thread safety — dual lock (asyncio + threading) for concurrent reads (BUG-001, BUG-005)
- Grace deadline parse crash on empty string (BUG-002)
- `_bisect_merges` IndexError on empty SHA list (BUG-003)
- `fix_iteration` not passed to notifier calls (BUG-004)
- Merge agent receives verification context on post-merge failure (BUG-006, BUG-009)
- Supervisor `_verification_feedback()` using wrong dict keys (BUG-010)
- Self-update diff truncation notice missing (BUG-007)
- `prompts.py` docstrings stale after `_SafeDict` change (BUG-008)
- Explicit `--config` path silently falling back to defaults (BUG-011)
- Env var expansion silent on missing variable (BUG-012)
- Data retention cleanup crashing on TOCTOU/permission errors (BUG-013)
- Merge queue callback exception leaving state inconsistent (REL-004)
- Checkpoint phase not cleared on crash recovery (REL-006)
- Prompt placeholder literals rendering when conditions unmet (INFRA-003)
- Self-update temp file leak with hardcoded `/tmp/golem-verify` path (INFRA-005)
- Clarity check fail-open logging at WARNING instead of ERROR (INFRA-006)
- Verification timeout not propagated to MergeQueue (INFRA-008)
- Tautological tests, `str()` substring matching, misleading mock (TEST-001)

### Changed
- TODO.md restructured with priority-first sections and category-based IDs (BUG/SEC/REL/INFRA/FEAT/TEST/UX)

---

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
