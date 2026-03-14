---
name: systematic-debugging
description: Systematic root-cause debugging for Golem agent tasks. Use when encountering a bug, test failure, unexpected behavior, validator feedback, retry after partial completion, or when asked to fix or debug. Enforces evidence-first investigation over guess-and-check.
---

# Systematic Debugging

Adapted from [obra/superpowers](https://github.com/obra/superpowers) for autonomous Golem agent execution.

Random fixes waste time and create new bugs. Find root cause before attempting fixes.

## The Iron Law

```
NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST
```

If you haven't completed Phase 1, you cannot propose fixes.

## The Four Phases

### Phase 1: Root Cause Investigation

BEFORE attempting ANY fix:

1. **Read error messages carefully** — note line numbers, file paths, stack traces. They often contain the answer.
2. **Reproduce consistently** — if not reproducible, gather more data, don't guess.
3. **Check recent changes** — `git diff`, `git log --oneline -10`. What changed that could cause this?
4. **Trace data flow** — where does the bad value originate? Keep tracing backward until you find the source.

For multi-component issues, add diagnostic output at each component boundary before fixing:

```bash
# Log what enters and exits each layer
# Run once to gather evidence showing WHERE it breaks
# THEN investigate that specific component
```

### Phase 2: Pattern Analysis

1. **Find working examples** — what works that's similar to what's broken?
2. **Compare** — list every difference, however small. Don't assume "that can't matter."
3. **Understand dependencies** — what assumptions does the broken code make?

### Phase 3: Hypothesis and Testing

1. **Form a single hypothesis** — "I think X is the root cause because Y"
2. **Test minimally** — one variable at a time, smallest possible change
3. **Verify** — did it work? If not, form a NEW hypothesis. Don't stack fixes.

### Phase 4: Implementation

1. **Write a failing test** that reproduces the bug
2. **Implement a single fix** addressing the root cause (not the symptom)
3. **Verify** — run full verification: `black --check .`, `pylint --errors-only golem/`, `pytest --cov=golem --cov-fail-under=100`

If fix doesn't work after 3 attempts: report status as **blocked** with an explanation of what you investigated, what you tried, and why it didn't work. Do not keep guessing.

## Red Flags — STOP and Return to Phase 1

- "It's probably X, let me fix that"
- "Just try changing X and see if it works"
- Proposing solutions before tracing data flow
- "I don't fully understand but this might work"
- Each fix reveals a new problem in a different place

## Quick Reference

| Phase | Key Activity | Done When |
|---|---|---|
| 1. Root Cause | Read errors, reproduce, check changes, trace flow | Understand WHAT and WHY |
| 2. Pattern | Find working examples, compare differences | Identified the gap |
| 3. Hypothesis | Form theory, test one variable | Confirmed or new hypothesis |
| 4. Implementation | Failing test, single fix, full verification | Bug resolved, all tests pass |

## Related Skills

- **verification-before-completion**: After fixing the root cause, invoke before claiming the fix is complete. Fresh passing output from all three checks required.
- **test-driven-development**: If the bug lacks a reproduction test, write one before attempting the fix.
