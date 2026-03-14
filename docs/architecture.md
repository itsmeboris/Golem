# Golem Architecture Reference

> Reference material for Golem's runtime pipeline, task lifecycle, and agent system.
> For key modules, coding conventions, and common commands, see CLAUDE.md.

## Task Lifecycle (7 states)

```
DETECTED → RUNNING → VERIFYING → VALIDATING → COMPLETED
                                             → RETRYING → COMPLETED
                                                        → FAILED
```

## Sub-agents (`.claude/agents/`)

Used by both the Golem runtime (via `subagent_type` in orchestrate_task.txt)
and interactive development. Each file sets model, tools, and turn limits.

| Agent | Model | Tools | Role |
|---|---|---|---|
| `scout` | haiku | Read, Grep, Glob | Codebase research (interactive only) |
| `builder` | sonnet | All | Implements features/fixes; invokes TDD skill |
| `reviewer` | sonnet | Read-only | Spec + quality review; confidence >= 80 |
| `verifier` | haiku | Bash only | Runs black + pylint + pytest; 5 turns max |
| `code-reviewer` | sonnet | Read-only | Standalone PR/diff review (interactive) |

## Skills (`.claude/skills/`)

- `test-driven-development` - red-green-refactor cycle
- `verification-before-completion` - run all checks before reporting done
- `systematic-debugging` - structured failure diagnosis
- `subagent-driven-development` - scout → builder → reviewer → verifier flow
- `continual-learning`, `create-skill`, `writing-docs`, `ast-grep`

## Runtime Pipeline

The daemon orchestrator (`orchestrator.py`) spawns **one Claude session per task**
using `golem/prompts/orchestrate_task.txt`. That session self-orchestrates a
5-phase workflow via the built-in Agent tool:

1. **Understand** - orchestrator reads files directly (Scout only if needed)
2. **Build** - dispatches Builder subagents (`subagent_type: "builder"`)
3. **Review** - Spec Reviewer + Quality Reviewer (both `subagent_type: "builder"`)
4. **Fix cycle** - Builder addresses reviewer findings (up to `validator_fix_depth`)
5. **Verify** - deterministic checks: black + pylint + pytest

After the session completes, the orchestrator runs external verification
(`verifier.py`) and validation (`validation.py`) as separate subprocesses.
On PASS, changes are committed. On PARTIAL, the session retries with feedback.

### Post-task learning loop

After each task, `pitfall_extractor.py` extracts pitfalls (validation
concerns, test failures, errors, retry summaries) from recent sessions,
filters out positive outcomes and noise, and classifies each into a
category. `pitfall_writer.py` deduplicates and atomically writes them to
the repo-root `AGENTS.md` under categorized sections: "Recurring
Antipatterns", "Coverage & Verification Gaps", and "Architecture Notes".
This runs as an awaited step before the "Task completed" event, with
dashboard visibility. Failures are emitted but never block the pipeline.

### Conversation mining (continual-learning skill)

A SessionEnd hook (`.claude/skills/continual-learning/scripts/session-end-hook.sh`)
fires after significant interactive sessions (8+ user turns), extracts
conversation turns, and invokes `claude -p --model sonnet` to mine durable
learnings. These go into "Learned User Preferences" and "Learned Workspace
Facts" sections at the top of AGENTS.md. The runtime pitfall loop preserves
these sections as preamble. See the `continual-learning` skill for details.

The AGENTS.md file is auto-maintained; do not edit manually.

## Development Agents (`.claude/agents/`)

These are for **interactive development of Golem itself**, not runtime:

| Agent | Model | Purpose |
|---|---|---|
| `scout` | haiku | Codebase research when developing Golem |
| `builder` | sonnet | Implement features/fixes on Golem |
| `reviewer` | sonnet | Adversarial review of Golem changes |
| `verifier` | haiku | Run black + pylint + pytest on Golem |
| `code-reviewer` | sonnet | Standalone PR/diff review |
