# TODO

Items use GitHub issue numbers as IDs. See https://github.com/itsmeboris/Golem/issues

## Active

| GH  | Status | Task                                                                                                                                                        | Impact | Priority       |
| --- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | -------------- |
| #20 | [ ]    | **Post-merge re-verification** — run black/pylint/pytest after merge conflict resolution; fail merge if checks don't pass                                   | High   | P1             |
| #2  | [ ]    | **Dashboard prompt comparison UI** — table/chart for `/api/analytics/by-prompt` data (API exists, frontend missing)                                         | Low    | P3             |
| #22 | [ ]    | **State management audit rule** — detect innerHTML without state preservation, polling without concurrency guards, shared mutable state in async code        | Medium | P2             |
| #23 | [ ]    | **Codebase contract linting** — static check that function return types match consumer expectations across module boundaries                                | Medium | P2             |
| #13 | [ ]    | **Context budget system** — dynamic prompt content sizing                                                                                                   | Medium | P2             |
| #14 | [ ]    | **A-Mem knowledge graph** — structured knowledge graph for AGENTS.md                                                                                        | Medium | P2             |
| #15 | [ ]    | **Evaluator-optimizer loop** — prompt auto-tuning                                                                                                           | Medium | P3             |
| #16 | [ ]    | **OpenTelemetry tracing** — agent observability                                                                                                             | Medium | P3             |
| #17 | [ ]    | **Mutation testing** — mutmut integration                                                                                                                   | Low    | P3             |
| #18 | [ ]    | **MCP tool schema validation** — poisoning defense                                                                                                          | Medium | P2             |
| #19 | [ ]    | **Runtime subprocess sandboxing** — OS-level containment                                                                                                    | High   | P2             |

## Completed

| GH  | Task                                                                                                                                                        | Impact | Priority |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | -------- |
| #1  | **SSE-based dashboard live updates** — replace 5s polling with SSE for real-time dashboard updates                                                          | Medium | P3       |
| #3  | **Validator fix-cycle depth** — multi-iteration build→review→fix loop                                                                                       | Medium | P3       |
| #4  | **Task replay + dashboard controls** — re-run, edit-and-resubmit modal, cancel button in task detail view                                                   | Medium | P2       |
| #5  | **Post-task learning loop** — extract pitfalls into AGENTS.md after each task                                                                               | Medium | P2       |
| #6  | **Integration smoke tests** — FastAPI TestClient integration tests for key endpoints; `@pytest.mark.integration` marker                                     | High   | P1       |
| #12 | **GitHub Issues self-serve** — let Golem pick up and close its own issues                                                                                   | High   | P2       |
| #21 | **Worktree and data isolation** — gitignore `data/`, ensure all tests use `tmp_path` instead of real repo for worktree/merge ops                            | High   | P0       |
