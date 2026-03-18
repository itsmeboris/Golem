"""Scanner for innerHTML assignments in JS files without state save/restore."""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_INNERHTML_PATTERN = re.compile(r"\.innerHTML\s*\+?=(?!=)")

_STATE_PRESERVATION_KEYWORDS = (
    "scrollTop",
    "scrollLeft",
    "value",
    "activeElement",
    "checked",
    "selectionStart",
    "selectionEnd",
    "focus",
    "wasOpen",
    "offsetTop",
    "selectedIndex",
)

_LOOKBACK_LINES = 5


def _has_state_preservation(lines: list[str], match_line_index: int) -> bool:
    """Return True if any state preservation keyword appears in the preceding lines."""
    start = max(0, match_line_index - _LOOKBACK_LINES)
    for line in lines[start:match_line_index]:
        if any(keyword in line for keyword in _STATE_PRESERVATION_KEYWORDS):
            return True
    return False


def scan_innerhtml_patterns(root: Path) -> list[dict]:
    """Scan JS files for innerHTML assignments without state save/restore.

    Args:
        root: The root directory to scan recursively.

    Returns:
        A list of dicts with keys ``file``, ``line``, ``pattern``, and
        ``message``.  Returns an empty list when no issues are found.
    """
    results: list[dict] = []

    for js_file in sorted(root.rglob("*.js")):
        relative = str(js_file.relative_to(root))
        try:
            content = js_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            logger.warning("Skipping non-UTF-8 file: %s", js_file)
            continue
        lines = content.splitlines()

        for line_index, line in enumerate(lines):
            match = _INNERHTML_PATTERN.search(line)
            if match is None:
                continue

            if _has_state_preservation(lines, line_index):
                continue

            matched_text = match.group(0)
            results.append(
                {
                    "file": relative,
                    "line": line_index + 1,
                    "pattern": matched_text,
                    "message": (
                        "innerHTML assignment without state preservation"
                        f" in preceding {_LOOKBACK_LINES} lines"
                    ),
                }
            )

    results.sort(key=lambda r: (r["file"], r["line"]))
    return results
