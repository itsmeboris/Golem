"""Mutation testing configuration and smoke test."""

import subprocess
import sys


class TestMutmutAvailable:
    def test_mutmut_importable(self):
        """mutmut package is installed and importable."""
        import mutmut  # noqa: F401

        assert mutmut is not None

    def test_mutmut_cli_available(self):
        """mutmut CLI is callable."""
        result = subprocess.run(
            [sys.executable, "-m", "mutmut", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "mutmut" in result.stdout.lower()


class TestMutmutConfig:
    def test_pyproject_has_mutmut_section(self):
        """pyproject.toml contains [tool.mutmut] configuration."""
        import tomllib
        from pathlib import Path

        pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
        with open(pyproject, "rb") as f:
            config = tomllib.load(f)

        assert "mutmut" in config.get(
            "tool", {}
        ), "Missing [tool.mutmut] in pyproject.toml"
        mutmut_conf = config["tool"]["mutmut"]
        assert "paths_to_mutate" in mutmut_conf
