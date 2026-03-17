"""AST-based return type extractor for Python functions."""

import ast
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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
