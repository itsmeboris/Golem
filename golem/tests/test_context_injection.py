# pylint: disable=too-few-public-methods
"""Tests for golem.context_injection — full coverage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from golem.context_injection import (
    _MAX_CONTEXT_BYTES,
    ContextBudget,
    _find_and_read,
    build_role_context_section,
    build_system_prompt,
    load_all_role_contexts,
    load_role_context,
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
    def test_returns_role_contexts_even_when_no_workspace_files(self, tmp_path):
        # Role context files exist on disk; prompt is returned even without
        # workspace-specific AGENTS.md / CLAUDE.md
        result = build_system_prompt(str(tmp_path))
        assert "# Workspace Context" in result
        assert "Role Contexts" in result

    def test_returns_empty_only_when_no_sections_at_all(self, tmp_path):
        # When role contexts are also absent the result is empty
        with patch(
            "golem.context_injection.build_role_context_section", return_value=""
        ):
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
        mock_cli, _ = _patches
        # Place AGENTS.md so context is non-empty
        (tmp_path / "AGENTS.md").write_text("# Guidelines\n- use TDD\n")

        config = _make_config(context_injection=True)
        sup = _make_supervisor(config=config)
        await sup.run()

        cli_config: CLIConfig = mock_cli.call_args[0][1]
        assert cli_config.system_prompt != ""
        assert "Workspace Context" in cli_config.system_prompt

    async def test_no_injection_when_disabled(self, _patches, tmp_path):
        mock_cli, _ = _patches
        (tmp_path / "AGENTS.md").write_text("# Guidelines\n")

        config = _make_config(context_injection=False)
        sup = _make_supervisor(config=config)
        await sup.run()

        cli_config: CLIConfig = mock_cli.call_args[0][1]
        assert cli_config.system_prompt == ""

    async def test_role_contexts_included_when_no_workspace_files(self, _patches):
        mock_cli, _ = _patches
        # tmp_path has no AGENTS.md or CLAUDE.md, but role context files exist

        config = _make_config(context_injection=True)
        sup = _make_supervisor(config=config)
        await sup.run()

        cli_config: CLIConfig = mock_cli.call_args[0][1]
        # Role context files always exist; prompt should include them
        assert "Workspace Context" in cli_config.system_prompt


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

        mock_cli, _ = _orch_patches
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

        mock_cli, _ = _orch_patches
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


# ---------------------------------------------------------------------------
# load_role_context
# ---------------------------------------------------------------------------


class TestLoadRoleContext:
    def test_valid_role_returns_content(self):
        content = load_role_context("builder")
        assert "Builder Mode Context" in content
        assert "TDD" in content

    def test_unknown_role_returns_empty(self):
        result = load_role_context("unknown_role")
        assert result == ""

    def test_missing_file_returns_empty(self, tmp_path):
        with patch("golem.context_injection._ROLE_CONTEXT_DIR", tmp_path):
            result = load_role_context("builder")
        assert result == ""

    def test_unreadable_file_returns_empty(self, tmp_path):
        fake_dir = tmp_path
        (fake_dir / "builder.md").write_text("some content")
        with patch("golem.context_injection._ROLE_CONTEXT_DIR", fake_dir):
            with patch.object(
                Path, "read_text", side_effect=OSError("permission denied")
            ):
                result = load_role_context("builder")
        assert result == ""

    @pytest.mark.parametrize(
        "role", sorted(["builder", "reviewer", "verifier", "explorer"])
    )
    def test_all_valid_roles_have_files(self, role):
        content = load_role_context(role)
        assert len(content) > 0, f"Role {role!r} returned empty content"

    def test_content_is_stripped(self, tmp_path):
        (tmp_path / "builder.md").write_text("  content with spaces  \n\n")
        with patch("golem.context_injection._ROLE_CONTEXT_DIR", tmp_path):
            result = load_role_context("builder")
        assert result == "content with spaces"


# ---------------------------------------------------------------------------
# load_all_role_contexts
# ---------------------------------------------------------------------------


class TestLoadAllRoleContexts:
    def test_returns_all_roles_with_files(self):
        result = load_all_role_contexts()
        assert set(result.keys()) == {"builder", "reviewer", "verifier", "explorer"}

    def test_missing_role_omitted(self):
        def fake_load(role):
            if role == "builder":
                return ""
            return f"content for {role}"

        with patch("golem.context_injection.load_role_context", side_effect=fake_load):
            result = load_all_role_contexts()
        assert "builder" not in result
        assert len(result) == 3
        assert result["reviewer"] == "content for reviewer"

    def test_empty_when_no_files(self):
        with patch("golem.context_injection.load_role_context", return_value=""):
            result = load_all_role_contexts()
        assert result == {}


# ---------------------------------------------------------------------------
# build_role_context_section
# ---------------------------------------------------------------------------


class TestBuildRoleContextSection:
    def test_returns_formatted_section(self):
        section = build_role_context_section()
        assert "## Role-Specific Contexts" in section
        assert "prepend the matching context block" in section
        assert "Builder Mode Context" in section

    def test_returns_empty_when_no_contexts(self):
        with patch("golem.context_injection.load_all_role_contexts", return_value={}):
            result = build_role_context_section()
        assert result == ""

    def test_contains_all_role_headings(self):
        section = build_role_context_section()
        assert "### Builder Context" in section
        assert "### Reviewer Context" in section
        assert "### Verifier Context" in section
        assert "### Explorer Context" in section


# ---------------------------------------------------------------------------
# ContextBudget
# ---------------------------------------------------------------------------


class TestContextBudgetEstimateTokens:
    def test_empty_string_returns_zero(self):
        budget = ContextBudget(max_tokens=8000)
        assert budget.estimate_tokens("") == 0

    def test_four_chars_equals_one_token(self):
        budget = ContextBudget(max_tokens=8000)
        # 4 chars → 1 token (integer division)
        assert budget.estimate_tokens("abcd") == 1

    def test_large_text_proportional(self):
        budget = ContextBudget(max_tokens=8000)
        text = "a" * 4000
        assert budget.estimate_tokens(text) == 1000

    def test_odd_length_rounds_down(self):
        budget = ContextBudget(max_tokens=8000)
        # 7 chars → 7 // 4 = 1
        assert budget.estimate_tokens("abcdefg") == 1

    @pytest.mark.parametrize(
        "text,expected_tokens",
        [
            ("", 0),
            ("abcd", 1),
            ("a" * 400, 100),
            ("a" * 401, 100),  # floor division
            ("a" * 8000, 2000),
        ],
        ids=["empty", "four_chars", "400_chars", "401_chars_floor", "8000_chars"],
    )
    def test_parametrized_estimates(self, text, expected_tokens):
        budget = ContextBudget(max_tokens=8000)
        assert budget.estimate_tokens(text) == expected_tokens


class TestContextBudgetFitSections:
    def test_empty_sections_returns_empty_string(self):
        budget = ContextBudget(max_tokens=8000)
        result = budget.fit_sections([])
        assert result == ""

    def test_single_section_fits_entirely(self):
        budget = ContextBudget(max_tokens=8000)
        content = "Hello world rules."
        sections = [(1, "CLAUDE.md", content)]
        result = budget.fit_sections(sections)
        assert "## CLAUDE.md" in result
        assert content in result
        assert "(truncated)" not in result

    def test_two_sections_both_fit(self):
        budget = ContextBudget(max_tokens=8000)
        sections = [
            (1, "CLAUDE.md", "Claude content."),
            (2, "AGENTS.md", "Agents content."),
        ]
        result = budget.fit_sections(sections)
        assert "## CLAUDE.md" in result
        assert "## AGENTS.md" in result
        assert "---" in result

    def test_priority_ordering_lower_is_more_important(self):
        budget = ContextBudget(max_tokens=8000)
        # Priority 1 should appear before priority 2 in output
        sections = [
            (2, "AGENTS.md", "Agents content."),
            (1, "CLAUDE.md", "Claude content."),
        ]
        result = budget.fit_sections(sections)
        assert result.index("CLAUDE.md") < result.index("AGENTS.md")

    def test_oversized_section_is_truncated(self):
        # Budget of 200 tokens: remaining > 100 threshold, but section needs ~2500 tokens
        budget = ContextBudget(max_tokens=200)
        big_content = "word\n" * 2000  # ~10000 chars = ~2500 tokens
        sections = [(1, "BIG", big_content)]
        result = budget.fit_sections(sections)
        assert "truncated" in result
        # Result should be much smaller than the original 2500-token content
        assert budget.estimate_tokens(result) < 500

    def test_truncation_prefers_newline_boundary(self):
        # Budget: 200 tokens; content has many lines so truncation hits a newline
        budget = ContextBudget(max_tokens=200)
        # 150 lines of 10 chars each → ~1500 chars = ~375 tokens (exceeds budget)
        content = "\n".join(f"line {i:04d}" for i in range(150))
        sections = [(1, "SEC", content)]
        result = budget.fit_sections(sections)
        # Should be truncated and the result ends at a newline boundary
        assert "(truncated)" in result
        # The "...(truncated to fit context budget)" marker should appear after a newline
        lines_before_marker = result.split("...(truncated")[0]
        assert lines_before_marker.endswith("\n")

    def test_low_remaining_budget_skips_truncation(self):
        # If less than 100 tokens remain, don't include a truncated fragment
        budget = ContextBudget(max_tokens=1)  # essentially nothing
        big_content = "A" * 10000
        sections = [(1, "SEC", big_content)]
        result = budget.fit_sections(sections)
        # With 1 token budget, section header itself exceeds budget,
        # and remaining_tokens <= 100 so no truncated fragment is added
        assert result == ""

    def test_second_section_skipped_when_no_budget_left(self):
        budget = ContextBudget(max_tokens=20)
        # First section consumes all budget
        first = "A" * (20 * 4)  # exactly 20 tokens
        second = "B content."
        sections = [(1, "FIRST", first), (2, "SECOND", second)]
        result = budget.fit_sections(sections)
        assert "SECOND" not in result

    def test_sections_joined_with_separator(self):
        budget = ContextBudget(max_tokens=8000)
        sections = [
            (1, "SEC1", "content one"),
            (2, "SEC2", "content two"),
        ]
        result = budget.fit_sections(sections)
        assert "\n\n---\n\n" in result

    def test_default_max_tokens_is_8000(self):
        budget = ContextBudget()
        assert budget.max_tokens == 8000


class TestBuildSystemPromptWithBudget:
    def test_max_tokens_parameter_accepted(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Rules\n- rule one\n")
        result = build_system_prompt(str(tmp_path), max_tokens=8000)
        assert "# Workspace Context" in result
        assert "rule one" in result

    def test_default_max_tokens_still_works(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("# Guidelines\n- use TDD\n")
        result = build_system_prompt(str(tmp_path))
        assert "# Workspace Context" in result

    def test_tiny_budget_truncates_content(self, tmp_path):
        big_content = "important rule\n" * 500
        (tmp_path / "CLAUDE.md").write_text(big_content)
        result = build_system_prompt(str(tmp_path), max_tokens=50)
        # With tiny budget, should still produce a valid prompt or truncate
        assert "Workspace Context" in result or result == ""

    def test_priority_order_claude_before_agents(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("Claude rules here")
        (tmp_path / "AGENTS.md").write_text("Agents rules here")
        result = build_system_prompt(str(tmp_path))
        # CLAUDE.md has priority 1, AGENTS.md has priority 2
        assert result.index("CLAUDE.md") < result.index("AGENTS.md")

    def test_returns_empty_when_no_sections_at_all(self, tmp_path):
        with patch(
            "golem.context_injection.build_role_context_section", return_value=""
        ):
            result = build_system_prompt(str(tmp_path), max_tokens=8000)
        assert result == ""


class TestContextBudgetConfigField:
    def test_golem_flow_config_has_context_budget_tokens(self):
        config = GolemFlowConfig()
        assert config.context_budget_tokens == 8000

    def test_config_field_can_be_set(self):
        config = GolemFlowConfig(context_budget_tokens=4000)
        assert config.context_budget_tokens == 4000

    def test_parse_golem_config_reads_context_budget_tokens(self):
        from golem.core.config import _parse_golem_config

        cfg = _parse_golem_config({"context_budget_tokens": 16000})
        assert cfg.context_budget_tokens == 16000

    def test_parse_golem_config_default_is_8000(self):
        from golem.core.config import _parse_golem_config

        cfg = _parse_golem_config({})
        assert cfg.context_budget_tokens == 8000


# ---------------------------------------------------------------------------
# Integration: supervisor role context injection via _build_prompt
# ---------------------------------------------------------------------------


class TestSupervisorRoleContextInjection:
    """Verify _build_prompt includes role_contexts when context_injection=True."""

    def _make_supervisor_with_template(self, context_injection: bool):
        """Return a supervisor whose prompt_provider echoes template variables."""
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "description"

        # Capture kwargs so tests can inspect what was passed to format()
        captured = {}

        def fake_format(_name, **kwargs):
            captured.update(kwargs)
            # Return a simple string that includes the role_contexts value
            return kwargs.get("role_contexts", "")

        profile.prompt_provider.format.side_effect = fake_format
        profile.tool_provider.servers_for_subject.return_value = []
        profile.state_backend = MagicMock()
        profile.notifier = MagicMock()

        config = _make_config(context_injection=context_injection)
        sup = _make_supervisor(config=config, profile=profile)
        return sup, captured

    def test_build_prompt_includes_role_contexts_when_enabled(self, tmp_path):
        sup, captured = self._make_supervisor_with_template(context_injection=True)
        sup._build_prompt(issue_id=1, description="test task", work_dir=str(tmp_path))
        role_contexts = captured.get("role_contexts", "MISSING")
        assert "Role-Specific Contexts" in role_contexts

    def test_build_prompt_no_role_contexts_when_disabled(self, tmp_path):
        sup, captured = self._make_supervisor_with_template(context_injection=False)
        sup._build_prompt(issue_id=1, description="test task", work_dir=str(tmp_path))
        role_contexts = captured.get("role_contexts", "MISSING")
        assert role_contexts == ""
