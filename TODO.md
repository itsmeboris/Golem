# TODO

Category-based IDs. GitHub issue refs in parentheses.
See https://github.com/itsmeboris/Golem/issues

---

## Open Issues

### P1 — Important

- [x] SEC-006: **MCP tool schema validation** — validate_and_filter_tools() in KeywordToolProvider (GH #18, 2026-03-29)
- [x] SEC-006b: **MCP validation not wired into registration** — wired _handle_mcp_tool_validation into supervisor event callback (GH #131, 2026-03-29)
- [x] SEC-007: **Runtime subprocess sandboxing** — SandboxLimits + make_sandbox_preexec wired into cli_wrapper (GH #19, 2026-03-29)
- [x] SEC-007b: **Sandbox config dead code** — wired sandbox_cpu_seconds/sandbox_memory_gb through CLIConfig to make_sandbox_preexec(); added _sandbox_preexec helper (GH #133, 2026-03-29)
- [x] SEC-007c: **Sandbox not applied to verifier/flow/heartbeat** — added make_sandbox_preexec() to ~28 subprocess.run calls across 5 modules (GH #137, 2026-03-29)
- [x] SEC-007d: **Remaining unsandboxed subprocess calls** — added preexec_fn to worktree_manager, committer, ast_analysis, git_utils, merge_review, github, init_wizard (GH #144, 2026-03-29)
- [x] INFRA-018: **Sandbox failure silently degrades to DEBUG** — upgraded to WARNING with resource_id context (GH #140, 2026-03-29)
- [x] SEC-012: **XSS via single-quote injection in esc()** — added ' → &#39; and " → &quot; escaping (GH #146, 2026-03-29)
- [x] FEAT-002c: **Knowledge graph subject not wired** — passed subject from session to build_system_prompt(); added query punctuation stripping (GH #142, 2026-03-29)
- [x] FEAT-007b: **Logging filter/formatter never installed** — added setup_logging() with json_mode; set phase in orchestrator transitions (GH #143, 2026-03-29)
- [x] FEAT-007c: **setup_logging() not idempotent** — added isinstance guard; _tick_human_review now sets task context (GH #145, 2026-03-29)
- [x] TEST-007: **Test quality audit** — fixed 3 zero-assertion tests in test_flow; improved shallow assertions in test_heartbeat_worker (GH #116, 2026-03-29)
- [x] INFRA-012: **Error handling and async patterns rules** — added .claude/rules/error-handling.md (GH #117, 2026-03-29)
- [x] INFRA-016: **Security patterns rule** — added .claude/rules/security.md (GH #127, 2026-03-29)
- [x] TEST-005: **Context budget tautological test** — fixed assertions to verify actual content and budget effect (GH #114, 2026-03-29)
- [x] TEST-006: **prompt_analytics.js route missing test** — added test for /dashboard/prompt_analytics.js route (GH #115, 2026-03-29)

### P2 — Normal

- [x] INFRA-001: **State management audit rule** — wired into pre-push hook as non-blocking warning (GH #76, 2026-03-29)
- [x] INFRA-002: **Codebase contract linting** — wired into pre-push hook as non-blocking warning (GH #23, 2026-03-29)
- [x] FEAT-001: **Context budget system** — ContextBudget with token estimation, priority-based section fitting, and configurable budget (GH #13, 2026-03-29)
- [x] FEAT-002: **A-Mem knowledge graph** — KnowledgeGraph with keyword/file indexing and relevance-scored query (GH #14, 2026-03-29)
- [x] FEAT-003: **Dashboard prompt comparison UI** — added Prompts tab with table showing hash, runs, success rate bar, cost, duration (GH #82, 2026-03-29)
- [x] FEAT-004: **CLI `logs` command** — golem logs -n 50 --follow with tail and follow modes (GH #83, 2026-03-29)
- [x] FEAT-002b: **Knowledge graph not integrated** — wired into context_injection as priority-4 section; fixed punctuation stripping (GH #132, 2026-03-29)
- [x] FEAT-005b: **Evaluator-optimizer not wired into daemon** — added periodic _run_prompt_evaluation() in detection loop with opt-in config (GH #134, 2026-03-29)
- [x] FEAT-006b: **OTel spans not instrumented** — added trace_span() to orchestrator tick/BUILD/VERIFY/REVIEW and flow detection/session processing; token attributes on CLI spans (GH #135, 2026-03-29)
- [x] TEST-002: **Mutation testing** — mutmut config in pyproject.toml + Makefile targets + smoke test (GH #17, 2026-03-29)
- [x] UX-005: **Dashboard empty states** — added first-time guidance and no-match feedback messages (GH #118, 2026-03-29)
- [x] UX-006: **Toast notification system** — replaced 9 alert() calls with styled toast/snackbar; added btn-loading class (GH #119, 2026-03-29)
- [x] UX-006b: **Missing success toasts** — added success toasts after cancel/rerun/submit operations (GH #147, 2026-03-29)
- [x] UX-007: **Loading states** — added loading-spinner/loading-overlay CSS; skeleton cards on initial load; spinners on fetch calls (GH #120, 2026-03-29)
- [x] UX-008: **Keyboard shortcuts and mobile layout** — keydown handler (Escape, arrows, Ctrl+K, 1-5); @media queries at 1024px/600px with 44px touch targets (GH #121, 2026-03-29)
- [x] INFRA-013: **Error recovery skill** — added .claude/skills/error-recovery/ with failure classification, recovery protocol, phase-specific guidance (GH #122, 2026-03-29)
- [x] INFRA-014: **Expand CLAUDE.md** — added architecture overview, concurrency model, data flow, common pitfalls, debugging tips, async patterns, git workflow (GH #123, 2026-03-29)
- [x] FEAT-007: **Structured logging with task correlation** — TaskContextFilter + JsonFormatter with contextvars task_id/phase (GH #124, 2026-03-29)
- [x] INFRA-015: **Git workflow rule** — added .claude/rules/git-workflow.md (GH #126, 2026-03-29)
- [x] INFRA-017: **Phase transition criteria in prompts** — added exit criteria table and transient vs deterministic guidance to orchestrate_task.txt (GH #128, 2026-03-29)
- [x] UX-010: **Copy-to-clipboard** — added copyToClipboard() with toast feedback for IDs, hashes, SHAs, error text (GH #129, 2026-03-29)
- [x] UX-011: **Deep linking / URL sharing** — hash-based routing (#overview, #merge-queue, #prompts, #task/<id>) (GH #130, 2026-03-29)
- [x] UX-002: **Dashboard missing pagination and search** — added client-side search, state filter, and 25-per-page pagination (GH #93, 2026-03-29)
- [x] TEST-003: **Lint modules lack tests** — verified: all 9 modules have dedicated test files (10 test files total) (GH #95, 2026-03-29)

### P3 — Low Priority

- [x] FEAT-005: **Evaluator-optimizer loop** — PromptEvaluator + PromptOptimizer with scoring, underperforming detection, and suggestion generation (GH #15, 2026-03-29)
- [x] FEAT-006: **OpenTelemetry tracing** — optional OTel with init_tracing(), get_tracer(), trace_span(), NoOp fallback (GH #16, 2026-03-29)
- [x] TEST-004: **Multiple source modules lack test files** — verified: all 90 modules have coverage (76 dedicated + 14 via integration tests); 100% coverage enforced (GH #97, 2026-03-29)
- [x] UX-009: **Data visualization** — added sparkline() and barChart(); renderOverviewStats with success rate, cost-by-model, and phase duration charts (GH #125, 2026-03-29)
- [x] INFRA-014b: **CLAUDE.md missing sections** — added data models, state persistence table, dependency notes (GH #136, 2026-03-29)

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
