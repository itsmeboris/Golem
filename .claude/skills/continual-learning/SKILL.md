---
name: continual-learning
description: Mine Claude Code conversation transcripts for durable learnings and update AGENTS.md. Use when asked to mine sessions, maintain AGENTS.md, review past conversations, extract learnings, set up the learning loop, or run continual learning. Supports manual in-session analysis and automatic SessionEnd hook processing.
---

# Continual Learning

Extract durable learnings from Claude Code conversation transcripts and merge
them into the repo-root `AGENTS.md`. Complements the runtime pitfall loop
(which captures validation failures) by mining **interactive sessions** for
user corrections, workspace facts, and process preferences.

## Architecture

```
AGENTS.md
├── ## Learned User Preferences     ← conversation mining (this skill)
├── ## Learned Workspace Facts       ← conversation mining (this skill)
├── ## Recurring Antipatterns        ← runtime pitfall loop
├── ## Coverage & Verification Gaps  ← runtime pitfall loop
└── ## Architecture Notes            ← runtime pitfall loop
```

The learned sections come BEFORE the runtime sections. The runtime
`pitfall_writer.py` preserves them as preamble when it rewrites its own
sections — no code change needed.

## Manual Mode (in-session)

When invoked via `/continual-learning` or asked to mine sessions:

1. Read the incremental index:
   ```
   .claude/state/continual-learning-index.json
   ```

2. Find unprocessed transcripts:
   ```bash
   python3 .claude/skills/continual-learning/scripts/extract-turns.py \
     ~/.claude/projects/<project-slug>/<session-id>.jsonl
   ```
   The project slug is the project root path with `/` replaced by `-`,
   prefixed with `-`. For this project:
   `~/.claude/projects/-home-bsobol-projects-Golem/`

3. For each unprocessed transcript with 8+ user turns:
   a. Run `extract-turns.py <path>` to get condensed conversation text
   b. Analyze turns for corrections, preferences, and durable facts
   c. Update the learned sections of AGENTS.md
   d. Record the session in the incremental index

4. Use `extract-turns.py <path> --count` to check turn counts quickly.

## Automatic Mode (SessionEnd hook)

The `scripts/session-end-hook.sh` fires after significant sessions:

1. Receives `session_id` and `transcript_path` via stdin JSON
2. Checks turn threshold (8+ user turns, configurable via `CL_MIN_TURNS`)
3. Checks incremental index (skips already-processed sessions)
4. Backgrounds the analysis (SessionEnd timeout is 1.5s)
5. Invokes `claude -p --model haiku` to analyze and update AGENTS.md
6. Updates the incremental index

### Hook installation

Add to `.claude/settings.local.json`:
```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": ".claude/skills/continual-learning/scripts/session-end-hook.sh"
          }
        ]
      }
    ]
  }
}
```

## What to Extract

### User Corrections (highest priority)
Patterns: "no, don't...", "that's wrong...", "stop doing...", "instead do..."

### Workspace Facts
Architecture decisions, module responsibilities, non-obvious conventions
not already in CLAUDE.md.

### Process Preferences
Workflow expectations, communication style, tool usage preferences.

## Inclusion Bar

Keep only if ALL true:
- Actionable in future sessions
- Stable across sessions (not a one-off instruction)
- Repeated in multiple sessions, or explicitly stated as a broad rule
- Not a positive outcome ("code is clean", "implemented correctly")

## Exclusions

Never store:
- Secrets, tokens, credentials, personal data
- One-off task instructions or ephemeral debugging details
- Transient details (branch names, commit hashes, temp errors)
- Information already in CLAUDE.md or derivable from code
- Positive assessments

## AGENTS.md Output Contract

- File: repo root `./AGENTS.md`
- Learned sections: `## Learned User Preferences`, `## Learned Workspace Facts`
- Runtime sections (preserved, not modified): `## Recurring Antipatterns`,
  `## Coverage & Verification Gaps`, `## Architecture Notes`
- Plain bullet points only
- Max 12 entries per learned section
- No metadata, confidence tags, or evidence blocks

## Incremental Index

Located at `.claude/state/continual-learning-index.json`:
```json
{
  "version": 1,
  "sessions": {
    "<session-id>": {
      "processedAt": "2026-03-14T12:00:00Z",
      "turnCount": 42
    }
  }
}
```
