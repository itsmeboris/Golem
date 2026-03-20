"""Regression tests: dead-code lint tools must report zero violations on the real codebase."""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class TestDeadCodeRegressionChecks:
    def test_pylint_no_unused_imports_variables_or_arguments(self):
        """SPEC-1: pylint with W0611/W0612/W0101/W0613 enabled must exit 0 on golem/."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pylint",
                "--disable=all",
                "--enable=W0611,W0612,W0101,W0613",
                "golem/",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "pylint reported dead-code violations:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    def test_pyflakes_no_violations(self):
        """SPEC-2: scripts/pyflakes_noqa.py must exit 0 on golem/."""
        result = subprocess.run(
            [sys.executable, "scripts/pyflakes_noqa.py", "golem/"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "pyflakes_noqa.py reported violations:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )

    def test_vulture_no_dead_code(self):
        """SPEC-3: vulture must exit 0 on golem/ with the project whitelist."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "vulture",
                "golem/",
                "vulture_whitelist.py",
                "--min-confidence",
                "80",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "vulture reported dead code:\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
