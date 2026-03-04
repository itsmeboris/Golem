# Code Quality Reviewer Prompt Template

Use this template when dispatching a code quality reviewer Agent.

**Purpose:** Verify implementation is well-built (clean, tested, maintainable)

**Only dispatch after spec compliance review passes.**

```
Agent tool:
  subagent_type: "general-purpose"
  description: "Code quality review for Task N"
  model: "sonnet"
  prompt: |
    You are reviewing code quality for a completed implementation.

    ## What Was Implemented

    [From implementer's report]

    ## Requirements

    Task N from [plan context]

    ## Review Scope

    Review the changes between BASE_SHA and HEAD_SHA:
    - BASE_SHA: [commit before task]
    - HEAD_SHA: [current commit]

    Run: git diff BASE_SHA..HEAD_SHA

    ## Review Criteria

    Rate each issue on confidence 0-100. Only report issues >= 80.

    **Project Guidelines (CLAUDE.md):**
    - 100% test coverage required
    - Black formatting
    - pylint clean (errors-only)
    - Dataclass patterns (field(default_factory=...) for mutables)
    - No f-strings in logging
    - Proper mock usage in tests

    **Code Quality:**
    - Logic errors, edge cases, race conditions
    - Code duplication, missing error handling
    - Naming clarity and consistency
    - Test quality (verify behavior, not mocks)

    **Report Format:**
    - Strengths: [what's well done]
    - Issues (Critical/Important/Minor): [confidence score, file:line, fix suggestion]
    - Assessment: APPROVED or NEEDS_FIXES
```

**Code reviewer returns:** Strengths, Issues (Critical/Important/Minor), Assessment
