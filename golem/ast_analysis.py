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

logger = logging.getLogger("golem.ast_analysis")


def is_ast_grep_available() -> bool:
    """Return True if the ast-grep (sg) binary is on PATH."""
    return shutil.which("sg") is not None


def run_ast_analysis(
    work_dir: str,
    changed_files: list[str],
    *,
    timeout: int = 30,
) -> list[str]:
    """Run ast-grep rules against changed files and return concern strings.

    Returns an empty list if ast-grep is not installed or no rules match.
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
            )
            if result.stdout.strip():
                for line in result.stdout.strip().splitlines():
                    try:
                        match = json.loads(line)
                        filepath = match.get("file", "unknown")
                        line_num = (
                            match.get("range", {}).get("start", {}).get("line", "?")
                        )
                        message = match.get("message", rule_file.stem)
                        concerns.append(f"AST: {message} in {filepath}:{line_num}")
                    except json.JSONDecodeError:
                        continue
        except (subprocess.SubprocessError, OSError) as exc:
            logger.debug("ast-grep rule %s failed: %s", rule_file.name, exc)

    return concerns
