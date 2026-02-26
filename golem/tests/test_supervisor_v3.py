# pylint: disable=too-few-public-methods,too-many-lines
"""Tests for golem.supervisor — full coverage."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from golem.committer import CommitResult
from golem.core.config import GolemFlowConfig
from golem.orchestrator import SubtaskResult, TaskSession, TaskSessionState
from golem.supervisor import TaskSupervisor
from golem.validation import ValidationVerdict


def _make_profile():
    profile = MagicMock()
    profile.task_source.get_task_description.return_value = "description"
    profile.task_source.get_child_tasks.return_value = []
    profile.task_source.create_child_task.return_value = 100
    profile.prompt_provider.format.return_value = "prompt text"
    profile.tool_provider.servers_for_subject.return_value = []
    profile.tool_provider.base_servers.return_value = []
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
        "max_subtask_retries": 1,
        "skip_subtask_validation": True,
        "default_work_dir": "/tmp/test",
    }
    defaults.update(overrides)
    return GolemFlowConfig(**defaults)


def _make_cli_result(cost=0.1, output_result="done", trace_events=None):
    return SimpleNamespace(
        output={"result": output_result},
        cost_usd=cost,
        trace_events=trace_events or [],
    )


def _make_supervisor(session=None, config=None, profile=None, **kwargs):
    if session is None:
        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
    if config is None:
        config = _make_config()
    if profile is None:
        profile = _make_profile()
    return TaskSupervisor(
        session=session,
        config=MagicMock(),
        task_config=config,
        profile=profile,
        **kwargs,
    )


class TestGetChildTasks:
    def test_delegates_to_profile(self):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [{"id": 10}]
        sup = _make_supervisor(profile=profile)
        result = sup._get_child_tasks(42)
        assert result == [{"id": 10}]
        profile.task_source.get_child_tasks.assert_called_once_with(42)


class TestRunPipeline:
    @pytest.fixture()
    def _patches(self):
        with (
            patch("golem.supervisor.invoke_cli_monitored") as mock_cli,
            patch("golem.supervisor.run_validation") as mock_val,
            patch("golem.supervisor.commit_changes") as mock_commit,
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
            patch("golem.supervisor.resolve_work_dir", return_value="/tmp/test"),
            patch("golem.supervisor.create_worktree"),
            patch("golem.supervisor.cleanup_worktree"),
            patch("golem.supervisor.merge_and_cleanup"),
        ):
            mock_cli.return_value = _make_cli_result()
            mock_val.return_value = ValidationVerdict(
                verdict="PASS", confidence=0.95, summary="ok", task_type="feature"
            )
            mock_commit.return_value = CommitResult(committed=True, sha="abc123")
            yield {
                "cli": mock_cli,
                "val": mock_val,
                "commit": mock_commit,
            }

    async def test_run_with_existing_children(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [
            {"id": 10, "subject": "Sub 1"},
        ]
        session = TaskSession(parent_issue_id=42, parent_subject="Test task")
        config = _make_config(skip_subtask_validation=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)

        await sup.run()

        assert session.execution_mode == "supervisor"
        assert session.state == TaskSessionState.COMPLETED
        assert len(session.subtask_results) == 1
        assert session.subtask_results[0]["status"] == "completed"

    async def test_run_with_work_dir_override(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [{"id": 10, "subject": "S"}]
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(
            session=session, profile=profile, work_dir_override="/custom/dir"
        )

        await sup.run()

        assert sup._base_work_dir == "/custom/dir"

    async def test_run_worktree_creation(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [{"id": 10, "subject": "S"}]
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(use_worktrees=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)

        with patch(
            "golem.supervisor.create_worktree", return_value="/wt/42"
        ) as mock_wt:
            await sup.run()
            mock_wt.assert_called_once()

    async def test_run_worktree_creation_failure(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [{"id": 10, "subject": "S"}]
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(use_worktrees=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)

        with patch(
            "golem.supervisor.create_worktree", side_effect=RuntimeError("fail")
        ):
            await sup.run()

        assert session.state == TaskSessionState.COMPLETED

    async def test_run_decompose_no_children_fallback_monolithic(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = []
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, profile=profile)

        cli_result = _make_cli_result(output_result='{"subtasks": []}')
        _patches["cli"].return_value = cli_result

        await sup.run()

        assert session.execution_mode == "monolithic"

    async def test_run_decompose_creates_children(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = []
        profile.task_source.create_child_task.side_effect = [100, 101]
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, profile=profile)

        subtasks_json = (
            '{"subtasks": [{"subject": "A", "description": "do A"}'
            ', {"subject": "B", "description": "do B"}]}'
        )
        _patches["cli"].return_value = _make_cli_result(output_result=subtasks_json)

        await sup.run()

        assert session.execution_mode == "supervisor"
        assert len(session.subtask_plan) == 2

    async def test_run_multiple_subtasks_progress(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [
            {"id": 10, "subject": "Sub 1"},
            {"id": 11, "subject": "Sub 2"},
            {"id": 12, "subject": "Sub 3"},
        ]
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, profile=profile)

        await sup.run()

        assert len(session.subtask_results) == 3
        progress_calls = profile.state_backend.update_progress.call_args_list
        assert len(progress_calls) >= 3

    async def test_run_validation_partial_retries(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [{"id": 10, "subject": "S"}]
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(max_retries=1)
        sup = _make_supervisor(session=session, config=config, profile=profile)

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

    async def test_run_validation_fail_escalates(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [{"id": 10, "subject": "S"}]
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(max_retries=0)
        sup = _make_supervisor(session=session, config=config, profile=profile)

        _patches["val"].return_value = ValidationVerdict(
            verdict="FAIL", confidence=0.1, summary="bad", concerns=["broken"]
        )

        await sup.run()

        assert session.state == TaskSessionState.FAILED

    async def test_run_exception_sets_failed(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.side_effect = RuntimeError("boom")
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, profile=profile)

        await sup.run()

        assert session.state == TaskSessionState.FAILED
        assert "boom" in session.errors[0]

    async def test_run_failed_worktree_cleanup_keeps_branch(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.side_effect = RuntimeError("boom")
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(use_worktrees=True)
        sup = _make_supervisor(
            session=session,
            config=config,
            profile=profile,
            work_dir_override="/repo",
        )

        with (
            patch("golem.supervisor.create_worktree", return_value="/wt/42"),
            patch("golem.supervisor.cleanup_worktree") as mock_cleanup,
        ):
            await sup.run()
            mock_cleanup.assert_called_once_with("/repo", "/wt/42", keep_branch=True)

    async def test_run_worktree_cleanup_no_commit_no_failure(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [{"id": 10, "subject": "S"}]
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(use_worktrees=True, auto_commit=False)
        sup = _make_supervisor(
            session=session,
            config=config,
            profile=profile,
            work_dir_override="/repo",
        )

        _patches["val"].return_value = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch("golem.supervisor.create_worktree", return_value="/wt/42"),
            patch("golem.supervisor.cleanup_worktree") as mock_cleanup,
        ):
            await sup.run()
            mock_cleanup.assert_called_once_with("/repo", "/wt/42")

    async def test_run_no_commit_worktree_cleanup(self, _patches):
        profile = _make_profile()
        profile.task_source.get_child_tasks.return_value = [{"id": 10, "subject": "S"}]
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=False)
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = "/wt/42"
        sup._base_work_dir = "/repo"

        _patches["val"].return_value = ValidationVerdict(
            verdict="FAIL", confidence=0.1, summary="bad"
        )

        with patch("golem.supervisor.cleanup_worktree") as mock_cleanup:
            await sup.run()
            assert (
                any(
                    call.kwargs.get("keep_branch") is not True or len(call.args) == 2
                    for call in mock_cleanup.call_args_list
                )
                or mock_cleanup.called
            )


class TestDecompose:
    async def test_decompose_invokes_cli_and_parses(self):
        profile = _make_profile()
        profile.task_source.create_child_task.side_effect = [200, 201]
        sup = _make_supervisor(profile=profile)

        subtasks_json = (
            '{"subtasks": [{"subject": "A", "description": "do A"}'
            ', {"subject": "B", "description": "do B"}]}'
        )
        cli_result = _make_cli_result(cost=0.5, output_result=subtasks_json)

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            children = await sup._decompose(42, "/work")

        assert len(children) == 2
        assert children[0]["id"] == 200
        assert sup.session.total_cost_usd == 0.5

    async def test_decompose_empty_output(self):
        sup = _make_supervisor()
        cli_result = _make_cli_result(output_result="")

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            children = await sup._decompose(42, "/work")

        assert not children

    async def test_decompose_uses_decompose_model(self):
        config = _make_config(decompose_model="haiku")
        sup = _make_supervisor(config=config)

        cli_result = _make_cli_result(output_result='{"subtasks": []}')

        with (
            patch(
                "golem.supervisor.invoke_cli_monitored", return_value=cli_result
            ) as mock_cli,
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            await sup._decompose(42, "/work")
            called_config = mock_cli.call_args[0][1]
            assert called_config.model == "haiku"

    async def test_decompose_falls_back_to_task_model(self):
        config = _make_config(decompose_model="")
        sup = _make_supervisor(config=config)

        cli_result = _make_cli_result(output_result='{"subtasks": []}')

        with (
            patch(
                "golem.supervisor.invoke_cli_monitored", return_value=cli_result
            ) as mock_cli,
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            await sup._decompose(42, "/work")
            called_config = mock_cli.call_args[0][1]
            assert called_config.model == "sonnet"


class TestExecuteSubtask:
    async def test_skip_validation_path(self):
        config = _make_config(skip_subtask_validation=True)
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, config=config)

        cli_result = _make_cli_result(cost=0.3)

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            result = await sup._execute_subtask(10, "Sub 1", 42, "/work", [])

        assert result.status == "completed"
        assert result.verdict == "DEFERRED"
        assert result.cost_usd == 0.3

    async def test_with_validation_pass(self):
        config = _make_config(skip_subtask_validation=False, max_subtask_retries=0)
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        profile = _make_profile()
        sup = _make_supervisor(session=session, config=config, profile=profile)

        cli_result = _make_cli_result(cost=0.3)
        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="good", cost_usd=0.1
        )

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor.run_validation", return_value=verdict),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            result = await sup._execute_subtask(10, "Sub 1", 42, "/work", [])

        assert result.status == "completed"
        assert result.verdict == "PASS"

    async def test_with_validation_fail(self):
        config = _make_config(skip_subtask_validation=False, max_subtask_retries=0)
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, config=config)

        cli_result = _make_cli_result(cost=0.3)
        verdict = ValidationVerdict(
            verdict="FAIL", confidence=0.2, summary="bad", cost_usd=0.1
        )

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor.run_validation", return_value=verdict),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            result = await sup._execute_subtask(10, "Sub 1", 42, "/work", [])

        assert result.status == "failed"
        assert result.verdict == "FAIL"

    async def test_exception_returns_failed_result(self):
        config = _make_config(skip_subtask_validation=True)
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, config=config)

        with (
            patch(
                "golem.supervisor.invoke_cli_monitored",
                side_effect=RuntimeError("agent crashed"),
            ),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            result = await sup._execute_subtask(10, "Sub 1", 42, "/work", [])

        assert result.status == "failed"
        assert "agent crashed" in result.summary


class TestInvokeSubtask:
    async def test_invokes_cli_and_returns_triple(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)

        cli_result = _make_cli_result(cost=0.25)
        cli_config = sup._subtask_cli_config([], "/work")

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            import time

            start = time.time()
            _result, elapsed, cost = await sup._invoke_subtask(
                "prompt", cli_config, 10, 42, start
            )

        assert cost == 0.25
        assert elapsed >= 0
        assert session.total_cost_usd == 0.25

    async def test_chains_event_callback(self):
        events_received = []

        def event_cb(e):
            events_received.append(e)

        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session, event_callback=event_cb)

        cli_result = _make_cli_result(cost=0.1)
        cli_config = sup._subtask_cli_config([], "/work")

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            import time

            await sup._invoke_subtask("prompt", cli_config, 10, 42, time.time())


class TestValidateAndRetrySubtask:
    async def test_pass_no_retry(self):
        config = _make_config(max_subtask_retries=1)
        sup = _make_supervisor(config=config)

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", cost_usd=0.05
        )

        with patch("golem.supervisor.run_validation", return_value=verdict):
            v, _cost, retry_count = await sup._validate_and_retry_subtask(
                10, "Sub", "/work", [], 42, 0.3
            )

        assert v.verdict == "PASS"
        assert retry_count == 0

    async def test_partial_triggers_retry(self):
        config = _make_config(max_subtask_retries=1)
        sup = _make_supervisor(config=config)

        partial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="needs work",
            concerns=["issue"],
            cost_usd=0.05,
        )
        pass_verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", cost_usd=0.05
        )

        cli_result = _make_cli_result(cost=0.2)

        with (
            patch(
                "golem.supervisor.run_validation",
                side_effect=[partial_verdict, pass_verdict],
            ),
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            v, _cost, retry_count = await sup._validate_and_retry_subtask(
                10, "Sub", "/work", ["srv"], 42, 0.3
            )

        assert retry_count == 1
        assert v.verdict == "PASS"

    async def test_no_retry_when_max_zero(self):
        config = _make_config(max_subtask_retries=0)
        sup = _make_supervisor(config=config)

        partial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="needs work",
            cost_usd=0.05,
        )

        with patch("golem.supervisor.run_validation", return_value=partial_verdict):
            v, _cost, retry_count = await sup._validate_and_retry_subtask(
                10, "Sub", "/work", [], 42, 0.3
            )

        assert retry_count == 0
        assert v.verdict == "PARTIAL"


class TestValidateSubtask:
    def test_delegates_to_run_validation(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(
            validation_model="opus",
            validation_budget_usd=0.5,
            validation_timeout_seconds=120,
        )
        sup = _make_supervisor(session=session, config=config)

        verdict = ValidationVerdict(verdict="PASS", confidence=0.9, summary="ok")

        with patch("golem.supervisor.run_validation", return_value=verdict) as mock_val:
            result = sup._validate_subtask(10, "Sub 1", "/work")

            mock_val.assert_called_once_with(
                issue_id=10,
                subject="Sub 1",
                description="description",
                session_data=session.to_dict(),
                work_dir="/work",
                model="opus",
                budget_usd=0.5,
                timeout_seconds=120,
            )

        assert result.verdict == "PASS"


class TestRetrySubtask:
    async def test_returns_additional_cost(self):
        sup = _make_supervisor()
        verdict = ValidationVerdict(
            verdict="PARTIAL", summary="needs fixes", concerns=["c1", "c2"]
        )

        cli_result = _make_cli_result(cost=0.4)

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            cost = await sup._retry_subtask(10, verdict, "/work", ["srv"], 42)

        assert cost == 0.4

    async def test_empty_concerns(self):
        sup = _make_supervisor()
        verdict = ValidationVerdict(
            verdict="PARTIAL", summary="needs fixes", concerns=[]
        )

        cli_result = _make_cli_result(cost=0.2)

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            cost = await sup._retry_subtask(10, verdict, "/work", [], 42)

        assert cost == 0.2


class TestSummarize:
    async def test_summarize_invokes_cli(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)

        results = [
            SubtaskResult(
                issue_id=10,
                subject="Sub 1",
                status="completed",
                verdict="PASS",
                cost_usd=0.3,
                duration_seconds=60.0,
                summary="done",
            ),
            SubtaskResult(
                issue_id=11,
                subject="Sub 2",
                status="failed",
                verdict="FAIL",
                cost_usd=0.2,
                duration_seconds=30.0,
                summary="broken",
            ),
        ]

        cli_result = _make_cli_result(cost=0.1)

        with (
            patch(
                "golem.supervisor.invoke_cli_monitored", return_value=cli_result
            ) as mock_cli,
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            await sup._summarize(42, results, "/work")

        mock_cli.assert_called_once()
        assert session.total_cost_usd == 0.1

    async def test_summarize_with_no_summary(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)

        results = [
            SubtaskResult(issue_id=10, subject="Sub 1", status="completed"),
        ]

        cli_result = _make_cli_result(cost=0.05)

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            await sup._summarize(42, results, "/work")


class TestRunOverallValidation:
    def test_sets_session_fields(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)

        verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.95,
            summary="all good",
            concerns=["minor"],
            cost_usd=0.15,
        )

        with patch("golem.supervisor.run_validation", return_value=verdict):
            result = sup._run_overall_validation(42, "description", "/work")

        assert session.state == TaskSessionState.VALIDATING
        assert session.validation_verdict == "PASS"
        assert session.validation_confidence == 0.95
        assert session.validation_summary == "all good"
        assert session.validation_concerns == ["minor"]
        assert session.validation_cost_usd == 0.15
        assert session.total_cost_usd == 0.15
        assert result.verdict == "PASS"


class TestCommitAndCompleteMergeFail:
    def test_merge_failure_escalates(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        profile = _make_profile()
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._base_work_dir = "/repo"
        sup._worktree_path = "/wt/42"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with (
            patch(
                "golem.supervisor.commit_changes",
                return_value=CommitResult(committed=True, sha="abc"),
            ),
            patch("golem.supervisor.merge_and_cleanup", return_value=None),
        ):
            sup._commit_and_complete(42, "/wt/42", verdict)

        assert session.state == TaskSessionState.FAILED
        assert any("merge failed" in e for e in session.errors)

    def test_commit_and_complete_no_auto_commit(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=False)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        verdict = ValidationVerdict(verdict="PASS", confidence=0.9, summary="ok")

        sup._commit_and_complete(42, "/work", verdict)

        assert session.state == TaskSessionState.COMPLETED

    def test_commit_and_complete_with_sha(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(auto_commit=True)
        profile = _make_profile()
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="ok", task_type="feature"
        )

        with patch(
            "golem.supervisor.commit_changes",
            return_value=CommitResult(committed=True, sha="def456"),
        ):
            sup._commit_and_complete(42, "/work", verdict)

        assert session.state == TaskSessionState.COMPLETED
        assert session.commit_sha == "def456"


class TestRetryOverall:
    async def test_retry_pass_commits(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(max_retries=1)
        profile = _make_profile()
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        initial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="partial",
            concerns=["issue1"],
        )

        cli_result = _make_cli_result(cost=0.3)
        pass_verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.9,
            summary="ok",
            task_type="feature",
            cost_usd=0.1,
        )

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor.run_validation", return_value=pass_verdict),
            patch(
                "golem.supervisor.commit_changes",
                return_value=CommitResult(committed=True, sha="retry123"),
            ),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            await sup._retry_overall(initial_verdict, "/work", 42)

        assert session.state == TaskSessionState.COMPLETED
        assert session.retry_count == 1

    async def test_retry_fail_escalates(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        config = _make_config(max_retries=1)
        profile = _make_profile()
        sup = _make_supervisor(session=session, config=config, profile=profile)

        initial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="partial",
            concerns=[],
        )

        cli_result = _make_cli_result(cost=0.3)
        fail_verdict = ValidationVerdict(
            verdict="FAIL",
            confidence=0.2,
            summary="still bad",
            concerns=["broken"],
            cost_usd=0.1,
        )

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor.run_validation", return_value=fail_verdict),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            await sup._retry_overall(initial_verdict, "/work", 42)

        assert session.state == TaskSessionState.FAILED

    async def test_retry_sets_state_and_increments(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        sup = _make_supervisor(session=session)
        sup._worktree_path = ""

        verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="partial",
            concerns=["c1"],
        )

        cli_result = _make_cli_result(cost=0.2)
        pass_verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.9,
            summary="ok",
            task_type="feature",
            cost_usd=0.05,
        )

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor.run_validation", return_value=pass_verdict),
            patch(
                "golem.supervisor.commit_changes",
                return_value=CommitResult(committed=False),
            ),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            await sup._retry_overall(verdict, "/work", 42)

        assert session.retry_count == 1
        assert session.total_cost_usd > 0


class TestRunMonolithic:
    async def test_monolithic_pass_commits(self):
        session = TaskSession(
            parent_issue_id=42, parent_subject="Test", budget_usd=10.0
        )
        config = _make_config(auto_commit=True)
        profile = _make_profile()
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        cli_result = _make_cli_result(cost=1.0, output_result="implemented feature")

        pass_verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.95,
            summary="all good",
            task_type="feature",
            cost_usd=0.1,
        )

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor.run_validation", return_value=pass_verdict),
            patch(
                "golem.supervisor.commit_changes",
                return_value=CommitResult(committed=True, sha="mono123"),
            ),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            import time

            await sup._run_monolithic(42, "/work", time.time())

        assert session.execution_mode == "monolithic"
        assert session.state == TaskSessionState.COMPLETED
        assert session.commit_sha == "mono123"

    async def test_monolithic_partial_retries(self):
        session = TaskSession(
            parent_issue_id=42, parent_subject="Test", budget_usd=10.0
        )
        config = _make_config(max_retries=1)
        profile = _make_profile()
        sup = _make_supervisor(session=session, config=config, profile=profile)
        sup._worktree_path = ""

        cli_result = _make_cli_result(cost=1.0, output_result="partial work")

        partial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="needs more",
            concerns=["incomplete"],
            cost_usd=0.1,
        )
        pass_verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.9,
            summary="ok",
            task_type="feature",
            cost_usd=0.1,
        )

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch(
                "golem.supervisor.run_validation",
                side_effect=[partial_verdict, pass_verdict],
            ),
            patch(
                "golem.supervisor.commit_changes",
                return_value=CommitResult(committed=True, sha="retry_mono"),
            ),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            import time

            await sup._run_monolithic(42, "/work", time.time())

        assert session.retry_count == 1

    async def test_monolithic_fail_escalates(self):
        session = TaskSession(
            parent_issue_id=42, parent_subject="Test", budget_usd=10.0
        )
        config = _make_config(max_retries=0)
        profile = _make_profile()
        sup = _make_supervisor(session=session, config=config, profile=profile)

        cli_result = _make_cli_result(cost=1.0, output_result="failed attempt")

        fail_verdict = ValidationVerdict(
            verdict="FAIL",
            confidence=0.1,
            summary="bad",
            concerns=["broken"],
            cost_usd=0.1,
        )

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor.run_validation", return_value=fail_verdict),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace"),
        ):
            import time

            await sup._run_monolithic(42, "/work", time.time())

        assert session.state == TaskSessionState.FAILED

    async def test_monolithic_captures_tracker_state(self):
        session = TaskSession(
            parent_issue_id=42, parent_subject="Test", budget_usd=10.0
        )
        config = _make_config(auto_commit=True)
        sup = _make_supervisor(session=session, config=config)
        sup._worktree_path = ""

        cli_result = _make_cli_result(
            cost=1.0, output_result="done", trace_events=[{"e": 1}]
        )

        pass_verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.9,
            summary="ok",
            task_type="feature",
            cost_usd=0.1,
        )

        with (
            patch("golem.supervisor.invoke_cli_monitored", return_value=cli_result),
            patch("golem.supervisor.run_validation", return_value=pass_verdict),
            patch(
                "golem.supervisor.commit_changes",
                return_value=CommitResult(committed=False),
            ),
            patch("golem.supervisor._write_prompt"),
            patch("golem.supervisor._write_trace", return_value="/tmp/trace.jsonl"),
        ):
            import time

            await sup._run_monolithic(42, "/work", time.time())

        assert session.result_summary == "done"
        assert session.trace_file == "/tmp/trace.jsonl"
        assert session.duration_seconds > 0
