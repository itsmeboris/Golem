# Implementer Agent Prompt Template

Use this template when dispatching an implementer Agent.

```
Agent tool:
  subagent_type: "general-purpose"
  description: "Implement Task N: [task name]"
  model: "sonnet"
  prompt: |
    You are implementing Task N: [task name]

    ## Task Description

    [FULL TEXT of task from plan - paste it here, don't make agent read file]

    ## Context

    [Scene-setting: where this fits, dependencies, architectural context]

    ## Before You Begin

    If you have questions about:
    - The requirements or acceptance criteria
    - The approach or implementation strategy
    - Dependencies or assumptions
    - Anything unclear in the task description

    **Ask them now.** Raise any concerns before starting work.

    ## Your Job

    Once you're clear on requirements:
    1. Implement exactly what the task specifies
    2. Write tests (100% coverage required)
    3. Run verification:
       - pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100
       - black --check golem/
       - pylint --errors-only golem/
    4. Commit your work
    5. Self-review (see below)
    6. Report back

    Work from: [directory]

    **While you work:** If you encounter something unexpected or unclear,
    **ask questions**. Don't guess or make assumptions.

    ## Golem Conventions

    - Use dataclasses with field(default_factory=...) for mutable defaults
    - No f-strings in logging: logger.info("msg %s", val)
    - Tests use pytest + unittest.mock, no fixtures
    - Black formatting, pylint clean

    ## Before Reporting Back: Self-Review

    Review your work with fresh eyes:

    **Completeness:**
    - Did I fully implement everything in the spec?
    - Are there edge cases I didn't handle?

    **Quality:**
    - Are names clear and accurate?
    - Is the code clean and maintainable?

    **Discipline:**
    - Did I avoid overbuilding (YAGNI)?
    - Did I follow existing patterns in the codebase?

    **Testing:**
    - Do tests verify behavior (not just mock behavior)?
    - Is coverage at 100%?

    If you find issues during self-review, fix them now.

    ## Report Format

    When done, report:
    - What you implemented
    - What you tested and test results
    - Files changed
    - Self-review findings (if any)
    - Any issues or concerns
```
