---
name: subagent-driven-development
description: Use when executing implementation plans with independent tasks in the current session using Claude Code's Agent tool
---

# Subagent-Driven Development

Execute a plan by dispatching a fresh Agent per task, with two-stage review after each: spec compliance review first, then code quality review.

**Core principle:** Fresh Agent per task + two-stage review (spec then quality) = high quality, fast iteration

## When to Use

```
Have implementation plan?
  ├── no → Manual execution or brainstorm first
  └── yes → Tasks mostly independent?
        ├── no (tightly coupled) → Manual execution
        └── yes → Use subagent-driven-development
```

## The Process

1. Read plan, extract all tasks with full text and context
2. Create task list (TaskCreate) with all tasks
3. **Per task:**
   a. Dispatch implementer Agent (`general-purpose`) using `./implementer-prompt.md`
   b. If implementer asks questions → answer, re-dispatch
   c. Implementer implements, tests, commits, self-reviews
   d. Dispatch spec reviewer Agent (`general-purpose`) using `./spec-reviewer-prompt.md`
   e. If spec issues found → implementer fixes → re-review
   f. Dispatch code quality reviewer Agent (`.claude/agents/code-reviewer.md`) using `./code-quality-reviewer-prompt.md`
   g. If quality issues found → implementer fixes → re-review
   h. Mark task complete
4. After all tasks: dispatch final code reviewer for entire implementation
5. Run verification: `pytest`, `black --check`, `pylint --errors-only`

## Agent Tool Syntax

```
Agent tool call:
  subagent_type: "general-purpose"    # or "complex-task" for deep work
  description: "Implement Task N: [short name]"
  prompt: |
    [Full task context — agents start with zero context]
  model: "sonnet"                     # or "opus" for complex, "haiku" for simple
  run_in_background: false            # true for independent parallel work
  isolation: "worktree"               # optional: isolated git worktree
```

### Agent Type Selection

| Agent Type | Best For | Model |
|-----------|----------|-------|
| `general-purpose` | Implementation, multi-step tasks | `sonnet` or `opus` |
| `complex-task` | Architecture decisions, deep debugging | `opus` |
| `Explore` | Finding files, searching code | `haiku` |

### Model Selection

- `haiku` — fast, cheap: searches, lookups, simple edits
- `sonnet` — balanced: most implementation tasks
- `opus` — thorough: complex logic, architecture, reviews
- Omit `model` — inherits parent model

## Prompt Templates

- `./implementer-prompt.md` — Dispatch implementer Agent
- `./spec-reviewer-prompt.md` — Dispatch spec compliance reviewer Agent
- `./code-quality-reviewer-prompt.md` — Dispatch code quality reviewer Agent

## Golem-Specific Quality Gates

Implementer and reviewer agents must verify:
- `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100` (100% coverage)
- `black --check golem/` (formatting)
- `pylint --errors-only golem/` (no errors)
- Dataclass patterns (no `__init__`, use `field(default_factory=...)`)
- No f-strings in logging (`logger.info("msg %s", val)`)
- Proper mock usage in tests

## Red Flags

**Never:**
- Skip reviews (spec compliance OR code quality)
- Proceed with unfixed issues
- Dispatch multiple implementation Agents in parallel (conflicts)
- Make Agent read plan file (provide full text instead)
- Skip scene-setting context (Agent needs to understand where task fits)
- Accept "close enough" on spec compliance
- **Start code quality review before spec compliance is approved**
- Move to next task while either review has open issues

**If Agent asks questions:**
- Answer clearly and completely
- Provide additional context if needed

**If reviewer finds issues:**
- Implementer (same Agent, resumed if possible) fixes them
- Reviewer reviews again
- Repeat until approved

**If Agent fails task:**
- Dispatch fix Agent with specific instructions
- Don't try to fix manually (context pollution)

## Reference: supervisor_v2_subagent.py Pattern

The Golem orchestrator (`supervisor_v2_subagent.py`) uses a similar pattern:
- Single Claude Code session with Agent tool delegation
- Explorer, Implementer, Reviewer, Tester subagents
- Each subagent gets full context in its prompt
- Results aggregated by the orchestrating session
