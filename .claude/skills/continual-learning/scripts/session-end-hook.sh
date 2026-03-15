#!/usr/bin/env bash
# -----------------------------------------------------------------------
# Continual Learning — SessionEnd Hook for Claude Code
#
# Fires after each session. Checks turn-count threshold, extracts
# conversation turns, invokes claude (haiku) to analyze learnings,
# and updates the "Learned" sections of AGENTS.md.
#
# The analysis runs in a background process because SessionEnd hooks
# have a 1.5-second timeout by default.
#
# Configuration:
#   CL_MIN_TURNS  — minimum user turns to trigger (default: 8)
#
# Install by adding to .claude/settings.local.json:
#   {
#     "hooks": {
#       "SessionEnd": [{
#         "matcher": ".*",
#         "hooks": [{
#           "type": "command",
#           "command": ".claude/skills/continual-learning/scripts/session-end-hook.sh"
#         }]
#       }]
#     }
#   }
# -----------------------------------------------------------------------
set -euo pipefail

# --- Configuration ---
MIN_TURNS="${CL_MIN_TURNS:-8}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
AGENTS_MD="$PROJECT_ROOT/AGENTS.md"
STATE_DIR="$PROJECT_ROOT/.claude/state"
INDEX_FILE="$STATE_DIR/continual-learning-index.json"

# --- Parse hook input (JSON on stdin) ---
HOOK_INPUT=""
[ ! -t 0 ] && HOOK_INPUT=$(cat) || true

# Try to get transcript path and session ID from stdin
TRANSCRIPT=""
SESSION_ID=""
if [ -n "$HOOK_INPUT" ]; then
    TRANSCRIPT=$(echo "$HOOK_INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('transcript_path', ''))
except Exception:
    pass
" 2>/dev/null) || true
    SESSION_ID=$(echo "$HOOK_INPUT" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('session_id', ''))
except Exception:
    pass
" 2>/dev/null) || true
fi

# Fallback: find most recent transcript for this project
if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
    SLUG=$(echo "$PROJECT_ROOT" | sed 's|^/||; s|/|-|g')
    TDIR="$HOME/.claude/projects/-${SLUG}"
    if [ -d "$TDIR" ]; then
        TRANSCRIPT=$(ls -t "$TDIR"/*.jsonl 2>/dev/null | head -1) || true
    fi
fi

[ -z "$TRANSCRIPT" ] && exit 0
[ ! -f "$TRANSCRIPT" ] && exit 0
[ -z "$SESSION_ID" ] && SESSION_ID=$(basename "$TRANSCRIPT" .jsonl)

# --- Check turn threshold ---
USER_TURNS=$(python3 "$SCRIPT_DIR/extract-turns.py" "$TRANSCRIPT" --count 2>/dev/null) || exit 0
[ "$USER_TURNS" -lt "$MIN_TURNS" ] && exit 0

# --- Check incremental index (skip already-processed sessions) ---
mkdir -p "$STATE_DIR"
if [ -f "$INDEX_FILE" ]; then
    python3 -c "
import json, sys
with open('$INDEX_FILE') as f:
    idx = json.load(f)
if '$SESSION_ID' in idx.get('sessions', {}):
    sys.exit(0)
sys.exit(1)
" 2>/dev/null && exit 0
fi

# --- Background the analysis (SessionEnd timeout is 1.5s) ---
(
    # Extract condensed conversation turns
    TURNS=$(python3 "$SCRIPT_DIR/extract-turns.py" "$TRANSCRIPT" 2>/dev/null) || exit 0
    [ -z "$TURNS" ] && exit 0

    # Check claude CLI is available
    command -v claude &>/dev/null || exit 0

    # Read current AGENTS.md
    CURRENT=""
    [ -f "$AGENTS_MD" ] && CURRENT=$(cat "$AGENTS_MD")

    # Invoke claude (haiku) to analyze the conversation
    UPDATED=$(claude -p --model sonnet <<PROMPT
Analyze this Claude Code conversation transcript and update AGENTS.md with durable learnings.

## Current AGENTS.md
---
$CURRENT
---

## Conversation Turns
---
$TURNS
---

## Instructions

Extract ONLY durable, actionable learnings:
1. User corrections: "don't do X", "that's wrong, actually...", "stop doing..."
2. Workspace facts: architecture decisions, conventions, module responsibilities
3. Process preferences: workflow expectations, communication style

Output the COMPLETE updated AGENTS.md. Use exactly these sections in this order:
- ## Learned User Preferences
- ## Learned Workspace Facts
- ## Recurring Antipatterns
- ## Coverage & Verification Gaps
- ## Architecture Notes

Rules:
- Keep ALL existing entries unless explicitly contradicted by the conversation
- Add only genuinely new, durable, actionable learnings from this conversation
- Deduplicate semantically similar entries
- Max 12 entries per learned section, 15 per runtime section
- No positive outcomes, no one-off task details, no secrets
- Start with '# AGENTS.md — Golem Learning' header
- Include '<!-- Auto-maintained -->' comment after header
- Output ONLY the markdown content, no code fences or explanations
PROMPT
    ) || exit 0

    # Validate output starts with expected header
    echo "$UPDATED" | head -1 | grep -q "^# AGENTS" || exit 0

    # Atomic write
    TMPFILE=$(mktemp "${AGENTS_MD}.XXXXXX")
    echo "$UPDATED" > "$TMPFILE"
    mv "$TMPFILE" "$AGENTS_MD"

    # Update incremental index
    python3 <<PYEOF
import json, os, time
idx_path = "$INDEX_FILE"
idx = {"version": 1, "sessions": {}}
if os.path.exists(idx_path):
    with open(idx_path) as f:
        idx = json.load(f)
idx["sessions"]["$SESSION_ID"] = {
    "processedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "turnCount": $USER_TURNS
}
with open(idx_path, "w") as f:
    json.dump(idx, f, indent=2)
PYEOF
) &
disown

exit 0
