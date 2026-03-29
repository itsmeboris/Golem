# TODO

Category-based IDs. GitHub issue refs in parentheses.
See https://github.com/itsmeboris/Golem/issues

---

## Open Issues

### P1 — Important

- [x] SEC-006: **MCP tool schema validation** — validate_and_filter_tools() in KeywordToolProvider (GH #18, 2026-03-29)
- [ ] SEC-007: **Runtime subprocess sandboxing** — OS-level containment (GH #19)
- [ ] TEST-005: **Context budget tautological test** — `test_second_section_skipped_when_no_budget_left` result is `""` so assertion passes trivially; also shallow assertion in `test_build_system_prompt_respects_budget` (GH #114)
- [ ] TEST-006: **prompt_analytics.js route missing test** — new dashboard JS route has no coverage test; violates 100% requirement (GH #115)

### P2 — Normal

- [x] INFRA-001: **State management audit rule** — wired into pre-push hook as non-blocking warning (GH #76, 2026-03-29)
- [x] INFRA-002: **Codebase contract linting** — wired into pre-push hook as non-blocking warning (GH #23, 2026-03-29)
- [x] FEAT-001: **Context budget system** — ContextBudget with token estimation, priority-based section fitting, and configurable budget (GH #13, 2026-03-29)
- [ ] FEAT-002: **A-Mem knowledge graph** — structured knowledge graph for AGENTS.md (GH #14)
- [x] FEAT-003: **Dashboard prompt comparison UI** — added Prompts tab with table showing hash, runs, success rate bar, cost, duration (GH #82, 2026-03-29)
- [ ] FEAT-004: **CLI `logs` command** — no `golem logs` / `golem logs --follow` subcommand; `status --watch` shows counters but not log output (GH #83)
- [ ] TEST-002: **Mutation testing** — mutmut integration (GH #17)
- [x] UX-002: **Dashboard missing pagination and search** — added client-side search, state filter, and 25-per-page pagination (GH #93, 2026-03-29)
- [x] TEST-003: **Lint modules lack tests** — verified: all 9 modules have dedicated test files (10 test files total) (GH #95, 2026-03-29)

### P3 — Low Priority

- [ ] FEAT-005: **Evaluator-optimizer loop** — prompt auto-tuning (GH #15)
- [ ] FEAT-006: **OpenTelemetry tracing** — agent observability (GH #16)
- [ ] TEST-004: **Multiple source modules lack test files** — 13+ modules (batch_cli, profile, prompts, core/slack, backends/github, notifiers, mcp_tools) have no dedicated tests; error paths unverified (GH #97)

---

## Completed (2026-03-29)

### P0 — Critical

- [x] BUG-001: **Merge queue thread safety** — added `_processing` list; guarded with asyncio.Lock
- [x] BUG-002: **Grace deadline parse crash** — guard empty `grace_deadline` before parsing (GH #61)
- [x] BUG-003: **`_bisect_merges` empty-list guard** — early return None for empty list (GH #62)
- [x] BUG-005: **Merge queue lock-free reads** — added threading.Lock for thread-safe reads (GH #85)
- [x] SEC-001: **API file-read path traversal** — removed untrusted work_dir; only CWD and registry trusted; O_NOFOLLOW (GH #63, #84)
- [x] SEC-008: **merge_review path traversal** — validate resolved path stays within base_dir (GH #86)
- [x] SEC-011: **clear-failed and health_router endpoints missing auth** — added _require_api_key (GH #111)

### P1 — Security & Reliability

- [x] BUG-004: **Notifier fix_iteration passthrough** — passed through in both notify calls
- [x] BUG-006: **Merge agent blind to verification failures** — added verification_summary parameter (GH #88)
- [x] BUG-010: **Supervisor verification pipeline gap** — fixed dict keys (GH #102)
- [x] BUG-013: **data_retention cleanup crashes on TOCTOU** — wrapped stat/unlink in try/except (GH #112)
- [x] SEC-002: **Dashboard event_id path traversal** — added `_is_within()` guard (GH #64)
- [x] SEC-003: **API missing CORS protection** — CORSMiddleware restricted to localhost (GH #65)
- [x] SEC-004: **API missing rate limiting** — sliding-window 10 req/min on mutations (GH #66)
- [x] SEC-005: **Dashboard API unauthenticated** — _require_api_key on all /api/* reads (GH #67)
- [x] SEC-009: **TOCTOU race in API file-read** — atomic open with O_NOFOLLOW (GH #90)
- [x] SEC-010: **cancel_task missing API key auth** — added _require_api_key (GH #98)
- [x] REL-001: **Notifier delivery resilience** — retry loop (2 retries + 1s backoff) (GH #68)
- [x] REL-002: **Subprocess timeout gaps** — timeout=30 _detect_base_branch, timeout=120 rsync (GH #69)
- [x] REL-003: **Ensemble cost budget guard** — check budget before spawning candidates (GH #70)
- [x] REL-004: **Merge queue callback safety** — try/except with graceful fallback (GH #71)
- [x] REL-005: **Validation loop cost overflow** — budget guard returns SKIP (GH #72)
- [x] REL-006: **Checkpoint phase not cleared on recovery** — clear in recover_sessions() (GH #73)
- [x] REL-008: **batch_monitor.load() crashes on corrupt state** — catch JSONDecodeError (GH #89)
- [x] REL-009: **No graceful shutdown drain** — graceful_stop() with state save + task drain (GH #103)
- [x] REL-010: **GitHub `_gh()` wrapper missing timeout** — timeout=60 (GH #109)
- [x] REL-011: **graceful_stop doesn't await cancelled tasks** — added asyncio.gather (GH #110, #113)
- [x] TEST-001: **Test quality violations** — fixed tautological, str() matching, misleading mock (GH #74)

### P2 — Infrastructure, UX & Bugs

- [x] BUG-007: **Self-update review truncates large diffs** — truncation notice + warning (GH #91)
- [x] BUG-008: **prompts.py stale docstrings** — updated to reflect empty-string default (GH #99)
- [x] BUG-009: **verification_summary always empty** — pass formatted output on failure (GH #100)
- [x] BUG-011: **Explicit --config path falls back to defaults** — raise FileNotFoundError (GH #107)
- [x] BUG-012: **Env var expansion silent on missing var** — added logger.warning (GH #108)
- [x] REL-007: **Session state save not atomic** — two-phase write with temp + rename (GH #75)
- [x] INFRA-003: **Prompt placeholder fallbacks** — _SafeDict returns empty string (GH #77)
- [x] INFRA-004: **AGENTS.md growth bound** — age out high-seen entries after 90 days (GH #78)
- [x] INFRA-005: **Self-update worktree temp file leak** — unique temp path per SHA (GH #79)
- [x] INFRA-006: **Clarity check fail-open silently** — upgraded to logger.error (GH #80)
- [x] INFRA-007: **Hardcoded verification timeout** — configurable via config field (GH #81)
- [x] INFRA-008: **verification_timeout not propagated to MergeQueue** — via constructor param (GH #101)
- [x] INFRA-009: **Worktree orphans on crash recovery** — cleanup at startup (GH #104)
- [x] INFRA-010: **Trace/checkpoint data retention** — remove >30 day files at startup (GH #105)
- [x] INFRA-011: **No startup dependency validation** — validate git/claude in PATH (GH #106)
- [x] UX-001: **Dashboard accessibility gaps** — ARIA, focus, contrast improvements (GH #92)
- [x] UX-003: **No confirmation for destructive actions** — confirm() dialogs (GH #94)
- [x] UX-004: **Frontend fetch calls lack timeout** — AbortSignal.timeout (GH #96)

### Pre-session (2026-03-27 — 2026-03-28)

- [x] BUG-E01: **Worktree and data isolation** — gitignore `data/`, all tests use `tmp_path` (GH #21)
- [x] REL-E01: **Post-merge re-verification** — run checks after conflict resolution (GH #20)
- [x] REL-E02: **Ensemble retry wiring** — parallel candidates with validation
- [x] REL-E03: **Integration validation binary search** — bisect merge order
- [x] REL-E04: **Health check result propagation** — UNHEALTHY pauses detection
- [x] REL-E05: **Silent dependency skip** — log warning on missing dep
- [x] REL-E06: **Async subprocess blocking** — wrapped with `asyncio.to_thread()`
- [x] REL-E07: **Handoff validation enforcement** — invalid handoffs rejected
- [x] REL-E08: **Retry signal promotion** — promoted signals drive escalation
- [x] REL-E09: **Human feedback loop guard** — identical feedback detection + retry cap
- [x] REL-E10: **Checkpoint restoration resilience** — .corrupt backup at ERROR level
- [x] FEAT-E01: **SSE-based dashboard live updates** (GH #1)
- [x] FEAT-E02: **Validator fix-cycle depth** (GH #3)
- [x] FEAT-E03: **Task replay + dashboard controls** (GH #4)
- [x] FEAT-E04: **Post-task learning loop** (GH #5)
- [x] FEAT-E05: **GitHub Issues self-serve** (GH #12)
- [x] FEAT-E06: **Redmine/Local heartbeat Tier 1**
- [x] TEST-E01: **Integration smoke tests** (GH #6)
- [x] TEST-E02: **Dashboard API test coverage**
- [x] INFRA-E01: **Ghost config properties** — removed undocumented properties
- [x] INFRA-E02: **Heartbeat state cleanup on detach**
