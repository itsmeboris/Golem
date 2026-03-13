# Agent Workflow Rules

## Golem Runtime Pipeline (orchestrate_task.txt)
Single Claude session per task, self-orchestrates 5 phases:
UNDERSTAND → BUILD → REVIEW (spec + quality) → FIX → VERIFY

All runtime subagents use `subagent_type: "builder"` or `"Explore"`.
External verification + validation run as separate subprocesses after.

## Development Agents (.claude/agents/)
For interactive development of Golem itself:
scout (haiku) → builder (sonnet) → reviewer (opus) → verifier (haiku)

## Model Selection
- **haiku**: Fast read-only tasks (scouting, verification)
- **sonnet**: Code generation (building, implementing)
- **opus**: Complex reasoning (review, architecture decisions)

## Key Principles
- Agents should check for applicable skills before starting work
- Builder writes tests first (TDD), then implementation
- Reviewer uses confidence scoring (>=80 threshold) - skip style nits
- Verifier runs all three checks (black, pylint, pytest) even if one fails
- Never commit from an agent - leave changes uncommitted
- Never push to remote from an agent
