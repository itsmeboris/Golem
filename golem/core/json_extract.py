"""Extract JSON objects from Claude's free-form text output."""

import json
import logging
import re
from typing import Any

logger = logging.getLogger("golem.core.json_extract")


def extract_json(text: str, require_key: str | None = None) -> dict[str, Any] | None:
    """Best-effort extraction of a JSON object from *text*.

    Strategies (tried in order):
    1. Parse the entire text as JSON.
    2. Find ```json fenced blocks (prefer the last one).
    3. Find raw {...} blocks via brace matching (prefer the last one).

    When *require_key* is set, only JSON dicts containing that key qualify.
    """
    if not text or not text.strip():
        return None

    result = _try_full_parse(text, require_key)
    if result is not None:
        return result

    result = _try_fenced_blocks(text, require_key)
    if result is not None:
        return result

    result = _try_brace_matching(text, require_key)
    if result is not None:
        return result

    logger.debug("No JSON object found in %d chars of text", len(text))
    return None


def _try_full_parse(text: str, require_key: str | None) -> dict | None:
    try:
        data = json.loads(text.strip())
        if isinstance(data, dict) and _has_key(data, require_key):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _try_fenced_blocks(text: str, require_key: str | None) -> dict | None:
    blocks = _extract_fenced_json(text)
    if not blocks:
        return None

    logger.debug("Found %d fenced JSON blocks", len(blocks))
    for block in reversed(blocks):
        data = _safe_parse(block, require_key)
        if data is not None:
            return data
    return None


def _try_brace_matching(text: str, require_key: str | None) -> dict | None:
    candidates = _find_json_objects(text)
    if not candidates:
        return None

    logger.debug("Found %d brace-matched JSON candidates", len(candidates))
    for candidate in reversed(candidates):
        data = _safe_parse(candidate, require_key)
        if data is not None:
            return data
    return None


def _safe_parse(text: str, require_key: str | None) -> dict | None:
    try:
        data = json.loads(text)
        if isinstance(data, dict) and _has_key(data, require_key):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _has_key(data: dict, key: str | None) -> bool:
    return key is None or key in data


def _extract_fenced_json(text: str) -> list[str]:
    pattern = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)
    return [m.group(1).strip() for m in pattern.finditer(text)]


def _find_json_objects(text: str) -> list[str]:
    results = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            obj = _match_braces(text, i)
            if obj and len(obj) > 10:
                results.append(obj)
                i += len(obj)
                continue
        i += 1
    return results


def _match_braces(text: str, start: int) -> str | None:
    depth = 0
    in_string = False
    escape = False
    # Cap brace-matching at 50 KB to avoid O(n^2) scans on very large outputs.
    for i in range(start, min(start + 50000, len(text))):
        ch = text[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"' and not escape:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
