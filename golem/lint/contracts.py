"""AST-based return type extractor for Python functions."""

import ast
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Type method registry
# ---------------------------------------------------------------------------

TYPE_METHODS: dict[str, set[str]] = {
    "dict": {
        "items",
        "keys",
        "values",
        "get",
        "update",
        "pop",
        "setdefault",
        "clear",
        "copy",
        "fromkeys",
        "popitem",
    },
    "list": {
        "append",
        "extend",
        "insert",
        "remove",
        "pop",
        "clear",
        "index",
        "count",
        "sort",
        "reverse",
        "copy",
    },
    "str": {
        "split",
        "join",
        "strip",
        "lstrip",
        "rstrip",
        "upper",
        "lower",
        "replace",
        "find",
        "rfind",
        "index",
        "rindex",
        "startswith",
        "endswith",
        "format",
        "encode",
        "count",
        "center",
        "ljust",
        "rjust",
        "zfill",
        "title",
        "capitalize",
        "casefold",
        "swapcase",
        "isalpha",
        "isdigit",
        "isalnum",
        "isspace",
        "istitle",
        "isupper",
        "islower",
        "expandtabs",
        "partition",
        "rpartition",
        "maketrans",
        "translate",
        "removeprefix",
        "removesuffix",
        "splitlines",
    },
    "set": {
        "add",
        "remove",
        "discard",
        "pop",
        "clear",
        "union",
        "intersection",
        "difference",
        "symmetric_difference",
        "update",
        "intersection_update",
        "difference_update",
        "symmetric_difference_update",
        "issubset",
        "issuperset",
        "isdisjoint",
        "copy",
    },
    "tuple": {"count", "index"},
    "int": {
        "bit_length",
        "bit_count",
        "to_bytes",
        "from_bytes",
        "as_integer_ratio",
        "conjugate",
    },
    "float": {
        "is_integer",
        "as_integer_ratio",
        "hex",
        "fromhex",
        "conjugate",
    },
    "bool": {
        "bit_length",
        "bit_count",
        "to_bytes",
        "from_bytes",
        "as_integer_ratio",
        "conjugate",
    },
}

# Matches "Optional[X]" to extract X
_OPTIONAL_RE = re.compile(r"^Optional\[(.+)\]$")
# Matches "X | None" or "None | X" to extract X
_UNION_NONE_RE = re.compile(r"^(?:None \| (.+)|(.+) \| None)$")


def _collect_functions(
    tree: ast.AST, class_name: str | None = None
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Collect all public function/method definitions with their qualified names."""
    results: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            # Recurse into class body using the fully-qualified class name
            if class_name is not None:
                nested_name = f"{class_name}.{node.name}"
            else:
                nested_name = node.name
            results.extend(_collect_functions(node, class_name=nested_name))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            if class_name is not None:
                qualified = f"{class_name}.{node.name}"
            else:
                qualified = node.name
            results.append((qualified, node))
    return results


def extract_return_types(root: Path) -> dict[str, str | None]:
    """Scan all .py files under root and map public function names to return type annotations.

    Args:
        root: The root directory to scan recursively.

    Returns:
        A dict mapping ``"module.path:function_name"`` to the return annotation
        string, or ``None`` when no annotation is present.
    """
    output: dict[str, str | None] = {}
    root_parent = root.parent

    for py_file in root.rglob("*.py"):
        # Derive module path from file path relative to root's parent
        relative = py_file.relative_to(root_parent)
        module_path = str(relative.with_suffix("")).replace("/", ".")
        if module_path.endswith(".__init__"):
            module_path = module_path[: -len(".__init__")]

        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            logger.warning("Skipping file with syntax error: %s", py_file)
            continue

        for qualified_name, func_node in _collect_functions(tree):
            key = f"{module_path}:{qualified_name}"
            if func_node.returns is not None:
                value: str | None = ast.unparse(func_node.returns)
            else:
                value = None
            output[key] = value

    return output


def _extract_base_type(annotation: str | None) -> str | None:
    """Extract base type name from an annotation string.

    Handles:
    - Simple types: ``"dict"`` → ``"dict"``
    - Generic types: ``"dict[str, int]"`` → ``"dict"``
    - Optional: ``"Optional[str]"`` → ``"str"``
    - Union with None: ``"str | None"`` → ``"str"``

    Returns ``None`` if the base type is not in ``TYPE_METHODS``.
    """
    if annotation is None:
        return None

    # Handle Optional[X] → extract X recursively
    m = _OPTIONAL_RE.match(annotation)
    if m:
        inner = m.group(1)
        return _extract_base_type(inner)

    # Handle "X | None" or "None | X" → extract X recursively
    m = _UNION_NONE_RE.match(annotation)
    if m:
        inner = m.group(1) or m.group(2)
        return _extract_base_type(inner)

    # Handle generic forms like dict[str, int] → extract "dict"
    bracket_pos = annotation.find("[")
    if bracket_pos != -1:
        base = annotation[:bracket_pos]
    else:
        base = annotation

    return base if base in TYPE_METHODS else None


def _resolve_imports(tree: ast.AST, root: Path | None) -> dict[str, str]:
    """Map local names to ``module:func`` keys from import statements.

    Handles:
    - ``from pkg.mod import func`` → ``{"func": "pkg.mod:func"}``
    - ``from pkg.mod import func as alias`` → ``{"alias": "pkg.mod:func"}``
    - ``import pkg.mod`` → ``{"pkg.mod": "pkg.mod"}``

    Args:
        tree: Parsed AST of the file being analysed.
        root: Unused; kept for a consistent signature.

    Returns:
        Dict mapping local name to ``"module:func"`` registry key.
    """
    del root  # kept for consistent signature
    import_map: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name
                import_map[local_name] = f"{module}:{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                # import pkg.mod — stored so attribute-style calls can be resolved
                local_name = alias.asname if alias.asname else alias.name
                import_map[local_name] = alias.name

    return import_map


def _find_call_assignments(
    tree: ast.AST,
    import_map: dict[str, str],
    type_registry: dict[str, str | None],
) -> dict[str, str]:
    """Return mapping of variable names to their return type annotation.

    Scans assignment statements of the form ``x = func(...)`` where ``func``
    resolves via ``import_map`` to a key in ``type_registry``.  When a
    variable is reassigned to a non-tracked call, it is removed from the
    mapping (last assignment wins).

    Args:
        tree: Parsed AST of the consumer file.
        import_map: Output of ``_resolve_imports``.
        type_registry: Output of ``extract_return_types``.

    Returns:
        Dict mapping variable name → return type annotation string.
    """
    var_types: dict[str, str] = {}

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Do not descend into nested scopes — inner-scope assignments must
            # not affect outer-scope variable tracking.
            continue
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Call):
            # Assignment to non-call — remove variable from tracking
            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_types.pop(target.id, None)
            continue

        # Determine the registry key for the called function
        registry_key = _call_to_registry_key(node.value, import_map)
        if registry_key is None:
            # Unknown call — remove variable from tracking
            for target in node.targets:
                if isinstance(target, ast.Name):
                    var_types.pop(target.id, None)
            continue

        annotation = type_registry.get(registry_key)
        for target in node.targets:
            if not isinstance(target, ast.Name):
                continue
            if annotation is not None:
                var_types[target.id] = annotation
            else:
                var_types.pop(target.id, None)

    return var_types


def _unparse_dotted_name(node: ast.expr) -> str | None:
    """Reconstruct a dotted name from nested Attribute or Name nodes.

    For example, given the AST for ``pkg.mod``, returns ``"pkg.mod"``.
    Returns ``None`` if the node is not a pure dotted name chain.
    """
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _unparse_dotted_name(node.value)
        if parent is not None:
            return f"{parent}.{node.attr}"
    return None


def _call_to_registry_key(
    call_node: ast.Call,
    import_map: dict[str, str],
) -> str | None:
    """Derive a ``module:func`` registry key from a ``Call`` AST node.

    Handles ``func(...)`` (simple name), ``module.func(...)``
    (attribute-style), and ``pkg.mod.func(...)`` (chained attribute) calls.

    Returns ``None`` when the call cannot be resolved.
    """
    func = call_node.func
    if isinstance(func, ast.Name):
        return import_map.get(func.id)
    if isinstance(func, ast.Attribute):
        dotted = _unparse_dotted_name(func.value)
        if dotted is not None:
            module_key = import_map.get(dotted)
            if module_key is not None:
                # import_map[mod] stores module path for `import mod` statements
                return f"{module_key}:{func.attr}"
    return None


def check_consumer_producer_types(root: Path) -> list[dict]:
    """Cross-check function return types against how callers use their values.

    For each ``.py`` file under ``root``:

    1. Resolves imports to map local names to registry keys.
    2. Finds ``x = func(...)`` assignments where the function has a known
       return type.
    3. Detects attribute accesses like ``x.items()`` on those variables.
    4. Reports a finding when the accessed attribute is incompatible with
       the declared return type.

    Args:
        root: The root directory to scan recursively.

    Returns:
        List of finding dicts, each with keys: ``file``, ``line``,
        ``function``, ``return_type``, ``invalid_access``, ``message``.
    """
    type_registry = extract_return_types(root)
    findings: list[dict] = []

    for py_file in sorted(root.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            logger.warning("Skipping file with syntax error: %s", py_file)
            continue

        rel_file = str(py_file.relative_to(root))
        import_map = _resolve_imports(tree, root)
        var_types = _find_call_assignments(tree, import_map, type_registry)

        if not var_types:
            continue

        # Build reverse map: registry_key → local function name used in call
        # We need the function name for the finding message.
        # Also build a map from variable name → registry key for easy lookup.
        var_to_key: dict[str, str] = {}
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Do not descend into nested scopes.
                continue
            if not isinstance(node, ast.Assign):
                continue
            if not isinstance(node.value, ast.Call):
                continue
            rkey = _call_to_registry_key(node.value, import_map)
            if rkey is None:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in var_types:
                    var_to_key[target.id] = rkey

        findings.extend(
            _check_attribute_accesses(tree, var_types, var_to_key, rel_file)
        )

    return findings


def _walk_top_level(tree: ast.AST):
    """Yield all nodes from the top-level scope, skipping nested function/class bodies.

    Unlike ``ast.walk``, this generator does **not** descend into
    ``FunctionDef``, ``AsyncFunctionDef``, or ``ClassDef`` nodes.  It visits
    every node that is reachable from *tree* without crossing a scope boundary.

    Args:
        tree: The root AST node (typically a ``Module``).

    Yields:
        ``ast.AST`` nodes at module scope (not inside nested scopes).
    """
    stack = list(ast.iter_child_nodes(tree))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Do not descend into nested scope bodies.
            continue
        stack.extend(ast.iter_child_nodes(node))


def _func_name_from_key(registry_key: str) -> str:
    """Extract the function name portion from a ``module:func`` registry key."""
    return registry_key.split(":")[-1]


def _check_attribute_accesses(
    tree: ast.AST,
    var_types: dict[str, str],
    var_to_key: dict[str, str],
    rel_file: str,
) -> list[dict]:
    """Walk module-level AST looking for attribute accesses on tracked variables.

    Skips nested function/class bodies to avoid false positives from inner-scope
    variables that happen to share the same name as outer-scope tracked variables.

    Returns a list of finding dicts for mismatched accesses.
    """
    findings: list[dict] = []

    for node in _walk_top_level(tree):
        # We want Attribute accesses: `x.attr` or `x.method(...)`
        if not isinstance(node, ast.Attribute):
            continue
        if not isinstance(node.value, ast.Name):
            continue

        var_name = node.value.id
        if var_name not in var_types:
            continue

        annotation = var_types[var_name]
        base_type = _extract_base_type(annotation)
        if base_type is None:
            # Unknown or non-primitive type — skip
            continue

        attr = node.attr
        valid_methods = TYPE_METHODS[base_type]
        if attr in valid_methods:
            continue

        registry_key = var_to_key.get(var_name, "")
        func_name = _func_name_from_key(registry_key)

        findings.append(
            {
                "file": rel_file,
                "line": node.lineno,
                "function": func_name,
                "return_type": annotation,
                "invalid_access": attr,
                "message": (
                    f"'{func_name}' returns {annotation!r}; "
                    f"'.{attr}' is not a valid method on {base_type!r}"
                ),
            }
        )

    return findings
