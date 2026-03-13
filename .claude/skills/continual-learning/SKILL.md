---
name: continual-learning
description: Incrementally extract recurring pitfalls, user corrections, and durable workspace facts from Golem task sessions, then update AGENTS.md with categorized bullet points. Use when the user asks to mine sessions, maintain AGENTS.md memory, or build a self-learning preference loop.
---

# Continual Learning

Keep the repo-root `AGENTS.md` current using Golem's post-task learning loop
and manual review triggers.

## How It Works

### Automatic (runtime)
After each Golem task completes, the supervisor calls:
1. `pitfall_extractor.extract_pitfalls()` — extracts from validation concerns,
   test failures, errors, and retry summaries; filters noise; deduplicates
2. `pitfall_writer.update_agents_md()` — classifies each pitfall and atomically
   merges into `AGENTS.md` under categorized sections

### Manual (interactive)
When triggered in a Claude Code session, review and curate `AGENTS.md`:
1. Read existing `AGENTS.md` at repo root
2. Review recent task sessions (via `golem status` or checkpoint files)
3. Extract high-signal, reusable information
4. Merge with existing entries, dedup semantically similar bullets
5. Remove stale or resolved entries

## AGENTS.md Output Contract

- File location: repo root `./AGENTS.md`
- Keep only these sections:
  - `## Recurring Antipatterns` — code patterns to avoid
  - `## Coverage & Verification Gaps` — missing tests, unverified claims
  - `## Architecture Notes` — cross-module issues, design decisions
- Use plain bullet points only
- Do not write evidence/confidence tags or metadata blocks

## Inclusion Bar

Keep an item only if all are true:
- actionable in future sessions
- stable across sessions (not a one-off)
- repeated in multiple tasks, or explicitly stated as a broad rule
- not a positive outcome ("implemented correctly", "code is clean", etc.)

## Exclusions

Never store:
- secrets, tokens, credentials, private personal data
- one-off task instructions or ephemeral debugging state
- transient details (branch names, commit hashes, temporary errors)
- positive assessments ("all requirements met", "no regressions")

## Categories

Classification is keyword-based in `pitfall_extractor.classify_pitfall()`:
- **antipatterns**: "antipattern", "dead code", "empty exception", "silently swallows", "tightly coupling", "unused"
- **coverage**: "no independent verification", "coverage", "no end-to-end test", "test pass claims"
- **architecture**: everything else (cross-module access, locking issues, untyped dict access, etc.)
