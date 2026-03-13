"""Write extracted pitfalls to AGENTS.md with categorization and atomic writes."""

import os
import tempfile
from pathlib import Path

from .core.config import PROJECT_ROOT
from .pitfall_extractor import (
    CATEGORY_ANTIPATTERNS,
    CATEGORY_ARCHITECTURE,
    CATEGORY_COVERAGE,
    _is_duplicate,
    classify_pitfall,
)

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

    # Classify and merge new pitfalls
    for pitfall in new_pitfalls:
        cat = classify_pitfall(pitfall)
        if not _is_duplicate(pitfall, categorized[cat]):
            categorized[cat].append(pitfall)

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
        except OSError:
            pass
        raise
