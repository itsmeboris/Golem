# golem/tests/test_detect_stack.py
"""Tests for golem.detect_stack — buildpack-style language detection."""

# pylint: disable=missing-function-docstring

import json
import subprocess
from unittest.mock import MagicMock, patch

from golem.detect_stack import (
    _detect_go,
    _detect_javascript,
    _detect_makefile_targets,
    _detect_python,
    _detect_ruby,
    _detect_rust,
    _dry_run_tool,
    _filter_unavailable,
    _parse_github_actions,
    detect_verify_config,
)
from golem.verify_config import VerifyCommand


class TestDetectPython:
    def test_black_in_pyproject_adds_black_format(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.black]\n")
        cmds = _detect_python(tmp_path)
        fmt = next((c for c in cmds if c.role == "format"), None)
        assert fmt is not None
        assert fmt.cmd == ["black", "--check", "."]

    def test_ruff_in_pyproject_adds_ruff_format_and_lint(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.ruff]\n")
        cmds = _detect_python(tmp_path)
        by_role = {c.role: c for c in cmds}
        assert by_role["format"].cmd == ["ruff", "format", "--check", "."]
        assert by_role["lint"].cmd == ["ruff", "check", "."]

    def test_black_without_ruff_adds_pylint_lint(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.black]\n")
        cmds = _detect_python(tmp_path)
        lint = next((c for c in cmds if c.role == "lint"), None)
        assert lint is not None
        assert lint.cmd[0] == "pylint"

    def test_pytest_ini_adds_pytest_test(self, tmp_path):
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        cmds = _detect_python(tmp_path)
        test = next((c for c in cmds if c.role == "test"), None)
        assert test is not None
        assert test.cmd[0] == "pytest"

    def test_pyproject_with_pytest_options_adds_pytest(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
        cmds = _detect_python(tmp_path)
        test = next((c for c in cmds if c.role == "test"), None)
        assert test is not None
        assert "pytest" in test.cmd

    def test_setup_py_is_python_marker(self, tmp_path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\n")
        cmds = _detect_python(tmp_path)
        assert len(cmds) > 0

    def test_no_markers_returns_empty(self, tmp_path):
        assert _detect_python(tmp_path) == []

    def test_all_sources_are_auto_detected(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.black]\n")
        cmds = _detect_python(tmp_path)
        assert all(c.source == "auto-detected" for c in cmds)


class TestDetectJavaScript:
    def test_test_script_adds_test_command(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}})
        )
        cmds = _detect_javascript(tmp_path)
        test = next((c for c in cmds if c.role == "test"), None)
        assert test is not None
        assert test.cmd == ["npm", "run", "test"]

    def test_lint_script_adds_lint_command(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"lint": "eslint src/"}})
        )
        cmds = _detect_javascript(tmp_path)
        lint = next((c for c in cmds if c.role == "lint"), None)
        assert lint is not None
        assert lint.cmd == ["npm", "run", "lint"]

    def test_typecheck_script_adds_typecheck(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"typecheck": "tsc --noEmit"}})
        )
        cmds = _detect_javascript(tmp_path)
        tc = next((c for c in cmds if c.role == "typecheck"), None)
        assert tc is not None

    def test_no_scripts_key_returns_empty(self, tmp_path):
        (tmp_path / "package.json").write_text(json.dumps({"name": "foo"}))
        assert _detect_javascript(tmp_path) == []

    def test_no_package_json_returns_empty(self, tmp_path):
        assert _detect_javascript(tmp_path) == []

    def test_invalid_json_returns_empty(self, tmp_path):
        (tmp_path / "package.json").write_text("{bad json}")
        assert _detect_javascript(tmp_path) == []


class TestDetectRust:
    def test_cargo_toml_adds_format_lint_test(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "myapp"\n')
        cmds = _detect_rust(tmp_path)
        roles = {c.role for c in cmds}
        assert {"format", "lint", "test"} <= roles

    def test_test_command_is_cargo_test(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
        test = next(c for c in _detect_rust(tmp_path) if c.role == "test")
        assert test.cmd == ["cargo", "test"]

    def test_format_command_is_cargo_fmt_check(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
        fmt = next(c for c in _detect_rust(tmp_path) if c.role == "format")
        assert fmt.cmd == ["cargo", "fmt", "--check"]

    def test_no_cargo_toml_returns_empty(self, tmp_path):
        assert _detect_rust(tmp_path) == []


class TestDetectGo:
    def test_go_mod_adds_format_lint_test(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/myapp\n")
        roles = {c.role for c in _detect_go(tmp_path)}
        assert {"format", "lint", "test"} <= roles

    def test_test_command_is_go_test(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/myapp\n")
        test = next(c for c in _detect_go(tmp_path) if c.role == "test")
        assert test.cmd == ["go", "test", "./..."]

    def test_no_go_mod_returns_empty(self, tmp_path):
        assert _detect_go(tmp_path) == []


class TestDetectRuby:
    def test_gemfile_adds_rspec(self, tmp_path):
        (tmp_path / "Gemfile").write_text('source "https://rubygems.org"\n')
        test = next((c for c in _detect_ruby(tmp_path) if c.role == "test"), None)
        assert test is not None
        assert "rspec" in test.cmd

    def test_rubocop_yml_adds_lint(self, tmp_path):
        (tmp_path / "Gemfile").write_text('source "https://rubygems.org"\n')
        (tmp_path / ".rubocop.yml").write_text("AllCops:\n  NewCops: enable\n")
        lint = next((c for c in _detect_ruby(tmp_path) if c.role == "lint"), None)
        assert lint is not None
        assert lint.cmd == ["rubocop"]

    def test_no_gemfile_returns_empty(self, tmp_path):
        assert _detect_ruby(tmp_path) == []


class TestDetectMakefileTargets:
    def test_test_target_detected(self, tmp_path):
        (tmp_path / "Makefile").write_text("test:\n\tpytest\n")
        assert "test" in _detect_makefile_targets(tmp_path)

    def test_multiple_targets_detected(self, tmp_path):
        (tmp_path / "Makefile").write_text("test:\n\tpytest\nlint:\n\tflake8\n")
        targets = _detect_makefile_targets(tmp_path)
        assert "test" in targets
        assert "lint" in targets

    def test_no_makefile_returns_empty_set(self, tmp_path):
        assert _detect_makefile_targets(tmp_path) == set()


class TestParseGitHubActions:
    def test_test_job_run_step_extracted(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n      - run: npm test\n"
        )
        cmds = _parse_github_actions(tmp_path)
        assert len(cmds) == 1
        assert cmds[0].role == "test"
        assert cmds[0].source == "ci-parsed"

    def test_lint_job_extracted(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  lint:\n    steps:\n      - run: eslint src/\n"
        )
        cmds = _parse_github_actions(tmp_path)
        assert cmds[0].role == "lint"

    def test_echo_lines_skipped(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n      - run: |\n"
            "          echo setup\n          npm test\n"
        )
        cmds = _parse_github_actions(tmp_path)
        assert cmds[0].cmd[0] == "npm"

    def test_no_workflows_dir_returns_empty(self, tmp_path):
        assert _parse_github_actions(tmp_path) == []

    def test_malformed_yaml_skipped(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "bad.yml").write_text("{bad yaml [")
        assert _parse_github_actions(tmp_path) == []

    def test_deploy_job_ignored(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  deploy:\n    steps:\n      - run: kubectl apply -f k8s/\n"
        )
        assert _parse_github_actions(tmp_path) == []


class TestDryRunTool:
    def test_returns_true_when_tool_available(self, tmp_path):
        with patch("golem.detect_stack.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert _dry_run_tool("npm", tmp_path) is True

    def test_returns_false_when_not_found(self, tmp_path):
        with patch(
            "golem.detect_stack.subprocess.run", side_effect=FileNotFoundError()
        ):
            assert _dry_run_tool("missing", tmp_path) is False

    def test_returns_false_on_nonzero_returncode(self, tmp_path):
        with patch("golem.detect_stack.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert _dry_run_tool("cargo", tmp_path) is False

    def test_returns_false_on_timeout(self, tmp_path):
        with patch(
            "golem.detect_stack.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["tool"], timeout=5),
        ):
            assert _dry_run_tool("slow", tmp_path) is False


class TestFilterUnavailable:
    def test_keeps_available_commands(self, tmp_path):
        cmds = [VerifyCommand(role="test", cmd=["npm", "test"], source="auto-detected")]
        with patch("golem.detect_stack._dry_run_tool", return_value=True):
            assert len(_filter_unavailable(cmds, tmp_path)) == 1

    def test_drops_unavailable_commands(self, tmp_path):
        cmds = [
            VerifyCommand(role="test", cmd=["cargo", "test"], source="auto-detected")
        ]
        with patch("golem.detect_stack._dry_run_tool", return_value=False):
            assert _filter_unavailable(cmds, tmp_path) == []

    def test_tool_check_cached_per_tool(self, tmp_path):
        cmds = [
            VerifyCommand(
                role="format", cmd=["cargo", "fmt", "--check"], source="auto-detected"
            ),
            VerifyCommand(role="test", cmd=["cargo", "test"], source="auto-detected"),
        ]
        with patch("golem.detect_stack._dry_run_tool", return_value=True) as mock_dry:
            _filter_unavailable(cmds, tmp_path)
        assert mock_dry.call_count == 1  # cargo checked once, not twice


class TestDetectVerifyConfig:
    def test_python_repo_stack_and_commands(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[tool.black]\n[tool.pytest.ini_options]\n"
        )
        result = detect_verify_config(str(tmp_path), dry_run=False)
        assert "python" in result.stack
        assert len(result.commands) > 0

    def test_unknown_repo_empty_commands(self, tmp_path):
        result = detect_verify_config(str(tmp_path), dry_run=False)
        assert result.commands == []
        assert result.stack == []

    def test_monorepo_multiple_stacks(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.black]\n")
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}})
        )
        result = detect_verify_config(str(tmp_path), dry_run=False)
        assert "python" in result.stack
        assert "javascript" in result.stack

    def test_detected_at_set(self, tmp_path):
        result = detect_verify_config(str(tmp_path), dry_run=False)
        assert result.detected_at != ""

    def test_version_is_1(self, tmp_path):
        result = detect_verify_config(str(tmp_path), dry_run=False)
        assert result.version == 1

    def test_ci_supplements_missing_roles(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.black]\n")
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n      - run: make test\n"
        )
        result = detect_verify_config(str(tmp_path), dry_run=False)
        test_cmds = [c for c in result.commands if c.role == "test"]
        assert len(test_cmds) >= 1

    def test_dry_run_calls_version_check(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
        with patch("golem.detect_stack.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="1.0", stderr="")
            detect_verify_config(str(tmp_path), dry_run=True)
        called = {call.args[0][0] for call in mock_run.call_args_list}
        assert "cargo" in called

    def test_dry_run_drops_unavailable(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
        with patch("golem.detect_stack.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=127, stdout="", stderr="not found"
            )
            result = detect_verify_config(str(tmp_path), dry_run=True)
        assert all(c.cmd[0] != "cargo" for c in result.commands)
