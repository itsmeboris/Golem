#!/usr/bin/env python3
"""Extract conversation turns from a Claude Code transcript JSONL file.

Outputs condensed text with user messages (full) and assistant messages
(truncated) for analysis by the continual learning pipeline.

Usage:
    extract-turns.py <transcript.jsonl>          # Print condensed turns
    extract-turns.py <transcript.jsonl> --count   # Print user turn count
"""

import json
import sys
from pathlib import Path

MAX_ASSISTANT_CHARS = 300
MAX_TOTAL_CHARS = 12000  # Stay well under haiku context budget


def _extract_text(content):
    """Extract plain text from a message content field."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(part["text"])
        return " ".join(parts).strip()
    return ""


def extract_turns(transcript_path: str) -> str:
    """Parse JSONL transcript and return condensed conversation text."""
    turns = []
    total_chars = 0

    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            if total_chars >= MAX_TOTAL_CHARS:
                break
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue

            entry_type = entry.get("type")
            if entry_type not in ("user", "assistant"):
                continue

            msg = entry.get("message", {})
            text = _extract_text(msg.get("content", ""))
            if not text:
                continue

            # Truncate assistant messages (user input is what matters most)
            if entry_type == "assistant" and len(text) > MAX_ASSISTANT_CHARS:
                text = text[:MAX_ASSISTANT_CHARS] + "..."

            turn = f"[{entry_type}] {text}"
            turns.append(turn)
            total_chars += len(turn)

    return "\n\n".join(turns)


def count_user_turns(transcript_path: str) -> int:
    """Count user turns in a transcript without loading full content."""
    count = 0
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            # Fast string match before parsing JSON
            if '"type":"user"' in line or '"type": "user"' in line:
                count += 1
    return count


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Usage: extract-turns.py <transcript.jsonl> [--count]",
            file=sys.stderr,
        )
        sys.exit(1)

    path = sys.argv[1]
    if not Path(path).exists():
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)

    if "--count" in sys.argv:
        print(count_user_turns(path))
    else:
        result = extract_turns(path)
        if result:
            print(result)
