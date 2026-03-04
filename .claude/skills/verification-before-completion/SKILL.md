---
name: verification-before-completion
description: Enforce evidence-based completion claims for Golem agent tasks. Use when about to report completion, claim tests pass, say work is done, or express satisfaction with results. Prevents false completion claims by requiring fresh verification output.
---

# Verification Before Completion

Adapted from [obra/superpowers](https://github.com/obra/superpowers) for autonomous Golem agent execution.

Claiming work is complete without verification is dishonesty, not efficiency.

## The Iron Law

```
NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE
```

If you haven't run the verification command in this session, you cannot claim it passes.

## The Gate Function

```
BEFORE claiming any status or expressing satisfaction:

1. IDENTIFY: What command proves this claim?
2. RUN: Execute the FULL command (fresh, complete)
3. READ: Full output, check exit code, count failures
4. VERIFY: Does output confirm the claim?
   - If NO: State actual status with evidence
   - If YES: State claim WITH evidence
5. ONLY THEN: Make the claim
```

## Project Verification Commands

Run ALL three before reporting task completion:

```bash
black --check .
```

```bash
pylint --errors-only golem/
```

```bash
pytest --cov=golem --cov-fail-under=100
```

All three must exit 0. If any fails, fix the issue — do not report completion.

## Common Failures

| Claim | Requires | Not Sufficient |
|---|---|---|
| Tests pass | `pytest` output: 0 failures | Previous run, "should pass" |
| Linter clean | `pylint` output: 0 errors | Partial check, extrapolation |
| Formatting correct | `black --check`: exit 0 | "I used black formatting" |
| Bug fixed | Failing test now passes | Code changed, assumed fixed |
| Coverage met | `--cov-fail-under=100`: exit 0 | "I wrote tests for it" |

## Red Flags — STOP

- ANY wording implying success without having run verification
- Using "should", "probably", "seems to"
- Relying on partial verification
- Expressing satisfaction before verification ("Great!", "Done!")
- About to report completion without fresh evidence

## When to Apply

ALWAYS before:

- Reporting task status as completed
- Expressing satisfaction with work
- Moving to the next task or subtask
- ANY positive statement about work state

## Golem Agent Context

You are running as an autonomous agent in an isolated git worktree.
The orchestrator handles commits after your work is validated.
Your job: make changes, verify they work, report honestly.
