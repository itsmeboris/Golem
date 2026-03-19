"""AST-based lint: detect module-level constants that duplicate schema dict values.

Two patterns are detected within the same .py file:

1. **Regex sync** — a module-level ``re.compile("...")`` call where the regex
   string also appears as a ``"pattern"`` value inside a dict literal.

2. **Enum sync** — a module-level ``frozenset({...})`` or ``set({...})`` where
   the string elements also appear as an ``"enum"`` list inside a dict literal.

These indicate two independent sources of truth that can drift apart over time.
"""

import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema value collection
# ---------------------------------------------------------------------------


def _collect_schema_patterns(tree: ast.AST) -> list[str]:
    """Collect all string values associated with ``"pattern"`` keys in dict literals.

    Walks the full AST (including nested dicts) to find constructs like::

        {"pattern": "^[a-z]+$"}

    Returns a list of pattern strings found anywhere in the file.
    """
    patterns: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "pattern"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                patterns.append(value.value)
    return patterns


def _collect_schema_enums(tree: ast.AST) -> list[frozenset]:
    """Collect all list-of-strings values associated with ``"enum"`` keys in dict literals.

    Walks the full AST to find constructs like::

        {"enum": ["read", "write", "execute"]}

    Returns a list of frozensets, one per ``"enum"`` list found.
    """
    enums: list[frozenset] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "enum"
                and isinstance(value, ast.List)
            ):
                elements = [
                    elt.value
                    for elt in value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
                # Only include if all elements were strings
                if len(elements) == len(value.elts):
                    enums.append(frozenset(elements))
    return enums


# ---------------------------------------------------------------------------
# Constant collection
# ---------------------------------------------------------------------------


def _extract_re_compile_pattern(call_node: ast.Call) -> str | None:
    """Extract the pattern string from a ``re.compile(...)`` call node.

    Handles both ``re.compile("pat")`` (attribute call) and
    ``compile("pat")`` (name call).  Returns ``None`` when the first
    positional argument is not a string literal.
    """
    if not call_node.args:
        return None
    first_arg = call_node.args[0]
    if not (isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str)):
        return None
    func = call_node.func
    # Accept: re.compile(...) or compile(...)
    if isinstance(func, ast.Attribute) and func.attr == "compile":
        return first_arg.value
    if isinstance(func, ast.Name) and func.id == "compile":
        return first_arg.value
    return None


def _extract_set_elements(call_node: ast.Call) -> frozenset | None:
    """Extract elements from a ``frozenset({...})`` or ``set({...})`` call.

    Returns a frozenset of string elements, or ``None`` when:
    - The call is not frozenset/set.
    - The argument is not a set literal.
    - Any element is not a string constant.
    """
    func = call_node.func
    if not (isinstance(func, ast.Name) and func.id in ("frozenset", "set")):
        return None
    if len(call_node.args) != 1:
        return None
    arg = call_node.args[0]
    if not isinstance(arg, ast.Set):
        return None
    elements = []
    for elt in arg.elts:
        if not (isinstance(elt, ast.Constant) and isinstance(elt.value, str)):
            return None
        elements.append(elt.value)
    return frozenset(elements)


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------


def _check_file(py_file: Path, root: Path) -> list[dict]:
    """Analyse a single Python file for schema constant sync violations.

    Args:
        py_file: Absolute path to the ``.py`` file.
        root: Root directory (used to compute the relative path in violations).

    Returns:
        List of violation dicts (may be empty).
    """
    source = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        logger.warning("Skipping file with syntax error: %s", py_file)
        return []

    schema_patterns = _collect_schema_patterns(tree)
    schema_enums = _collect_schema_enums(tree)

    if not schema_patterns and not schema_enums:
        return []

    rel_file = str(py_file.relative_to(root))
    violations: list[dict] = []

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue

        constant_name: str = node.targets[0].id
        call: ast.Call = node.value

        # --- Pattern 1: re.compile ---
        if schema_patterns:
            pattern_str = _extract_re_compile_pattern(call)
            if pattern_str is not None and pattern_str in schema_patterns:
                violations.append(
                    {
                        "file": rel_file,
                        "line": node.lineno,
                        "constant": constant_name,
                        "message": (
                            f"'{constant_name}' duplicates a schema 'pattern' value; "
                            "use the schema dict directly instead of a separate constant"
                        ),
                    }
                )

        # --- Pattern 2: frozenset / set ---
        if schema_enums:
            set_elements = _extract_set_elements(call)
            if set_elements is not None and set_elements in schema_enums:
                violations.append(
                    {
                        "file": rel_file,
                        "line": node.lineno,
                        "constant": constant_name,
                        "message": (
                            f"'{constant_name}' duplicates a schema 'enum' value; "
                            "use the schema dict directly instead of a separate constant"
                        ),
                    }
                )

    return violations


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def check_schema_constant_sync(root: Path) -> list[dict]:
    """Scan Python files for constants that duplicate schema dict values.

    Skips files inside ``tests/`` sub-directories.

    Args:
        root: The root directory to scan recursively.

    Returns:
        List of violations, each a dict with keys:
          ``file``: str (relative path),
          ``line``: int (line of the constant assignment),
          ``constant``: str (name of the constant),
          ``message``: str (human-readable explanation).
    """
    violations: list[dict] = []
    for py_file in sorted(root.rglob("*.py")):
        # Skip files inside any 'tests' directory
        parts = py_file.relative_to(root).parts
        if any(part == "tests" for part in parts):
            continue
        violations.extend(_check_file(py_file, root))
    return violations
