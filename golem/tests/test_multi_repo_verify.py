# golem/tests/test_multi_repo_verify.py
"""Integration tests for the attach → detect → verify pipeline on non-Python repos.

Exercises the full cycle: stack detection, verify.yaml save/load, run_verification
dispatch, and RepoRegistry.attach with run_detection=True.
"""

# pylint: disable=missing-function-docstring

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from golem.detect_stack import detect_verify_config
from golem.repo_registry import RepoRegistry
from golem.verify_config import (
    VerifyCommand,
    VerifyConfig,
    load_verify_config,
    save_verify_config,
)
from golem.verifier import run_verification

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_subprocess_mock(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Return a MagicMock subprocess.run result."""
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# 1. JavaScript repo: detect → round-trip → verify
# ---------------------------------------------------------------------------


class TestJavaScriptRepo:
    def test_stack_contains_javascript(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "lint": "eslint src/"}}),
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        assert "javascript" in config.stack

    def test_commands_include_test_and_lint_roles(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "lint": "eslint src/"}}),
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        roles = {c.role for c in config.commands}
        assert "test" in roles
        assert "lint" in roles

    def test_test_command_is_npm_run_test(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}}),
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        test_cmd = next(c for c in config.commands if c.role == "test")
        assert test_cmd.cmd == ["npm", "run", "test"]

    def test_lint_command_is_npm_run_lint(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "lint": "eslint src/"}}),
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        lint_cmd = next(c for c in config.commands if c.role == "lint")
        assert lint_cmd.cmd == ["npm", "run", "lint"]

    def test_round_trip_via_yaml(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "lint": "eslint src/"}}),
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        save_verify_config(str(tmp_path), config)
        loaded = load_verify_config(str(tmp_path))
        assert loaded is not None
        assert "javascript" in loaded.stack
        loaded_roles = {c.role for c in loaded.commands}
        assert "test" in loaded_roles
        assert "lint" in loaded_roles

    def test_run_verification_passes_with_mocked_subprocess(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "lint": "eslint src/"}}),
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        save_verify_config(str(tmp_path), config)

        with patch("golem.verifier.subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_mock(returncode=0, stdout="ok")
            result = run_verification(str(tmp_path))

        assert result.passed is True
        assert len(result.command_results) == len(config.commands)

    def test_run_verification_command_results_have_correct_roles(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "lint": "eslint src/"}}),
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        save_verify_config(str(tmp_path), config)

        with patch("golem.verifier.subprocess.run") as mock_run:
            mock_run.return_value = _make_subprocess_mock(returncode=0, stdout="ok")
            result = run_verification(str(tmp_path))

        result_roles = {cr["role"] for cr in result.command_results}
        expected_roles = {c.role for c in config.commands}
        assert result_roles == expected_roles


# ---------------------------------------------------------------------------
# 2. Rust repo: detect stack
# ---------------------------------------------------------------------------


class TestRustRepo:
    def test_stack_contains_rust(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        assert "rust" in config.stack

    def test_commands_include_test_and_lint_roles(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        roles = {c.role for c in config.commands}
        assert "test" in roles
        assert "lint" in roles

    def test_test_command_is_cargo_test(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "test"\nversion = "0.1.0"\n',
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        test_cmd = next(c for c in config.commands if c.role == "test")
        assert test_cmd.cmd == ["cargo", "test"]


# ---------------------------------------------------------------------------
# 3. Go repo: detect stack
# ---------------------------------------------------------------------------


class TestGoRepo:
    def test_stack_contains_go(self, tmp_path):
        (tmp_path / "go.mod").write_text(
            "module example.com/test\n\ngo 1.21\n",
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        assert "go" in config.stack

    def test_commands_include_test_role(self, tmp_path):
        (tmp_path / "go.mod").write_text(
            "module example.com/test\n\ngo 1.21\n",
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        roles = {c.role for c in config.commands}
        assert "test" in roles

    def test_test_command_is_go_test(self, tmp_path):
        (tmp_path / "go.mod").write_text(
            "module example.com/test\n\ngo 1.21\n",
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        test_cmd = next(c for c in config.commands if c.role == "test")
        assert test_cmd.cmd == ["go", "test", "./..."]


# ---------------------------------------------------------------------------
# 4. Makefile repo: detect make targets
# ---------------------------------------------------------------------------


class TestMakefileRepo:
    def test_commands_include_test_from_make_targets(self, tmp_path):
        (tmp_path / "Makefile").write_text(
            "test:\n\techo ok\n\nlint:\n\techo ok\n",
            encoding="utf-8",
        )
        # Makefile detection supplements via CI when no language markers present.
        # Since there's no CI workflow, commands come only from _parse_github_actions
        # (none) and language detectors (none). The Makefile targets are detected
        # as make targets but the current detect_verify_config doesn't map them
        # to commands unless they appear in a CI workflow.
        # Test that _detect_makefile_targets correctly sees both targets.
        from golem.detect_stack import _detect_makefile_targets

        targets = _detect_makefile_targets(tmp_path)
        assert "test" in targets
        assert "lint" in targets

    def test_makefile_targets_via_ci_workflow(self, tmp_path):
        """When Makefile targets appear in a GH Actions workflow, they are extracted."""
        (tmp_path / "Makefile").write_text(
            "test:\n\techo ok\n\nlint:\n\techo ok\n",
            encoding="utf-8",
        )
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n      - run: make test\n"
            "  lint:\n    steps:\n      - run: make lint\n",
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        roles = {c.role for c in config.commands}
        assert "test" in roles
        assert "lint" in roles

    def test_makefile_test_command_uses_make(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n      - run: make test\n",
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        test_cmd = next(c for c in config.commands if c.role == "test")
        assert test_cmd.cmd == ["make", "test"]


# ---------------------------------------------------------------------------
# 5. Unknown repo (no markers): fail closed
# ---------------------------------------------------------------------------


class TestUnknownRepo:
    def test_detect_returns_empty_commands(self, tmp_path):
        config = detect_verify_config(str(tmp_path), dry_run=False)
        assert config.commands == []

    def test_detect_returns_empty_stack(self, tmp_path):
        config = detect_verify_config(str(tmp_path), dry_run=False)
        assert config.stack == []

    def test_run_verification_fails_closed(self, tmp_path):
        result = run_verification(str(tmp_path))
        assert result.passed is False

    def test_run_verification_error_field_populated(self, tmp_path):
        result = run_verification(str(tmp_path))
        assert result.error != ""
        assert len(result.error) > 0


# ---------------------------------------------------------------------------
# 6. Existing verify.yaml preserved on re-attach; force regenerates
# ---------------------------------------------------------------------------


class TestExistingConfigPreservation:
    def _write_existing_config(self, tmp_path: Path) -> None:
        golem_dir = tmp_path / ".golem"
        golem_dir.mkdir(parents=True, exist_ok=True)
        (golem_dir / "verify.yaml").write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: [user-defined]\n"
            "commands:\n  - role: test\n    cmd: [my-custom-tool]\n    source: user\n",
            encoding="utf-8",
        )

    def test_run_detection_without_force_skips_detect(self, tmp_path):
        self._write_existing_config(tmp_path)
        with patch("golem.repo_registry.detect_verify_config") as mock_detect:
            reg = RepoRegistry(registry_path=tmp_path / "repos.json")
            reg._run_detection(str(tmp_path), force=False)
        mock_detect.assert_not_called()

    def test_run_detection_without_force_preserves_user_command(self, tmp_path):
        self._write_existing_config(tmp_path)
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg._run_detection(str(tmp_path), force=False)
        loaded = load_verify_config(str(tmp_path))
        assert loaded is not None
        assert loaded.commands[0].cmd == ["my-custom-tool"]

    def test_run_detection_with_force_calls_detect(self, tmp_path):
        self._write_existing_config(tmp_path)
        fresh_cfg = VerifyConfig(
            version=1,
            commands=[
                VerifyCommand(role="test", cmd=["npm", "test"], source="auto-detected")
            ],
            detected_at="2026-04-05T00:00:00Z",
            stack=["javascript"],
        )
        with (
            patch(
                "golem.repo_registry.detect_verify_config", return_value=fresh_cfg
            ) as mock_detect,
            patch("golem.repo_registry.save_verify_config"),
        ):
            reg = RepoRegistry(registry_path=tmp_path / "repos.json")
            reg._run_detection(str(tmp_path), force=True)
        mock_detect.assert_called_once()

    def test_run_detection_with_force_overwrites_config(self, tmp_path):
        self._write_existing_config(tmp_path)
        # Create a real JS package.json so detection produces something
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}}),
            encoding="utf-8",
        )
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg._run_detection(str(tmp_path), force=True)
        loaded = load_verify_config(str(tmp_path))
        assert loaded is not None
        # After force-detect, the JS stack should be present
        assert "javascript" in loaded.stack


# ---------------------------------------------------------------------------
# 7. GitHub Actions CI detection: setup commands filtered out
# ---------------------------------------------------------------------------


class TestGitHubActionsCIDetection:
    def test_pytest_extracted_not_pip_install(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n"
            "      - run: |\n"
            "          pip install -e .\n"
            "          pytest tests/\n",
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        test_cmds = [c for c in config.commands if c.role == "test"]
        assert len(test_cmds) == 1
        assert test_cmds[0].cmd[0] == "pytest"

    def test_ci_command_source_is_ci_parsed(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n"
            "      - run: |\n"
            "          pip install -e .\n"
            "          pytest tests/\n",
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        test_cmd = next(c for c in config.commands if c.role == "test")
        assert test_cmd.source == "ci-parsed"

    def test_pip_install_not_in_commands(self, tmp_path):
        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text(
            "jobs:\n  test:\n    steps:\n"
            "      - run: |\n"
            "          pip install -e .\n"
            "          pytest tests/\n",
            encoding="utf-8",
        )
        config = detect_verify_config(str(tmp_path), dry_run=False)
        all_cmds_flat = [c.cmd[0] for c in config.commands]
        assert "pip" not in all_cmds_flat


# ---------------------------------------------------------------------------
# 8. Generic verification with mixed pass/fail
# ---------------------------------------------------------------------------


class TestGenericVerificationMixedResults:
    def _write_two_command_config(self, tmp_path: Path) -> None:
        cfg = VerifyConfig(
            version=1,
            commands=[
                VerifyCommand(role="lint", cmd=["eslint", "src/"], source="user"),
                VerifyCommand(role="test", cmd=["jest"], source="user"),
            ],
            detected_at="2026-04-05T00:00:00Z",
            stack=["javascript"],
        )
        save_verify_config(str(tmp_path), cfg)

    def test_overall_passed_is_false_when_test_fails(self, tmp_path):
        self._write_two_command_config(tmp_path)
        side_effects = [
            _make_subprocess_mock(returncode=0, stdout="lint ok"),
            _make_subprocess_mock(returncode=1, stdout="", stderr="1 test failed"),
        ]
        with patch("golem.verifier.subprocess.run", side_effect=side_effects):
            result = run_verification(str(tmp_path))
        assert result.passed is False

    def test_command_results_has_two_entries(self, tmp_path):
        self._write_two_command_config(tmp_path)
        side_effects = [
            _make_subprocess_mock(returncode=0, stdout="lint ok"),
            _make_subprocess_mock(returncode=1, stdout="", stderr="1 test failed"),
        ]
        with patch("golem.verifier.subprocess.run", side_effect=side_effects):
            result = run_verification(str(tmp_path))
        assert len(result.command_results) == 2

    def test_first_command_result_passed(self, tmp_path):
        self._write_two_command_config(tmp_path)
        side_effects = [
            _make_subprocess_mock(returncode=0, stdout="lint ok"),
            _make_subprocess_mock(returncode=1, stdout="", stderr="1 test failed"),
        ]
        with patch("golem.verifier.subprocess.run", side_effect=side_effects):
            result = run_verification(str(tmp_path))
        lint_result = next(cr for cr in result.command_results if cr["role"] == "lint")
        assert lint_result["passed"] is True

    def test_second_command_result_failed(self, tmp_path):
        self._write_two_command_config(tmp_path)
        side_effects = [
            _make_subprocess_mock(returncode=0, stdout="lint ok"),
            _make_subprocess_mock(returncode=1, stdout="", stderr="1 test failed"),
        ]
        with patch("golem.verifier.subprocess.run", side_effect=side_effects):
            result = run_verification(str(tmp_path))
        test_result = next(cr for cr in result.command_results if cr["role"] == "test")
        assert test_result["passed"] is False

    def test_legacy_black_ok_reflects_overall_failure(self, tmp_path):
        self._write_two_command_config(tmp_path)
        side_effects = [
            _make_subprocess_mock(returncode=0, stdout="lint ok"),
            _make_subprocess_mock(returncode=1, stdout="", stderr="1 test failed"),
        ]
        with patch("golem.verifier.subprocess.run", side_effect=side_effects):
            result = run_verification(str(tmp_path))
        assert result.black_ok is False

    def test_pytest_output_contains_failed_command_output(self, tmp_path):
        self._write_two_command_config(tmp_path)
        side_effects = [
            _make_subprocess_mock(returncode=0, stdout="lint ok"),
            _make_subprocess_mock(returncode=1, stdout="FAIL", stderr="1 test failed"),
        ]
        with patch("golem.verifier.subprocess.run", side_effect=side_effects):
            result = run_verification(str(tmp_path))
        # The failed command output should appear in pytest_output (legacy field)
        assert "test" in result.pytest_output
        assert "jest" in result.pytest_output


# ---------------------------------------------------------------------------
# 9. Full attach flow via RepoRegistry
# ---------------------------------------------------------------------------


class TestFullAttachFlow:
    def test_attach_with_detection_creates_verify_yaml(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}}),
            encoding="utf-8",
        )
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        # Use dry_run=False patch so tools don't need to be installed
        with patch("golem.repo_registry.detect_verify_config") as mock_detect:
            mock_detect.return_value = detect_verify_config(
                str(tmp_path), dry_run=False
            )
            with patch("golem.repo_registry.save_verify_config") as mock_save:
                reg.attach(str(tmp_path), run_detection=True)
        mock_save.assert_called_once()

    def test_attach_with_detection_verify_yaml_has_commands(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}}),
            encoding="utf-8",
        )
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        # Let detect_verify_config run for real (dry_run=False avoids PATH checks)
        with patch(
            "golem.repo_registry.detect_verify_config",
            side_effect=lambda path, **kw: detect_verify_config(path, dry_run=False),
        ):
            reg.attach(str(tmp_path), run_detection=True)

        loaded = load_verify_config(str(tmp_path))
        assert loaded is not None
        assert len(loaded.commands) > 0

    def test_attach_adds_repo_to_registry(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest"}}),
            encoding="utf-8",
        )
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        with (
            patch("golem.repo_registry.detect_verify_config") as mock_detect,
            patch("golem.repo_registry.save_verify_config"),
        ):
            mock_detect.return_value = VerifyConfig(
                version=1, commands=[], detected_at="", stack=[]
            )
            reg.attach(str(tmp_path), run_detection=True)

        repos = reg.list_repos()
        assert len(repos) == 1
        assert repos[0]["path"] == str(tmp_path)

    def test_attach_js_stack_detection_correct_roles(self, tmp_path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "lint": "eslint src/"}}),
            encoding="utf-8",
        )
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        with patch(
            "golem.repo_registry.detect_verify_config",
            side_effect=lambda path, **kw: detect_verify_config(path, dry_run=False),
        ):
            reg.attach(str(tmp_path), run_detection=True)

        loaded = load_verify_config(str(tmp_path))
        assert loaded is not None
        roles = {c.role for c in loaded.commands}
        assert "test" in roles
        assert "lint" in roles


# ---------------------------------------------------------------------------
# Parametrized: stack detection across language families
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker_content,marker_name,expected_stack_entry,expected_test_role_cmd_prefix",
    [
        (
            json.dumps({"scripts": {"test": "jest"}}),
            "package.json",
            "javascript",
            "npm",
        ),
        (
            '[package]\nname = "test"\nversion = "0.1.0"\n',
            "Cargo.toml",
            "rust",
            "cargo",
        ),
        (
            "module example.com/test\n\ngo 1.21\n",
            "go.mod",
            "go",
            "go",
        ),
    ],
    ids=["javascript", "rust", "go"],
)
def test_language_stack_detected(
    tmp_path,
    marker_content,
    marker_name,
    expected_stack_entry,
    expected_test_role_cmd_prefix,
):
    (tmp_path / marker_name).write_text(marker_content, encoding="utf-8")
    config = detect_verify_config(str(tmp_path), dry_run=False)
    assert expected_stack_entry in config.stack
    test_cmd = next((c for c in config.commands if c.role == "test"), None)
    assert test_cmd is not None
    assert test_cmd.cmd[0] == expected_test_role_cmd_prefix


# ---------------------------------------------------------------------------
# Parametrized: verify.yaml round-trip for different stacks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stack,commands",
    [
        (
            ["javascript"],
            [
                VerifyCommand(
                    role="test", cmd=["npm", "run", "test"], source="auto-detected"
                )
            ],
        ),
        (
            ["rust"],
            [
                VerifyCommand(
                    role="format",
                    cmd=["cargo", "fmt", "--check"],
                    source="auto-detected",
                ),
                VerifyCommand(
                    role="test", cmd=["cargo", "test"], source="auto-detected"
                ),
            ],
        ),
        (
            ["go"],
            [
                VerifyCommand(
                    role="test", cmd=["go", "test", "./..."], source="auto-detected"
                )
            ],
        ),
    ],
    ids=["javascript_round_trip", "rust_round_trip", "go_round_trip"],
)
def test_verify_config_round_trip(tmp_path, stack, commands):
    cfg = VerifyConfig(
        version=1,
        commands=commands,
        detected_at="2026-04-05T00:00:00Z",
        stack=stack,
    )
    save_verify_config(str(tmp_path), cfg)
    loaded = load_verify_config(str(tmp_path))
    assert loaded is not None
    assert loaded.stack == stack
    assert len(loaded.commands) == len(commands)
    for orig, roundtripped in zip(commands, loaded.commands):
        assert roundtripped.role == orig.role
        assert roundtripped.cmd == orig.cmd
        assert roundtripped.source == orig.source


# ---------------------------------------------------------------------------
# Empty verify.yaml (zero commands): fail closed
# ---------------------------------------------------------------------------


class TestEmptyVerifyYaml:
    def test_zero_commands_run_verification_fails(self, tmp_path):
        cfg = VerifyConfig(
            version=1, commands=[], detected_at="2026-04-05T00:00:00Z", stack=[]
        )
        save_verify_config(str(tmp_path), cfg)
        result = run_verification(str(tmp_path))
        assert result.passed is False

    def test_zero_commands_error_field_populated(self, tmp_path):
        cfg = VerifyConfig(
            version=1, commands=[], detected_at="2026-04-05T00:00:00Z", stack=[]
        )
        save_verify_config(str(tmp_path), cfg)
        result = run_verification(str(tmp_path))
        assert result.error != ""
