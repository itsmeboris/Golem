---
name: golem-result-handling
description: Internal guidance for presenting Golem task output back to the user
user-invocable: false
---

# Golem Result Handling

When the companion script returns Golem output, follow these rules:

## Summary Mode (default)

Present results in this structure:
1. **Verdict:** PASSED / FAILED / RUNNING
2. **Summary:** 1-3 sentences describing what Golem did
3. **Files changed:** List of modified/created files (if available)
4. **Verification:** Test count, lint status, coverage (from verification_result)
5. **Next steps:** Suggested follow-up (e.g., "review changes on branch agent/xxx")

## Raw Mode

When `--raw` was requested, present the full output without summarization.

## Truncation

If output exceeds 500 lines, show the summary and tell the user:
"Full output available via `/golem:query <task-id> --raw`."

## Error Handling

- Golem invocation failed: report the error. If it looks like a config or daemon issue, suggest `/golem:setup`.
- Task failed during execution: show the verification output and which phase failed.
- CRITICAL: Never attempt the task inline as a fallback. Report the failure and stop.
- If Golem was never successfully invoked, do not generate a substitute answer.

## After Presenting Results

Do NOT auto-apply changes from Golem's output. Ask the user what they want to do next:
- Review the changes
- Merge the branch
- Run additional verification
