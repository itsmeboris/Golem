"""State management audit: combines all three JS scanners into a single report."""

import logging
import sys
from collections import Counter
from pathlib import Path

from golem.lint.js_innerhtml import scan_innerhtml_patterns
from golem.lint.js_polling import scan_polling_patterns
from golem.lint.js_shared_state import scan_shared_state_patterns

logger = logging.getLogger(__name__)

# Mapping from category name to the name of the scanner function as it appears
# in this module's namespace.  Resolved at call time so that test patches
# applied to the module attribute take effect instead of stale closure refs.
_SCANNER_NAMES = [
    ("innerHTML", "scan_innerhtml_patterns"),
    ("polling", "scan_polling_patterns"),
    ("shared_state", "scan_shared_state_patterns"),
]

# Suppress F401 — these names are used indirectly via _SCANNER_NAMES lookups.
__all__ = [
    "run_state_management_audit",
    "format_audit_report",
    "scan_innerhtml_patterns",
    "scan_polling_patterns",
    "scan_shared_state_patterns",
]


def run_state_management_audit(root: Path) -> list[dict]:
    """Scan a directory for JS state management issues using all three scanners.

    Calls :func:`scan_innerhtml_patterns`, :func:`scan_polling_patterns`, and
    :func:`scan_shared_state_patterns`.  Each result is annotated with a
    ``"category"`` field identifying which scanner produced it.  Results are
    combined and sorted by ``(file, line)``.

    If an individual scanner raises an exception, a warning is logged and
    scanning continues with the remaining scanners.

    Args:
        root: The root directory to scan recursively.

    Returns:
        A list of dicts, each containing the scanner's original fields plus a
        ``"category"`` key (one of ``"innerHTML"``, ``"polling"``, or
        ``"shared_state"``).  Returns an empty list when no issues are found.
    """
    this_module = sys.modules[__name__]
    combined: list[dict] = []

    for category, func_name in _SCANNER_NAMES:
        scanner = getattr(this_module, func_name)
        try:
            results = scanner(root)
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Scanner '%s' raised an exception and will be skipped", category
            )
            continue
        for item in results:
            annotated = dict(item)
            annotated["category"] = category
            combined.append(annotated)

    combined.sort(key=lambda r: (r["file"], r["line"]))
    return combined


def format_audit_report(results: list[dict]) -> str:
    """Return a human-readable summary of state management audit results.

    Args:
        results: The list of dicts returned by :func:`run_state_management_audit`.

    Returns:
        ``"No state management issues found."`` when ``results`` is empty.
        Otherwise a multi-line string summarising category counts and per-issue
        details.
    """
    if not results:
        return "No state management issues found."

    counts: Counter = Counter(r["category"] for r in results)
    total = len(results)

    lines: list[str] = [f"State management audit: {total} issue(s) found"]
    for category, count in sorted(counts.items()):
        lines.append(f"  {category}: {count}")

    lines.append("")
    lines.append("Issues:")
    for item in results:
        file_ = item["file"]
        line = item["line"]
        category = item["category"]
        message = item["message"]
        lines.append(f"  {file_}:{line} [{category}] {message}")

    return "\n".join(lines)
