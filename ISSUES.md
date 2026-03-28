# TODO

Items use GitHub issue numbers as IDs. See https://github.com/itsmeboris/Golem/issues

## Active

| GH  | Status | Task                                                                                                                                                        | Impact | Priority       |
| --- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | -------------- |
| #22 | [ ]    | **State management audit rule** — detect innerHTML without state preservation, polling without concurrency guards, shared mutable state in async code        | Medium | P2             |
| #23 | [ ]    | **Codebase contract linting** — static check that function return types match consumer expectations across module boundaries                                | Medium | P2             |
| #13 | [ ]    | **Context budget system** — dynamic prompt content sizing                                                                                                   | Medium | P2             |
| #14 | [ ]    | **A-Mem knowledge graph** — structured knowledge graph for AGENTS.md                                                                                        | Medium | P2             |
| #2  | [ ]    | **Dashboard prompt comparison UI** — table/chart for `/api/analytics/by-prompt` data (API exists, frontend missing)                                         | Low    | P3             |
| #36 | [ ]    | **CLI `logs` command** — no `golem logs` / `golem logs --follow` subcommand; `status --watch` shows counters but not log output                             | Low    | P3             |
| #15 | [ ]    | **Evaluator-optimizer loop** — prompt auto-tuning                                                                                                           | Medium | P3             |
| #16 | [ ]    | **OpenTelemetry tracing** — agent observability                                                                                                             | Medium | P3             |
| #17 | [ ]    | **Mutation testing** — mutmut integration                                                                                                                   | Low    | P3             |
| #18 | [ ]    | **MCP tool schema validation** — poisoning defense                                                                                                          | Medium | P2             |
| #19 | [ ]    | **Runtime subprocess sandboxing** — OS-level containment                                                                                                    | High   | P2             |
| #39 | [ ]    | **Notifier fix_iteration passthrough** — `flow.py` never passes `fix_iteration` to `notify_completed`/`notify_escalated` despite protocol requiring it       | High   | P2             |
| #40 | [ ]    | **Notifier delivery resilience** — all notifiers silently swallow exceptions; no retry, no failure signaling to orchestrator, no timeout on send operations   | High   | P2             |
| #41 | [ ]    | **Subprocess timeout gaps** — `_detect_base_branch()` and `rsync` in ensemble have no timeout; can hang indefinitely on unresponsive repos                   | Medium | P2             |
| #42 | [ ]    | **Ensemble cost budget guard** — ensemble retry spawns N parallel candidates without checking `max_cost_usd` first; can exceed budget                        | Medium | P2             |
| #43 | [ ]    | **Prompt placeholder fallbacks** — conditional placeholders (`{simplify_section}`, `{enhanced_review_section}`) render as literal text when conditions unmet  | Medium | P3             |
| #44 | [ ]    | **AGENTS.md growth bound** — `pitfall_writer._apply_decay()` never removes high-seen entries; file grows unbounded over time                                 | Medium | P3             |
| #45 | [ ]    | **Merge queue callback safety** — `on_merge_agent` callback not wrapped in try/except; exception leaves merge state inconsistent                             | Medium | P2             |

## Completed

| GH  | Task                                                                                                                                                        | Impact | Priority |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | -------- |
| #1  | **SSE-based dashboard live updates** — replace 5s polling with SSE for real-time dashboard updates                                                          | Medium | P3       |
| #3  | **Validator fix-cycle depth** — multi-iteration build→review→fix loop                                                                                       | Medium | P3       |
| #4  | **Task replay + dashboard controls** — re-run, edit-and-resubmit modal, cancel button in task detail view                                                   | Medium | P2       |
| #5  | **Post-task learning loop** — extract pitfalls into AGENTS.md after each task                                                                               | Medium | P2       |
| #6  | **Integration smoke tests** — FastAPI TestClient integration tests for key endpoints; `@pytest.mark.integration` marker                                     | High   | P1       |
| #12 | **GitHub Issues self-serve** — let Golem pick up and close its own issues                                                                                   | High   | P2       |
| #20 | **Post-merge re-verification** — run black/pylint/pytest after merge conflict resolution; fail merge if checks don't pass                                   | High   | P1       |
| #24 | **Ensemble retry wiring** — `ensemble.py` `pick_best_result()` wired into supervisor; parallel candidates with validation                                    | High   | P1       |
| #25 | **Integration validation binary search** — `flow.run_integration_validation()` bisects merge order to find which merge broke tests                           | High   | P1       |
| #26 | **Health check result propagation** — alerts propagated to flow control; UNHEALTHY pauses detection; exposed via properties                                  | High   | P2       |
| #27 | **Silent dependency skip** — `_wait_for_dependencies()` now logs warning when dep session ID is missing                                                      | Medium | P2       |
| #28 | **Async subprocess blocking** — `subprocess.run()` in async functions wrapped with `asyncio.to_thread()`                                                     | Medium | P2       |
| #29 | **Handoff validation enforcement** — invalid handoffs now rejected (not stored); downstream phases only receive valid context                                 | Medium | P2       |
| #30 | **Retry signal promotion** — promoted signals now drive escalation on last retry; stored on session for visibility                                            | Medium | P2       |
| #31 | **Ghost config properties** — removed undocumented properties from ops.md                                                                                    | Medium | P2       |
| #33 | **Human feedback loop guard** — identical feedback detection + retry cap prevents infinite feedback loops                                                     | Medium | P3       |
| #34 | **Dashboard API test coverage** — TestClient integration tests for analytics, cost-analytics, events SSE, and trace endpoints                                | Medium | P2       |
| #32 | **Redmine/Local heartbeat Tier 1** — `poll_untagged_tasks()` implemented for both backends with tag filtering and error handling                              | Medium | P2       |
| #35 | **Checkpoint restoration resilience** — corrupt checkpoints backed up to `.corrupt`, logged at ERROR; evidence preserved for recovery                         | Medium | P3       |
| #37 | **Heartbeat state cleanup on detach** — `delete_state()` on HeartbeatWorker; `_sync_workers()` cleans up files for detached repos                            | Low    | P3       |
| #21 | **Worktree and data isolation** — gitignore `data/`, ensure all tests use `tmp_path` instead of real repo for worktree/merge ops                            | High   | P0       |
| #38 | **Merge queue thread safety** — `snapshot()`, `pending`, `detect_overlaps()` read shared state without lock; added `_processing` list for in-flight entries  | High   | P1       |
