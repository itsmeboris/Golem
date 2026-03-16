"""Write extracted pitfalls to AGENTS.md with categorization and atomic writes."""

import logging
import os
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path

from .core.config import PROJECT_ROOT
from .pitfall_extractor import (
    CATEGORY_ANTIPATTERNS,
    CATEGORY_ARCHITECTURE,
    CATEGORY_COVERAGE,
    _is_duplicate,
    classify_pitfall,
)

logger = logging.getLogger("golem.pitfall_writer")

_HEADER = "# AGENTS.md — Golem Learning\n"
_AUTO_COMMENT = (
    "<!-- Auto-maintained by Golem's post-task learning loop."
    " Do not edit manually. -->\n"
)

# Section names in display order
_CATEGORIES = [
    (CATEGORY_ANTIPATTERNS, "## Recurring Antipatterns\n"),
    (CATEGORY_COVERAGE, "## Coverage & Verification Gaps\n"),
    (CATEGORY_ARCHITECTURE, "## Architecture Notes\n"),
]

# Legacy section header for migration
_LEGACY_SECTION = "## Known Pitfalls\n"
_LEGACY_COMMENT = (
    "<!-- Auto-maintained by Golem's post-task learning loop."
    " Do not edit this section manually. -->\n"
)

_METADATA_RE = re.compile(r"\s*<!--\s*seen:(\d+)\s+last:(\d{4}-\d{2}-\d{2})\s*-->$")
_DECAY_DAYS = 30
_DECAY_MIN_SEEN = 3


def _parse_metadata(entry: str) -> tuple[int, str | None]:
    """Parse seen count and last date from an entry's metadata tag.

    Returns (seen, last_date_str). Missing metadata returns (1, None).
    """
    match = _METADATA_RE.search(entry)
    if match:
        return int(match.group(1)), match.group(2)
    return 1, None


def _strip_metadata(entry: str) -> str:
    """Remove metadata tag from entry for comparison."""
    return _METADATA_RE.sub("", entry).rstrip()


def _format_metadata(entry_text: str, seen: int, last_date: str) -> str:
    """Append metadata tag to a bare entry string."""
    bare = _strip_metadata(entry_text)
    return f"{bare} <!-- seen:{seen} last:{last_date} -->"


def _apply_decay(entries: list[str], today: str | None = None) -> list[str]:
    """Remove stale entries based on seen count and age.

    - seen < 3 and last > 30 days ago: removed
    - seen in [3, 4]: persists regardless of age
    - seen >= 5: established, never removed
    - No metadata (migration): kept
    """
    if today is None:
        today = date.today().isoformat()
    today_date = date.fromisoformat(today)
    cutoff = today_date - timedelta(days=_DECAY_DAYS)

    result = []
    for entry in entries:
        seen, last_str = _parse_metadata(entry)
        if seen >= _DECAY_MIN_SEEN:
            result.append(entry)
            continue
        if last_str is None:
            result.append(entry)
            continue
        last_date_val = date.fromisoformat(last_str)
        if last_date_val >= cutoff:
            result.append(entry)
    return result


def _parse_section_bullets(text: str, header: str) -> tuple[list[str], str]:
    """Extract bullet items from a section, return (items, remaining_text)."""
    if header not in text:
        return [], text

    start = text.index(header)
    before = text[:start]
    after_header = text[start + len(header) :]

    # Strip auto-comment if present
    for comment in (_AUTO_COMMENT, _LEGACY_COMMENT):
        if after_header.startswith(comment):
            after_header = after_header[len(comment) :]

    lines = after_header.splitlines(keepends=True)
    items: list[str] = []
    rest_lines: list[str] = []
    in_section = True

    for line in lines:
        if in_section and line.startswith("## "):
            in_section = False
            rest_lines.append(line)
        elif in_section and line.startswith("- "):
            items.append(line[2:].rstrip("\n"))
        elif not in_section:
            rest_lines.append(line)

    return items, before + "".join(rest_lines)


def parse_agents_md(content: str) -> dict[str, list[str]]:
    """Parse AGENTS.md into {category: [pitfalls]}.

    Handles both the new categorized format and the legacy flat format.
    """
    result: dict[str, list[str]] = {cat: [] for cat, _ in _CATEGORIES}
    remaining = content

    # Try new categorized sections first
    for cat, header in _CATEGORIES:
        items, remaining = _parse_section_bullets(remaining, header)
        result[cat] = items

    # Handle legacy "## Known Pitfalls" section by classifying entries
    legacy_items, _ = _parse_section_bullets(remaining, _LEGACY_SECTION)
    for item in legacy_items:
        cat = classify_pitfall(item)
        result[cat].append(item)

    return result


def _preamble(content: str) -> str:
    """Extract text before the first categorized or legacy section."""
    earliest = len(content)
    for _, header in _CATEGORIES:
        if header in content:
            earliest = min(earliest, content.index(header))
    if _LEGACY_SECTION in content:
        earliest = min(earliest, content.index(_LEGACY_SECTION))
    return content[:earliest]


def format_agents_md(preamble: str, categorized: dict[str, list[str]]) -> str:
    """Format full AGENTS.md with categorized sections."""
    if preamble.strip():
        parts = [preamble.rstrip("\n") + "\n\n"]
    else:
        parts = [_HEADER + _AUTO_COMMENT + "\n"]

    for cat, header in _CATEGORIES:
        items = categorized.get(cat, [])
        if not items:
            continue
        parts.append(header)
        for item in items:
            parts.append(f"- {item}\n")
        parts.append("\n")

    return "".join(parts).rstrip("\n") + "\n"


def update_agents_md(
    new_pitfalls: list[str], agents_md_path: Path | None = None
) -> None:
    """Merge new pitfalls into AGENTS.md with categorization.

    Uses atomic write (temp file + os.replace) so partial writes never
    corrupt the file.  No file locking — concurrent writes are unlikely
    (tasks finish sequentially) and the worst case is one batch of
    pitfalls gets dropped; they will be re-extracted on the next run.

    Default path: PROJECT_ROOT.parent / "AGENTS.md" (repo root).
    """
    if not new_pitfalls:
        return

    path = agents_md_path or PROJECT_ROOT.parent / "AGENTS.md"
    dir_path = path.parent
    dir_path.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing_content = path.read_text(encoding="utf-8")
    else:
        existing_content = _HEADER + _AUTO_COMMENT

    categorized = parse_agents_md(existing_content)
    preamble = _preamble(existing_content)

    today = date.today().isoformat()

    # Migration: add metadata to bare entries (single pass)
    for cat in categorized:
        categorized[cat] = [
            _format_metadata(item, 1, today) if "<!-- seen:" not in item else item
            for item in categorized[cat]
        ]

    # Add/increment new pitfalls first (before decay)
    for pitfall in new_pitfalls:
        cat = classify_pitfall(pitfall)
        # Strip metadata from existing entries for fair comparison
        stripped_existing = [_strip_metadata(e) for e in categorized[cat]]
        match_idx = _is_duplicate(pitfall, stripped_existing)
        if match_idx is not None:
            old_entry = categorized[cat][match_idx]
            seen, _ = _parse_metadata(old_entry)
            categorized[cat][match_idx] = _format_metadata(
                _strip_metadata(old_entry), seen + 1, today
            )
        else:
            categorized[cat].append(_format_metadata(pitfall, 1, today))

    # Decay: remove stale entries after adding new ones
    for cat in categorized:
        categorized[cat] = _apply_decay(categorized[cat], today)

    new_content = format_agents_md(preamble, categorized)

    # Atomic write: temp file + rename
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, prefix=".agents_md_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_content)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError as exc:
            logger.debug("Failed to unlink pitfall_writer temp file: %s", exc)
        raise
