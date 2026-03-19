"""Cross-module lint: detect public definitions never imported by other modules."""

import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_test_file(path: Path) -> bool:
    """Return True if *path* lives inside a ``tests`` directory."""
    return any(part == "tests" for part in path.parts)


def _is_init_file(path: Path) -> bool:
    """Return True if *path* is an ``__init__.py`` file."""
    return path.name == "__init__.py"


def _is_public_name(name: str) -> bool:
    """Return True if *name* is a public, non-dunder identifier."""
    if name.startswith("_"):
        return False
    return True


def _collect_dunder_all(tree: ast.Module) -> set[str] | None:
    """Return the names listed in ``__all__``, or None if not present.

    Only handles the common ``__all__ = [...]`` / ``__all__ = (...)`` form.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == "__all__"):
                continue
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple)):
                names: set[str] = set()
                for elt in value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        names.add(elt.value)
                return names
    return None


def _collect_definitions(path: Path, root: Path) -> list[dict]:
    """Return module-level public class/function definitions from *path*.

    Each entry is a dict with keys: file, line, name, kind.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        logger.warning("Skipping unparseable file %s", path)
        return []

    dunder_all = _collect_dunder_all(tree)
    rel_path = str(path.relative_to(root))

    definitions = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            kind = "class"
            name = node.name
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "function"
            name = node.name
        else:
            continue

        if not _is_public_name(name):
            continue

        # If __all__ is defined, names in __all__ are explicitly exported —
        # treat them as "used" regardless of whether they are imported elsewhere.
        if dunder_all is not None and name in dunder_all:
            continue

        definitions.append(
            {
                "file": rel_path,
                "line": node.lineno,
                "name": name,
                "kind": kind,
            }
        )

    logger.debug("Collected %d definitions from %s", len(definitions), rel_path)
    return definitions


def _collect_imported_names(path: Path) -> set[str]:
    """Return all names explicitly imported in *path*.

    Handles:
    - ``import name``  → collects ``name``
    - ``import a.b as alias`` → collects ``alias``
    - ``from module import name`` → collects ``name``
    - ``from module import name as alias`` → collects both ``name`` and ``alias``
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        logger.warning("Skipping unparseable file %s during import scan", path)
        return set()

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # Always add the base name (first component of dotted name)
                names.add(alias.name.split(".")[0])
                if alias.asname:
                    names.add(alias.asname)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.name)
                if alias.asname:
                    names.add(alias.asname)
    return names


def check_unused_exports(root: Path) -> list[dict]:
    """Scan for public definitions that are never imported by other modules.

    Returns list of violations, each a dict with keys:
      file: str (relative path)
      line: int (line of the definition)
      name: str (name of the unused definition)
      kind: str ("class" or "function")
      message: str (human-readable explanation)
    """
    all_py_files = list(root.rglob("*.py"))

    # Pass 1 — collect definitions from non-test, non-__init__ files
    definitions: list[dict] = []
    for py_file in all_py_files:
        if _is_test_file(py_file):
            continue
        if _is_init_file(py_file):
            continue
        definitions.extend(_collect_definitions(py_file, root))

    logger.debug("Total definitions to check: %d", len(definitions))

    # Pass 2 — collect all imported names across the entire codebase
    all_imported: set[str] = set()
    for py_file in all_py_files:
        all_imported |= _collect_imported_names(py_file)

    logger.debug("Total imported names found: %d", len(all_imported))

    # Compare: flag definitions whose name is not imported anywhere
    violations: list[dict] = []
    for defn in definitions:
        if defn["name"] not in all_imported:
            violations.append(
                {
                    "file": defn["file"],
                    "line": defn["line"],
                    "name": defn["name"],
                    "kind": defn["kind"],
                    "message": (
                        "%s '%s' defined in %s (line %d) is never imported"
                        % (defn["kind"], defn["name"], defn["file"], defn["line"])
                    ),
                }
            )

    logger.info("check_unused_exports found %d violation(s)", len(violations))
    return violations
