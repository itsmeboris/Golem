# Quality Assurance Pipeline v2

**Date**: 2026-03-09
**Status**: Approved
**Problem**: Golem's validation pipeline reviews artifacts in isolation (diff + event log) without verifying them against the codebase they integrate into. Tests can confirm bugs when they share the same wrong assumptions as the code (circular validation). No structural enforcement of cross-module data contracts.

## Motivation

A Golem task shipped code that read event log dicts with keys `"type"` and `"message"`, while the real producer uses `"kind"` and `"summary"`. Tests passed because they used the same wrong keys. The validator reviewed the diff in isolation and saw internally consistent code. This is a systemic gap, not a one-off mistake.

Industry comparison:
- **Devin Review**: self-review loop with local command execution for context beyond diff
- **OpenClaw**: tool-boundary enforcement with signed evidence trails, 99.96% verifiable decisions
- **SWE-bench**: decontaminated evaluation against real-world task outcomes

Golem's validator today: LLM reading a text dump of diff + event log. No tools, no execution, no repo access.

## Architecture

Three independent layers, each preventing a different failure class:

```
Agent executes task
        |
        v
+---------------------+
|  Layer 1: Structure  |  TypedDicts in golem/types.py -- makes wrong keys
|  (compile-time)      |  a type error, not a runtime silence
+--------+------------+
         v
+---------------------+
|  Layer 2: Verifier   |  Deterministic: black + pylint + pytest
|  (no LLM, just run)  |  Fail -> retry immediately, skip reviewer
+--------+------------+
         | pass
         v
+---------------------+
|  Layer 3: Reviewer   |  LLM agent with read-only repo access
|  (contextual review) |  Cross-references diff against types.py,
|                      |  checks test fixtures vs real schemas,
|                      |  expanded antipattern detection
+--------+------------+
         | verdict
         v
    PASS / PARTIAL / FAIL
```

## Layer 1: Structural Contracts -- golem/types.py

A single module defining TypedDicts for all cross-module dict flows. Every producer and consumer imports from here.

### Contracts

1. **MilestoneDict** -- Event log entries (event_tracker -> orchestrator -> dashboard -> validation)
   - Keys: kind, tool_name, summary, timestamp, is_error, full_text (NotRequired)

2. **SessionDict** -- Session snapshots (orchestrator -> JSON -> dashboard)
   - All 37+ fields from TaskSession.to_dict() / from_dict()

3. **ActiveTaskDict** -- Live state active tasks (live_state -> dashboard)
   - Keys: event_id, flow, model, phase, elapsed_s

4. **CompletedTaskDict** -- Live state completed tasks (live_state -> dashboard)
   - Keys: event_id, flow, success, duration_s, cost_usd, finished_ago_s

5. **LiveSnapshotDict** -- Live state snapshots (live_state -> dashboard -> health)
   - Keys: uptime_s, active_tasks, recently_completed, models_active, queued_event_ids

6. **RunRecordDict** -- Run log entries (run_log -> dashboard)
   - Keys: success, started_at, event_id, flow, cost_usd, duration_s, input_tokens, output_tokens

7. **AlertDict** -- Health alerts (health -> dashboard)
   - Keys: type, message, value, threshold

8. **StreamEventDict** -- Claude CLI events (external -> event_tracker, dashboard, stream_printer)
   - Keys (total=False): type, subtype, session_id, tool_call, message, cost_usd, duration_ms

9. **TrackerExportDict** -- Tracker state export (event_tracker.to_dict() -> orchestrator)
   - Keys: tools_called, mcp_tools_called, cost_usd, milestone_count, finished, event_log

10. **ConfigSnapshotDict** -- Config snapshot (dashboard -> API)

11. **ValidationResultDict** -- Validation result (validation -> orchestrator -> committer)
    - Keys: verdict, confidence, summary, concerns, files_to_fix, test_failures, task_type

12. **VerificationResultDict** -- Verifier output (verifier -> orchestrator -> validation)
    - Keys: passed, black_ok, black_output, pylint_ok, pylint_output, pytest_ok, pytest_output, test_count, failures, coverage_pct, duration_seconds

### Migration approach

- Producers annotate return types: `def to_dict(self) -> MilestoneDict:`
- Consumers annotate parameters with typed dicts
- pylint and mypy catch key mismatches at check time
- Existing tests stay valid -- TypedDicts are structural, not runtime enforcement

## Layer 2: Deterministic Verifier -- golem/verifier.py

No LLM, no judgment -- runs commands and reports facts.

### Interface

```python
@dataclass
class VerificationResult:
    passed: bool
    black_ok: bool
    black_output: str
    pylint_ok: bool
    pylint_output: str
    pytest_ok: bool
    pytest_output: str
    test_count: int
    failures: list[str]
    coverage_pct: float
    duration_seconds: float

def run_verification(work_dir: str, *, timeout: int = 300) -> VerificationResult:
    """Run black, pylint, pytest sequentially. Return structured results."""
```

### Behavior

- Runs in the agent's worktree directory
- `black --check .` -- captures exit code + output
- `pylint --errors-only golem/` -- captures exit code + output
- `pytest --cov=golem --cov-fail-under=100` -- captures exit code + output, parses test count/failures/coverage
- All three run regardless of earlier failures (collect all evidence)
- Returns a VerificationResult -- the orchestrator decides what to do with it

### Pipeline integration

```
Agent finishes
    |
    v
run_verification(work_dir)
    |
    +-- passed=True  -> proceed to Layer 3 (reviewer)
    |
    +-- passed=False -> retry immediately with structured feedback:
         "Verification failed:
          - black: {output}
          - pylint: {output}
          - pytest: 3 failures: test_x, test_y, test_z
          Fix these issues."
```

### What this replaces

- Today the validator prompt says "check the event log for evidence of these commands." That check moves here as a hard gate.
- The agent still runs verification commands during execution (for its own feedback loop), but we no longer trust that it did -- we verify independently.
- Same 3-attempt circuit breaker -- if verification fails 3 times on the same error, escalate to FAILED.

## Layer 3: Enhanced Reviewer -- updated golem/validation.py

### Changes

1. **Reviewer gets tools** -- invoked via Claude CLI with read-only file access (Read, Grep, Glob). No write, no execute.

2. **Reviewer gets verifier evidence** -- VerificationResult injected into prompt as structured facts.

3. **New validation criteria** added to validate_task.txt:

   **Criterion 7 -- Cross-Module Consistency:**
   When the diff adds or modifies code that reads from shared data structures (dicts, JSON, dataclass fields):
   - Open golem/types.py and verify the keys used match the TypedDict contract
   - If the code accesses dict keys not in types.py, flag as HIGH concern
   - If test fixtures construct dicts, verify their shape matches types.py
   - If new TypedDicts should exist but don't, flag as concern

   **Criterion 8 -- Test Validity:**
   Tests must verify behavior, not just confirm assumptions:
   - Check that test fixtures use realistic data shapes (matching types.py)
   - Flag tests where mocks/fixtures mirror the implementation rather than the contract (circular validation)
   - For cross-module features, at least one test should exercise the producer -> consumer path end-to-end

4. **Expanded antipatterns** in scan_diff_antipatterns:
   - Raw dict key access without TypedDict backing in production code
   - Test fixtures with hardcoded dict literals that should reference types.py

5. **types.py content included in prompt** so reviewer can cross-reference without file reads:
   ```
   ## Shared Data Contracts (golem/types.py)
   {types_py_content}
   ```

6. **Verification evidence section** replaces "check the event log":
   ```
   ## Independent Verification Results
   - black: PASS/FAIL {output}
   - pylint: PASS/FAIL {output}
   - pytest: PASS/FAIL, N tests, M failures, coverage: X%
   These results are from independent execution, not the agent's event log.
   Do NOT override these results with event log claims.
   ```

### What stays the same

- PASS / PARTIAL / FAIL verdict structure
- Confidence scoring (0.0-1.0)
- Existing antipatterns (traceback leaks, private access, string control flow)
- Retry/escalation logic in orchestrator

## Pipeline Integration -- Orchestrator Changes

### New state machine

```
RUNNING -> VERIFYING -> pass -> VALIDATING -> verdict -> COMPLETED/RETRYING/FAILED
                |
                v fail
             RETRYING (with verification output)
```

New `VERIFYING` state added to `TaskSessionState`.

### Retry behavior

| Stage    | Failure        | Action                                          |
|----------|----------------|-------------------------------------------------|
| Verifier | black fails    | Retry with: "black formatting failed: {output}" |
| Verifier | pylint fails   | Retry with: "pylint errors: {output}"           |
| Verifier | pytest fails   | Retry with: "test failures: {names}: {output}"  |
| Reviewer | PARTIAL        | Retry with: reviewer concerns (same as today)   |
| Reviewer | FAIL           | Escalate to FAILED (same as today)              |
| Either   | 3 identical    | Circuit breaker -> FAILED                       |

### Session data changes

- TaskSession gets `verification_result: VerificationResult | None`
- Persisted to JSON via VerificationResultDict from types.py
- Dashboard shows verification status separately from review verdict

### Prompt changes

**run_task.txt** additions:
- "Your output will be independently verified. Focus on correctness, not on passing verification."
- "When accessing shared data structures, import and use TypedDicts from golem/types.py. Do not use raw string keys for cross-module data."

**retry_task.txt** additions:
- When retrying from verification failure, include the raw command output
- When retrying from reviewer PARTIAL, include both verification evidence and reviewer concerns

## File Change Summary

| File | Change |
|------|--------|
| golem/types.py | NEW -- all 12 TypedDict contracts |
| golem/verifier.py | NEW -- deterministic black/pylint/pytest runner |
| golem/validation.py | Enhanced reviewer prompt, types.py injection, verifier evidence, new antipatterns, tool-enabled CLI invocation |
| golem/orchestrator.py | New VERIFYING state, wire verifier -> reviewer pipeline, verification-failure retry path |
| golem/prompts/validate_task.txt | New criteria 7 and 8, verification evidence section, types.py reference |
| golem/prompts/run_task.txt | TypedDict usage instruction, independent verification notice |
| golem/prompts/retry_task.txt | Verification failure context in retry prompt |
| golem/event_tracker.py | Import and use MilestoneDict, TrackerExportDict |
| golem/core/dashboard.py | Import and use all relevant TypedDicts |
| golem/core/live_state.py | Import and use ActiveTaskDict, CompletedTaskDict, LiveSnapshotDict |
| golem/core/run_log.py | Import and use RunRecordDict |
| golem/health.py | Import and use AlertDict, LiveSnapshotDict |
| golem/core/stream_printer.py | Import and use StreamEventDict |
| Tests | New tests for verifier, updated validation tests, integration tests for producer->consumer paths |

## References

- Devin Review: https://cognition.ai/blog/devin-review
- OpenClaw governance: https://caisi.dev/openclaw-2026/
- Claude Code best practices: https://www.anthropic.com/engineering/claude-code-best-practices
- Red-Green TDD for agents: https://simonwillison.net/guides/agentic-engineering-patterns/red-green-tdd/
