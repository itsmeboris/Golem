"""Regex-based scanner for unguarded polling patterns in JavaScript files."""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Patterns that indicate async I/O inside a timer callback
_ASYNC_IO_PATTERN = re.compile(r"fetch\(|XMLHttpRequest|\.send\(")

# Guard variable names / substrings that indicate concurrency protection.
# Use word boundaries for full-word guards; keep InFlight without a leading \b
# since it is typically used as a suffix (e.g., _pollInFlight).
_GUARD_PATTERN = re.compile(
    r"\bisFetching\b|\bloading\b|\bpending\b|InFlight\b|\bAbortController\b"
)

# Detect setInterval( or setTimeout( on a line
_TIMER_PATTERN = re.compile(r"\b(setInterval|setTimeout)\s*\(")

# Detect a bare function-reference form: setInterval(name, ...) or setTimeout(name, ...)
# where `name` is an identifier (not a lambda/arrow/anonymous function)
_FUNC_REF_PATTERN = re.compile(
    r"\b(?:setInterval|setTimeout)\s*\(\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*,"
)

# JS keywords that must not be treated as function-reference names
_JS_KEYWORDS = frozenset(
    {
        "function",
        "async",
        "new",
        "class",
        "return",
        "var",
        "let",
        "const",
        "typeof",
        "void",
        "delete",
    }
)

_MESSAGE = (
    "Polling with fetch() but no concurrency guard "
    "(e.g., isFetching flag or AbortController)"
)


def _extract_inline_scope(lines: list[str], start_idx: int) -> str:
    """Extract the callback body from a setInterval/setTimeout by tracking braces.

    Starts scanning from `start_idx` (0-based), finds the first `{` that opens
    the inline callback, then scans forward until the depth returns to 0.

    Braces inside string literals (``"..."``, ``'...'``, `` `...` ``) and
    comments (``// ...`` to end-of-line, ``/* ... */``) are not counted.

    Returns the extracted text (joined lines) or empty string if no `{` found.
    """
    depth = 0
    found_open = False
    collected: list[str] = []

    # String/comment parsing state
    in_string: str = ""  # "", "'", '"', "`"
    in_block_comment = False

    for line in lines[start_idx:]:
        i = 0
        while i < len(line):
            ch = line[i]

            if in_block_comment:
                if ch == "*" and i + 1 < len(line) and line[i + 1] == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if in_string:
                if ch == "\\" and i + 1 < len(line):
                    # Skip escaped character
                    i += 2
                    continue
                if ch == in_string:
                    in_string = ""
                i += 1
                continue

            # Check for start of block comment
            if ch == "/" and i + 1 < len(line) and line[i + 1] == "*":
                in_block_comment = True
                i += 2
                continue

            # Check for line comment
            if ch == "/" and i + 1 < len(line) and line[i + 1] == "/":
                # Rest of line is a comment — stop processing this line's chars
                break

            # Check for start of string
            if ch in ('"', "'", "`"):
                in_string = ch
                i += 1
                continue

            if ch == "{":
                depth += 1
                found_open = True
            elif ch == "}":
                depth -= 1

            i += 1

        if found_open:
            collected.append(line)
        if found_open and depth == 0:
            break

    return "\n".join(collected)


def _find_function_body(lines: list[str], func_name: str) -> str:
    """Return the text of the first function definition named `func_name`.

    Looks for lines like:
        function funcName(
        async function funcName(
    and extracts the body by brace tracking.
    """
    func_def_re = re.compile(r"\bfunction\s+" + re.escape(func_name) + r"\s*\(")
    for idx, line in enumerate(lines):
        if func_def_re.search(line):
            return _extract_inline_scope(lines, idx)
    return ""


def scan_polling_patterns(root: Path) -> list[dict]:
    """Scan all .js files under ``root`` for unguarded polling patterns.

    Detection logic:
    1. Find all ``.js`` files recursively; skip ``.min.js`` files.
    2. For each file scan for ``setInterval(`` / ``setTimeout(`` calls.
    3. Extract the callback scope (inline brace tracking or referenced function body).
    4. If the scope contains async I/O (``fetch(``, ``XMLHttpRequest``, ``.send(``)
       and lacks a concurrency guard, add an entry to the results.

    Args:
        root: The root directory to scan recursively.

    Returns:
        A list of dicts, each with keys: ``file`` (str, relative to root),
        ``line`` (int, 1-based), ``pattern`` (str), ``message`` (str).
    """
    results: list[dict] = []

    for js_file in sorted(root.rglob("*.js")):
        if js_file.name.endswith(".min.js"):
            continue

        try:
            source = js_file.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            logger.warning("Skipping unreadable file: %s", js_file)
            continue

        lines = source.splitlines()
        relative = str(js_file.relative_to(root))

        for line_idx, line in enumerate(lines):
            m = _TIMER_PATTERN.search(line)
            if not m:
                continue

            pattern_name: str = m.group(1)

            # Determine if inline callback or function reference
            func_ref_m = _FUNC_REF_PATTERN.search(line)
            if func_ref_m:
                # Function-reference style: setTimeout(fetchConfig, 2000)
                ref_name = func_ref_m.group(1)
                if ref_name in _JS_KEYWORDS:
                    # Keyword captured — treat as inline callback instead
                    scope = _extract_inline_scope(lines, line_idx)
                else:
                    scope = _find_function_body(lines, ref_name)
            else:
                # Inline arrow/anonymous callback
                scope = _extract_inline_scope(lines, line_idx)

            if not scope:
                continue

            if not _ASYNC_IO_PATTERN.search(scope):
                continue

            if _GUARD_PATTERN.search(scope):
                continue

            results.append(
                {
                    "file": relative,
                    "line": line_idx + 1,
                    "pattern": pattern_name,
                    "message": _MESSAGE,
                }
            )

    return results
