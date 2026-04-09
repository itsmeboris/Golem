"""Tests for plugins/golem/scripts/lib/setup_flow.py."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import setup_flow

VALID_GOLEM_MD = """\
# My Project

## Stack
Python and TypeScript

### verify
- **role:** `test` | **cmd:** `["pytest", "-x"]` | **timeout:** 120
- **role:** `lint` | **cmd:** `["pylint", "--errors-only", "src/"]`
- **role:** `format` | **cmd:** `["black", "--check", "."]` | **timeout:** 60
"""

MINIMAL_GOLEM_MD = """\
### verify
- **role:** `test` | **cmd:** `["pytest"]`
"""

NO_VERIFY_SECTION = """\
# My Project

## Stack
Go

## Features
- something
"""

MALFORMED_CMD_JSON = """\
### verify
- **role:** `test` | **cmd:** `[not-valid-json]`
- **role:** `lint` | **cmd:** `["pylint", "src/"]`
"""

EMPTY_STRING = ""


class TestCollectRepoSignals:
    def test_detects_present_files(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "README.md").write_text("# readme")
        result = setup_flow.collect_repo_signals(str(tmp_path))

        assert result["repo_path"] == str(tmp_path.resolve())
        assert result["repo_name"] == tmp_path.name
        assert "pyproject.toml" in result["detected_files"]
        assert "README.md" in result["detected_files"]

    def test_absent_files_in_missing_dict(self, tmp_path):
        result = setup_flow.collect_repo_signals(str(tmp_path))
        assert "pyproject.toml" in result["missing_files"]
        assert "package.json" in result["missing_files"]

    def test_detects_golem_md_and_verify_yaml(self, tmp_path):
        (tmp_path / "golem.md").write_text("# golem")
        golem_dir = tmp_path / ".golem"
        golem_dir.mkdir()
        (golem_dir / "verify.yaml").write_text("version: 1")

        result = setup_flow.collect_repo_signals(str(tmp_path))
        assert result["detected_files"]["golem.md"] is True
        assert result["detected_files"][".golem/verify.yaml"] is True

    def test_detects_directory_as_signal(self, tmp_path):
        # .github/workflows is a directory
        workflows = tmp_path / ".github" / "workflows"
        workflows.mkdir(parents=True)
        result = setup_flow.collect_repo_signals(str(tmp_path))
        assert ".github/workflows" in result["detected_files"]

    def test_detected_and_missing_are_mutually_exclusive(self, tmp_path):
        (tmp_path / "Makefile").write_text("all:")
        result = setup_flow.collect_repo_signals(str(tmp_path))
        assert "Makefile" in result["detected_files"]
        assert "Makefile" not in result["missing_files"]


class TestFinalizeSetup:
    def test_returns_error_when_golem_md_missing(self, tmp_path):
        result = setup_flow.finalize_setup(str(tmp_path))
        assert result["ok"] is False
        assert result["error"] == "golem.md not found"

    def test_returns_error_when_no_verify_commands(self, tmp_path):
        (tmp_path / "golem.md").write_text(NO_VERIFY_SECTION)
        result = setup_flow.finalize_setup(str(tmp_path))
        assert result["ok"] is False
        assert "No verify commands" in result["error"]

    def test_returns_ok_true_on_success(self, tmp_path):
        (tmp_path / "golem.md").write_text(VALID_GOLEM_MD)
        with patch("setup_flow.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            result = setup_flow.finalize_setup(str(tmp_path))

        assert result["ok"] is True
        assert result["command_count"] == 3
        assert ".golem/verify.yaml" in result["verify_yaml_path"]

    def test_returns_error_when_subprocess_fails(self, tmp_path):
        (tmp_path / "golem.md").write_text(VALID_GOLEM_MD)
        with patch("setup_flow.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="import error"
            )
            result = setup_flow.finalize_setup(str(tmp_path))

        assert result["ok"] is False
        assert "save_verify_config failed" in result["error"]

    def test_returns_error_when_subprocess_not_found(self, tmp_path):
        (tmp_path / "golem.md").write_text(VALID_GOLEM_MD)
        with patch("setup_flow.subprocess.run", side_effect=FileNotFoundError):
            result = setup_flow.finalize_setup(str(tmp_path))

        assert result["ok"] is False
        assert "Failed to write verify.yaml" in result["error"]

    def test_returns_error_when_subprocess_times_out(self, tmp_path):
        (tmp_path / "golem.md").write_text(VALID_GOLEM_MD)
        with patch(
            "setup_flow.subprocess.run",
            side_effect=subprocess.TimeoutExpired("python3", 10),
        ):
            result = setup_flow.finalize_setup(str(tmp_path))

        assert result["ok"] is False
        assert "Failed to write verify.yaml" in result["error"]

    def test_adds_golem_md_to_gitignore(self, tmp_path):
        (tmp_path / "golem.md").write_text(MINIMAL_GOLEM_MD)
        with patch("setup_flow.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            setup_flow.finalize_setup(str(tmp_path))

        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        assert "golem.md" in gitignore.read_text()

    def test_returns_error_for_invalid_role(self, tmp_path):
        bad_md = '### verify\n- **role:** `invalid` | **cmd:** `["pytest"]`\n'
        (tmp_path / "golem.md").write_text(bad_md)
        result = setup_flow.finalize_setup(str(tmp_path))
        assert result["ok"] is False
        assert "Invalid role" in result["error"]


class TestParseVerifyCommands:
    @pytest.mark.parametrize(
        "content,expected_count,expected_roles",
        [
            (
                VALID_GOLEM_MD,
                3,
                {"test", "lint", "format"},
            ),
            (
                MINIMAL_GOLEM_MD,
                1,
                {"test"},
            ),
            (
                NO_VERIFY_SECTION,
                0,
                set(),
            ),
            (
                EMPTY_STRING,
                0,
                set(),
            ),
            (
                MALFORMED_CMD_JSON,
                1,
                {"lint"},  # Only the valid one is parsed; malformed is skipped
            ),
        ],
        ids=["valid_full", "minimal", "no_verify_section", "empty", "malformed_cmd"],
    )
    def test_parse_various_inputs(self, content, expected_count, expected_roles):
        result = setup_flow._parse_verify_commands(content)
        assert len(result) == expected_count
        assert {cmd["role"] for cmd in result} == expected_roles

    def test_extracts_correct_cmd_list(self):
        result = setup_flow._parse_verify_commands(VALID_GOLEM_MD)
        test_cmd = next(c for c in result if c["role"] == "test")
        assert test_cmd["cmd"] == ["pytest", "-x"]

    def test_extracts_timeout_when_present(self):
        result = setup_flow._parse_verify_commands(VALID_GOLEM_MD)
        test_cmd = next(c for c in result if c["role"] == "test")
        assert test_cmd["timeout"] == 120

    def test_no_timeout_key_when_absent(self):
        result = setup_flow._parse_verify_commands(VALID_GOLEM_MD)
        lint_cmd = next(c for c in result if c["role"] == "lint")
        assert "timeout" not in lint_cmd

    def test_source_field_is_agent_discovered(self):
        result = setup_flow._parse_verify_commands(MINIMAL_GOLEM_MD)
        assert result[0]["source"] == "agent-discovered"

    def test_stops_at_next_h3_section(self):
        content = """\
### verify
- **role:** `test` | **cmd:** `["pytest"]`
### other
- **role:** `lint` | **cmd:** `["pylint"]`
"""
        result = setup_flow._parse_verify_commands(content)
        assert len(result) == 1
        assert result[0]["role"] == "test"

    def test_entries_without_both_role_and_cmd_are_skipped(self):
        content = """\
### verify
- some unrelated line
- **role:** `test` | **cmd:** `["pytest"]`
"""
        result = setup_flow._parse_verify_commands(content)
        assert len(result) == 1


class TestDetectStackFromGolemMd:
    @pytest.mark.parametrize(
        "content,expected_stack",
        [
            ("## Stack\nPython\n", ["python"]),
            # Note: "java" is a substring of "javascript", so java is also detected.
            # The dict iteration order is: python, javascript, typescript, go, rust, java, ruby
            (
                "## Stack\nTypeScript and JavaScript\n",
                ["javascript", "typescript", "java"],
            ),
            ("## Stack\nGo\n", ["go"]),
            ("## Stack\nRust\n", ["rust"]),
            ("## Stack\n\n## Other\nstuff\n", []),
            ("No stack section here\n", []),
        ],
        ids=["python", "ts_and_js", "go", "rust", "empty_stack", "no_section"],
    )
    def test_detects_language(self, content, expected_stack):
        result = setup_flow._detect_stack_from_golem_md(content)
        assert result == expected_stack

    def test_stops_at_next_h2_section(self):
        content = "## Stack\nPython\n## Other\nGo\n"
        result = setup_flow._detect_stack_from_golem_md(content)
        assert result == ["python"]
        assert "go" not in result

    def test_deduplicates_languages(self):
        content = "## Stack\nPython python PYTHON\n"
        result = setup_flow._detect_stack_from_golem_md(content)
        assert result.count("python") == 1


class TestEnsureGitignored:
    def test_creates_gitignore_when_missing(self, tmp_path):
        setup_flow._ensure_gitignored(tmp_path, "golem.md")
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        assert gitignore.read_text() == "golem.md\n"

    def test_appends_entry_when_gitignore_exists_without_entry(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\n")
        setup_flow._ensure_gitignored(tmp_path, "golem.md")
        content = gitignore.read_text()
        assert "*.pyc" in content
        assert "golem.md" in content

    def test_does_not_duplicate_entry(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc\ngolem.md\n")
        setup_flow._ensure_gitignored(tmp_path, "golem.md")
        lines = [l for l in gitignore.read_text().splitlines() if l == "golem.md"]
        assert len(lines) == 1

    def test_appends_newline_before_entry_when_missing(self, tmp_path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text("*.pyc")  # no trailing newline
        setup_flow._ensure_gitignored(tmp_path, "secret.txt")
        content = gitignore.read_text()
        assert content == "*.pyc\nsecret.txt\n"


class TestResolvePythonCmd:
    """Test _resolve_python_cmd accept/reject cases."""

    @pytest.mark.parametrize(
        "cmd0",
        ["python", "python3", "python3.11", "python3.12", "python3.13", "python3.14.1"],
        ids=["python", "python3", "3.11", "3.12", "3.13", "3.14.1"],
    )
    def test_rewrites_python_variants(self, cmd0):
        result = setup_flow._resolve_python_cmd([cmd0, "-m", "black"])
        assert result[0] != cmd0
        assert result[1:] == ["-m", "black"]

    @pytest.mark.parametrize(
        "cmd0",
        ["python2", "python2.7", "python.11", "black", "pylint", "pytest", "pythonic", "node"],
        ids=["python2", "python2.7", "python.11", "black", "pylint", "pytest", "pythonic", "node"],
    )
    def test_does_not_rewrite_non_python3(self, cmd0):
        result = setup_flow._resolve_python_cmd([cmd0, "--check"])
        assert result[0] == cmd0

    def test_empty_cmd_unchanged(self):
        assert setup_flow._resolve_python_cmd([]) == []
