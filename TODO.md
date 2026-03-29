# TODO

Category-based IDs. GitHub issue refs in parentheses.
See https://github.com/itsmeboris/Golem/issues

---

## Open Issues

### P0 — Critical (broken in current code)

- [x] BUG-001: **Merge queue thread safety** — added `_processing` list for in-flight entries; guarded with asyncio.Lock (2026-03-29)
- [x] BUG-002: **Grace deadline parse crash** — guard empty `grace_deadline` before parsing (GH #61, 2026-03-29)
- [x] BUG-003: **`_bisect_merges` empty-list guard** — early return None for empty list (GH #62, 2026-03-29)
- [x] SEC-001: **API file-read path traversal** — removed untrusted work_dir from allowed bases; only CWD and registry trusted (GH #63, #84, 2026-03-29)
- [x] BUG-005: **Merge queue lock-free reads** — added threading.Lock for thread-safe reads of shared state (GH #85, 2026-03-29)
- [x] SEC-008: **merge_review path traversal** — validate resolved path stays within base_dir before reading (GH #86, 2026-03-29)

### P1 — Important

- [x] BUG-004: **Notifier fix_iteration passthrough** — `fix_iteration` now passed through in both notify calls (2026-03-29)
- [x] SEC-002: **Dashboard event_id path traversal** — added `_is_within()` guard on all resolved paths (GH #64, 2026-03-29)
- [x] SEC-003: **API missing CORS protection** — added CORSMiddleware restricted to localhost/127.0.0.1 origins (GH #65, 2026-03-29)
- [x] SEC-004: **API missing rate limiting** — added in-memory sliding-window rate limiter (10 req/min) on mutation endpoints (GH #66, 2026-03-29)
- [x] SEC-005: **Dashboard API unauthenticated** — added _require_api_key to all dashboard /api/* read endpoints (GH #67, 2026-03-29)
- [x] SEC-010: **cancel_task missing API key auth** — added _require_api_key check before rate limiter (GH #98, 2026-03-29)
- [x] BUG-010: **Supervisor verification pipeline gap** — fixed _verification_feedback() to use correct dict keys (black_output/pylint_output/pytest_output) (GH #102, 2026-03-29)
- [ ] REL-009: **No graceful shutdown drain** — SIGTERM immediately cancels active tasks via `stop_tick_loop()` without draining; in-progress work aborted, worktrees left inconsistent, no checkpoint saved before cancel (GH #103)
- [ ] SEC-006: **MCP tool schema validation** — poisoning defense (GH #18)
- [ ] SEC-007: **Runtime subprocess sandboxing** — OS-level containment (GH #19)
- [x] BUG-006: **Merge agent blind to verification failures** — added verification_summary parameter to run_merge_agent and callback type (GH #88, 2026-03-29)
- [x] REL-008: **batch_monitor.load() crashes on corrupt state** — catch JSONDecodeError, preserve existing state on corruption (GH #89, 2026-03-29)
- [x] SEC-009: **TOCTOU race in API file-read validation** — atomic open with O_NOFOLLOW prevents symlink swap (GH #90, 2026-03-29)
- [x] REL-001: **Notifier delivery resilience** — added retry loop (2 retries + 1s backoff) and ERROR logging on final failure (GH #68, 2026-03-29)
- [x] REL-002: **Subprocess timeout gaps** — added timeout=30 to _detect_base_branch, timeout=120 to rsync (GH #69, 2026-03-29)
- [x] REL-003: **Ensemble cost budget guard** — check remaining budget before spawning candidates; escalate if insufficient (GH #70, 2026-03-29)
- [x] REL-004: **Merge queue callback safety** — both callback sites wrapped in try/except with graceful fallback (GH #71, 2026-03-29)
- [x] REL-005: **Validation loop cost overflow** — budget guard in _run_overall_validation() returns SKIP when exceeded (GH #72, 2026-03-29)
- [x] REL-006: **Checkpoint phase not cleared on recovery** — clear checkpoint_phase in recover_sessions() (GH #73, 2026-03-29)
- [x] TEST-001: **Test quality violations** — fixed tautological, str() substring, and misleading mock violations (GH #74, 2026-03-29)

### P2 — Normal

- [x] REL-007: **Session state save not atomic** — two-phase write: serialize both, write both to temp, rename both (GH #75, 2026-03-29)
- [ ] INFRA-001: **State management audit rule** — detect innerHTML without state preservation, polling without concurrency guards, shared mutable state in async code (GH #76)
- [ ] INFRA-002: **Codebase contract linting** — static check that function return types match consumer expectations across module boundaries (GH #23)
- [x] INFRA-003: **Prompt placeholder fallbacks** — _SafeDict returns empty string for missing keys instead of literal placeholder text (GH #77, 2026-03-29)
- [x] INFRA-004: **AGENTS.md growth bound** — entries with seen >= 10 and age > 90 days now removed by _apply_decay() (GH #78, 2026-03-29)
- [x] INFRA-005: **Self-update worktree temp file leak** — use unique temp path per SHA instead of hardcoded /tmp/golem-verify (GH #79, 2026-03-29)
- [x] INFRA-006: **Clarity check fail-open silently** — upgraded to logger.error for operator visibility (GH #80, 2026-03-29)
- [x] INFRA-007: **Hardcoded verification timeout** — added verification_timeout_seconds config field; callers use config instead of hardcoded 120 (GH #81, 2026-03-29)
- [ ] FEAT-001: **Context budget system** — dynamic prompt content sizing (GH #13)
- [ ] FEAT-002: **A-Mem knowledge graph** — structured knowledge graph for AGENTS.md (GH #14)
- [ ] FEAT-003: **Dashboard prompt comparison UI** — table/chart for `/api/analytics/by-prompt` data; API exists, frontend missing (GH #82)
- [ ] FEAT-004: **CLI `logs` command** — no `golem logs` / `golem logs --follow` subcommand; `status --watch` shows counters but not log output (GH #83)
- [x] BUG-008: **prompts.py stale docstrings** — updated to reflect empty-string default behavior (GH #99, 2026-03-29)
- [x] BUG-009: **verification_summary always empty** — second agent call with formatted verification output on post-merge verify failure (GH #100, 2026-03-29)
- [x] INFRA-008: **verification_timeout not propagated to MergeQueue** — _verify_merge now uses configurable timeout via constructor param (GH #101, 2026-03-29)
- [ ] TEST-002: **Mutation testing** — mutmut integration (GH #17)
- [x] BUG-007: **Self-update review silently truncates large diffs** — added truncation notice in prompt and logger.warning (GH #91, 2026-03-29)
- [x] UX-001: **Dashboard accessibility gaps** — added ARIA roles/labels, role=dialog on modal, focus-visible outline, raised --text-muted to WCAG AA (GH #92, 2026-03-29)
- [ ] UX-002: **Dashboard missing pagination and search** — overview renders all sessions without pagination; no search/filter by subject, ID, or state; unusable at scale (GH #93)
- [x] UX-003: **No confirmation for destructive dashboard actions** — added confirm() dialogs to clear/cancel/trigger buttons (GH #94, 2026-03-29)
- [ ] TEST-003: **Lint modules lack tests** — all 9 `golem/lint/` modules have no dedicated test files; pre-commit hooks can crash silently or produce false positives (GH #95)
- [ ] INFRA-009: **Worktree orphans on crash recovery** — daemon restart doesn't clean up stale worktrees from previous crashed runs; orphaned dirs accumulate in `data/agent/worktrees/` (GH #104)
- [ ] INFRA-010: **Trace/checkpoint data retention** — traces (`data/traces/`, 154M+) and checkpoints grow unbounded; `LoggingConfig.max_bytes`/`backup_count` defined but never wired to a handler; no cleanup policy (GH #105)
- [ ] INFRA-011: **No startup dependency validation** — daemon starts without checking git/claude in PATH, disk space, port availability; missing tools cause cryptic subprocess errors on first task (GH #106)
- [ ] BUG-011: **Explicit --config path silently falls back to defaults** — `load_config()` returns empty Config if path doesn't exist; user thinks config is applied when it's not (GH #107)

### P3 — Low Priority

- [ ] FEAT-005: **Evaluator-optimizer loop** — prompt auto-tuning (GH #15)
- [ ] FEAT-006: **OpenTelemetry tracing** — agent observability (GH #16)
- [x] UX-004: **Frontend fetch calls lack timeout** — added AbortSignal.timeout (10s GET, 30s POST) to all fetch calls (GH #96, 2026-03-29)
- [ ] TEST-004: **Multiple source modules lack test files** — 13+ modules (batch_cli, profile, prompts, core/slack, backends/github, notifiers, mcp_tools) have no dedicated tests; error paths unverified (GH #97)
- [ ] BUG-012: **Env var expansion silently returns empty on missing var** — `os.environ.get(env_var, "")` in config YAML expansion returns empty string for unset vars; required fields (API keys) become empty without warning (GH #108)

---

## Completed

- [x] BUG-E01: **Worktree and data isolation** — gitignore `data/`, all tests use `tmp_path` (GH #21, 2026-03-28)
- [x] REL-E01: **Post-merge re-verification** — run black/pylint/pytest after merge conflict resolution (GH #20, 2026-03-29)
- [x] REL-E02: **Ensemble retry wiring** — `pick_best_result()` wired into supervisor; parallel candidates with validation (2026-03-29)
- [x] REL-E03: **Integration validation binary search** — `run_integration_validation()` bisects merge order to find breakage (2026-03-29)
- [x] REL-E04: **Health check result propagation** — UNHEALTHY pauses detection; exposed via properties (2026-03-29)
- [x] REL-E05: **Silent dependency skip** — `_wait_for_dependencies()` logs warning when dep session ID missing (2026-03-29)
- [x] REL-E06: **Async subprocess blocking** — `subprocess.run()` wrapped with `asyncio.to_thread()` (2026-03-29)
- [x] REL-E07: **Handoff validation enforcement** — invalid handoffs rejected, not stored (2026-03-29)
- [x] REL-E08: **Retry signal promotion** — promoted signals drive escalation on last retry (2026-03-29)
- [x] REL-E09: **Human feedback loop guard** — identical feedback detection + retry cap (2026-03-29)
- [x] REL-E10: **Checkpoint restoration resilience** — corrupt checkpoints backed up to `.corrupt`, logged at ERROR (2026-03-29)
- [x] FEAT-E01: **SSE-based dashboard live updates** — replace 5s polling with SSE (GH #1, 2026-03-27)
- [x] FEAT-E02: **Validator fix-cycle depth** — multi-iteration build-review-fix loop (GH #3, 2026-03-27)
- [x] FEAT-E03: **Task replay + dashboard controls** — re-run, edit-and-resubmit, cancel button (GH #4, 2026-03-27)
- [x] FEAT-E04: **Post-task learning loop** — extract pitfalls into AGENTS.md after each task (GH #5, 2026-03-27)
- [x] FEAT-E05: **GitHub Issues self-serve** — let Golem pick up and close its own issues (GH #12, 2026-03-28)
- [x] FEAT-E06: **Redmine/Local heartbeat Tier 1** — `poll_untagged_tasks()` with tag filtering and error handling (2026-03-29)
- [x] TEST-E01: **Integration smoke tests** — FastAPI TestClient integration tests; `@pytest.mark.integration` (GH #6, 2026-03-27)
- [x] TEST-E02: **Dashboard API test coverage** — TestClient tests for analytics, cost-analytics, events SSE, traces (2026-03-29)
- [x] INFRA-E01: **Ghost config properties** — removed undocumented properties from ops.md (2026-03-29)
- [x] INFRA-E02: **Heartbeat state cleanup on detach** — `delete_state()` + `_sync_workers()` cleanup (2026-03-29)
