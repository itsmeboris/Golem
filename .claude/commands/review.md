---
description: Run a code review on current changes using the scout and reviewer agent pipeline
argument-hint: "[commit-range or empty for unstaged]"
---

Review the current code changes using a structured pipeline:

1. First, use the **scout** agent to explore and understand the changed files:
   - Run `git diff` (or the specified commit range: $ARGUMENTS) to identify changes
   - Read the changed files for full context

2. Then, use the **reviewer** agent to perform adversarial code review:
   - Check for bugs, logic errors, and convention violations
   - Use confidence-based filtering (only report issues >= 80 confidence)
   - Verify test coverage for new code paths

3. Report findings in the reviewer's standard format (Critical/Important/Assessment)
