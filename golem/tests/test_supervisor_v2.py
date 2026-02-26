# pylint: disable=too-few-public-methods
"""Tests for golem.supervisor — supervisor helpers and state management."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def _make_config():
    return GolemFlowConfig(
        enabled=True,
        task_model="sonnet",
        supervisor_mode=True,
        use_worktrees=False,
        auto_commit=True,
        max_retries=1,
        max_subtask_retries=1,
        skip_subtask_validation=True,
        default_work_dir="/tmp/test",
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


class TestSupervisorProfileHelpers:
    def test_update_task_status(self):
        profile = _make_profile()
        sup = _make_supervisor(profile=profile)
        sup._update_task(42, status="in_progress")
        profile.state_backend.update_status.assert_called_once_with(42, "in_progress")

    def test_update_task_progress(self):
        profile = _make_profile()
        sup = _make_supervisor(profile=profile)
        sup._update_task(42, progress=50)
        profile.state_backend.update_progress.assert_called_once_with(42, 50)

    def test_update_task_comment(self):
        profile = _make_profile()
        sup = _make_supervisor(profile=profile)
        sup._update_task(42, comment="hello")
        profile.state_backend.post_comment.assert_called_once_with(42, "hello")

    def test_update_task_noop(self):
        profile = _make_profile()
        sup = _make_supervisor(profile=profile)
        sup._update_task(42)
        profile.state_backend.update_status.assert_not_called()
        profile.state_backend.update_progress.assert_not_called()
        profile.state_backend.post_comment.assert_not_called()

    def test_get_description(self):
        profile = _make_profile()
        profile.task_source.get_task_description.return_value = "task desc"
        sup = _make_supervisor(profile=profile)
        assert sup._get_description(42) == "task desc"

    def test_format_prompt(self):
        profile = _make_profile()
        profile.prompt_provider.format.return_value = "formatted"
        sup = _make_supervisor(profile=profile)
        assert sup._format_prompt("run_task.txt", x=1) == "formatted"

    def test_get_mcp_servers(self):
        profile = _make_profile()
        profile.tool_provider.servers_for_subject.return_value = ["jenkins"]
        sup = _make_supervisor(profile=profile)
        assert sup._get_mcp_servers("test") == ["jenkins"]

    def test_get_base_mcp_servers(self):
        profile = _make_profile()
        profile.tool_provider.base_servers.return_value = ["redmine"]
        sup = _make_supervisor(profile=profile)
        assert sup._get_base_mcp_servers() == ["redmine"]


class TestSupervisorChainEventCallback:
    def test_without_event_callback(self):
        sup = _make_supervisor()
        tracker_cb = MagicMock()
        result = sup._chain_event_callback(tracker_cb)
        assert result is tracker_cb

    def test_with_event_callback(self):
        calls = []

        def event_cb(e):
            calls.append(("event", e))

        def tracker_cb(e):
            calls.append(("tracker", e))

        sup = _make_supervisor(event_callback=event_cb)
        chained = sup._chain_event_callback(tracker_cb)
        chained({"type": "test"})
        assert len(calls) == 2
        assert calls[0][0] == "event"
        assert calls[1][0] == "tracker"


class TestBuildSiblingStatus:
    def test_no_prior_results(self):
        sup = _make_supervisor()
        assert "No prior" in sup._build_sibling_status([])

    def test_with_results(self):
        results = [
            SubtaskResult(
                issue_id=1, subject="Sub 1", status="completed", summary="ok"
            ),
            SubtaskResult(issue_id=2, subject="Sub 2", status="failed", summary="bad"),
        ]
        sup = _make_supervisor()
        status = sup._build_sibling_status(results)
        assert "#1" in status
        assert "#2" in status
        assert "completed" in status
        assert "failed" in status


class TestSupervisorEmitEvent:
    def test_appends_to_event_log(self):
        session = TaskSession(parent_issue_id=42)
        sup = _make_supervisor(session=session)
        sup._emit_supervisor_event("Step 1 done")
        assert len(session.event_log) == 1
        assert session.event_log[0]["kind"] == "supervisor"
        assert session.event_log[0]["summary"] == "Step 1 done"

    def test_error_flag(self):
        session = TaskSession(parent_issue_id=42)
        sup = _make_supervisor(session=session)
        sup._emit_supervisor_event("boom", is_error=True)
        assert session.event_log[0]["is_error"] is True

    def test_caps_at_500(self):
        session = TaskSession(parent_issue_id=42)
        session.event_log = [{"kind": "old"}] * 500
        sup = _make_supervisor(session=session)
        sup._emit_supervisor_event("new")
        assert len(session.event_log) == 500

    def test_increments_milestone_count(self):
        session = TaskSession(parent_issue_id=42)
        sup = _make_supervisor(session=session)
        sup._emit_supervisor_event("a")
        sup._emit_supervisor_event("b")
        assert session.milestone_count == 2


class TestSupervisorCheckpoint:
    def test_calls_save_callback(self):
        cb = MagicMock()
        sup = _make_supervisor(save_callback=cb)
        sup._checkpoint()
        cb.assert_called_once()

    def test_no_callback(self):
        sup = _make_supervisor()
        sup._checkpoint()

    def test_callback_failure_logged(self):
        cb = MagicMock(side_effect=RuntimeError("disk full"))
        sup = _make_supervisor(save_callback=cb)
        sup._checkpoint()


class TestParseDecomposeOutput:
    def test_valid_output(self):
        result = SimpleNamespace(
            output={
                "result": '{"subtasks": [{"subject": "Sub 1", "description": "Do thing 1"}]}'
            }
        )
        sup = _make_supervisor()
        defs = sup._parse_decompose_output(result, 42)
        assert len(defs) == 1
        assert defs[0]["subject"] == "Sub 1"

    def test_empty_output(self):
        result = SimpleNamespace(output={"result": ""})
        sup = _make_supervisor()
        assert sup._parse_decompose_output(result, 42) == []

    def test_no_subtasks_key(self):
        result = SimpleNamespace(output={"result": '{"other": "data"}'})
        sup = _make_supervisor()
        assert sup._parse_decompose_output(result, 42) == []

    def test_invalid_json(self):
        result = SimpleNamespace(output={"result": "not json at all"})
        sup = _make_supervisor()
        assert sup._parse_decompose_output(result, 42) == []


class TestCreateChildren:
    def test_creates_and_returns(self):
        profile = _make_profile()
        profile.task_source.create_child_task.side_effect = [100, 101]
        sup = _make_supervisor(profile=profile)
        defs = [
            {"subject": "Sub 1", "description": "Do 1"},
            {"subject": "Sub 2", "description": "Do 2"},
        ]
        children = sup._create_children(42, defs)
        assert len(children) == 2
        assert children[0]["id"] == 100
        assert children[1]["id"] == 101

    def test_skips_failed_creation(self):
        profile = _make_profile()
        profile.task_source.create_child_task.side_effect = [100, None, 102]
        sup = _make_supervisor(profile=profile)
        defs = [
            {"subject": "A", "description": ""},
            {"subject": "B", "description": ""},
            {"subject": "C", "description": ""},
        ]
        children = sup._create_children(42, defs)
        assert len(children) == 2


class TestUpdateChildStatus:
    def test_pass_verdict(self):
        profile = _make_profile()
        sup = _make_supervisor(profile=profile)
        verdict = ValidationVerdict(verdict="PASS")
        sup._update_child_status(10, verdict, cost=0.5, elapsed=60.0)
        profile.state_backend.update_status.assert_called()
        profile.state_backend.update_progress.assert_called_with(10, 100)

    def test_fail_verdict(self):
        profile = _make_profile()
        sup = _make_supervisor(profile=profile)
        verdict = ValidationVerdict(verdict="FAIL", summary="bad")
        sup._update_child_status(10, verdict, cost=0.5, elapsed=60.0)
        profile.state_backend.post_comment.assert_called()


class TestEscalate:
    def test_sets_failed(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Test")
        profile = _make_profile()
        sup = _make_supervisor(session=session, profile=profile)
        verdict = ValidationVerdict(
            verdict="FAIL",
            confidence=0.1,
            summary="bad",
            concerns=["c1", "c2"],
        )
        sup._escalate(verdict)
        assert session.state == TaskSessionState.FAILED
        profile.state_backend.update_status.assert_called()
        profile.state_backend.post_comment.assert_called()


class TestSubtaskCliConfig:
    def test_uses_subtask_model(self):
        config = _make_config()
        config.subtask_model = "haiku"
        sup = _make_supervisor(config=config)
        cli = sup._subtask_cli_config(["server1"], "/work")
        assert cli.model == "haiku"

    def test_falls_back_to_task_model(self):
        config = _make_config()
        config.subtask_model = ""
        config.task_model = "sonnet"
        sup = _make_supervisor(config=config)
        cli = sup._subtask_cli_config([], "/work")
        assert cli.model == "sonnet"


class TestPersistTrace:
    @patch("golem.supervisor._write_prompt")
    @patch("golem.supervisor._write_trace")
    def test_writes_both(self, mock_trace, mock_prompt):
        result = SimpleNamespace(trace_events=[{"e": 1}])
        TaskSupervisor._persist_trace("golem-42", "prompt text", result)
        mock_prompt.assert_called_once()
        mock_trace.assert_called_once()

    @patch("golem.supervisor._write_prompt")
    @patch("golem.supervisor._write_trace")
    def test_skips_trace_if_empty(self, mock_trace, mock_prompt):
        result = SimpleNamespace(trace_events=[])
        TaskSupervisor._persist_trace("golem-42", "prompt text", result)
        mock_prompt.assert_called_once()
        mock_trace.assert_not_called()

    @patch("golem.supervisor._write_prompt")
    @patch("golem.supervisor._write_trace")
    def test_handles_none_result(self, mock_trace, mock_prompt):
        TaskSupervisor._persist_trace("golem-42", "prompt text", None)
        mock_prompt.assert_called_once()
        mock_trace.assert_not_called()
