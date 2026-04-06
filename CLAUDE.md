# Golem — Claude Code Reference

Golem is an autonomous AI coding agent daemon written in Python. It polls issue
trackers, spawns Claude sub-agents per task, verifies results, and commits
passing work — all without human intervention.

---

## Key Modules
| Module | Role |
|---|---|
| `golem/orchestrator.py` | Durable state-machine; checkpoints every tick |
| `golem/flow.py` | Agent invocation and event-streaming pipeline |
| `golem/handoff.py` | Structured phase handoff documents between orchestrator phases |
| `golem/validation.py` | Validation agent dispatch and verdict parsing |
| `golem/parallel_review.py` | Multi-perspective reviewer coordination |
| `golem/verifier.py` | Deterministic checks; three-way dispatch: config-driven generic, Python hardcoded, or fail-closed |
| `golem/verify_config.py` | Per-repo verification config loader/saver (`.golem/verify.yaml`); path-traversal safe |
| `golem/detect_stack.py` | Buildpack-style language detection + CI parsing for auto-generating verify configs |
| `golem/observation_hooks.py` | Deterministic signal extraction from verification output |
| `golem/worktree_manager.py` | Git worktree lifecycle for parallel isolation |
| `golem/merge_queue.py` | Sequential merge pipeline with conflict resolution; dual-lock (asyncio + threading) for thread-safe reads |
| `golem/ensemble.py` | Parallel candidate retry strategy on second attempt; wired into supervisor via `_run_ensemble_retry` |
| `golem/event_tracker.py` | Converts stream-json events → `Milestone` objects |
| `golem/types.py` | Shared `TypedDict` contracts — import from here, not inline |
| `golem/clarity.py` | Haiku-based task clarity scoring before execution |
| `golem/context_injection.py` | Injects AGENTS.md + CLAUDE.md into agent sessions; `ContextBudget` fits sections within token limit using priority scoring |
| `golem/knowledge_graph.py` | A-Mem keyword/file-ref indexing of pitfalls for selective, relevance-scored context injection |
| `golem/mcp_validator.py` | Runtime MCP tool schema validation; rejects tools with invalid schemas to prevent agent confusion |
| `golem/health.py` | Daemon health monitoring with threshold-based alerting; `compute_status()` derives three-tier health status; UNHEALTHY pauses detection |
| `golem/instinct_store.py` | Confidence-weighted pitfall memory with decay/promotion |
| `golem/core/dashboard.py` | Flask web UI for live task monitoring |
| `golem/pitfall_extractor.py` | Extracts + deduplicates pitfalls from session data |
| `golem/pitfall_writer.py` | Categorized write of pitfalls to root `AGENTS.md` |
| `golem/heartbeat.py` | Heartbeat scheduler: round-robin across workers, global budget, inflight tracking |
| `golem/heartbeat_worker.py` | Per-repo heartbeat scan logic: tiers, dedup, coverage, cooldowns, promotion |
| `golem/repo_registry.py` | Attach/detach registry for multi-repo management (`~/.golem/repos.json`) |
| `golem/git_utils.py` | Git detection (`is_git_repo`) and GitHub remote parsing (`detect_github_remote`) |
| `golem/data_retention.py` | Startup cleanup of old traces/checkpoints (>30 days) |
| `golem/startup.py` | Startup dependency validation (git, claude in PATH) |
| `golem/batch_cli.py` | Batch submission CLI subcommands (submit, status, list) |
| `golem/backends/` | Issue-tracker adapters (GitHub, Redmine, local, MCP) |
| `golem/prompts/` | Prompt templates for each agent role; `_SafeDict` replaces missing placeholders with `""` |

> See `docs/architecture.md` for runtime pipeline, task lifecycle, sub-agents, and skills.

---

## Architecture Overview

### 5-Phase Task Pipeline
Each task flows through: **DETECT → PLAN → BUILD → REVIEW → VERIFY**

1. **DETECT**: Heartbeat polls issue trackers; clarity check scores the task
2. **PLAN**: Orchestrator builds the prompt; context budget selects relevant knowledge
3. **BUILD**: Claude CLI session executes the task in an isolated git worktree
4. **REVIEW**: Validation agent assesses the output; parallel reviewers check security/quality/tests
5. **VERIFY**: Deterministic checks via `.golem/verify.yaml` (auto-detected per repo) or Python fallback (black, pylint, pytest); merge queue with conflict resolution

### Concurrency Model
- **asyncio event loop** — one per daemon process; detection loop + session tasks
- **Session tasks** — each `_process_session()` is an independent `asyncio.Task`
- **Subprocess isolation** — Claude CLI runs in `subprocess.Popen` with sandbox limits
- **Git worktrees** — parallel branches for build isolation; cleaned on crash recovery
- **Merge queue** — dual-lock (asyncio.Lock for async, threading.Lock for thread-safe reads)
- **Graceful shutdown** — `graceful_stop()` saves state, drains tasks, then kills subprocesses

### Data Flow
```
Issue tracker → HeartbeatWorker → GolemFlow._detection_loop()
  → TaskOrchestrator._tick_detected() → SubagentSupervisor.run_pipeline()
    → CLI session (BUILD) → VerificationResult → ValidationVerdict
      → MergeQueue.process_all() → fast-forward to main branch
```

---

## Common Pitfalls & Debugging

### Known Gotchas
- **`_save_state()` is two-phase atomic**: sessions + batches are written to temp files, fsynced, then renamed together. Don't call `save_sessions()` directly outside `_save_state()`.
- **`asyncio.Lock` vs `threading.Lock`**: The merge queue uses BOTH. asyncio.Lock serializes async operations; threading.Lock protects sync reads (snapshot, pending) that may be called from threads.
- **Context injection priority**: CLAUDE.md (1) > AGENTS.md (2) > Role contexts (3) > Knowledge graph (4). Lower priority sections get truncated first when budget is tight.
- **Checkpoint phase must be cleared on recovery**: `recover_sessions()` resets `checkpoint_phase=""` alongside the state reset.
- **`_SafeDict` returns `""`**: Missing prompt placeholders silently become empty strings (not literal `{key}` text).

### Debugging Tips
- `golem logs --follow` — real-time daemon log output
- `golem status` — quick view of recent sessions
- Dashboard at `http://localhost:8081/dashboard` — visual session timeline
- Trace files in `data/traces/golem/` — JSONL event streams per session
- Checkpoint files in `data/checkpoints/` — JSON state snapshots

---

## Async Patterns

### Do
```python
# Use asyncio.to_thread for blocking I/O
result = await asyncio.to_thread(subprocess.run, cmd, ...)

# Use asyncio.wait with timeout for task draining
done, pending = await asyncio.wait(tasks, timeout=30)

# Use asyncio.gather with return_exceptions for cleanup
await asyncio.gather(*cancelled_tasks, return_exceptions=True)
```

### Don't
```python
# Never use asyncio.sleep in tests — use Event-based synchronization
await asyncio.sleep(0.1)  # BAD — flaky

# Never call subprocess.run directly in async functions
subprocess.run(cmd)  # BAD — blocks event loop

# Never hold asyncio.Lock across await points longer than necessary
async with lock:
    await long_running_operation()  # BAD — starves other coroutines
```

---

## Git Workflow

- **Branch**: all agent work happens on `agent/{session_id}` branches in worktrees
- **Merge**: sequential through `MergeQueue` with rebase + fast-forward
- **Verification**: per-repo commands from `.golem/verify.yaml` (or black + pylint + pytest for Golem) run BEFORE merge (post-merge re-verification too)
- **Conflict resolution**: merge agent gets a second chance with verification output

---

## Key Data Models

### TaskSession
Central state object for each tracked task. Persisted in `data/state.json`.
Key fields: `parent_issue_id`, `state` (DETECTED → RUNNING → COMPLETED/FAILED),
`budget_usd`, `parent_subject`, `checkpoint_phase`, `human_feedback`.
Serialized via `to_dict()`/`from_dict()` — add new fields to both.

### Milestone
Event log entry from `TaskEventTracker`. Fields: `kind` (tool_call, text,
phase_change), `summary`, `timestamp`, `duration_ms`. Stored in trace files.

### VerificationResult
Output from `verifier.run_verification()`. Fields: `passed`, `test_count`,
`test_failures`, `lint_errors`, `coverage_pct`, `error`. Used by orchestrator
to decide retry vs commit.

## State Persistence

| Data | Location | Format |
|---|---|---|
| Session state | `data/state.json` | JSON (two-phase atomic write) |
| Batch state | `data/batches.json` | JSON (two-phase atomic write) |
| Checkpoints | `data/checkpoints/` | JSON per session |
| Traces | `data/traces/golem/` | JSONL per session |
| Instincts | `.golem/instincts.json` | JSON (InstinctStore) |
| Repos | `~/.golem/repos.json` | JSON (RepoRegistry) |
| Verify config | `{repo}/.golem/verify.yaml` | YAML (per-repo verification commands) |
| Daemon logs | `data/logs/` | Rotated text logs |
| Prompt runs | `data/prompt_runs/` | JSON per run |

## Dependencies

### Required
- Python 3.11+
- `git` in PATH
- `claude` CLI (optional but needed for agent execution)

### Python packages (core)
- `pyyaml` — config parsing
- `fastapi` + `uvicorn` — dashboard API
- `aiofiles` — async file I/O in dashboard

### Python packages (optional)
- `opentelemetry-api` + `opentelemetry-sdk` — tracing (NoOp fallback if absent)
- `mutmut` — mutation testing
- `playwright` — e2e dashboard tests

---

## Coding Conventions

### Formatting and lint
- **black** — enforced; run `black .` to fix, `black --check .` to verify
- **pylint** — errors-only gate: `pylint --errors-only golem/`
- No `# pylint: disable` unless strictly necessary and explained

### Logging
Never use f-strings in log calls. Use `%`-style formatting:
```python
# WRONG
logger.info(f"Processing task {task_id}")

# CORRECT
logger.info("Processing task %s", task_id)
```

### Dataclasses
Always use `field(default_factory=...)` for mutable defaults:
```python
# WRONG
@dataclass
class Foo:
    items: list = []

# CORRECT
@dataclass
class Foo:
    items: list = field(default_factory=list)
```

### TypedDicts
Define shared dict shapes in `golem/types.py`. Never define inline TypedDicts
in individual modules — key-mismatch bugs come from scattered definitions.

### Imports
- Standard library first, then third-party, then local (`golem.*`)
- No circular imports; `types.py` has no local imports

---

## Testing Requirements

- **100% coverage is required** — `pytest --cov=golem --cov-fail-under=100`
- Tests live in `golem/tests/`
- Use `@pytest.mark.parametrize` for any test with repeated logic patterns
- Write tests before implementation (TDD — see `test-driven-development` skill)
- Tests must be deterministic; mock external I/O and time-dependent calls
- Each new public function needs at least one test for the happy path and one
  for the primary error path

---

## Common Commands

```bash
# Run all checks (same as CI)
make lint && make test

# Format
black golem/

# Lint (errors only)
pylint --errors-only golem/

# Tests with coverage
pytest golem/tests/ --cov=golem --cov-fail-under=100 -q

# Fast failure — stop at first failing test
pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100

# Start the daemon
python -m golem

# Dashboard (default port 8081)
# Starts automatically with the daemon; see golem/core/dashboard.py

# View daemon logs
golem logs -n 50 --follow
```
