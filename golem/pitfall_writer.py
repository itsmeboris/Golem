"""Write extracted pitfalls to AGENTS.md with file locking and atomic writes."""

import fcntl
import os
import tempfile
from pathlib import Path

from .core.config import PROJECT_ROOT
from .pitfall_extractor import _is_duplicate

_HEADER = "# AGENTS.md — Golem Learning\n"
_SECTION_START = "## Known Pitfalls\n"
_SECTION_COMMENT = "<!-- Auto-maintained by Golem's post-task learning loop. Do not edit this section manually. -->\n"


def parse_pitfalls_section(content: str) -> tuple[str, list[str], str]:
    """Parse AGENTS.md content into (before_section, existing_pitfalls, after_section)."""
    if _SECTION_START not in content:
        return content, [], ""

    start_idx = content.index(_SECTION_START)
    before = content[:start_idx]
    after_section_start = content[start_idx + len(_SECTION_START) :]

    # Strip section comment if present
    if after_section_start.startswith(_SECTION_COMMENT):
        after_section_start = after_section_start[len(_SECTION_COMMENT) :]

    # Find end of section (next ## header or end of file)
    lines = after_section_start.splitlines(keepends=True)
    pitfall_lines: list[str] = []
    after_lines: list[str] = []
    in_section = True

    for line in lines:
        if in_section and line.startswith("## "):
            in_section = False
            after_lines.append(line)
        elif in_section:
            if line.startswith("- "):
                pitfall_lines.append(line[2:].rstrip("\n"))
        else:
            after_lines.append(line)

    after = "".join(after_lines)
    return before, pitfall_lines, after


def format_pitfalls_section(pitfalls: list[str]) -> str:
    """Format pitfalls as markdown bullet list with section header."""
    lines = [_SECTION_START, _SECTION_COMMENT]
    for pitfall in pitfalls:
        lines.append(f"- {pitfall}\n")
    return "".join(lines)


def update_agents_md(
    new_pitfalls: list[str], agents_md_path: Path | None = None
) -> None:
    """Merge new pitfalls into AGENTS.md, creating it if needed.

    Uses file locking (fcntl.flock) and atomic write (temp + rename).
    Default path: PROJECT_ROOT / "AGENTS.md"
    """
    if not new_pitfalls:
        return

    path = agents_md_path or PROJECT_ROOT / "AGENTS.md"
    dir_path = path.parent
    dir_path.mkdir(parents=True, exist_ok=True)
    lock_path = str(path) + ".lock"

    # Hold an exclusive lock across the entire read-modify-write cycle
    # so concurrent tasks don't overwrite each other's pitfalls.
    with open(lock_path, "w") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            if path.exists():
                existing_content = path.read_text(encoding="utf-8")
            else:
                existing_content = _HEADER

            before, existing_pitfalls, after = parse_pitfalls_section(existing_content)

            # Merge: add new pitfalls not already present
            merged = list(existing_pitfalls)
            for pitfall in new_pitfalls:
                if not _is_duplicate(pitfall, merged):
                    merged.append(pitfall)

            section = format_pitfalls_section(merged)
            new_content = before + section + after

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
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)
