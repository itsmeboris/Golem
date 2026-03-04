# pylint: disable=too-few-public-methods,too-many-lines
"""Tests for golem.supervisor_v2_subagent — full coverage."""
from unittest.mock import MagicMock, patch

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
        sup = _make_supervisor(profile=profile)
        result = sup._build_prompt(42, "desc", "/work")
        profile.prompt_provider.format.assert_called_once_with(
            "orchestrate_task.txt",
            issue_id=42,
            parent_subject="Test task",
            task_description="desc",
            work_dir="/work",
            inner_retry_max=3,
        )
        assert result == "prompt text"


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
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value="/tmp/test",
            ),
            patch("golem.supervisor_v2_subagent.create_worktree"),
            patch("golem.supervisor_v2_subagent.cleanup_worktree"),
            patch("golem.supervisor_v2_subagent.merge_and_cleanup"),
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
        """invoke → validate(PARTIAL) → resume → validate(PASS)."""
        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(max_retries=1, resume_on_partial=True)
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

        assert session.retry_count == 1
        assert session.state == TaskSessionState.COMPLETED
        # Two CLI calls: orchestration + retry
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
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(use_worktrees=True)
        sup = _make_supervisor(session=session, config=config)

        with patch(
            "golem.supervisor_v2_subagent.create_worktree",
            side_effect=RuntimeError("fail"),
        ):
            await sup.run()

        assert session.state == TaskSessionState.COMPLETED

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
    def test_merge_called_when_worktree(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config)
        sup._base_work_dir = "/repo"
        sup._worktree_path = "/wt/42"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch(
                "golem.supervisor_v2_subagent.merge_and_cleanup",
                return_value="merge123",
            ) as mock_merge,
        ):
            sup._commit_and_complete(42, "/wt/42", verdict)

        mock_merge.assert_called_once_with("/repo", 42, "/wt/42")
        assert session.commit_sha == "merge123"
        assert sup._worktree_path == ""

    def test_no_merge_without_worktree(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch("golem.supervisor_v2_subagent.merge_and_cleanup") as mock_merge,
        ):
            sup._commit_and_complete(42, "/work", verdict)

        mock_merge.assert_not_called()
        assert session.commit_sha == "abc"

    def test_commit_error_does_not_merge(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = "/wt/42"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(
                    committed=False, error="pre-commit hook failed"
                ),
            ),
            patch("golem.supervisor_v2_subagent.merge_and_cleanup") as mock_merge,
        ):
            sup._commit_and_complete(42, "/wt/42", verdict)

        mock_merge.assert_not_called()
        assert session.state == TaskSessionState.FAILED

    def test_merge_failure_escalates(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config)
        sup._base_work_dir = "/repo"
        sup._worktree_path = "/wt/42"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor_v2_subagent.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch(
                "golem.supervisor_v2_subagent.merge_and_cleanup",
                return_value=None,
            ),
        ):
            sup._commit_and_complete(42, "/wt/42", verdict)

        assert session.state == TaskSessionState.FAILED
        assert any("merge failed" in e for e in session.errors)

    def test_no_auto_commit(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=False)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        verdict = ValidationVerdict(verdict="PASS", confidence=0.9, summary="ok")

        sup._commit_and_complete(42, "/work", verdict)

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

    def test_emit_event_caps_at_500(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        session.event_log = [{"kind": "test"} for _ in range(500)]
        sup = _make_supervisor(session=session)
        sup._emit_event("overflow event")
        assert len(session.event_log) == 500

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
            patch("golem.supervisor_v2_subagent.merge_and_cleanup"),
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
            patch(
                "golem.supervisor_v2_subagent._write_trace",
                return_value="/tmp/trace.jsonl",
            ) as mock_trace,
            patch(
                "golem.supervisor_v2_subagent.resolve_work_dir",
                return_value="/tmp/test",
            ),
            patch("golem.supervisor_v2_subagent.create_worktree"),
            patch("golem.supervisor_v2_subagent.cleanup_worktree"),
            patch("golem.supervisor_v2_subagent.merge_and_cleanup"),
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

        assert session.retry_trace_file == "/tmp/trace.jsonl"
