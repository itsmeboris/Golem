"""Setuptools build hooks for packaging the bundled golem plugin."""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_IGNORE_PATTERNS = shutil.ignore_patterns(
    "__pycache__",
    ".pytest_cache",
    "*.pyc",
    "*.pyo",
)


class build_py(_build_py):
    """Stage plugin assets into golem/_plugin_data before packaging."""

    def run(self):
        root = Path(__file__).parent.resolve()
        source = root / "plugins" / "golem"
        staged = root / "golem" / "_plugin_data"

        if not source.is_dir():
            raise FileNotFoundError(f"Bundled plugin source not found: {source}")

        if staged.exists():
            shutil.rmtree(staged)

        shutil.copytree(source, staged, ignore=_IGNORE_PATTERNS)
        try:
            super().run()
        finally:
            if staged.exists():
                shutil.rmtree(staged)


setup(cmdclass={"build_py": build_py})
