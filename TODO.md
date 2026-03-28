# TODO

Category-based IDs. GitHub issue refs in parentheses.
See https://github.com/itsmeboris/Golem/issues

---

## Open Issues

### P0 — Critical (broken in current code)

- [x] BUG-001: **Merge queue thread safety** — added `_processing` list for in-flight entries; guarded with asyncio.Lock (2026-03-29)
- [x] BUG-002: **Grace deadline parse crash** — guard empty `grace_deadline` before parsing (GH #61, 2026-03-29)
- [x] BUG-003: **`_bisect_merges` empty-list guard** — early return None for empty list (GH #62, 2026-03-29)
- [x] SEC-001: **API file-read path traversal** — validate file path against CWD/work_dir/registry before reading (GH #63, 2026-03-29)

### P1 — Important

- [x] BUG-004: **Notifier fix_iteration passthrough** — `fix_iteration` now passed through in both notify calls (2026-03-29)
- [ ] SEC-002: **Dashboard event_id path traversal** — user-controlled `event_id` in `_resolve_paths()` used in file path construction without sufficient sanitization (GH #64)
- [ ] SEC-003: **API missing CORS protection** — no `CORSMiddleware` on FastAPI app; cross-origin requests unrestricted from any browser origin (GH #65)
- [ ] SEC-004: **API missing rate limiting** — `/api/submit`, `/api/submit/batch`, `/api/cancel` have no rate limits; resource exhaustion risk (GH #66)
- [ ] SEC-005: **Dashboard API unauthenticated** — all `/api/*` endpoints (traces, analytics, SSE) publicly accessible; no auth middleware (GH #67)
- [ ] SEC-006: **MCP tool schema validation** — poisoning defense (GH #18)
- [ ] SEC-007: **Runtime subprocess sandboxing** — OS-level containment (GH #19)
- [ ] REL-001: **Notifier delivery resilience** — all notifiers silently swallow exceptions; no retry, no failure signaling to orchestrator, no timeout on send operations (GH #68)
- [ ] REL-002: **Subprocess timeout gaps** — `_detect_base_branch()` and `rsync` in ensemble have no timeout; can hang indefinitely on unresponsive repos (GH #69)
- [ ] REL-003: **Ensemble cost budget guard** — ensemble retry spawns N parallel candidates without checking `max_cost_usd` first; can exceed budget (GH #70)
- [ ] REL-004: **Merge queue callback safety** — `on_merge_agent` callback not wrapped in try/except; exception leaves merge state inconsistent (GH #71)
- [ ] REL-005: **Validation loop cost overflow** — base orchestrate can exceed `max_cost_usd`; subsequent validation runs proceed without budget guard (GH #72)
- [ ] REL-006: **Checkpoint phase not cleared on recovery** — crashed sessions keep stale checkpoint phase on reset to DETECTED; may skip phases on restart (GH #73)
- [ ] TEST-001: **Test quality violations** — tautological tests in `test_human_feedback.py:12-29`, `str()` substring matching in `test_orchestrator_v2.py:1353` and `test_supervisor_v2_subagent.py:2786`, shallow `hasattr` assertions, misleading mock in `test_checkpoint.py:242` (GH #74)

### P2 — Normal

- [ ] REL-007: **Session state save not atomic** — crash between `save_sessions()` and `batch_monitor.save()` in `flow.py` leaves inconsistent state (GH #75)
- [ ] INFRA-001: **State management audit rule** — detect innerHTML without state preservation, polling without concurrency guards, shared mutable state in async code (GH #76)
- [ ] INFRA-002: **Codebase contract linting** — static check that function return types match consumer expectations across module boundaries (GH #23)
- [ ] INFRA-003: **Prompt placeholder fallbacks** — conditional placeholders (`{simplify_section}`, `{enhanced_review_section}`) render as literal text when conditions unmet (GH #77)
- [ ] INFRA-004: **AGENTS.md growth bound** — `pitfall_writer._apply_decay()` never removes high-seen entries; file grows unbounded over time (GH #78)
- [ ] INFRA-005: **Self-update worktree temp file leak** — `/tmp/golem-verify` not cleaned on exception; hardcoded path, no tempfile context manager (GH #79)
- [ ] INFRA-006: **Clarity check fail-open silently** — returns score=5 on any error with no warning; unclear tasks execute without operator visibility (GH #80)
- [ ] INFRA-007: **Hardcoded verification timeout** — `run_verification()` uses fixed 120s in both `orchestrator.py` and `supervisor_v2_subagent.py`; not configurable (GH #81)
- [ ] FEAT-001: **Context budget system** — dynamic prompt content sizing (GH #13)
- [ ] FEAT-002: **A-Mem knowledge graph** — structured knowledge graph for AGENTS.md (GH #14)
- [ ] FEAT-003: **Dashboard prompt comparison UI** — table/chart for `/api/analytics/by-prompt` data; API exists, frontend missing (GH #82)
- [ ] FEAT-004: **CLI `logs` command** — no `golem logs` / `golem logs --follow` subcommand; `status --watch` shows counters but not log output (GH #83)
- [ ] TEST-002: **Mutation testing** — mutmut integration (GH #17)

### P3 — Low Priority

- [ ] FEAT-005: **Evaluator-optimizer loop** — prompt auto-tuning (GH #15)
- [ ] FEAT-006: **OpenTelemetry tracing** — agent observability (GH #16)

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
