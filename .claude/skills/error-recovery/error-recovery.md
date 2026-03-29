---
name: error-recovery
description: Systematic recovery from transient failures during task execution
---

# Error Recovery

When a step fails, don't retry blindly. Classify, then act.

## Failure Classification

| Signal | Classification | Action |
|---|---|---|
| Timeout, connection reset, 429 | **Transient** | Retry with backoff |
| Syntax error, import error, type error | **Deterministic** | Fix the code |
| Same test fails 3+ times with same error | **Persistent** | Escalate (circuit breaker) |
| Permission denied, missing binary | **Environmental** | Report BLOCKED |
| OOM, disk full | **Resource** | Reduce scope or report BLOCKED |

## Recovery Protocol

### Step 1: Classify the failure
Read the error message. Match it to the table above. Do NOT guess — if unsure, treat as deterministic and investigate.

### Step 2: Act based on classification

**Transient failures:**
```
for attempt in 1..max_retries:
    try operation
    on success: continue
    on failure: wait (2^attempt seconds, max 30s), retry
```

**Deterministic failures:**
1. Read the error carefully
2. Identify the root cause (don't just change random things)
3. Fix the specific issue
4. Re-run the failing step only
5. If the same error persists after fix, invoke `systematic-debugging`

**Persistent failures (circuit breaker):**
After N identical failures:
1. Stop retrying
2. Log the failure pattern
3. Report BLOCKED with the error and attempts made
4. Move to VERIFY phase to capture partial results

**Environmental failures:**
1. Log the missing dependency or permission
2. Report BLOCKED — don't attempt workarounds
3. Include the exact error in the report

### Step 3: Resume
After recovery, resume from the failed step — don't restart the entire phase.

## Integration with 5-Phase Pipeline

| Phase | Common failures | Recovery |
|---|---|---|
| UNDERSTAND | File not found | Check path, try alternatives |
| BUILD | Test failure | Fix code (deterministic) |
| BUILD | Subprocess timeout | Retry once (transient) |
| REVIEW | Reviewer crash | Re-dispatch reviewer |
| VERIFY | Flaky test | Retry once; if still fails, mark as known-flaky |
| VERIFY | Coverage drop | Find uncovered lines, add tests |

## Anti-patterns

- Retrying a syntax error (it will never self-heal)
- Catching all exceptions and continuing (masks real bugs)
- Infinite retry loops (always have a max)
- Changing code randomly when a test fails (investigate first)
- Treating all failures as transient (most are deterministic)
