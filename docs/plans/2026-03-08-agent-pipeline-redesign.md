# Agent Pipeline Redesign: Context Forwarding & Role Specialization

**Date:** 2026-03-08
**Status:** Approved

## Problem Statement

Analysis of 5 real task execution sessions revealed three systemic performance
issues costing ~30-40% unnecessary tokens and time:

1. **Missing task descriptions** — Sessions 4 and 5 had empty `{task_description}`
   placeholders, causing Explorer agents to thrash wildly (126 milestones for a
   3-file single-line edit).
2. **Massive duplicate reads across subagents** — Every subagent starts from
   scratch. `validation.py` was read 5+ times, `orchestrator.py` 6+ times in a
   single task.
3. **Role confusion** — Reviewer reuses `complex-task.md` (has Edit/Write),
   Tester uses built-in `general-purpose` (no Golem conventions), orphaned agent
   files (`code-architect.md`, `code-explorer.md`) are never used.

Additional waste: ToolSearch called redundantly (~21 times across batch), Skill
invocations adding overhead without benefit.

## Evidence

| Session | Subject | Milestones | Cost | Efficiency |
|---------|---------|-----------|------|------------|
| #...173 | Validation feedback | 107 | $2.48 | Good (complex) |
| #...183 | Merge verification | 23 | $0.76 | Good (no-op) |
| #...197 | GitHub backend | 82 | $2.11 | OK (new feature) |
| #...205 | Antipattern detection | 119 | $1.68 | **Poor** (2-file edit) |
| #...214 | Parametrize guidance | 126 | $1.44 | **Poor** (3-file tiny edit) |

Sessions 4 and 5 had empty task descriptions. Their Explorers read README,
TODO.md, CONTRIBUTING.md, git logs — all trying to reverse-engineer what to do.

## Industry Research

- **Anthropic multi-agent research system:** Opus lead + Sonnet workers
  outperformed single-agent Opus by 90.2%. Subagent instructions must include
  objective, output format, tool/source guidance, and task boundaries.
  ([source](https://www.anthropic.com/engineering/built-multi-agent-research-system))
- **Anthropic docs on subagents:** Lightweight agents (<3k tokens) enable fluid
  orchestration. Heavy agents (25k+) create bottlenecks. Haiku delivers 90% of
  Sonnet's agentic performance at 2x speed, 3x cost savings.
  ([source](https://code.claude.com/docs/en/sub-agents))
- **wshobson/agents (112 agents):** Four-tier model strategy — Opus for
  architecture/review, Sonnet for implementation, Haiku for fast ops.
  ([source](https://github.com/wshobson/agents))
- **undeadlist/claude-code-agents:** Context flows through generated markdown
  artifacts. Audit agents produce reports, planners consolidate, fixers read
  consolidated plans.
  ([source](https://github.com/undeadlist/claude-code-agents))

## Design

### New Agent Roles & Model Tiers

| Role | Agent file | Model | Tools | Max Turns | Purpose |
|------|-----------|-------|-------|-----------|---------|
| Orchestrator | (prompt template) | opus | Agent, Read, Grep, Glob | — | Plans, decomposes, coordinates. Strongest model for strategy. |
| Scout | `scout.md` | haiku | Read, Grep, Glob | 15 | Focused codebase research. Given specific questions, returns structured file:line findings. |
| Builder | `builder.md` | sonnet | All | 30 | Writes code + tests. Receives context from Scout phase. Also handles fix cycles. |
| Reviewer | `reviewer.md` | opus | Read, Grep, Glob, Bash | 20 | Adversarial code review with confidence scoring (>=80). Read-only. |
| Verifier | `verifier.md` | haiku | Bash | 5 | Runs black/pylint/pytest. Returns structured pass/fail. Minimal turns. |

**Removed agents:** `complex-task.md`, `quick-task.md`, `code-explorer.md`,
`code-architect.md`.

**Model rationale:**
- Opus for Orchestrator + Reviewer: strategic/judgment roles benefit most from
  strongest reasoning (Anthropic's own finding).
- Sonnet for Builder: excellent at code implementation, good balance of
  capability and cost.
- Haiku for Scout + Verifier: read-only research and command execution are
  speed-sensitive, not reasoning-sensitive.

### Revised Pipeline (5 phases)

```
Orchestrator (opus)
│
├─ Phase 1: Scout (haiku, 1-2 parallel)
│  Input:  specific questions from orchestrator
│  Output: structured findings with file:line refs
│  Note:   orchestrator captures output as context artifact
│
├─ Phase 2: Plan (orchestrator itself)
│  Input:  Scout findings + task description
│  Output: implementation plan, file list, parallelization decision
│  Note:   orchestrator may read files directly for planning
│
├─ Phase 3: Build (sonnet, parallel if independent)
│  Input:  Scout context + specific task instructions
│  Output: code changes (uncommitted)
│
├─ Phase 4: Review (opus, read-only)
│  Input:  Scout context + Build summary
│  Output: confidence-scored issues list (>=80 only)
│
├─ Phase 5: Verify (haiku)
│  Input:  runs black --check . && pylint --errors-only golem/ && pytest ...
│  Output: structured JSON { black: pass/fail, pylint: pass/fail, pytest: pass/fail }
│
├─ Fix loop (if verify or review fails):
│  Builder (sonnet) receives specific issues → re-verify → up to N cycles
│
└─ Report: merged into final verify output, orchestrator emits JSON
```

**Changes from current 6-phase:**
- Explorer → Scout (focused questions, not open-ended exploration)
- Implementer → Builder (receives context, not blank slate)
- Tester → Verifier (minimal haiku agent, 3 commands only)
- Reviewer gets dedicated agent file with disallowedTools
- Phase 6 (Report) merged into verify output
- Orchestrator allowed to read files directly for planning

### Context Forwarding

The orchestrator captures each subagent's return value and injects it into the
next subagent's prompt:

```
Scout findings
    ↓ injected as "## Context from exploration"
Builder prompt
    ↓ Builder summary injected as "## Changes made"
Reviewer prompt
    ↓ if issues found, injected as "## Review feedback"
Builder prompt (fix cycle)
```

This eliminates the #1 waste pattern (duplicate reads). Builders don't need to
rediscover what Scouts already found.

### Effort Scaling

The orchestrator prompt includes effort-scaling rules:

- **Trivial tasks** (subject suggests <3 files, simple edit): skip Scout phase,
  go directly to Build.
- **Standard tasks**: 1 Scout, 1 Builder, optional Review.
- **Complex tasks** (new module, refactor, >5 files): 2 Scouts in parallel,
  multiple Builders, full Review.

This prevents the 126-milestone problem for 3-file edits.

### Task Description Guard

In the prompt formatting code, if `task_description` is empty after template
substitution:
- Fall back to: "Implement the following based on the subject: {subject}"
- Log a warning: "Task description is empty for issue #{issue_id}"

This prevents the #1 observed failure mode.

### Prompt Design Principles (from research)

1. **Lightweight** — each agent prompt <3k tokens. No long preambles.
2. **Action-oriented descriptions** — "Focused codebase research returning
   structured findings" not "A code explorer that traces features."
3. **Explicit output format** — every agent prompt specifies exact output
   structure.
4. **Specific questions, not open exploration** — Scout gets "What files
   implement validation? What is the ValidationVerdict dataclass structure?"
   not "Explore the validation system."
5. **disallowedTools enforced** — Reviewer and Scout cannot Edit/Write via
   agent frontmatter, not just prompt instructions.

## Files to Change

### New files
- `.claude/agents/scout.md` — haiku, read-only, structured research output
- `.claude/agents/builder.md` — sonnet, all tools, receives context
- `.claude/agents/reviewer.md` — opus, read-only, confidence-scored review
- `.claude/agents/verifier.md` — haiku, bash-only, runs 3 commands

### Modified files
- `golem/prompts/orchestrate_task.txt` — new 5-phase pipeline, context
  forwarding instructions, effort scaling, new role names
- `golem/core/config.py` — `orchestrate_model` default to "opus"
- `golem/prompts.py` — guard against empty task_description

### Deleted files
- `.claude/agents/complex-task.md`
- `.claude/agents/quick-task.md`
- `.claude/agents/code-explorer.md`
- `.claude/agents/code-architect.md`

### Unchanged files
- `golem/prompts/run_task.txt` — still used for non-supervisor single-agent mode
- `golem/prompts/validate_task.txt` — external validation unchanged
- `golem/prompts/retry_task.txt` — retry flow unchanged
- `golem/core/cli_wrapper.py` — no changes needed (model passed through config)

## Cost Estimate

Based on observed session data (5 tasks, $8.47 total):

| Component | Current | New | Change |
|-----------|---------|-----|--------|
| Orchestrator | sonnet | opus | +cost per token, but small token count |
| Scout (was Explorer) | haiku | haiku | same, but fewer tokens (focused) |
| Builder (was Implementer) | sonnet | sonnet | same, but fewer tokens (has context) |
| Reviewer | sonnet | opus | +cost, but small token count |
| Verifier (was Tester) | general-purpose/sonnet | haiku | -cost |
| Duplicate reads | ~30-40% waste | ~0% | major savings |

**Expected net effect:** 20-30% cost reduction on typical tasks through
eliminated duplicate reads and effort scaling, with better output quality from
Opus orchestration and review.
