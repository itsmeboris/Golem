"""AST-based code analysis using ast-grep (sg).

Provides structural analysis that regex-based antipattern detection cannot:
unused imports, unreachable code, mismatched signatures, etc.

Falls back gracefully when ast-grep is not installed.
"""

import json
import logging
import shutil
import subprocess
from pathlib import Path

from golem.sandbox import make_sandbox_preexec

logger = logging.getLogger("golem.ast_analysis")

_UNUSED_IMPORT_PREFIX = "Potentially unused import: "


def is_ast_grep_available() -> bool:
    """Return True if the ast-grep (sg) binary is on PATH."""
    return shutil.which("sg") is not None


def _is_test_file(filepath: str) -> bool:
    """Return True if the basename of filepath matches test_*.py or *_test.py."""
    basename = Path(filepath).name
    return basename.startswith("test_") or basename.endswith("_test.py")


def _is_unused_import_concern(message: str) -> bool:
    """Return True if message matches the unused-import pattern."""
    return message.startswith(_UNUSED_IMPORT_PREFIX)


def _is_import_used(work_dir: str, filepath: str, module_name: str) -> bool:
    """Return True if module_name appears in any non-import line of the file.

    Reads work_dir/filepath. If the file cannot be read, returns False so the
    caller treats the import as unused and keeps the concern.
    """
    try:
        content = (Path(work_dir) / filepath).read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return False

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            continue
        if module_name in line:
            return True
    return False


def run_ast_analysis(
    work_dir: str,
    changed_files: list[str],
    *,
    timeout: int = 30,
) -> list[str]:
    """Run ast-grep rules against changed files and return concern strings.

    Returns an empty list if ast-grep is not installed or no rules match.
    Unused-import concerns are suppressed for test files and for non-test files
    where the imported symbol is actually referenced in the code.
    """
    if not is_ast_grep_available():
        return []

    if not changed_files:
        return []

    # Filter to Python files only
    py_files = [f for f in changed_files if f.endswith(".py")]
    if not py_files:
        return []

    rules_dir = Path(__file__).parent / "ast_rules"
    if not rules_dir.is_dir():
        return []

    rule_files = list(rules_dir.glob("*.yaml"))
    if not rule_files:
        return []

    concerns: list[str] = []
    for rule_file in rule_files:
        try:
            result = subprocess.run(
                ["sg", "scan", "--rule", str(rule_file), "--json", *py_files],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                preexec_fn=make_sandbox_preexec(),
            )
            if result.stdout.strip():
                matches = _parse_sg_output(result.stdout)
                for match in matches:
                    filepath = match.get("file", "unknown")
                    line_num = match.get("range", {}).get("start", {}).get("line", "?")
                    message = match.get("message", rule_file.stem)
                    concern = f"AST: {message} in {filepath}:{line_num}"

                    if _is_unused_import_concern(message):
                        if _is_test_file(filepath):
                            continue
                        module_name = message[len(_UNUSED_IMPORT_PREFIX) :]
                        if _is_import_used(work_dir, filepath, module_name):
                            continue

                    concerns.append(concern)
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("ast-grep rule %s failed: %s", rule_file.name, exc)

    return concerns


def _parse_sg_output(stdout: str) -> list[dict]:
    """Parse ast-grep --json output (JSON array) into a list of match dicts.

    Tries full JSON array parse first, then falls back to line-by-line JSONL.
    """
    stripped = stdout.strip()
    if not stripped:
        return []

    # ast-grep outputs a JSON array
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, list):
            return [m for m in parsed if isinstance(m, dict)]
        if isinstance(parsed, dict):
            return [parsed]
        return []
    except json.JSONDecodeError:
        pass

    # Fallback: try line-by-line JSONL
    matches: list[dict] = []
    for line in stripped.splitlines():
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                matches.append(obj)
        except json.JSONDecodeError:
            continue
    return matches
