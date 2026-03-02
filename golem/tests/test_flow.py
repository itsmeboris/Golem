# pylint: disable=too-few-public-methods
"""Tests for golem.flow — full coverage."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from golem.core.config import Config, GolemFlowConfig
from golem.core.triggers.base import TriggerEvent
from golem.event_tracker import Milestone
from golem.orchestrator import TaskSession, TaskSessionState


def _make_test_profile():
    from golem.backends.local import (
        LocalFileTaskSource,
        LogNotifier,
        NullStateBackend,
        NullToolProvider,
    )
    from golem.profile import GolemProfile
    from golem.prompts import FilePromptProvider

    return GolemProfile(
        name="test",
        task_source=LocalFileTaskSource("/tmp/test-tasks"),
        state_backend=NullStateBackend(),
        notifier=LogNotifier(),
        tool_provider=NullToolProvider(),
        prompt_provider=FilePromptProvider(None),
    )


def _make_flow(monkeypatch, tmp_path, profile=None, **flow_kwargs):
    from golem.flow import GolemFlow

    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

    profile = profile or _make_test_profile()
    fc_kwargs = {"enabled": True, "projects": ["test-project"], "profile": "test"}
    fc_kwargs.update(flow_kwargs)
    config = Config(golem=GolemFlowConfig(**fc_kwargs))
    monkeypatch.setattr(
        "golem.flow.build_profile",
        lambda _name, _cfg: profile,
    )
    return GolemFlow(config)


def _make_event(issue_id=None, subject="", **extra):
    data = {**extra}
    if issue_id is not None:
        data["issue_id"] = issue_id
    if subject:
        data["subject"] = subject
    return TriggerEvent(
        flow_name="golem",
        event_id="test-ev",
        data=data,
        timestamp=datetime.now(),
        source="test",
    )


class TestHandleMissingIssueId:
    async def test_returns_error_when_no_issue_id(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        result = await flow.handle(_make_event())
        assert not result.success
        assert "Missing issue_id" in result.error


class TestHandleAlreadyProcessed:
    async def test_skips_already_processed(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._processed_ids.add(42)
        result = await flow.handle(_make_event(issue_id=42))
        assert result.success
        assert result.data["skipped"]
        assert "already processed" in result.data["reason"]


class TestHandleSpawnsWhenRunning:
    async def test_spawns_session_task_when_running(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        spawned = []
        monkeypatch.setattr(flow, "_spawn_session_task", spawned.append)

        result = await flow.handle(_make_event(issue_id=99, subject="Test task"))
        assert result.success
        assert result.data["session_created"]
        assert 99 in spawned


class TestPollNoProjects:
    def test_returns_empty_when_no_projects(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path, projects=[])
        assert not flow.poll_new_items()


class TestOnItemSuccess:
    def test_on_item_success_is_noop(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow.on_item_success(123)


class TestParseWebhookPayload:
    def test_with_issue_block(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        result = flow.parse_webhook_payload(
            {
                "issue": {"id": 10, "subject": "Fix bug"},
            }
        )
        assert result["issue_id"] == 10
        assert result["subject"] == "Fix bug"

    def test_without_issue_block(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        result = flow.parse_webhook_payload({"issue_id": 20})
        assert result["issue_id"] == 20

    def test_empty_payload(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        result = flow.parse_webhook_payload({})
        assert result["issue_id"] is None


class TestGenerateWebhookEventId:
    def test_format(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        eid = flow.generate_webhook_event_id({"issue_id": 55})
        assert eid.startswith("wh-golem-55-")

    def test_unknown_fallback(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        eid = flow.generate_webhook_event_id({})
        assert "unknown" in eid


class TestStartTickLoop:
    async def test_starts_detection_and_spawns(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=1,
            parent_subject="active",
            state=TaskSessionState.RUNNING,
        )
        flow._sessions[1] = session

        spawned = []
        monkeypatch.setattr(flow, "_spawn_session_task", spawned.append)

        async def fake_detection_loop():
            pass

        monkeypatch.setattr(flow, "_detection_loop", fake_detection_loop)

        task = flow.start_tick_loop()
        assert flow._running
        assert flow._detection_task is not None
        assert 1 in spawned

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_idempotent(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        async def fake_detection_loop():
            pass

        monkeypatch.setattr(flow, "_detection_loop", fake_detection_loop)

        task1 = flow.start_tick_loop()
        task2 = flow.start_tick_loop()
        assert task1 is task2

        task1.cancel()
        try:
            await task1
        except asyncio.CancelledError:
            pass


class TestStopTickLoop:
    async def test_stops_detection_and_cancels_sessions(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        detection_task = asyncio.create_task(asyncio.sleep(100))
        flow._detection_task = detection_task

        session_task = asyncio.create_task(asyncio.sleep(100))
        flow._session_tasks[42] = session_task

        flow.stop_tick_loop()

        assert not flow._running
        assert flow._detection_task is None
        assert not flow._session_tasks

        await asyncio.sleep(0)
        assert detection_task.cancelled()
        assert session_task.cancelled()


class TestDetectionLoop:
    async def test_runs_one_iteration_then_stops(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path, tick_interval=0)
        flow._running = True

        call_count = 0

        def fake_detect():
            nonlocal call_count
            call_count += 1
            flow._running = False

        monkeypatch.setattr(flow, "_detect_new_issues", fake_detect)
        await flow._detection_loop()
        assert call_count == 1

    async def test_handles_exception_in_detect(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path, tick_interval=0)
        flow._running = True

        calls = 0

        def exploding_detect():
            nonlocal calls
            calls += 1
            flow._running = False
            raise RuntimeError("boom")

        monkeypatch.setattr(flow, "_detect_new_issues", exploding_detect)
        await flow._detection_loop()
        assert calls == 1


class TestDetectNewIssues:
    def test_creates_sessions_and_spawns(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        monkeypatch.setattr(
            flow,
            "poll_new_items",
            lambda: [{"issue_id": 10, "subject": "Task A"}],
        )

        spawned = []
        monkeypatch.setattr(flow, "_spawn_session_task", spawned.append)

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._detect_new_issues()

        assert 10 in flow._sessions
        assert flow._sessions[10].parent_subject == "Task A"
        assert 10 in spawned
        mock_live.enqueue.assert_called_once()
        mock_live.update_phase.assert_called_once()

    def test_skips_items_without_id(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        monkeypatch.setattr(
            flow,
            "poll_new_items",
            lambda: [{"subject": "no id"}],
        )

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._detect_new_issues()
        assert not flow._sessions


class TestSpawnSessionTask:
    async def test_creates_task(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=50,
            parent_subject="test",
            state=TaskSessionState.DETECTED,
        )
        flow._sessions[50] = session

        async def fake_run(sid):
            pass

        monkeypatch.setattr(flow, "_run_session", fake_run)
        flow._spawn_session_task(50)

        assert 50 in flow._session_tasks
        flow._session_tasks[50].cancel()
        try:
            await flow._session_tasks[50]
        except asyncio.CancelledError:
            pass

    async def test_skips_if_already_spawned(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        existing_task = asyncio.create_task(asyncio.sleep(100))
        flow._session_tasks[50] = existing_task

        flow._spawn_session_task(50)
        assert flow._session_tasks[50] is existing_task

        existing_task.cancel()
        try:
            await existing_task
        except asyncio.CancelledError:
            pass


class TestSpawnExistingSessions:
    async def test_spawns_non_terminal_only(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        flow._sessions = {
            1: TaskSession(parent_issue_id=1, state=TaskSessionState.DETECTED),
            2: TaskSession(parent_issue_id=2, state=TaskSessionState.RUNNING),
            3: TaskSession(parent_issue_id=3, state=TaskSessionState.COMPLETED),
            4: TaskSession(parent_issue_id=4, state=TaskSessionState.FAILED),
        }

        spawned = []
        monkeypatch.setattr(flow, "_spawn_session_task", spawned.append)
        flow._spawn_existing_sessions()

        assert 1 in spawned
        assert 2 in spawned
        assert 3 not in spawned
        assert 4 not in spawned


class TestRunSession:
    async def test_completes_normally(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        session = TaskSession(
            parent_issue_id=60,
            parent_subject="complete me",
            state=TaskSessionState.DETECTED,
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[60] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        async def completing_tick(self_orch):
            self_orch.session.state = TaskSessionState.COMPLETED
            return self_orch.session

        monkeypatch.setattr(TaskOrchestrator, "tick", completing_tick)

        await flow._run_session(60)
        assert 60 not in flow._session_tasks

    async def test_crash_marks_failed(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        session = TaskSession(
            parent_issue_id=70,
            parent_subject="crash me",
            state=TaskSessionState.DETECTED,
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[70] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        async def crashing_tick(self_orch):
            raise RuntimeError("orchestrator boom")

        monkeypatch.setattr(TaskOrchestrator, "tick", crashing_tick)

        transitions = []
        monkeypatch.setattr(
            flow,
            "_handle_state_transition",
            lambda s, prev: transitions.append((s.state, prev)),
        )

        await flow._run_session(70)
        assert session.state == TaskSessionState.FAILED
        assert "session task crashed" in session.errors
        assert transitions
        assert transitions[-1] == (TaskSessionState.FAILED, TaskSessionState.RUNNING)

    async def test_cancelled_session(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        session = TaskSession(
            parent_issue_id=80,
            parent_subject="cancel me",
            state=TaskSessionState.DETECTED,
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[80] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        async def cancelling_tick(self_orch):
            raise asyncio.CancelledError()

        monkeypatch.setattr(TaskOrchestrator, "tick", cancelling_tick)
        await flow._run_session(80)
        assert 80 not in flow._session_tasks

    async def test_sleep_between_ticks(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path, tick_interval=0)
        flow._running = True

        session = TaskSession(
            parent_issue_id=90,
            parent_subject="multi tick",
            state=TaskSessionState.DETECTED,
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[90] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        tick_count = 0

        async def multi_tick(self_orch):
            nonlocal tick_count
            tick_count += 1
            if tick_count >= 2:
                self_orch.session.state = TaskSessionState.COMPLETED
            else:
                self_orch.session.state = TaskSessionState.RUNNING
            return self_orch.session

        monkeypatch.setattr(TaskOrchestrator, "tick", multi_tick)
        await flow._run_session(90)
        assert tick_count == 2


class TestOnAgentProgress:
    def test_updates_live_state(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(parent_issue_id=100, parent_subject="progress test")
        milestone = Milestone(kind="tool_call", tool_name="Read")

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._on_agent_progress(session, milestone)

        mock_live.update_phase.assert_called_once_with("golem-100", "tool:Read")

    def test_without_tool_name(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(parent_issue_id=101, parent_subject="progress test")
        milestone = Milestone(kind="text")

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._on_agent_progress(session, milestone)

        mock_live.update_phase.assert_called_once_with("golem-101", "text")

    def test_none_kind_defaults_to_running(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(parent_issue_id=102, parent_subject="progress test")
        milestone = Milestone(kind="")

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._on_agent_progress(session, milestone)

        mock_live.update_phase.assert_called_once_with("golem-102", "running")


class TestLoadStateWithRecovery:
    def test_logs_recovered_sessions(self, monkeypatch, tmp_path):
        sessions_path = tmp_path / "golem_sessions.json"
        sessions_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "sessions": {
                "500": {
                    "parent_issue_id": 500,
                    "parent_subject": "in-flight",
                    "state": "running",
                },
            },
            "completed_ids": [],
        }
        sessions_path.write_text(json.dumps(data))

        monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

        profile = _make_test_profile()
        config = Config(
            golem=GolemFlowConfig(enabled=True, projects=["p"], profile="test"),
        )
        monkeypatch.setattr(
            "golem.flow.build_profile",
            lambda _name, _cfg: profile,
        )

        from golem.flow import GolemFlow

        flow = GolemFlow(config)

        assert 500 in flow._sessions
        assert flow._sessions[500].state == TaskSessionState.DETECTED


class TestResetState:
    def test_clears_everything_and_deletes_file(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._sessions[1] = TaskSession(parent_issue_id=1)
        flow._processed_ids.add(1)

        sessions_path = tmp_path / "sessions.json"
        sessions_path.write_text("{}")

        monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

        flow.reset_state()

        assert not flow._sessions
        assert not flow._processed_ids
        assert not flow._trackers
        assert not sessions_path.exists()


class TestHandleStateTransition:
    def _make_flow_and_notifier(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        mock_notifier = MagicMock()
        flow._profile.notifier = mock_notifier
        return flow, mock_notifier

    def test_detected_to_running(self, monkeypatch, tmp_path):
        flow, notifier = self._make_flow_and_notifier(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=200,
            parent_subject="transition test",
            state=TaskSessionState.RUNNING,
        )
        flow._handle_state_transition(session, TaskSessionState.DETECTED)

        mock_live.dequeue_start.assert_called_once_with("golem-200")
        notifier.notify_started.assert_called_once()

    def test_to_completed(self, monkeypatch, tmp_path):
        flow, notifier = self._make_flow_and_notifier(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=201,
            parent_subject="done",
            state=TaskSessionState.COMPLETED,
            total_cost_usd=1.5,
            duration_seconds=60.0,
            milestone_count=5,
            validation_verdict="PASS",
            validation_confidence=0.95,
            commit_sha="abc123",
        )
        flow._handle_state_transition(session, TaskSessionState.RUNNING)

        assert 201 in flow._processed_ids
        mock_live.finish.assert_called_once_with(
            "golem-201", success=True, cost_usd=1.5
        )
        notifier.notify_completed.assert_called_once()

    def test_to_failed_with_verdict(self, monkeypatch, tmp_path):
        flow, notifier = self._make_flow_and_notifier(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=202,
            parent_subject="failed",
            state=TaskSessionState.FAILED,
            validation_verdict="FAIL",
            validation_summary="bad code",
            total_cost_usd=2.0,
        )
        flow._handle_state_transition(session, TaskSessionState.RUNNING)

        mock_live.finish.assert_called_once_with(
            "golem-202", success=False, cost_usd=2.0
        )
        notifier.notify_escalated.assert_called_once()
        notifier.notify_failed.assert_not_called()

    def test_to_failed_without_verdict(self, monkeypatch, tmp_path):
        flow, notifier = self._make_flow_and_notifier(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=203,
            parent_subject="failed no verdict",
            state=TaskSessionState.FAILED,
            errors=["Budget exceeded"],
        )
        flow._handle_state_transition(session, TaskSessionState.RUNNING)

        notifier.notify_failed.assert_called_once()
        notifier.notify_escalated.assert_not_called()

    def test_to_failed_no_errors_uses_unknown(self, monkeypatch, tmp_path):
        flow, notifier = self._make_flow_and_notifier(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=204,
            parent_subject="failed empty errors",
            state=TaskSessionState.FAILED,
        )
        flow._handle_state_transition(session, TaskSessionState.RUNNING)

        call_args = notifier.notify_failed.call_args
        assert "Unknown error" in call_args[0][2]

    def test_no_transition_when_same_state(self, monkeypatch, tmp_path):
        flow, notifier = self._make_flow_and_notifier(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=205,
            parent_subject="no change",
            state=TaskSessionState.COMPLETED,
        )
        flow._handle_state_transition(session, TaskSessionState.COMPLETED)

        notifier.notify_completed.assert_not_called()


class TestGenerateEventId:
    def test_format(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        eid = flow.generate_event_id({"issue_id": 77})
        assert eid.startswith("golem-77-")

    def test_unknown_fallback(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        eid = flow.generate_event_id({})
        assert "unknown" in eid


class TestFlowName:
    def test_name_is_golem(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        assert flow.name == "golem"


class TestSubmitTask:
    def test_creates_file_and_session(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        spawned = []
        monkeypatch.setattr(flow, "_spawn_session_task", spawned.append)

        result = flow.submit_task("refactor the auth module", subject="[AGENT] Auth")
        assert result["status"] == "submitted"
        task_id = result["task_id"]

        assert task_id in flow._sessions
        session = flow._sessions[task_id]
        assert session.execution_mode == "prompt"
        assert session.parent_subject == "[AGENT] Auth"
        assert task_id in spawned

        deadline = datetime.fromisoformat(session.grace_deadline)
        assert deadline <= datetime.now(timezone.utc)

        task_file = flow._submissions_dir / f"{task_id}.json"
        assert task_file.exists()

    def test_auto_generates_subject(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_task("do something cool")
        task_id = result["task_id"]
        session = flow._sessions[task_id]
        assert session.parent_subject.startswith("[AGENT]")

    def test_not_spawned_when_not_running(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = False

        spawned = []
        monkeypatch.setattr(flow, "_spawn_session_task", spawned.append)

        flow.submit_task("test prompt")
        assert not spawned


class TestScanSubmissions:
    def test_picks_up_json_files(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        sub_dir = flow._submissions_dir
        sub_dir.mkdir(parents=True, exist_ok=True)

        task_data = {
            "id": "9001",
            "subject": "[AGENT] File drop test",
            "description": "do it",
        }
        (sub_dir / "9001.json").write_text(json.dumps(task_data))

        spawned = []
        monkeypatch.setattr(flow, "_spawn_session_task", spawned.append)

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._scan_submissions()

        assert 9001 in flow._sessions
        assert flow._sessions[9001].execution_mode == "prompt"
        deadline = datetime.fromisoformat(flow._sessions[9001].grace_deadline)
        assert deadline <= datetime.now(timezone.utc)
        assert 9001 in spawned

        done_file = sub_dir / "done" / "9001.json"
        assert done_file.exists()
        assert not (sub_dir / "9001.json").exists()

    def test_skips_already_tracked(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        sub_dir = flow._submissions_dir
        sub_dir.mkdir(parents=True, exist_ok=True)

        task_data = {"id": "9002", "subject": "[AGENT] Dup", "description": "dup"}
        (sub_dir / "9002.json").write_text(json.dumps(task_data))

        flow._sessions[9002] = TaskSession(parent_issue_id=9002)

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._scan_submissions()
        mock_live.enqueue.assert_not_called()

    def test_empty_dir(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._scan_submissions()

    def test_invalid_id_skipped(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        sub_dir = flow._submissions_dir
        sub_dir.mkdir(parents=True, exist_ok=True)

        (sub_dir / "bad.json").write_text(
            json.dumps({"id": "not-a-number", "subject": "test"})
        )

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._scan_submissions()
        assert not flow._sessions


class TestSubmitTaskWithWorkDir:
    def test_includes_work_dir(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_task("do work", work_dir="/my/project")
        task_id = result["task_id"]

        task_file = flow._submissions_dir / f"{task_id}.json"
        data = json.loads(task_file.read_text())
        assert data["work_dir"] == "/my/project"


class TestScanSubmissionsEdgeCases:
    def test_skips_directories(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        sub_dir = flow._submissions_dir
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "done.json").mkdir()

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._scan_submissions()
        assert not flow._sessions

    def test_skips_non_json_files(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        sub_dir = flow._submissions_dir
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "readme.txt").write_text("not a task")

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._scan_submissions()
        assert not flow._sessions

    def test_skips_unparseable_files(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        sub_dir = flow._submissions_dir
        sub_dir.mkdir(parents=True, exist_ok=True)
        (sub_dir / "bad.json").write_text("{invalid json")

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._scan_submissions()
        assert not flow._sessions

    def test_nonexistent_dir(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._submissions_dir = tmp_path / "nonexistent"
        flow._scan_submissions()


class TestDetectNewIssuesWithSubmissions:
    def test_scans_submissions_in_detection(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        monkeypatch.setattr(flow, "poll_new_items", lambda: [])

        scanned = []
        monkeypatch.setattr(flow, "_scan_submissions", lambda: scanned.append(True))

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        flow._detect_new_issues()
        assert scanned


class TestRunSessionWithSubmissionProfile:
    async def test_uses_submission_profile_for_prompt_sessions(
        self, monkeypatch, tmp_path
    ):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        session = TaskSession(
            parent_issue_id=8001,
            parent_subject="prompt task",
            state=TaskSessionState.DETECTED,
            execution_mode="prompt",
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[8001] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        captured_profiles = []

        orig_init = TaskOrchestrator.__init__

        def capture_init(self_orch, *args, **kwargs):
            orig_init(self_orch, *args, **kwargs)
            captured_profiles.append(self_orch.profile)

        monkeypatch.setattr(TaskOrchestrator, "__init__", capture_init)

        async def completing_tick(self_orch):
            self_orch.session.state = TaskSessionState.COMPLETED

        monkeypatch.setattr(TaskOrchestrator, "tick", completing_tick)

        await flow._run_session(8001)

        assert len(captured_profiles) == 1
        assert captured_profiles[0].name == "submission"

    async def test_uses_main_profile_for_normal_sessions(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        session = TaskSession(
            parent_issue_id=8002,
            parent_subject="normal task",
            state=TaskSessionState.DETECTED,
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[8002] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        captured_profiles = []

        orig_init = TaskOrchestrator.__init__

        def capture_init(self_orch, *args, **kwargs):
            orig_init(self_orch, *args, **kwargs)
            captured_profiles.append(self_orch.profile)

        monkeypatch.setattr(TaskOrchestrator, "__init__", capture_init)

        async def completing_tick(self_orch):
            self_orch.session.state = TaskSessionState.COMPLETED

        monkeypatch.setattr(TaskOrchestrator, "tick", completing_tick)

        await flow._run_session(8002)

        assert len(captured_profiles) == 1
        assert captured_profiles[0].name == "test"


class TestSaveState:
    def test_delegates_to_save_sessions(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        saved = []
        monkeypatch.setattr(
            "golem.flow.save_sessions",
            lambda sessions: saved.append(dict(sessions)),
        )
        flow._save_state()
        assert len(saved) == 1
