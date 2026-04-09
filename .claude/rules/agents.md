# Agent Workflow Rules

## Golem Runtime Pipeline (orchestrate_task.txt)
Single Claude session per task, self-orchestrates 5 phases:
UNDERSTAND → PLAN → BUILD → REVIEW (spec + quality) → VERIFY

All runtime subagents use `subagent_type: "builder"` or `"Explore"`.
External verification + validation run as separate subprocesses after.

## Development Agents (.claude/agents/)
For interactive development of Golem itself:
scout (sonnet) → builder (sonnet) → reviewer (opus) → verifier (sonnet)

The `code-reviewer` agent (opus) is a standalone PR/diff reviewer, not part
of the main pipeline.

Skills are preloaded via `skills` frontmatter — agents receive full skill
content at startup without needing to invoke the Skill tool:
- **builder**: `test-driven-development`, `systematic-debugging`
- **verifier**: `verification-before-completion`
- **scout**: `ast-grep`

## Model Selection
- **sonnet**: Most agents — scouting, building, implementing, verification
- **opus**: Deep reasoning — reviewing, standalone code review

## Key Principles
- Builder writes tests first (TDD), then implementation
- Reviewer uses confidence scoring (>=80 threshold) — skip style nits
- Verifier runs all three checks (black, pylint, pytest) even if one fails
- Each phase must be a separate assistant turn (for dashboard observability)
- Never commit from an agent — leave changes uncommitted
- Never push to remote from an agent
