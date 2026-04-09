---
name: delegation-heuristics
description: Internal guidance for deciding when to delegate tasks to Golem versus handling them inline
user-invocable: false
---

# Delegation Heuristics

Use this skill when evaluating whether a task should be delegated to Golem via `/golem:run`.

## Decision Framework

Evaluate the task against these signals. This is a judgment call, not a scored formula.

### Positive Signals (delegate to Golem)

| Signal | Weight | What to look for |
|---|---|---|
| File scope >3 files | High | Task mentions multiple files, directories, or "across the codebase" |
| Cross-cutting change | High | Refactor, rename, migration, API change across modules |
| Needs verification pipeline | Medium | Task requires writing tests, fixing lint, multi-step validation |
| Multi-step implementation | Medium | Task involves plan, build, verify cycle |
| Independence | Medium | Task has no dependency on the current conversation state |

### Negative Signals (keep inline)

| Signal | Weight | What to look for |
|---|---|---|
| Single-file fix | High | Simple bug fix, typo, config change in one file |
| Conversational | High | Needs back-and-forth, clarification, design decisions |
| Depends on dirty local state | High | Uncommitted changes, unsaved buffers that Golem cannot see |
| Needs secrets/env not in config | High | Task requires credentials Golem does not have |
| Interactive diagnosis | Medium | Debugging that requires real-time observation |
| Current-context dependent | Medium | Task references "this file" or "what we just discussed" |

## Decision Outcomes

1. **Delegate** — positive signals clearly dominate. Tell the user what Golem will do and which execution mode you recommend.
2. **Too small** — negative signals dominate. Say: "This looks small enough to handle inline — [specific reason]. Use `--delegate-all` if you want Golem to take it anyway."
3. **Uncertain** — mixed signals. Ask the user: "This could go either way. Should I delegate to Golem or handle it here?"

## Override

`--delegate-all` bypasses all heuristics. Always delegate when this flag is present.

## Proactive delegation

When evaluating ANY task (not just `/golem:run`), consider whether Golem would handle it better:
- If the user asks for a large refactor, multi-file feature, or anything that would benefit from Golem's full pipeline — suggest `/golem:run`. Do not call the `golem:golem-delegate` agent directly (it skips heuristic evaluation and prompt shaping).
- If you are about to spawn a heavy subagent for implementation work, consider routing through Golem instead.
- Always ask the user before auto-delegating. Say: "This looks like a good fit for Golem — want me to delegate it?"
