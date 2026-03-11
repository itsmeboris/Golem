# pylint: disable=too-few-public-methods
"""Tests for golem.context_injection — full coverage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from golem.context_injection import (
    _MAX_CONTEXT_BYTES,
    _find_and_read,
    build_system_prompt,
    load_workspace_context,
    write_back_discoveries,
)
from golem.core.cli_wrapper import CLIConfig, CLIResult
from golem.core.config import GolemFlowConfig
from golem.orchestrator import TaskSession
from golem.supervisor_v2_subagent import SubagentSupervisor


# ---------------------------------------------------------------------------
# load_workspace_context
# ---------------------------------------------------------------------------


class TestLoadWorkspaceContext:
    def test_no_files_returns_empty(self, tmp_path):
        result = load_workspace_context(str(tmp_path))
        assert result == ""

    def test_only_agents_md(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Guidelines\n- rule one\n")
        result = load_workspace_context(str(tmp_path))
        assert "## AGENTS.md" in result
        assert "rule one" in result
        assert "CLAUDE.md" not in result

    def test_only_claude_md(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Claude config\n- tip one\n")
        result = load_workspace_context(str(tmp_path))
        assert "## CLAUDE.md" in result
        assert "tip one" in result
        assert "AGENTS.md" not in result

    def test_both_files_concatenated(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("agents content")
        (tmp_path / "CLAUDE.md").write_text("claude content")
        result = load_workspace_context(str(tmp_path))
        assert "## AGENTS.md" in result
        assert "agents content" in result
        assert "## CLAUDE.md" in result
        assert "claude content" in result
        # separator between sections
        assert "---" in result

    def test_both_files_agents_before_claude(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("agents content")
        (tmp_path / "CLAUDE.md").write_text("claude content")
        result = load_workspace_context(str(tmp_path))
        assert result.index("AGENTS.md") < result.index("CLAUDE.md")

    def test_content_is_stripped(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("  trimmed  \n\n\n")
        result = load_workspace_context(str(tmp_path))
        assert result.endswith("trimmed")


# ---------------------------------------------------------------------------
# _find_and_read
# ---------------------------------------------------------------------------


class TestFindAndRead:
    def test_missing_file_returns_empty(self, tmp_path):
        result = _find_and_read(tmp_path, "MISSING.md")
        assert result == ""

    def test_existing_file_returns_content(self, tmp_path):
        (tmp_path / "FILE.md").write_text("hello")
        result = _find_and_read(tmp_path, "FILE.md")
        assert result == "hello"

    def test_unreadable_file_returns_empty(self, tmp_path):
        p = tmp_path / "AGENTS.md"
        p.write_text("content")
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = _find_and_read(tmp_path, "AGENTS.md")
        assert result == ""

    def test_oversized_file_returns_empty(self, tmp_path):
        p = tmp_path / "AGENTS.md"
        p.write_text("x")
        # Write a file that is one byte over the cap by patching stat
        real_stat = p.stat()
        oversized = MagicMock(wraps=real_stat)
        oversized.st_size = _MAX_CONTEXT_BYTES + 1
        with patch.object(p.__class__, "stat", return_value=oversized):
            # is_file() also calls stat(); restore it to avoid breaking the check
            with patch.object(p.__class__, "is_file", return_value=True):
                result = _find_and_read(tmp_path, "AGENTS.md")
        assert result == ""

    def test_exactly_at_limit_is_read(self, tmp_path):
        p = tmp_path / "AGENTS.md"
        p.write_text("x")
        real_stat = p.stat()
        at_limit = MagicMock(wraps=real_stat)
        at_limit.st_size = _MAX_CONTEXT_BYTES
        with patch.object(p.__class__, "stat", return_value=at_limit):
            with patch.object(p.__class__, "is_file", return_value=True):
                result = _find_and_read(tmp_path, "AGENTS.md")
        assert result == "x"


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_returns_empty_when_no_files(self, tmp_path):
        result = build_system_prompt(str(tmp_path))
        assert result == ""

    def test_returns_formatted_prompt_with_context(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Guidelines\n- follow TDD\n")
        result = build_system_prompt(str(tmp_path))
        assert "# Workspace Context" in result
        assert "Guidelines" in result
        assert "follow TDD" in result
        assert "# Discovery Write-Back" in result
        assert "AGENTS.md" in result

    def test_prompt_includes_discovery_instructions(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("some content")
        result = build_system_prompt(str(tmp_path))
        assert "append them to AGENTS.md" in result


# ---------------------------------------------------------------------------
# write_back_discoveries
# ---------------------------------------------------------------------------


class TestWriteBackDiscoveries:
    def test_empty_list_returns_false(self, tmp_path):
        result = write_back_discoveries(str(tmp_path), [])
        assert result is False

    def test_creates_new_agents_md(self, tmp_path):
        result = write_back_discoveries(str(tmp_path), ["Found X pattern"])
        assert result is True
        agents_path = tmp_path / "AGENTS.md"
        assert agents_path.exists()
        content = agents_path.read_text()
        assert "# Agent Guidelines" in content
        assert "Found X pattern" in content
        assert "## Discoveries" in content

    def test_appends_to_existing_agents_md(self, tmp_path):
        agents_path = tmp_path / "AGENTS.md"
        agents_path.write_text("# Existing\n\nOld content.\n")
        result = write_back_discoveries(str(tmp_path), ["New discovery"])
        assert result is True
        content = agents_path.read_text()
        assert "Old content." in content
        assert "New discovery" in content

    def test_discoveries_formatted_as_bullets(self, tmp_path):
        write_back_discoveries(str(tmp_path), ["first", "second"])
        content = (tmp_path / "AGENTS.md").read_text()
        assert "- first" in content
        assert "- second" in content

    def test_blank_discoveries_skipped(self, tmp_path):
        write_back_discoveries(str(tmp_path), ["   ", "real discovery", ""])
        content = (tmp_path / "AGENTS.md").read_text()
        assert "real discovery" in content
        # blank entries should not produce a bare bullet
        assert "- \n" not in content
        assert "-    \n" not in content

    def test_timestamp_in_section_header(self, tmp_path):
        write_back_discoveries(str(tmp_path), ["discovery"])
        content = (tmp_path / "AGENTS.md").read_text()
        assert "## Discoveries (" in content

    def test_unwritable_path_returns_false(self, tmp_path):
        with patch(
            "golem.context_injection.Path.write_text", side_effect=OSError("no")
        ):
            result = write_back_discoveries(str(tmp_path), ["x"])
        assert result is False

    def test_all_blank_discoveries_returns_false(self, tmp_path):
        # Non-empty list but all blanks: treated same as empty list
        result = write_back_discoveries(str(tmp_path), ["   ", "  "])
        assert result is False


# ---------------------------------------------------------------------------
# Integration: SubagentSupervisor context injection
# ---------------------------------------------------------------------------


def _make_profile():
    profile = MagicMock()
    profile.task_source.get_task_description.return_value = "description"
    profile.prompt_provider.format.return_value = "prompt text"
    profile.tool_provider.servers_for_subject.return_value = []
    profile.state_backend = MagicMock()
    profile.notifier = MagicMock()
    return profile


def _make_config(**overrides):
    defaults = {
        "enabled": True,
        "task_model": "sonnet",
        "supervisor_mode": True,
        "use_worktrees": False,
        "auto_commit": True,
        "max_retries": 1,
        "default_work_dir": "/tmp/test",
    }
    defaults.update(overrides)
    return GolemFlowConfig(**defaults)


def _make_supervisor(session=None, config=None, profile=None, **kwargs):
    if session is None:
        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
    if config is None:
        config = _make_config()
    if profile is None:
        profile = _make_profile()
    return SubagentSupervisor(
        session=session,
        config=MagicMock(),
        task_config=config,
        profile=profile,
        **kwargs,
    )


class TestSupervisorContextInjection:
    """Verify CLIConfig.system_prompt is set when context_injection=True."""

    @pytest.fixture()
    def _patches(self, tmp_path):
        from golem.committer import CommitResult
        from golem.validation import ValidationVerdict

        with (
            patch("golem.supervisor_v2_subagent.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor_v2_subagent.run_validation") as mock_val,
            patch("golem.supervisor_v2_subagent.commit_changes") as mock_commit,
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch("golem.supervisor_v2_subagent._StreamingTraceWriter"),
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value=str(tmp_path),
            ),
            patch("golem.supervisor_v2_subagent.create_worktree"),
            patch("golem.supervisor_v2_subagent.cleanup_worktree"),
            patch(
                "golem.supervisor_v2_subagent.run_verification",
                return_value=MagicMock(passed=True, duration_s=0.1),
            ),
        ):
            mock_cli.return_value = CLIResult(
                output={"result": '{"status": "COMPLETE", "summary": "done"}'},
                cost_usd=0.1,
                trace_events=[],
                session_id="sess-1",
            )
            mock_val.return_value = ValidationVerdict(
                verdict="PASS",
                confidence=0.95,
                summary="ok",
                task_type="feature",
            )
            mock_commit.return_value = CommitResult(committed=True, sha="abc123")
            yield mock_cli, str(tmp_path)

    async def test_injects_context_when_enabled(self, _patches, tmp_path):
        mock_cli, work_dir = _patches
        # Place AGENTS.md so context is non-empty
        (tmp_path / "AGENTS.md").write_text("# Guidelines\n- use TDD\n")

        config = _make_config(context_injection=True)
        sup = _make_supervisor(config=config)
        await sup.run()

        cli_config: CLIConfig = mock_cli.call_args[0][1]
        assert cli_config.system_prompt != ""
        assert "Workspace Context" in cli_config.system_prompt

    async def test_no_injection_when_disabled(self, _patches, tmp_path):
        mock_cli, work_dir = _patches
        (tmp_path / "AGENTS.md").write_text("# Guidelines\n")

        config = _make_config(context_injection=False)
        sup = _make_supervisor(config=config)
        await sup.run()

        cli_config: CLIConfig = mock_cli.call_args[0][1]
        assert cli_config.system_prompt == ""

    async def test_empty_system_prompt_when_no_context_files(self, _patches):
        mock_cli, work_dir = _patches
        # tmp_path has no AGENTS.md or CLAUDE.md

        config = _make_config(context_injection=True)
        sup = _make_supervisor(config=config)
        await sup.run()

        cli_config: CLIConfig = mock_cli.call_args[0][1]
        assert cli_config.system_prompt == ""


# ---------------------------------------------------------------------------
# Integration: monolithic orchestrator context injection
# ---------------------------------------------------------------------------


class TestMonolithicOrchestratorContextInjection:
    """Verify _invoke_agent passes system_prompt via CLIConfig."""

    @pytest.fixture()
    def _orch_patches(self, tmp_path):
        from golem.committer import CommitResult
        from golem.validation import ValidationVerdict
        from golem.verifier import VerificationResult

        with (
            patch("golem.orchestrator.invoke_cli_monitored") as mock_cli,
            patch("golem.orchestrator.run_validation") as mock_val,
            patch("golem.orchestrator.commit_changes") as mock_commit,
            patch("golem.orchestrator._write_prompt"),
            patch("golem.orchestrator._StreamingTraceWriter"),
            patch(
                "golem.orchestrator.resolve_work_dir",
                return_value=str(tmp_path),
            ),
            patch("golem.orchestrator.create_worktree"),
            patch("golem.orchestrator.cleanup_worktree"),
            patch("golem.orchestrator.save_checkpoint"),
            patch("golem.orchestrator.delete_checkpoint"),
            patch(
                "golem.orchestrator.run_verification",
                return_value=VerificationResult(
                    passed=True,
                    black_ok=True,
                    pylint_ok=True,
                    pytest_ok=True,
                    black_output="",
                    pylint_output="",
                    pytest_output="",
                    duration_s=0.1,
                ),
            ),
            patch("golem.orchestrator.TaskOrchestrator._preflight_check"),
        ):
            mock_cli.return_value = CLIResult(
                output={"result": '{"status": "COMPLETE", "summary": "done"}'},
                cost_usd=0.1,
                trace_events=[],
                session_id="sess-2",
            )
            mock_val.return_value = ValidationVerdict(
                verdict="PASS",
                confidence=0.95,
                summary="ok",
                task_type="feature",
            )
            mock_commit.return_value = CommitResult(committed=True, sha="def456")
            yield mock_cli, str(tmp_path)

    async def test_monolithic_injects_context_when_enabled(
        self, _orch_patches, tmp_path
    ):
        from golem.orchestrator import TaskOrchestrator

        mock_cli, work_dir = _orch_patches
        (tmp_path / "AGENTS.md").write_text("# Monolithic Guidelines\n- pattern A\n")

        config = _make_config(context_injection=True, supervisor_mode=False)
        profile = _make_profile()
        session = TaskSession(
            parent_issue_id=99,
            parent_subject="Monolithic task",
            budget_usd=5.0,
        )

        orch = TaskOrchestrator(
            session=session,
            config=MagicMock(),
            task_config=config,
            profile=profile,
        )
        await orch._run_agent_monolithic()

        cli_config: CLIConfig = mock_cli.call_args[0][1]
        assert cli_config.system_prompt != ""
        assert "Workspace Context" in cli_config.system_prompt

    async def test_monolithic_skips_injection_when_disabled(
        self, _orch_patches, tmp_path
    ):
        from golem.orchestrator import TaskOrchestrator

        mock_cli, work_dir = _orch_patches
        (tmp_path / "AGENTS.md").write_text("# Guidelines\n")

        config = _make_config(context_injection=False, supervisor_mode=False)
        profile = _make_profile()
        session = TaskSession(
            parent_issue_id=99,
            parent_subject="Monolithic task",
            budget_usd=5.0,
        )

        orch = TaskOrchestrator(
            session=session,
            config=MagicMock(),
            task_config=config,
            profile=profile,
        )
        await orch._run_agent_monolithic()

        cli_config: CLIConfig = mock_cli.call_args[0][1]
        assert cli_config.system_prompt == ""
