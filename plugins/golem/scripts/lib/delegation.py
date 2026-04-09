"""Task metadata structuring for delegation heuristic."""

import re


def structure_task_metadata(prompt: str) -> dict:
    """Extract structured metadata from a task prompt for AI-side heuristic.

    Returns a dict with keywords, file references, and complexity signals.
    The AI-side delegation-heuristics skill uses this to make the decision.
    """
    words = prompt.lower().split()

    # File references (paths with extensions or slash-separated)
    file_refs = [w for w in words if "/" in w or re.match(r"\w+\.\w+", w)]

    # Complexity keywords
    complexity_keywords = {
        "refactor",
        "migrate",
        "migration",
        "rename",
        "restructure",
        "rewrite",
        "overhaul",
        "redesign",
        "across",
        "all",
        "every",
        "cross-cutting",
        "modules",
        "components",
    }
    found_complexity = [w for w in words if w in complexity_keywords]

    # Simplicity keywords
    simplicity_keywords = {
        "fix",
        "typo",
        "tweak",
        "bump",
        "update",
        "change",
        "rename",
        "config",
        "comment",
    }
    found_simplicity = [w for w in words if w in simplicity_keywords]

    return {
        "file_ref_count": len(file_refs),
        "file_refs": file_refs[:10],  # cap for readability
        "word_count": len(words),
        "complexity_keywords": found_complexity,
        "simplicity_keywords": found_simplicity,
    }
