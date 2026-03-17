"""Regex-based scanner for top-level ``let`` variables mutated in async contexts."""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Matches top-level `let` declaration lines (we later verify depth == 0)
_LET_DECL_RE = re.compile(r"^\s*let\s+(.+?)(?:;|$)")

# Async context start patterns (on the same line)
_ASYNC_CONTEXT_RE = re.compile(
    r"""
    async\s+function   # async function declaration
    | async\s+\(       # async arrow with params: async (...) =>
    | async\s+=>       # async arrow without params
    | async\s*\(\s*\)  # async () => ...
    | \.then\s*\(      # .then( callback
    | addEventListener\s*\(  # addEventListener( callback
    | setInterval\s*\(       # setInterval( callback
    | setTimeout\s*\(        # setTimeout( callback
    """,
    re.VERBOSE,
)

# Guard patterns — nearby lines containing these words suppress a finding
_GUARD_RE = re.compile(r"\b(mutex|lock|queue|semaphore)\b", re.IGNORECASE)

# Number of lines before/after a mutation to look for a guard
_GUARD_WINDOW = 3


def _extract_let_names(decl_body: str) -> list[str]:
    """Extract variable names from the body of a ``let`` statement.

    Handles ``let a = 1, b = 2`` by splitting on commas (roughly).
    """
    names: list[str] = []
    # Split on commas to handle multi-declaration: let a=1, b=2
    for token in decl_body.split(","):
        m = re.match(r"\s*([A-Za-z_$][A-Za-z0-9_$]*)", token)
        if m:
            names.append(m.group(1))
    return names


def _build_mutation_pattern(var: str) -> re.Pattern:
    """Build a regex that matches mutations of ``var`` but not sub-variable names."""
    escaped = re.escape(var)
    return re.compile(
        rf"""
        (?:
            # Postfix: x++ or x--
            (?<![A-Za-z0-9_$])   # negative lookbehind: not preceded by identifier char
            {escaped}             # variable name
            (?:                   # either:
                \s*(?:\+\+|--)    #   ++ or --
              | \s*(?:\+|-|\*|/|%|&|\||\^|<<|>>|>>>)?=(?!=)  # =, +=, -=, etc. (not == or ===)
              | \.push\s*\(       #   .push(
              | \.pop\s*\(        #   .pop(
              | \.splice\s*\(     #   .splice(
            )
          |
            # Prefix: ++x or --x
            (?:\+\+|--)           # prefix operator
            \s*
            (?<![A-Za-z0-9_$])   # not preceded by identifier char (after whitespace stripping)
            {escaped}             # variable name
            (?![A-Za-z0-9_$])    # not followed by identifier char
        )
        """,
        re.VERBOSE,
    )


def _strip_strings_and_comments(line: str) -> str:
    """Remove string contents and // comments before brace counting."""
    line = re.sub(r"//.*$", "", line)
    line = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', '""', line)
    line = re.sub(r"'[^'\\]*(?:\\.[^'\\]*)*'", "''", line)
    line = re.sub(r"`[^`\\]*(?:\\.[^`\\]*)*`", "``", line)
    return line


def scan_shared_state_patterns(root: Path) -> list[dict]:
    """Scan JS files for top-level ``let`` variables mutated in async contexts.

    Args:
        root: The root directory to scan recursively.

    Returns:
        A list of findings, each a dict with keys:
        ``file`` (str), ``line`` (int), ``variable`` (str), ``message`` (str).
    """
    findings: list[dict] = []

    for js_file in sorted(root.rglob("*.js")):
        try:
            source = js_file.read_text(encoding="utf-8")
        except OSError:
            logger.warning("Cannot read file: %s", js_file)
            continue

        findings.extend(_scan_file(js_file, source))

    return findings


def _scan_file(js_file: Path, source: str) -> list[dict]:
    """Scan a single JS file and return findings."""
    lines = source.splitlines()

    # Phase 1 — find top-level let declarations
    top_level_lets = _find_top_level_lets(lines)

    if not top_level_lets:
        return []

    # Build mutation regexes for each variable
    mutation_patterns = {var: _build_mutation_pattern(var) for var in top_level_lets}

    # Phase 2 — find async context line ranges
    async_ranges = _find_async_ranges(lines)

    # Phase 3 & 4 — find mutations inside async contexts, filter guards
    findings: list[dict] = []
    for lineno_0, line in enumerate(lines):
        lineno_1 = lineno_0 + 1  # 1-based line number
        if not _is_in_async_context(lineno_0, async_ranges):
            continue
        for var, pat in mutation_patterns.items():
            if pat.search(line):
                if _is_guarded(lines, lineno_0):
                    continue
                findings.append(
                    {
                        "file": str(js_file.resolve()),
                        "line": lineno_1,
                        "variable": var,
                        "message": (
                            f"Top-level let '{var}' mutated inside async context"
                        ),
                    }
                )

    return findings


def _find_top_level_lets(lines: list[str]) -> list[str]:
    """Return variable names declared with top-level ``let`` (brace depth == 0)."""
    depth = 0
    names: list[str] = []

    for line in lines:
        # Strip string/comment content before counting braces to avoid false
        # depth changes from braces inside string literals or comments.
        stripped = _strip_strings_and_comments(line)
        open_count = stripped.count("{")
        close_count = stripped.count("}")

        if depth == 0:
            m = _LET_DECL_RE.match(line)
            if m:
                names.extend(_extract_let_names(m.group(1)))

        # Update depth after processing the line
        depth += open_count - close_count
        if depth < 0:
            depth = 0

    return names


def _find_async_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """Return a list of (start_depth, entry_line_0) tuples representing async blocks.

    Actually returns (start_line_0, end_line_0) ranges (inclusive) of async blocks.
    We track them by watching brace depth and recording blocks opened by async
    context lines.
    """
    ranges: list[tuple[int, int]] = []
    # Stack of (block_open_depth, block_open_line_0) for open async blocks
    stack: list[tuple[int, int]] = []
    depth = 0

    for lineno_0, line in enumerate(lines):
        stripped = _strip_strings_and_comments(line)
        open_count = stripped.count("{")
        close_count = stripped.count("}")

        # Check if this line opens an async context
        if _ASYNC_CONTEXT_RE.search(line):
            # The block starts at the opening brace found on/after this line
            # Record depth after any opening braces on this very line
            block_start_depth = depth + open_count
            stack.append((block_start_depth, lineno_0))

        # Update depth
        depth += open_count - close_count
        if depth < 0:
            depth = 0

        # Check if any tracked async blocks have closed
        new_stack = []
        for block_depth, block_start in stack:
            # The block is "closed" when depth falls below block_depth after
            # processing closing braces
            if depth < block_depth:
                # Block ended: record range from block_start to current line
                ranges.append((block_start, lineno_0))
            else:
                new_stack.append((block_depth, block_start))
        stack = new_stack

    # Any blocks still open extend to end of file
    for block_depth, block_start in stack:
        ranges.append((block_start, len(lines) - 1))

    return ranges


def _is_in_async_context(lineno_0: int, ranges: list[tuple[int, int]]) -> bool:
    """Return True if lineno_0 (0-based) falls inside any async range."""
    for start, end in ranges:
        if start <= lineno_0 <= end:
            return True
    return False


def _is_guarded(lines: list[str], lineno_0: int) -> bool:
    """Return True if any line in the guard window near lineno_0 contains a guard word."""
    start = max(0, lineno_0 - _GUARD_WINDOW)
    end = min(len(lines), lineno_0 + _GUARD_WINDOW + 1)
    for line in lines[start:end]:
        if _GUARD_RE.search(line):
            return True
    return False
