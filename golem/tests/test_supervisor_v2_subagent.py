# pylint: disable=too-few-public-methods,too-many-lines
"""Tests for golem.supervisor_v2_subagent — full coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.committer import CommitResult
from golem.core.cli_wrapper import CLIResult
from golem.core.config import GolemFlowConfig
from golem.orchestrator import TaskSession, TaskSessionState
from golem.supervisor_v2_subagent import SubagentSupervisor
from golem.validation import ValidationVerdict


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


def _make_cli_result(cost=0.1, output_result="done", trace_events=None, session_id=""):
    return CLIResult(
        output={"result": output_result},
        cost_usd=cost,
        trace_events=trace_events or [],
        session_id=session_id,
    )


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


# -- Prompt building --------------------------------------------------------


class TestBuildPrompt:
    def test_delegates_to_profile(self):
        profile = _make_profile()
        config = _make_config(enable_simplify_pass=True)
        sup = _make_supervisor(profile=profile, config=config)
        result = sup._build_prompt(42, "desc", "/work")
        call_kwargs = profile.prompt_provider.format.call_args[1]
        assert profile.prompt_provider.format.call_args[0][0] == "orchestrate_task.txt"
        assert call_kwargs["issue_id"] == 42
        assert call_kwargs["parent_subject"] == "Test task"
        assert call_kwargs["task_description"] == "desc"
        assert call_kwargs["work_dir"] == "/work"
        assert call_kwargs["inner_retry_max"] == 3
        assert call_kwargs["validator_fix_depth"] == 3
        assert isinstance(call_kwargs["simplify_section"], str)
        assert call_kwargs["simplify_section"] != ""
        assert result == "prompt text"

    def test_build_prompt_disables_simplify_when_config_false(self):
        profile = _make_profile()
        config = _make_config(enable_simplify_pass=False)
        sup = _make_supervisor(profile=profile, config=config)
        sup._build_prompt(42, "desc", "/work")
        call_kwargs = profile.prompt_provider.format.call_args[1]
        assert call_kwargs["simplify_section"] == ""

    def test_build_prompt_simplify_section_contains_skill_reference(self):
        profile = _make_profile()
        config = _make_config(enable_simplify_pass=True)
        sup = _make_supervisor(profile=profile, config=config)
        sup._build_prompt(42, "desc", "/work")
        call_kwargs = profile.prompt_provider.format.call_args[1]
        simplify_section = call_kwargs["simplify_section"]
        assert "simplify" in simplify_section
        assert "Phase 3.5" in simplify_section
        assert "SIMPLIFY" in simplify_section
        assert "builder" in simplify_section


class TestBuildRetryPrompt:
    """Tests for enriched retry prompts."""

    def test_warm_prompt_includes_iteration_context(self):
        session = TaskSession(
            parent_issue_id=42, parent_subject="Test", cli_session_id="sess-1"
        )
        config = _make_config(resume_on_partial=True)
        sup = _make_supervisor(session=session, config=config)

        verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="missing tests",
            concerns=["no unit tests"],
            files_to_fix=["foo.py"],
            test_failures=["test_bar"],
        )

        prompt, sid = sup._build_retry_prompt(
            True, verdict, "- no unit tests", 42, fix_iteration=2, fix_depth=3
        )

        assert "Fix iteration**: 2/3" in prompt
        assert "1 attempt(s) remaining" in prompt
        assert "- foo.py" in prompt
        assert "- test_bar" in prompt
        assert sid == "sess-1"

    def test_warm_prompt_without_iteration(self):
        session = TaskSession(
            parent_issue_id=42, parent_subject="Test", cli_session_id="sess-1"
        )
        sup = _make_supervisor(session=session)

        verdict = ValidationVerdict(
            verdict="PARTIAL", confidence=0.5, summary="partial", concerns=["c"]
        )

        prompt, _ = sup._build_retry_prompt(True, verdict, "- c", 42)

        assert "Fix iteration" not in prompt
        assert "Verdict**: PARTIAL" in prompt

    def test_cold_prompt_passes_all_template_vars(self):
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            verification_result={"passed": False, "stdout": "FAILED test_x"},
        )
        profile = _make_profile()
        sup = _make_supervisor(session=session, profile=profile)

        verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="partial",
            concerns=["c"],
            files_to_fix=["a.py"],
            test_failures=["test_x"],
        )

        sup._build_retry_prompt(False, verdict, "- c", 42, fix_iteration=1, fix_depth=3)

        call_kwargs = profile.prompt_provider.format.call_args[1]
        assert call_kwargs["fix_iteration"] == 1
        assert call_kwargs["fix_depth"] == 3
        assert "- a.py" in call_kwargs["files_to_fix"]
        assert "- test_x" in call_kwargs["test_failures"]
        assert "FAILED test_x" in call_kwargs["verification_feedback"]


class TestVerificationFeedback:
    """Tests for _verification_feedback helper."""

    def test_no_verification_result(self):
        session = TaskSession(parent_issue_id=1, parent_subject="t")
        sup = _make_supervisor(session=session)
        assert sup._verification_feedback() == "(no verification failures)"

    def test_passed_verification(self):
        session = TaskSession(
            parent_issue_id=1,
            parent_subject="t",
            verification_result={"passed": True},
        )
        sup = _make_supervisor(session=session)
        assert sup._verification_feedback() == "(verification passed)"

    def test_failed_verification_with_output(self):
        session = TaskSession(
            parent_issue_id=1,
            parent_subject="t",
            verification_result={
                "passed": False,
                "stdout": "FAIL test_foo",
                "stderr": "error details",
            },
        )
        sup = _make_supervisor(session=session)
        result = sup._verification_feedback()
        assert "FAIL test_foo" in result
        assert "error details" in result


# -- Report parsing ---------------------------------------------------------


class TestParseReport:
    def test_valid_json(self):
        result = _make_cli_result(
            output_result='```json\n{"status": "COMPLETE", "summary": "done"}\n```'
        )
        report = SubagentSupervisor._parse_report(result)
        assert report["status"] == "COMPLETE"
        assert report["summary"] == "done"

    def test_malformed_output(self):
        result = _make_cli_result(output_result="not json at all")
        report = SubagentSupervisor._parse_report(result)
        assert report["status"] == "UNKNOWN"
        assert "not json" in report["summary"]

    def test_empty_output(self):
        result = _make_cli_result(output_result="")
        report = SubagentSupervisor._parse_report(result)
        assert report["status"] == "UNKNOWN"

    def test_blocked_status(self):
        result = _make_cli_result(
            output_result='{"status": "BLOCKED", "summary": "stuck", "concerns": ["err"]}'
        )
        report = SubagentSupervisor._parse_report(result)
        assert report["status"] == "BLOCKED"
        assert report["concerns"] == ["err"]

    def test_json_without_status_key(self):
        result = _make_cli_result(output_result='{"summary": "no status field"}')
        report = SubagentSupervisor._parse_report(result)
        assert report["status"] == "UNKNOWN"


# -- Full pipeline ----------------------------------------------------------


class TestRunPipeline:
    @pytest.fixture()
    def _patches(self):
        with (
            patch("golem.supervisor_v2_subagent.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor_v2_subagent.run_validation") as mock_val,
            patch("golem.supervisor_v2_subagent.commit_changes") as mock_commit,
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch("golem.supervisor_v2_subagent._StreamingTraceWriter"),
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value="/tmp/test",
            ),
            patch("golem.supervisor_v2_subagent.create_worktree"),
            patch("golem.supervisor_v2_subagent.cleanup_worktree"),
            patch(
                "golem.supervisor_v2_subagent.run_verification",
                return_value=MagicMock(passed=True, duration_s=0.1),
            ),
        ):
            mock_cli.return_value = _make_cli_result(
                output_result='{"status": "COMPLETE", "summary": "done"}',
                session_id="sess-123",
            )
            mock_val.return_value = ValidationVerdict(
                verdict="PASS",
                confidence=0.95,
                summary="ok",
                task_type="feature",
            )
            mock_commit.return_value = CommitResult(committed=True, sha="abc123")
            yield {
                "cli": mock_cli,
                "val": mock_val,
                "commit": mock_commit,
            }

    async def test_full_pipeline_pass(self, _patches):
        """End-to-end: invoke → parse → validate(PASS) → commit."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        sup = _make_supervisor(session=session)

        await sup.run()

        assert session.execution_mode == "subagent"
        assert session.state == TaskSessionState.COMPLETED
        assert session.commit_sha == "abc123"
        _patches["cli"].assert_called_once()
        _patches["val"].assert_called_once()

    async def test_full_pipeline_partial_retry(self, _patches):
        """invoke → validate(PARTIAL) → fix loop → validate(PASS)."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(
            max_retries=1, resume_on_partial=True, validator_fix_depth=1
        )
        sup = _make_supervisor(session=session, config=config)

        _patches["val"].side_effect = [
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.5,
                summary="partial",
                concerns=["issue1"],
            ),
            ValidationVerdict(
                verdict="PASS",
                confidence=0.9,
                summary="ok",
                task_type="feature",
            ),
        ]

        await sup.run()

        assert session.fix_iteration == 1
        assert session.state == TaskSessionState.COMPLETED
        # Two CLI calls: orchestration + fix loop iteration
        assert _patches["cli"].call_count == 2

    async def test_full_pipeline_fail(self, _patches):
        """invoke → validate(FAIL) → escalate."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(max_retries=0)
        sup = _make_supervisor(session=session, config=config)

        _patches["val"].return_value = ValidationVerdict(
            verdict="FAIL",
            confidence=0.1,
            summary="bad",
            concerns=["broken"],
        )

        await sup.run()

        assert session.state == TaskSessionState.FAILED

    async def test_execution_mode_set(self, _patches):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        await sup.run()
        assert session.execution_mode == "subagent"

    async def test_session_id_captured(self, _patches):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        await sup.run()
        assert session.cli_session_id == "sess-123"

    async def test_work_dir_override(self, _patches):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, work_dir_override="/custom/dir")
        await sup.run()
        assert sup._base_work_dir == "/custom/dir"

    async def test_worktree_creation(self, _patches):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(use_worktrees=True)
        sup = _make_supervisor(session=session, config=config)

        with patch(
            "golem.supervisor_v2_subagent.create_worktree",
            return_value="/wt/42",
        ) as mock_wt:
            await sup.run()
            mock_wt.assert_called_once()

    async def test_worktree_creation_failure(self, _patches):
        """Worktree failure raises InfrastructureError — never falls back to shared dir."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(use_worktrees=True)
        sup = _make_supervisor(session=session, config=config)

        with patch(
            "golem.supervisor_v2_subagent.create_worktree",
            side_effect=RuntimeError("branch already exists"),
        ):
            await sup.run()

        assert session.state == TaskSessionState.FAILED
        assert any("Worktree creation failed" in e for e in session.errors)

    async def test_exception_sets_failed(self, _patches):
        profile = _make_profile()
        profile.task_source.get_task_description.side_effect = RuntimeError("boom")
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, profile=profile)

        await sup.run()

        assert session.state == TaskSessionState.FAILED
        assert "boom" in session.errors[0]

    async def test_failed_worktree_cleanup_keeps_branch(self, _patches):
        """Error after worktree creation → cleanup with keep_branch=True."""
        # Make the CLI call fail (after worktree is already created)
        _patches["cli"].side_effect = RuntimeError("boom")
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(use_worktrees=True)
        sup = _make_supervisor(
            session=session,
            config=config,
            work_dir_override="/repo",
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.create_worktree",
                return_value="/wt/42",
            ),
            patch("golem.supervisor_v2_subagent.cleanup_worktree") as mock_cleanup,
        ):
            await sup.run()
            mock_cleanup.assert_called_once_with("/repo", "/wt/42", keep_branch=True)

    async def test_invoke_orchestrator_single_call(self, _patches):
        """Verify only a single CLI call is made for orchestration."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        await sup.run()
        _patches["cli"].assert_called_once()

    async def test_uses_orchestrate_model(self, _patches):
        """Orchestrate model overrides task_model."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(orchestrate_model="opus")
        sup = _make_supervisor(session=session, config=config)
        await sup.run()
        called_config = _patches["cli"].call_args[0][1]
        assert called_config.model == "opus"

    async def test_falls_back_to_task_model(self, _patches):
        """Empty orchestrate_model falls back to task_model."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(orchestrate_model="")
        sup = _make_supervisor(session=session, config=config)
        await sup.run()
        called_config = _patches["cli"].call_args[0][1]
        assert called_config.model == "sonnet"


# -- Retry with resume -------------------------------------------------------


class TestRetryWithResume:
    @pytest.fixture()
    def _patches(self):
        with (
            patch("golem.supervisor_v2_subagent.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor_v2_subagent.run_validation") as mock_val,
            patch("golem.supervisor_v2_subagent.commit_changes") as mock_commit,
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch("golem.supervisor_v2_subagent._StreamingTraceWriter"),
        ):
            mock_cli.return_value = _make_cli_result(cost=0.2, session_id="sess-456")
            mock_val.return_value = ValidationVerdict(
                verdict="PASS",
                confidence=0.9,
                summary="ok",
                task_type="feature",
            )
            mock_commit.return_value = CommitResult(committed=True, sha="retry_sha")
            yield {
                "cli": mock_cli,
                "val": mock_val,
                "commit": mock_commit,
            }

    async def test_warm_retry(self, _patches):
        """Verify resume_session_id is set in CLIConfig for warm retry."""
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-original",
        )
        config = _make_config(resume_on_partial=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="partial",
            concerns=["issue"],
        )

        await sup._retry_with_resume(verdict, "/work", 42)

        cli_config = _patches["cli"].call_args[0][1]
        assert cli_config.resume_session_id == "sess-original"
        assert session.retry_count == 1

    async def test_cold_fallback_no_session_id(self, _patches):
        """No session_id → cold retry (no --resume)."""
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="",  # No session ID
        )
        config = _make_config(resume_on_partial=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="partial",
            concerns=["issue"],
        )

        await sup._retry_with_resume(verdict, "/work", 42)

        cli_config = _patches["cli"].call_args[0][1]
        assert cli_config.resume_session_id == ""

    async def test_resume_disabled(self, _patches):
        """resume_on_partial=False → cold retry."""
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-abc",
        )
        config = _make_config(resume_on_partial=False)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="partial",
            concerns=[],
        )

        await sup._retry_with_resume(verdict, "/work", 42)

        cli_config = _patches["cli"].call_args[0][1]
        assert cli_config.resume_session_id == ""

    async def test_retry_fail_escalates(self, _patches):
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-abc",
        )
        config = _make_config(resume_on_partial=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        _patches["val"].return_value = ValidationVerdict(
            verdict="FAIL",
            confidence=0.2,
            summary="still bad",
            concerns=["broken"],
        )

        verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="partial",
            concerns=["issue"],
        )

        await sup._retry_with_resume(verdict, "/work", 42)

        assert session.state == TaskSessionState.FAILED


# -- Commit and complete -----------------------------------------------------


class TestCommitAndComplete:
    @pytest.mark.asyncio
    async def test_merge_ready_set_when_worktree(self):
        """Supervisor sets merge_ready instead of merging inline."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config)
        sup._base_work_dir = "/repo"
        sup._worktree_path = "/wt/42"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with patch(
            "golem.supervisor_v2_subagent.commit_changes",
            return_value=CommitResult(committed=True, sha="abc"),
        ):
            await sup._commit_and_complete(42, "/wt/42", verdict)

        assert session.merge_ready is True
        assert session.worktree_path == "/wt/42"
        assert session.base_work_dir == "/repo"
        assert session.state == TaskSessionState.COMPLETED

    @pytest.mark.asyncio
    async def test_no_merge_ready_without_worktree(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with patch(
            "golem.supervisor_v2_subagent.commit_changes",
            return_value=CommitResult(committed=True, sha="abc"),
        ):
            await sup._commit_and_complete(42, "/work", verdict)

        assert session.merge_ready is False
        assert session.commit_sha == "abc"

    @pytest.mark.asyncio
    async def test_commit_error_does_not_set_merge_ready(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = "/wt/42"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with patch(
            "golem.supervisor_v2_subagent.commit_changes",
            return_value=CommitResult(committed=False, error="pre-commit hook failed"),
        ):
            await sup._commit_and_complete(42, "/wt/42", verdict)

        assert session.merge_ready is False
        assert session.state == TaskSessionState.FAILED

    @pytest.mark.asyncio
    async def test_no_changes_skips_merge(self):
        """When commit_changes reports no changes, merge_ready stays False."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = "/wt/42"
        sup._base_work_dir = "/repo"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="review"
        )

        with patch(
            "golem.supervisor_v2_subagent.commit_changes",
            return_value=CommitResult(committed=False, message="No changes to commit"),
        ):
            await sup._commit_and_complete(42, "/wt/42", verdict)

        assert session.merge_ready is False
        assert session.state == TaskSessionState.COMPLETED
        assert not session.errors

    @pytest.mark.asyncio
    async def test_no_auto_commit(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=False)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        verdict = ValidationVerdict(verdict="PASS", confidence=0.9, summary="ok")

        await sup._commit_and_complete(42, "/work", verdict)

        assert session.state == TaskSessionState.COMPLETED


# -- Escalation --------------------------------------------------------------


class TestEscalate:
    def test_sets_failed(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        verdict = ValidationVerdict(
            verdict="FAIL",
            confidence=0.1,
            summary="bad",
            concerns=["broken"],
        )
        sup._escalate(verdict)
        assert session.state == TaskSessionState.FAILED

    def test_posts_comment(self):
        profile = _make_profile()
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, profile=profile)
        verdict = ValidationVerdict(
            verdict="FAIL",
            confidence=0.1,
            summary="bad",
            concerns=["broken"],
        )
        sup._escalate(verdict)
        profile.state_backend.post_comment.assert_called()


# -- Helper methods ----------------------------------------------------------


class TestHelpers:
    def test_emit_event(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        sup._emit_event("test event")
        assert len(session.event_log) == 1
        assert session.event_log[0]["summary"] == "test event"
        assert session.milestone_count == 1

    def test_emit_event_grows_without_cap(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        session.event_log = [{"kind": "test"} for _ in range(500)]
        sup = _make_supervisor(session=session)
        sup._emit_event("overflow event")
        assert len(session.event_log) == 501

    def test_checkpoint_calls_save(self):
        save = MagicMock()
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, save_callback=save)
        sup._checkpoint()
        save.assert_called_once()

    def test_checkpoint_no_callback(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        sup._checkpoint()  # Should not raise

    def test_checkpoint_exception_swallowed(self):
        save = MagicMock(side_effect=OSError("disk full"))
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, save_callback=save)
        sup._checkpoint()  # Should not raise

    def test_chain_event_callback_with_external(self):
        """When _event_callback is set, chained callback calls both."""
        external_events = []
        tracker_events = []

        def track(event):
            tracker_events.append(event)

        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(
            session=session,
            event_callback=external_events.append,
        )

        chained = sup._chain_event_callback(track)
        chained({"type": "test"})

        assert len(external_events) == 1
        assert len(tracker_events) == 1

    def test_chain_event_callback_without_external(self):
        """When _event_callback is None, returns tracker callback as-is."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        tracker_cb = MagicMock()
        result = sup._chain_event_callback(tracker_cb)
        assert result is tracker_cb


class TestWorktreeCleanupBranches:
    """Cover lines 203-204: worktree cleanup when not failed + no commit_sha."""

    async def test_cleanup_normal_when_no_commit_sha(self):
        """Worktree cleanup without keep_branch when COMPLETED but no commit."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(use_worktrees=True, auto_commit=False)
        sup = _make_supervisor(
            session=session, config=config, work_dir_override="/repo"
        )

        with (
            patch("golem.supervisor_v2_subagent.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor_v2_subagent.run_validation") as mock_val,
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value="/repo",
            ),
            patch(
                "golem.supervisor_v2_subagent.create_worktree",
                return_value="/wt/42",
            ),
            patch("golem.supervisor_v2_subagent.cleanup_worktree") as mock_cleanup,
            patch(
                "golem.supervisor_v2_subagent.run_verification",
                return_value=MagicMock(passed=True, duration_s=0.1),
            ),
        ):
            mock_cli.return_value = _make_cli_result(
                output_result='{"status": "COMPLETE", "summary": "done"}',
            )
            mock_val.return_value = ValidationVerdict(
                verdict="PASS", confidence=0.9, summary="ok"
            )
            await sup.run()

        # No commit_sha (auto_commit=False) + state=COMPLETED → cleanup without keep_branch
        mock_cleanup.assert_called_once_with("/repo", "/wt/42")


class TestTraceEventsPersistence:
    """Cover line 273 in _invoke_orchestrator and line 416 in _retry_with_resume."""

    @pytest.fixture()
    def _patches(self):
        with (
            patch("golem.supervisor_v2_subagent.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor_v2_subagent.run_validation") as mock_val,
            patch("golem.supervisor_v2_subagent.commit_changes") as mock_commit,
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace") as mock_trace,
            patch(
                "golem.supervisor_v2_subagent._StreamingTraceWriter",
            ) as mock_streaming,
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value="/tmp/test",
            ),
            patch("golem.supervisor_v2_subagent.create_worktree"),
            patch("golem.supervisor_v2_subagent.cleanup_worktree"),
            patch(
                "golem.supervisor_v2_subagent.run_verification",
                return_value=MagicMock(passed=True, duration_s=0.1),
            ),
        ):
            mock_cli.return_value = _make_cli_result(
                output_result='{"status": "COMPLETE", "summary": "done"}',
                session_id="sess-t",
                trace_events=[{"type": "trace"}],
            )
            mock_val.return_value = ValidationVerdict(
                verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
            )
            mock_commit.return_value = CommitResult(committed=True, sha="abc")
            mock_streaming.return_value.relative_path = "/tmp/trace.jsonl"
            mock_trace.return_value = "/tmp/retry_trace.jsonl"
            yield {
                "cli": mock_cli,
                "val": mock_val,
                "trace": mock_trace,
            }

    async def test_trace_file_saved(self, _patches):
        """When trace_events exist, trace_file is set on session."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        await sup.run()
        assert session.trace_file == "/tmp/trace.jsonl"

    async def test_retry_trace_file_saved(self, _patches):
        """When retry trace_events exist, retry_trace_file is set on session."""
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        config = _make_config(resume_on_partial=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="partial",
            concerns=["issue"],
        )

        await sup._retry_with_resume(verdict, "/work", 42)

        assert session.retry_trace_file == "/tmp/retry_trace.jsonl"


class TestStreamingCallbackWiring:
    """Verify the _streaming_callback inner function in _invoke_orchestrator."""

    async def test_callback_streams_events(self):
        captured_cb = None

        def _capture_cli(prompt, config, callback=None):
            nonlocal captured_cb
            captured_cb = callback
            if callback:
                callback(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": "hello"}]},
                    }
                )
            return _make_cli_result(
                output_result='{"status": "COMPLETE", "summary": "done"}',
                session_id="sess-x",
                trace_events=[],
            )

        with (
            patch(
                "golem.supervisor_v2_subagent.invoke_cli_monitored",
                side_effect=_capture_cli,
            ),
            patch(
                "golem.supervisor_v2_subagent.run_validation",
                return_value=ValidationVerdict(
                    verdict="PASS", confidence=0.9, summary="ok", task_type="f"
                ),
            ),
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch("golem.supervisor_v2_subagent._StreamingTraceWriter"),
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value="/tmp/test",
            ),
            patch("golem.supervisor_v2_subagent.create_worktree"),
            patch("golem.supervisor_v2_subagent.cleanup_worktree"),
            patch(
                "golem.supervisor_v2_subagent.run_verification",
                return_value=MagicMock(passed=True, duration_s=0.1),
            ),
        ):
            session = TaskSession(parent_issue_id=42, parent_subject="Test")
            sup = _make_supervisor(session=session)
            await sup.run()
        assert captured_cb is not None


# -- Checkpoint resume (skip-ahead) -----------------------------------------


class TestCheckpointResume:
    @pytest.fixture()
    def _patches(self):
        with (
            patch("golem.supervisor_v2_subagent.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor_v2_subagent.run_validation") as mock_val,
            patch("golem.supervisor_v2_subagent.commit_changes") as mock_commit,
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch("golem.supervisor_v2_subagent._StreamingTraceWriter"),
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value="/tmp/test",
            ),
            patch("golem.supervisor_v2_subagent.create_worktree"),
            patch("golem.supervisor_v2_subagent.cleanup_worktree"),
            patch("golem.supervisor_v2_subagent.save_checkpoint"),
            patch("golem.supervisor_v2_subagent.delete_checkpoint"),
            patch(
                "golem.supervisor_v2_subagent.run_verification",
                return_value=MagicMock(passed=True, duration_s=0.1),
            ),
        ):
            mock_cli.return_value = _make_cli_result(
                output_result='{"status": "COMPLETE", "summary": "done"}',
                session_id="sess-123",
            )
            mock_val.return_value = ValidationVerdict(
                verdict="PASS",
                confidence=0.95,
                summary="ok",
                task_type="feature",
            )
            mock_commit.return_value = CommitResult(committed=True, sha="abc123")
            yield {
                "cli": mock_cli,
                "val": mock_val,
                "commit": mock_commit,
            }

    async def test_resume_post_execute_skips_cli(self, _patches):
        """checkpoint_phase='post_execute' skips CLI, runs validation."""
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            checkpoint_phase="post_execute",
        )
        sup = _make_supervisor(session=session)
        await sup.run()

        _patches["cli"].assert_not_called()
        _patches["val"].assert_called_once()
        assert session.state == TaskSessionState.COMPLETED
        assert session.checkpoint_phase == ""

    async def test_resume_post_validate_pass_skips_to_commit(self, _patches):
        """checkpoint_phase='post_validate' + PASS verdict skips to commit."""
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            checkpoint_phase="post_validate",
            validation_verdict="PASS",
            validation_confidence=0.9,
            validation_summary="ok",
        )
        sup = _make_supervisor(session=session)
        await sup.run()

        _patches["cli"].assert_not_called()
        _patches["val"].assert_not_called()
        assert session.state == TaskSessionState.COMPLETED

    async def test_resume_post_validate_partial_retries(self, _patches):
        """checkpoint_phase='post_validate' + PARTIAL verdict goes to fix loop."""
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            checkpoint_phase="post_validate",
            validation_verdict="PARTIAL",
            validation_confidence=0.5,
            validation_summary="partial",
            validation_concerns=["issue"],
        )
        config = _make_config(max_retries=1, validator_fix_depth=1)
        sup = _make_supervisor(session=session, config=config)

        _patches["val"].return_value = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        await sup.run()

        # CLI called once for fix loop iteration, not for initial execution
        assert _patches["cli"].call_count == 1
        assert session.fix_iteration == 1
        assert session.state == TaskSessionState.COMPLETED

    async def test_checkpoint_phase_cleared_after_use(self, _patches):
        """checkpoint_phase is consumed (set to '') at start of run."""
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            checkpoint_phase="post_execute",
        )
        sup = _make_supervisor(session=session)
        await sup.run()
        assert session.checkpoint_phase == ""


class TestCheckpointHelpers:
    def test_save_checkpoint_error_swallowed(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        with patch(
            "golem.supervisor_v2_subagent.save_checkpoint",
            side_effect=OSError("disk full"),
        ):
            sup._save_checkpoint(42, "test_phase")  # should not raise

    def test_delete_checkpoint_error_swallowed(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        with patch(
            "golem.supervisor_v2_subagent.delete_checkpoint",
            side_effect=OSError("disk full"),
        ):
            sup._delete_checkpoint(42)  # should not raise


class TestPreflightSupervisor:
    """Cover preflight verification failure branches in _setup_work_dir."""

    @pytest.mark.asyncio
    async def test_preflight_all_checks_fail(self):
        from golem.errors import InfrastructureError

        cfg = _make_config(preflight_verify=True, use_worktrees=False)
        sup = _make_supervisor(config=cfg)
        mock_vr = MagicMock(
            passed=False,
            black_ok=False,
            black_output="would reformat foo.py",
            pylint_ok=False,
            pylint_output="E0001: syntax error",
            pytest_ok=False,
            pytest_output="FAILED test_bar",
        )
        with (
            patch(
                "golem.supervisor_v2_subagent.run_verification", return_value=mock_vr
            ),
            patch("pathlib.Path.is_dir", return_value=True),
            pytest.raises(InfrastructureError, match="black.*pylint.*pytest"),
        ):
            await sup._setup_work_dir(42, "desc")


class TestVerifiedRef:
    """Cover verified_ref fallback and on_verified_ref callback."""

    @pytest.mark.asyncio
    async def test_initial_worktree_always_uses_head(self):
        """Initial worktree creation always uses HEAD (no start_point)."""
        cfg = _make_config(
            use_worktrees=True, preflight_verify=False, default_work_dir="/tmp/test"
        )
        sup = _make_supervisor(
            config=cfg,
            verified_ref="abc123",
            work_dir_override="/repo",
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.create_worktree",
                return_value="/wt/42",
            ) as mock_wt,
            patch("golem.supervisor_v2_subagent.run_verification"),
            patch("pathlib.Path.is_dir", return_value=True),
        ):
            await sup._setup_work_dir(42, "desc")
            mock_wt.assert_called_once_with("/repo", 42)

    @pytest.mark.asyncio
    async def test_on_verified_ref_called_on_preflight_pass(self):
        """on_verified_ref callback fires with HEAD SHA after pre-flight passes."""
        cfg = _make_config(
            preflight_verify=True, use_worktrees=False, default_work_dir="/tmp/test"
        )
        callback = MagicMock()
        sup = _make_supervisor(
            config=cfg, on_verified_ref=callback, work_dir_override="/repo"
        )

        mock_vr = MagicMock(
            passed=True,
            duration_s=10.0,
        )
        head_result = MagicMock(returncode=0, stdout="deadbeef\n")
        with (
            patch(
                "golem.supervisor_v2_subagent.run_verification", return_value=mock_vr
            ),
            patch("pathlib.Path.is_dir", return_value=True),
            patch(
                "golem.supervisor_v2_subagent.subprocess.run",
                return_value=head_result,
            ),
        ):
            await sup._setup_work_dir(42, "desc")
            callback.assert_called_once_with("deadbeef")

    @pytest.mark.asyncio
    async def test_preflight_fail_falls_back_to_verified_ref(self):
        """Pre-flight failure with verified_ref recreates worktree from verified_ref."""
        cfg = _make_config(
            preflight_verify=True, use_worktrees=True, default_work_dir="/tmp/test"
        )
        sup = _make_supervisor(
            config=cfg, verified_ref="goodsha", work_dir_override="/repo"
        )

        mock_vr_fail = MagicMock(
            passed=False,
            black_ok=True,
            black_output="",
            pylint_ok=False,
            pylint_output="E1123: bad arg",
            pytest_ok=False,
            pytest_output="FAILED",
        )
        create_calls = []

        def fake_create(base, iid, start_point=None):
            create_calls.append(start_point)
            return f"/wt/{iid}"

        with (
            patch(
                "golem.supervisor_v2_subagent.create_worktree",
                side_effect=fake_create,
            ),
            patch(
                "golem.supervisor_v2_subagent.cleanup_worktree",
            ) as mock_cleanup,
            patch(
                "golem.supervisor_v2_subagent.run_verification",
                return_value=mock_vr_fail,
            ),
            patch("pathlib.Path.is_dir", return_value=True),
        ):
            work_dir = await sup._setup_work_dir(42, "desc")
            # First call uses HEAD (no start_point kwarg), second with verified_ref
            assert len(create_calls) == 2
            assert create_calls[0] is None  # HEAD
            assert create_calls[1] == "goodsha"  # fallback
            # First worktree should be cleaned up
            mock_cleanup.assert_called_once()
            assert work_dir == "/wt/42"

    @pytest.mark.asyncio
    async def test_preflight_fail_no_verified_ref_raises(self):
        """Pre-flight failure without verified_ref still raises InfrastructureError."""
        from golem.errors import InfrastructureError

        cfg = _make_config(
            preflight_verify=True, use_worktrees=False, default_work_dir="/tmp/test"
        )
        sup = _make_supervisor(config=cfg, work_dir_override="/repo")

        mock_vr = MagicMock(
            passed=False,
            black_ok=True,
            black_output="",
            pylint_ok=False,
            pylint_output="error",
            pytest_ok=True,
            pytest_output="",
        )
        with (
            patch(
                "golem.supervisor_v2_subagent.run_verification", return_value=mock_vr
            ),
            patch("pathlib.Path.is_dir", return_value=True),
            pytest.raises(InfrastructureError, match="Base branch verification failed"),
        ):
            await sup._setup_work_dir(42, "desc")


class TestClarityGate:
    """Cover the clarity check failure path in _execute_phases."""

    @pytest.mark.asyncio
    async def test_clarity_too_low_raises(self):
        from golem.clarity import check_clarity as _real  # noqa: F401
        from golem.errors import TaskExecutionError

        cfg = _make_config(
            clarity_check=True,
            clarity_threshold=3,
            use_worktrees=False,
            preflight_verify=False,
        )
        sup = _make_supervisor(config=cfg)
        mock_cr = MagicMock(score=1, reason="too vague", cost_usd=0.01)
        mock_cr.is_clear.return_value = False
        with (
            patch("golem.clarity.check_clarity", return_value=mock_cr),
            pytest.raises(TaskExecutionError, match="clarity below threshold"),
        ):
            await sup._execute_phases(42, "desc", "/work", 0.0)


class TestExtractPitfalls:
    """Cover _extract_pitfalls and _run_post_task_learning."""

    def test_extract_pitfalls_raises_on_error(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        with patch(
            "golem.orchestrator.load_sessions",
            side_effect=RuntimeError("db unavailable"),
        ):
            with pytest.raises(RuntimeError, match="db unavailable"):
                sup._extract_pitfalls()

    def test_extract_pitfalls_happy_path(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        mock_session = MagicMock()
        mock_session.state = TaskSessionState.COMPLETED
        mock_session.to_dict.return_value = {
            "validation_concerns": ["antipattern: dead code after return in module"],
            "validation_test_failures": [],
            "errors": [],
            "retry_count": 0,
            "validation_summary": "",
        }
        with (
            patch(
                "golem.orchestrator.load_sessions",
                return_value={"s1": mock_session},
            ),
            patch("golem.supervisor_v2_subagent.update_agents_md") as mock_update,
        ):
            count = sup._extract_pitfalls()
            mock_update.assert_called_once()
            assert count > 0

    def test_extract_pitfalls_returns_zero_when_no_pitfalls(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        with patch(
            "golem.orchestrator.load_sessions",
            return_value={},
        ):
            count = sup._extract_pitfalls()
            assert count == 0

    @pytest.mark.asyncio
    async def test_run_post_task_learning_happy(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        with patch.object(sup, "_extract_pitfalls", return_value=3):
            await sup._run_post_task_learning()
        events = [e["summary"] for e in session.event_log]
        assert any("Running post-task learning" in e for e in events)
        assert any("3 pitfall(s)" in e for e in events)

    @pytest.mark.asyncio
    async def test_run_post_task_learning_no_pitfalls(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        with patch.object(sup, "_extract_pitfalls", return_value=0):
            await sup._run_post_task_learning()
        events = [e["summary"] for e in session.event_log]
        assert any("no new pitfalls" in e for e in events)

    @pytest.mark.asyncio
    async def test_run_post_task_learning_catches_error(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        with patch.object(sup, "_extract_pitfalls", side_effect=RuntimeError("boom")):
            # Should not raise — exception is caught and emitted
            await sup._run_post_task_learning()
        events = [e["summary"] for e in session.event_log]
        assert any("failed (non-fatal)" in e for e in events)


class TestEnsembleRetryBranch:
    """Cover the not-yet-implemented ensemble retry branch."""

    @pytest.mark.asyncio
    async def test_ensemble_eligible_escalates(self):
        cfg = _make_config(
            ensemble_on_second_retry=True,
            ensemble_candidates=2,
            max_retries=2,
            use_worktrees=False,
            preflight_verify=False,
        )
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        session.retry_count = 0  # will be incremented to 1 inside _retry_with_resume
        sup = _make_supervisor(session=session, config=cfg)

        mock_cli = _make_cli_result(output_result="done")
        retry_verdict = MagicMock(verdict="PARTIAL", confidence=0.5)
        with (
            patch.object(sup, "_build_retry_prompt", return_value=("retry prompt", "")),
            patch(
                "golem.supervisor_v2_subagent.invoke_cli_monitored",
                return_value=mock_cli,
            ),
            patch.object(sup, "_get_description", return_value="desc"),
            patch.object(
                sup,
                "_run_overall_validation",
                new=AsyncMock(return_value=retry_verdict),
            ),
            patch.object(sup, "_escalate") as mock_esc,
            patch.object(sup, "_save_checkpoint"),
            patch.object(sup, "_emit_event"),
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
        ):
            await sup._retry_with_resume(MagicMock(), "/work", 42)
            mock_esc.assert_called_once_with(retry_verdict)


class TestFixLoop:
    """Tests for the _fix_loop inner fix cycle."""

    @pytest.fixture()
    def _patches(self):
        with (
            patch("golem.supervisor_v2_subagent.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor_v2_subagent.run_validation") as mock_val,
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch("golem.supervisor_v2_subagent._StreamingTraceWriter"),
        ):
            mock_cli.return_value = _make_cli_result(cost=0.1, session_id="sess-fix")
            yield {
                "cli": mock_cli,
                "val": mock_val,
            }

    async def test_pass_on_first_iteration(self, _patches):
        """Fix loop returns PASS immediately when first iteration passes."""
        config = _make_config(validator_fix_depth=3, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        _patches["val"].return_value = ValidationVerdict(
            verdict="PASS", confidence=0.95, summary="fixed", task_type="feature"
        )

        initial_verdict = ValidationVerdict(
            verdict="PARTIAL", confidence=0.5, summary="partial", concerns=["issue1"]
        )

        result = await sup._fix_loop(initial_verdict, "/work", 42, "desc")

        assert result.verdict == "PASS"
        assert session.fix_iteration == 1
        assert _patches["cli"].call_count == 1
        assert _patches["val"].call_count == 1

    async def test_pass_on_second_iteration(self, _patches):
        """Fix loop tries again when first iteration is PARTIAL, passes on second."""
        config = _make_config(validator_fix_depth=3, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        _patches["val"].side_effect = [
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.6,
                summary="still partial",
                concerns=["remaining"],
            ),
            ValidationVerdict(
                verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
            ),
        ]

        initial_verdict = ValidationVerdict(
            verdict="PARTIAL", confidence=0.5, summary="partial", concerns=["issue1"]
        )

        result = await sup._fix_loop(initial_verdict, "/work", 42, "desc")

        assert result.verdict == "PASS"
        assert session.fix_iteration == 2
        assert _patches["cli"].call_count == 2

    async def test_exhausted_returns_partial(self, _patches):
        """All iterations return PARTIAL → returns last PARTIAL verdict."""
        config = _make_config(validator_fix_depth=2, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        partial1 = ValidationVerdict(
            verdict="PARTIAL", confidence=0.5, summary="still bad 1", concerns=["c1"]
        )
        partial2 = ValidationVerdict(
            verdict="PARTIAL", confidence=0.6, summary="still bad 2", concerns=["c2"]
        )
        _patches["val"].side_effect = [partial1, partial2]

        initial_verdict = ValidationVerdict(
            verdict="PARTIAL", confidence=0.4, summary="initial", concerns=["issue"]
        )

        result = await sup._fix_loop(initial_verdict, "/work", 42, "desc")

        assert result.verdict == "PARTIAL"
        assert result.summary == "still bad 2"
        assert session.fix_iteration == 2
        assert _patches["cli"].call_count == 2

    async def test_fix_iteration_tracked_in_session(self, _patches):
        """fix_iteration is set on each loop iteration."""
        config = _make_config(validator_fix_depth=1, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        _patches["val"].return_value = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        initial = ValidationVerdict(
            verdict="PARTIAL", confidence=0.5, summary="p", concerns=["x"]
        )

        await sup._fix_loop(initial, "/work", 42, "desc")

        assert session.fix_iteration == 1

    async def test_emits_events_per_iteration(self, _patches):
        """Each iteration emits start and result events."""
        config = _make_config(validator_fix_depth=1, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        _patches["val"].return_value = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        initial = ValidationVerdict(
            verdict="PARTIAL", confidence=0.5, summary="p", concerns=["x"]
        )

        await sup._fix_loop(initial, "/work", 42, "desc")

        summaries = [e["summary"] for e in session.event_log]
        assert any("Fix iteration 1/1" in s and "addressing" in s for s in summaries)
        assert any("Fix iteration 1/1 validation: PASS" in s for s in summaries)

    async def test_session_id_updated_between_iterations(self, _patches):
        """Session ID is updated from CLI result for subsequent iterations."""
        config = _make_config(validator_fix_depth=2, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        _patches["cli"].return_value = _make_cli_result(
            cost=0.1, session_id="sess-updated"
        )
        _patches["val"].side_effect = [
            ValidationVerdict(
                verdict="PARTIAL", confidence=0.5, summary="p", concerns=["c"]
            ),
            ValidationVerdict(
                verdict="PASS", confidence=0.9, summary="ok", task_type="f"
            ),
        ]

        initial = ValidationVerdict(
            verdict="PARTIAL", confidence=0.5, summary="p", concerns=["x"]
        )

        await sup._fix_loop(initial, "/work", 42, "desc")

        assert session.cli_session_id == "sess-updated"

    async def test_trace_file_saved_on_fix_iteration(self, _patches):
        """When trace_events exist, fix_trace_files is appended."""
        config = _make_config(validator_fix_depth=1, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        _patches["cli"].return_value = _make_cli_result(
            cost=0.1, session_id="sess-fix", trace_events=[{"type": "trace"}]
        )
        _patches["val"].return_value = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="f"
        )

        with patch(
            "golem.supervisor_v2_subagent._write_trace",
            return_value="/tmp/fix_trace.jsonl",
        ):
            initial = ValidationVerdict(
                verdict="PARTIAL", confidence=0.5, summary="p", concerns=["x"]
            )
            await sup._fix_loop(initial, "/work", 42, "desc")

        assert session.fix_trace_files == ["/tmp/fix_trace.jsonl"]

    async def test_multiple_traces_appended(self, _patches):
        """Multiple fix iterations append to fix_trace_files."""
        config = _make_config(validator_fix_depth=2, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        _patches["cli"].return_value = _make_cli_result(
            cost=0.1, session_id="sess-fix", trace_events=[{"type": "trace"}]
        )
        _patches["val"].side_effect = [
            ValidationVerdict(
                verdict="PARTIAL", confidence=0.5, summary="p", concerns=["c"]
            ),
            ValidationVerdict(
                verdict="PASS", confidence=0.9, summary="ok", task_type="f"
            ),
        ]

        with patch(
            "golem.supervisor_v2_subagent._write_trace",
            side_effect=["/tmp/fix1.jsonl", "/tmp/fix2.jsonl"],
        ):
            initial = ValidationVerdict(
                verdict="PARTIAL", confidence=0.5, summary="p", concerns=["x"]
            )
            await sup._fix_loop(initial, "/work", 42, "desc")

        assert session.fix_trace_files == ["/tmp/fix1.jsonl", "/tmp/fix2.jsonl"]

    async def test_zero_depth_returns_immediately(self, _patches):
        """validator_fix_depth=0 means no fix iterations, returns input verdict."""
        config = _make_config(validator_fix_depth=0, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        initial = ValidationVerdict(
            verdict="PARTIAL", confidence=0.5, summary="p", concerns=["x"]
        )

        result = await sup._fix_loop(initial, "/work", 42, "desc")

        assert result.verdict == "PARTIAL"
        assert _patches["cli"].call_count == 0
        assert session.fix_iteration == 0

    async def test_fail_mid_loop_exits_early(self, _patches):
        """FAIL verdict during fix loop exits immediately."""
        config = _make_config(validator_fix_depth=3, resume_on_partial=True)
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Test",
            cli_session_id="sess-orig",
        )
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        _patches["val"].return_value = ValidationVerdict(
            verdict="FAIL", confidence=0.1, summary="broken", concerns=["fatal"]
        )

        initial = ValidationVerdict(
            verdict="PARTIAL", confidence=0.5, summary="p", concerns=["x"]
        )

        result = await sup._fix_loop(initial, "/work", 42, "desc")

        assert result.verdict == "FAIL"
        assert session.fix_iteration == 1
        assert _patches["cli"].call_count == 1
        assert _patches["val"].call_count == 1


class TestRunWithFixLoop:
    """Test the run() method uses _fix_loop for PARTIAL verdicts."""

    @pytest.fixture()
    def _patches(self):
        with (
            patch("golem.supervisor_v2_subagent.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor_v2_subagent.run_validation") as mock_val,
            patch("golem.supervisor_v2_subagent.commit_changes") as mock_commit,
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch("golem.supervisor_v2_subagent._StreamingTraceWriter"),
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value="/tmp/test",
            ),
            patch("golem.supervisor_v2_subagent.create_worktree"),
            patch("golem.supervisor_v2_subagent.cleanup_worktree"),
            patch(
                "golem.supervisor_v2_subagent.run_verification",
                return_value=MagicMock(passed=True, duration_s=0.1),
            ),
        ):
            mock_cli.return_value = _make_cli_result(
                output_result='{"status": "COMPLETE", "summary": "done"}',
                session_id="sess-123",
            )
            mock_commit.return_value = CommitResult(committed=True, sha="abc123")
            yield {
                "cli": mock_cli,
                "val": mock_val,
                "commit": mock_commit,
            }

    async def test_partial_then_pass_via_fix_loop(self, _patches):
        """PARTIAL → fix loop returns PASS → commit and complete."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(validator_fix_depth=3)
        sup = _make_supervisor(session=session, config=config)

        _patches["val"].side_effect = [
            # Initial validation
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.5,
                summary="partial",
                concerns=["issue1"],
            ),
            # Fix loop iteration 1
            ValidationVerdict(
                verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
            ),
        ]

        await sup.run()

        assert session.state == TaskSessionState.COMPLETED
        assert session.fix_iteration == 1

    async def test_partial_exhausted_no_retries_escalates(self, _patches):
        """PARTIAL → fix loop exhausted → max_retries=0 → escalate."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(validator_fix_depth=2, max_retries=0)
        sup = _make_supervisor(session=session, config=config)

        _patches["val"].side_effect = [
            # Initial validation
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.5,
                summary="partial",
                concerns=["issue1"],
            ),
            # Fix loop iteration 1
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.5,
                summary="still partial 1",
                concerns=["c1"],
            ),
            # Fix loop iteration 2
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.6,
                summary="still partial 2",
                concerns=["c2"],
            ),
        ]

        await sup.run()

        assert session.state == TaskSessionState.FAILED
        assert session.fix_iteration == 2
        assert session.retry_count == 0

    async def test_partial_exhausted_falls_back_to_full_retry(self, _patches):
        """PARTIAL → fix loop exhausted → full retry → PASS → complete."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(validator_fix_depth=1, max_retries=1)
        sup = _make_supervisor(session=session, config=config)

        _patches["val"].side_effect = [
            # Initial validation
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.5,
                summary="partial",
                concerns=["issue1"],
            ),
            # Fix loop iteration 1 (still PARTIAL → exhausted)
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.5,
                summary="still partial",
                concerns=["c1"],
            ),
            # Full retry validation → PASS
            ValidationVerdict(
                verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
            ),
        ]

        await sup.run()

        assert session.state == TaskSessionState.COMPLETED
        assert session.fix_iteration == 1
        assert session.retry_count == 1
        # 3 CLI calls: orchestration + fix iteration + full retry
        assert _patches["cli"].call_count == 3

    async def test_partial_exhausted_full_retry_also_fails_escalates(self, _patches):
        """PARTIAL → fix exhausted → full retry → FAIL → escalate."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(validator_fix_depth=1, max_retries=1)
        sup = _make_supervisor(session=session, config=config)

        _patches["val"].side_effect = [
            # Initial validation
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.5,
                summary="partial",
                concerns=["issue1"],
            ),
            # Fix loop iteration 1 (still PARTIAL → exhausted)
            ValidationVerdict(
                verdict="PARTIAL",
                confidence=0.5,
                summary="still partial",
                concerns=["c1"],
            ),
            # Full retry validation → FAIL
            ValidationVerdict(
                verdict="FAIL", confidence=0.2, summary="broken", concerns=["fatal"]
            ),
        ]

        await sup.run()

        assert session.state == TaskSessionState.FAILED
        assert session.fix_iteration == 1
        assert session.retry_count == 1


class TestValidatorFixDepthConfig:
    """Config parsing for validator_fix_depth."""

    def test_default_value(self):
        cfg = GolemFlowConfig()
        assert cfg.validator_fix_depth == 3

    def test_custom_value(self):
        cfg = GolemFlowConfig(validator_fix_depth=5)
        assert cfg.validator_fix_depth == 5

    def test_parsed_from_yaml_data(self):
        from golem.core.config import _parse_golem_config

        data = {"validator_fix_depth": 7, "projects": ["test"]}
        cfg = _parse_golem_config(data)
        assert cfg.validator_fix_depth == 7

    def test_default_when_missing_from_yaml(self):
        from golem.core.config import _parse_golem_config

        data = {"projects": ["test"]}
        cfg = _parse_golem_config(data)
        assert cfg.validator_fix_depth == 3


class TestFixIterationSerialization:
    """TaskSession fix_iteration field serialization."""

    def test_to_dict_includes_fix_iteration(self):
        session = TaskSession(parent_issue_id=1, parent_subject="t")
        session.fix_iteration = 2
        d = session.to_dict()
        assert d["fix_iteration"] == 2

    def test_from_dict_reads_fix_iteration(self):
        data = {
            "parent_issue_id": 1,
            "state": "detected",
            "fix_iteration": 5,
        }
        session = TaskSession.from_dict(data)
        assert session.fix_iteration == 5

    def test_from_dict_default_when_missing(self):
        data = {
            "parent_issue_id": 1,
            "state": "detected",
        }
        session = TaskSession.from_dict(data)
        assert session.fix_iteration == 0

    def test_roundtrip(self):
        session = TaskSession(parent_issue_id=1, parent_subject="t")
        session.fix_iteration = 3
        d = session.to_dict()
        restored = TaskSession.from_dict(d)
        assert restored.fix_iteration == 3


class TestFixTraceFilesSerialization:
    """TaskSession fix_trace_files field serialization."""

    def test_to_dict_includes_fix_trace_files(self):
        session = TaskSession(parent_issue_id=1, parent_subject="t")
        session.fix_trace_files = ["/tmp/a.jsonl", "/tmp/b.jsonl"]
        d = session.to_dict()
        assert d["fix_trace_files"] == ["/tmp/a.jsonl", "/tmp/b.jsonl"]

    def test_from_dict_reads_fix_trace_files(self):
        data = {
            "parent_issue_id": 1,
            "state": "detected",
            "fix_trace_files": ["/tmp/trace1.jsonl"],
        }
        session = TaskSession.from_dict(data)
        assert session.fix_trace_files == ["/tmp/trace1.jsonl"]

    def test_from_dict_default_when_missing(self):
        data = {
            "parent_issue_id": 1,
            "state": "detected",
        }
        session = TaskSession.from_dict(data)
        assert session.fix_trace_files == []


# -- assign_issue on pickup --------------------------------------------------


class TestAssignIssueOnPickup:
    """Supervisor calls assign_issue on task pickup when backend supports it."""

    @pytest.fixture()
    def _patches(self):
        with (
            patch("golem.supervisor_v2_subagent.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor_v2_subagent.run_validation") as mock_val,
            patch("golem.supervisor_v2_subagent.commit_changes") as mock_commit,
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch("golem.supervisor_v2_subagent._StreamingTraceWriter"),
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value="/tmp/test",
            ),
            patch("golem.supervisor_v2_subagent.create_worktree"),
            patch("golem.supervisor_v2_subagent.cleanup_worktree"),
            patch(
                "golem.supervisor_v2_subagent.run_verification",
                return_value=MagicMock(passed=True, duration_s=0.1),
            ),
        ):
            mock_cli.return_value = _make_cli_result(
                output_result='{"status": "COMPLETE", "summary": "done"}',
            )
            mock_val.return_value = ValidationVerdict(
                verdict="PASS",
                confidence=0.95,
                summary="ok",
                task_type="feature",
            )
            mock_commit.return_value = CommitResult(committed=True, sha="abc123")
            yield {"cli": mock_cli, "val": mock_val, "commit": mock_commit}

    async def test_assign_issue_called_when_backend_supports_it(self, _patches):
        """assign_issue is called on task pickup when backend has the method."""
        profile = _make_profile()
        profile.state_backend.assign_issue = MagicMock(return_value=True)

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        sup = _make_supervisor(session=session, profile=profile)

        await sup.run()

        profile.state_backend.assign_issue.assert_called_once_with(42)

    async def test_assign_issue_not_called_when_backend_lacks_method(self, _patches):
        """assign_issue is not called when backend doesn't have the method."""
        profile = _make_profile()
        # Ensure assign_issue is not present (MagicMock has it by default, so delete it)
        del profile.state_backend.assign_issue

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        sup = _make_supervisor(session=session, profile=profile)

        await sup.run()

        # No AttributeError and pipeline completes
        assert session.state == TaskSessionState.COMPLETED

    async def test_assign_issue_failure_is_non_fatal(self, _patches):
        """assign_issue failure does not block the pipeline."""
        profile = _make_profile()
        profile.state_backend.assign_issue = MagicMock(
            side_effect=RuntimeError("API error")
        )

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        sup = _make_supervisor(session=session, profile=profile)

        await sup.run()

        # Pipeline completes despite assign_issue failure
        assert session.state == TaskSessionState.COMPLETED


# -- create_pull_request after commit ----------------------------------------


class TestDetectBaseBranch:
    """Tests for _detect_base_branch static method."""

    @pytest.mark.parametrize(
        "returncode,stdout,expected",
        [
            (0, "refs/remotes/origin/main\n", "main"),
            (0, "refs/remotes/origin/master\n", "master"),
            (0, "refs/remotes/origin/develop\n", "develop"),
            (1, "", "master"),
            (128, "", "master"),
        ],
    )
    def test_detect_base_branch(self, returncode, stdout, expected):
        """Returns correct branch or defaults to master on failure."""
        mock_result = MagicMock(returncode=returncode, stdout=stdout)
        with patch(
            "golem.supervisor_v2_subagent.subprocess.run",
            return_value=mock_result,
        ):
            result = SubagentSupervisor._detect_base_branch("/work")
            assert result == expected


class TestCreatePullRequestAfterCommit:
    """Supervisor calls create_pull_request after a successful commit."""

    @pytest.fixture()
    def _base_patches(self):
        with (
            patch("golem.supervisor_v2_subagent._write_prompt"),
            patch("golem.supervisor_v2_subagent._write_trace"),
            patch("golem.supervisor_v2_subagent._StreamingTraceWriter"),
        ):
            yield

    @pytest.mark.asyncio
    async def test_create_pr_called_after_commit(self, _base_patches):
        """create_pull_request is called when backend supports it and commit succeeded."""
        profile = _make_profile()
        profile.state_backend.create_pull_request = MagicMock(
            return_value="https://github.com/org/repo/pull/7"
        )

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch.object(
                SubagentSupervisor,
                "_detect_base_branch",
                return_value="main",
            ),
        ):
            await sup._commit_and_complete(42, "/work", verdict)

        profile.state_backend.create_pull_request.assert_called_once_with(
            head="agent/42",
            base="main",
            title="#42: Test task",
            body=profile.state_backend.create_pull_request.call_args[1]["body"],
        )

    @pytest.mark.asyncio
    async def test_pr_url_included_in_completion_comment(self, _base_patches):
        """PR URL is appended to the completion comment."""
        profile = _make_profile()
        profile.state_backend.create_pull_request = MagicMock(
            return_value="https://github.com/org/repo/pull/7"
        )

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch.object(
                SubagentSupervisor,
                "_detect_base_branch",
                return_value="main",
            ),
        ):
            await sup._commit_and_complete(42, "/work", verdict)

        comment_call = profile.state_backend.post_comment.call_args
        assert comment_call is not None
        comment_text = comment_call[0][1]
        assert "https://github.com/org/repo/pull/7" in comment_text

    @pytest.mark.asyncio
    async def test_pr_event_emitted_when_pr_url_returned(self, _base_patches):
        """An event is emitted with the PR URL when creation succeeds."""
        profile = _make_profile()
        profile.state_backend.create_pull_request = MagicMock(
            return_value="https://github.com/org/repo/pull/99"
        )

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch.object(
                SubagentSupervisor,
                "_detect_base_branch",
                return_value="main",
            ),
        ):
            await sup._commit_and_complete(42, "/work", verdict)

        event_summaries = [e["summary"] for e in session.event_log]
        assert any("Created PR:" in s for s in event_summaries)

    @pytest.mark.asyncio
    async def test_create_pr_not_called_when_backend_lacks_method(self, _base_patches):
        """create_pull_request is not called when backend doesn't have the method."""
        profile = _make_profile()
        del profile.state_backend.create_pull_request

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with patch(
            "golem.supervisor_v2_subagent.commit_changes",
            return_value=CommitResult(committed=True, sha="abc"),
        ):
            await sup._commit_and_complete(42, "/work", verdict)

        assert session.state == TaskSessionState.COMPLETED

    @pytest.mark.asyncio
    async def test_create_pr_not_called_when_no_commit(self, _base_patches):
        """create_pull_request is not called when there was nothing to commit."""
        profile = _make_profile()
        profile.state_backend.create_pull_request = MagicMock(return_value="")

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with patch(
            "golem.supervisor_v2_subagent.commit_changes",
            return_value=CommitResult(committed=False, message="No changes"),
        ):
            await sup._commit_and_complete(42, "/work", verdict)

        profile.state_backend.create_pull_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_create_pr_failure_is_non_fatal(self, _base_patches):
        """create_pull_request failure does not block pipeline completion."""
        profile = _make_profile()
        profile.state_backend.create_pull_request = MagicMock(
            side_effect=RuntimeError("GitHub API error")
        )

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch.object(
                SubagentSupervisor,
                "_detect_base_branch",
                return_value="main",
            ),
        ):
            await sup._commit_and_complete(42, "/work", verdict)

        # Pipeline completes and comment does NOT contain PR URL
        assert session.state == TaskSessionState.COMPLETED
        comment_call = profile.state_backend.post_comment.call_args
        assert comment_call is not None
        comment_text = comment_call[0][1]
        assert "PR:" not in comment_text

    @pytest.mark.asyncio
    async def test_no_pr_note_in_comment_when_pr_url_empty(self, _base_patches):
        """When create_pull_request returns empty string, comment has no PR note."""
        profile = _make_profile()
        profile.state_backend.create_pull_request = MagicMock(return_value="")

        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch.object(
                SubagentSupervisor,
                "_detect_base_branch",
                return_value="main",
            ),
        ):
            await sup._commit_and_complete(42, "/work", verdict)

        comment_call = profile.state_backend.post_comment.call_args
        assert comment_call is not None
        comment_text = comment_call[0][1]
        assert "\nPR:" not in comment_text

    def test_roundtrip(self):
        session = TaskSession(parent_issue_id=1, parent_subject="t")
        session.fix_trace_files = ["/tmp/fix1.jsonl", "/tmp/fix2.jsonl"]
        d = session.to_dict()
        restored = TaskSession.from_dict(d)
        assert restored.fix_trace_files == ["/tmp/fix1.jsonl", "/tmp/fix2.jsonl"]
