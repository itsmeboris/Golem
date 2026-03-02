# pylint: disable=too-few-public-methods,too-many-lines
"""Tests for golem.orchestrator — session lifecycle, persistence, and pipeline helpers."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.committer import CommitResult
from golem.core.cli_wrapper import CLIResult
from golem.event_tracker import Milestone, TaskEventTracker, TrackerState
from golem.orchestrator import (
    SubtaskResult,
    TaskOrchestrator,
    TaskSession,
    TaskSessionState,
    _now_iso,
    load_sessions,
    recover_sessions,
    save_sessions,
)
from golem.validation import ValidationVerdict


class TestSubtaskResult:
    def test_defaults(self):
        r = SubtaskResult(issue_id=1, subject="task")
        assert r.status == ""
        assert r.verdict == ""
        assert r.cost_usd == 0.0
        assert r.retry_count == 0


class TestTaskSessionSerialization:
    def test_round_trip(self):
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Fix it",
            state=TaskSessionState.RUNNING,
            total_cost_usd=1.5,
            tools_called=["Read", "Write"],
            errors=["oops"],
        )
        d = session.to_dict()
        assert d["state"] == "running"
        assert d["parent_issue_id"] == 42

        restored = TaskSession.from_dict(d)
        assert restored.parent_issue_id == 42
        assert restored.state == TaskSessionState.RUNNING
        assert restored.total_cost_usd == 1.5
        assert restored.tools_called == ["Read", "Write"]

    def test_from_dict_defaults(self):
        session = TaskSession.from_dict({"parent_issue_id": 1, "state": "detected"})
        assert session.budget_usd == 10.0
        assert not session.errors
        assert session.milestone_count == 0


class TestSessionPersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "sessions.json"
        sessions = {
            1: TaskSession(parent_issue_id=1, state=TaskSessionState.COMPLETED),
            2: TaskSession(parent_issue_id=2, state=TaskSessionState.RUNNING),
        }
        save_sessions(sessions, path)
        assert path.exists()

        loaded = load_sessions(path)
        assert len(loaded) == 2
        assert loaded[1].state == TaskSessionState.COMPLETED
        assert loaded[2].state == TaskSessionState.RUNNING

    def test_load_nonexistent(self, tmp_path):
        assert not load_sessions(tmp_path / "nope.json")

    def test_load_corrupt(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json at all")
        assert not load_sessions(path)

    def test_atomic_write(self, tmp_path):
        path = tmp_path / "sessions.json"
        sessions = {1: TaskSession(parent_issue_id=1)}
        save_sessions(sessions, path)
        data = json.loads(path.read_text())
        assert "sessions" in data
        assert "last_updated" in data
        assert "completed_ids" in data


class TestRecoverSessions:
    def test_resets_in_flight(self):
        sessions = {
            1: TaskSession(parent_issue_id=1, state=TaskSessionState.RUNNING),
            2: TaskSession(parent_issue_id=2, state=TaskSessionState.VALIDATING),
            3: TaskSession(parent_issue_id=3, state=TaskSessionState.COMPLETED),
            4: TaskSession(parent_issue_id=4, state=TaskSessionState.RETRYING),
        }
        count = recover_sessions(sessions)
        assert count == 3
        assert sessions[1].state == TaskSessionState.DETECTED
        assert sessions[2].state == TaskSessionState.DETECTED
        assert sessions[3].state == TaskSessionState.COMPLETED
        assert sessions[4].state == TaskSessionState.DETECTED

    def test_no_in_flight(self):
        sessions = {
            1: TaskSession(parent_issue_id=1, state=TaskSessionState.COMPLETED),
            2: TaskSession(parent_issue_id=2, state=TaskSessionState.FAILED),
        }
        assert recover_sessions(sessions) == 0


class TestNowIso:
    def test_returns_iso_string(self):
        ts = _now_iso()
        assert "T" in ts
        assert "+" in ts or "Z" in ts


class TestOrchestratorInit:
    def test_creates_lock_if_not_provided(self):
        session = TaskSession(parent_issue_id=1)
        config = MagicMock()
        tc = MagicMock()
        orch = TaskOrchestrator(session, config, tc)
        assert orch._work_dir_lock is not None

    def test_accepts_custom_lock(self):
        lock = asyncio.Lock()
        session = TaskSession(parent_issue_id=1)
        orch = TaskOrchestrator(session, MagicMock(), MagicMock(), work_dir_lock=lock)
        assert orch._work_dir_lock is lock


class TestOrchestratorChainEventCallback:
    def test_without_event_callback(self):
        orch = TaskOrchestrator(
            TaskSession(parent_issue_id=1), MagicMock(), MagicMock()
        )
        tracker_cb = MagicMock()
        result = orch._chain_event_callback(tracker_cb)
        assert result is tracker_cb

    def test_with_event_callback(self):
        calls = []

        def event_cb(e):
            calls.append(("event", e))

        def tracker_cb(e):
            calls.append(("tracker", e))

        orch = TaskOrchestrator(
            TaskSession(parent_issue_id=1),
            MagicMock(),
            MagicMock(),
            event_callback=event_cb,
        )
        chained = orch._chain_event_callback(tracker_cb)
        chained({"type": "test"})

        assert len(calls) == 2
        assert calls[0][0] == "event"
        assert calls[1][0] == "tracker"


class TestOrchestratorApplyVerdict:
    def test_stores_verdict_in_session(self):
        session = TaskSession(parent_issue_id=1)
        orch = TaskOrchestrator(session, MagicMock(), MagicMock())

        verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.95,
            summary="good",
            concerns=["minor"],
            cost_usd=0.10,
        )
        orch._apply_verdict(verdict)

        assert session.validation_verdict == "PASS"
        assert session.validation_confidence == 0.95
        assert session.validation_summary == "good"
        assert session.validation_concerns == ["minor"]
        assert session.validation_cost_usd == 0.10
        assert session.total_cost_usd == 0.10


class TestOrchestratorPopulateSession:
    def test_copies_tracker_state(self):
        session = TaskSession(parent_issue_id=1)
        orch = TaskOrchestrator(session, MagicMock(), MagicMock())

        tracker = TaskEventTracker(session_id=1)
        tracker.state.tools_called.append("Read")
        tracker.state.mcp_tools_called.append("redmine")
        tracker.state.errors.append("err1")
        tracker.state.milestone_count = 5
        tracker.state.last_text = "last thing"

        result = SimpleNamespace(
            cost_usd=0.50,
            output={"result": "done"},
            trace_events=[],
        )

        orch._populate_session_from_tracker(tracker, result, 120.0)

        assert "Read" in session.tools_called
        assert "redmine" in session.mcp_tools_called
        assert "err1" in session.errors
        assert session.milestone_count == 5
        assert session.duration_seconds == 120.0
        assert session.total_cost_usd == 0.50

    def test_handles_none_result(self):
        session = TaskSession(parent_issue_id=1)
        orch = TaskOrchestrator(session, MagicMock(), MagicMock())
        tracker = TaskEventTracker(session_id=1)
        tracker.state.cost_usd = 0.25

        orch._populate_session_from_tracker(tracker, None, 60.0)

        assert session.total_cost_usd == 0.25
        assert session.duration_seconds == 60.0


class TestOrchestratorThrottledCheckpoint:
    def test_skips_within_interval(self):
        session = TaskSession(parent_issue_id=1)
        cb = MagicMock()
        orch = TaskOrchestrator(session, MagicMock(), MagicMock(), save_callback=cb)
        orch._checkpoint_interval = 999
        orch._last_checkpoint_time = 9999999999.0

        orch._throttled_checkpoint()
        cb.assert_not_called()

    def test_persists_after_interval(self):
        session = TaskSession(parent_issue_id=1)
        cb = MagicMock()
        orch = TaskOrchestrator(session, MagicMock(), MagicMock(), save_callback=cb)
        orch._checkpoint_interval = 0
        orch._last_checkpoint_time = 0

        orch._throttled_checkpoint()
        cb.assert_called_once()


class TestOrchestratorThrottledCheckpointException:
    def test_save_callback_exception_swallowed(self):
        session = TaskSession(parent_issue_id=1)
        cb = MagicMock(side_effect=RuntimeError("disk error"))
        orch = TaskOrchestrator(session, MagicMock(), MagicMock(), save_callback=cb)
        orch._checkpoint_interval = 0
        orch._last_checkpoint_time = 0
        orch._throttled_checkpoint()
        cb.assert_called_once()


class TestOrchestratorEscalate:
    def test_sets_failed_and_notifies(self):
        session = TaskSession(parent_issue_id=99, parent_subject="Test")
        mock_profile = MagicMock()
        orch = TaskOrchestrator(session, MagicMock(), MagicMock(), profile=mock_profile)

        verdict = ValidationVerdict(
            verdict="FAIL",
            confidence=0.2,
            summary="bad",
            concerns=["issue1", "issue2"],
        )
        orch._escalate(verdict)

        assert session.state == TaskSessionState.FAILED
        mock_profile.state_backend.update_status.assert_called()
        mock_profile.state_backend.post_comment.assert_called()

    def test_escalate_no_concerns(self):
        session = TaskSession(parent_issue_id=1, parent_subject="x")
        mock_profile = MagicMock()
        orch = TaskOrchestrator(session, MagicMock(), MagicMock(), profile=mock_profile)
        verdict = ValidationVerdict(verdict="FAIL", confidence=0.1, summary="bad")
        orch._escalate(verdict)
        assert session.state == TaskSessionState.FAILED


def _make_orch(session=None, *, profile=None, task_config=None, **kwargs):
    session = session or TaskSession(parent_issue_id=42, parent_subject="Fix bug")
    profile = profile or MagicMock()
    if task_config is None:
        task_config = MagicMock()
        task_config.supervisor_mode = False
        task_config.use_worktrees = False
        task_config.task_model = "sonnet"
        task_config.task_timeout_seconds = 300
        task_config.validation_model = "opus"
        task_config.validation_budget_usd = 0.5
        task_config.validation_timeout_seconds = 120
        task_config.max_retries = 1
        task_config.auto_commit = True
        task_config.retry_budget_usd = 5.0
    config = MagicMock()
    return TaskOrchestrator(
        session,
        config,
        task_config,
        profile=profile,
        **kwargs,
    )


class TestTick:
    async def test_tick_detected_grace_elapsed(self):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.DETECTED,
            grace_deadline="2000-01-01T00:00:00+00:00",
        )
        orch = _make_orch(session)
        orch._run_agent = AsyncMock()
        result = await orch.tick()
        assert result is session
        orch._run_agent.assert_awaited_once()
        assert session.state != TaskSessionState.DETECTED

    async def test_tick_detected_grace_not_elapsed(self):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.DETECTED,
            grace_deadline="2999-01-01T00:00:00+00:00",
        )
        orch = _make_orch(session)
        orch._run_agent = AsyncMock()
        result = await orch.tick()
        assert result is session
        orch._run_agent.assert_not_awaited()
        assert session.state == TaskSessionState.DETECTED

    async def test_tick_completed_noop(self):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.COMPLETED,
        )
        orch = _make_orch(session)
        result = await orch.tick()
        assert result.state == TaskSessionState.COMPLETED

    async def test_tick_failed_noop(self):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.FAILED,
        )
        orch = _make_orch(session)
        result = await orch.tick()
        assert result.state == TaskSessionState.FAILED


class TestRunOnce:
    async def test_run_once_transitions_to_running(self):
        session = TaskSession(parent_issue_id=1)
        orch = _make_orch(session)
        orch._run_agent = AsyncMock()
        result = await orch.run_once()
        assert result is session
        orch._run_agent.assert_awaited_once()


class TestRunAgent:
    async def test_dispatches_supervisor_mode(self):
        session = TaskSession(parent_issue_id=1)
        tc = MagicMock()
        tc.supervisor_mode = True
        orch = _make_orch(session, task_config=tc)
        mock_sup_instance = MagicMock()
        mock_sup_instance.run = AsyncMock()
        mock_sup_cls = MagicMock(return_value=mock_sup_instance)
        fake_module = MagicMock(TaskSupervisor=mock_sup_cls)
        with patch.dict("sys.modules", {"golem.supervisor": fake_module}):
            await orch._run_agent()
        mock_sup_instance.run.assert_awaited_once()

    async def test_dispatches_monolithic_mode(self):
        session = TaskSession(parent_issue_id=1)
        orch = _make_orch(session)
        orch._run_agent_monolithic = AsyncMock()
        await orch._run_agent()
        orch._run_agent_monolithic.assert_awaited_once()


class TestRunAgentMonolithic:  # pylint: disable=confusing-with-statement
    def _mock_deps(self):
        patches = {
            "resolve": patch(
                "golem.orchestrator.resolve_work_dir", return_value="/work"
            ),
            "create_wt": patch(
                "golem.orchestrator.create_worktree", return_value="/wt"
            ),
            "cleanup_wt": patch("golem.orchestrator.cleanup_worktree"),
            "merge_wt": patch(
                "golem.orchestrator.merge_and_cleanup", return_value="abc123"
            ),
            "invoke": patch(
                "golem.orchestrator.invoke_cli_monitored",
                return_value=CLIResult(
                    output={"result": "done"},
                    cost_usd=0.5,
                    trace_events=[{"e": 1}],
                ),
            ),
            "run_val": patch(
                "golem.orchestrator.run_validation",
                return_value=ValidationVerdict(
                    verdict="PASS",
                    confidence=0.95,
                    summary="ok",
                    task_type="feature",
                ),
            ),
            "commit": patch(
                "golem.orchestrator.commit_changes",
                return_value=CommitResult(committed=True, sha="def456"),
            ),
            "write_prompt": patch("golem.orchestrator._write_prompt"),
            "write_trace": patch(
                "golem.orchestrator._write_trace", return_value="/trace"
            ),
        }
        return patches

    async def test_happy_path_pass_commit(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "prompt"
        profile.tool_provider.servers_for_subject.return_value = []
        orch = _make_orch(session, profile=profile)

        with self._mock_deps()["resolve"], self._mock_deps()[
            "invoke"
        ], self._mock_deps()["run_val"], self._mock_deps()["commit"], self._mock_deps()[
            "write_prompt"
        ], self._mock_deps()[
            "write_trace"
        ], patch.object(
            orch, "_write_report"
        ), patch.object(
            orch, "_record_run"
        ):
            await orch._run_agent_monolithic()

        assert session.state == TaskSessionState.COMPLETED
        assert session.commit_sha == "def456"

    async def test_pass_with_worktree_merge(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "prompt"
        profile.tool_provider.servers_for_subject.return_value = []
        tc = MagicMock()
        tc.supervisor_mode = False
        tc.use_worktrees = True
        tc.task_model = "sonnet"
        tc.task_timeout_seconds = 300
        tc.validation_model = "opus"
        tc.validation_budget_usd = 0.5
        tc.validation_timeout_seconds = 120
        tc.max_retries = 1
        tc.auto_commit = True
        tc.retry_budget_usd = 5.0
        orch = _make_orch(session, profile=profile, task_config=tc)

        deps = self._mock_deps()
        with deps["resolve"], deps["create_wt"] as m_create, deps[
            "merge_wt"
        ] as m_merge, deps["invoke"], deps["run_val"], deps["commit"], deps[
            "write_prompt"
        ], deps[
            "write_trace"
        ], patch.object(
            orch, "_write_report"
        ), patch.object(
            orch, "_record_run"
        ):
            await orch._run_agent_monolithic()

        assert session.state == TaskSessionState.COMPLETED
        assert session.commit_sha == "abc123"
        m_create.assert_called_once()
        m_merge.assert_called_once()

    async def test_worktree_creation_fails_fallback(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "prompt"
        profile.tool_provider.servers_for_subject.return_value = []
        tc = MagicMock()
        tc.supervisor_mode = False
        tc.use_worktrees = True
        tc.task_model = "sonnet"
        tc.task_timeout_seconds = 300
        tc.validation_model = "opus"
        tc.validation_budget_usd = 0.5
        tc.validation_timeout_seconds = 120
        tc.max_retries = 1
        tc.auto_commit = True
        tc.retry_budget_usd = 5.0
        orch = _make_orch(session, profile=profile, task_config=tc)

        deps = self._mock_deps()
        with deps["resolve"], patch(
            "golem.orchestrator.create_worktree", side_effect=RuntimeError("no git")
        ), deps["invoke"], deps["run_val"], deps["commit"], deps["write_prompt"], deps[
            "write_trace"
        ], patch(
            "golem.orchestrator.cleanup_worktree"
        ) as m_cleanup, patch.object(
            orch, "_write_report"
        ), patch.object(
            orch, "_record_run"
        ):
            await orch._run_agent_monolithic()

        assert session.state == TaskSessionState.COMPLETED
        m_cleanup.assert_not_called()

    async def test_work_dir_override(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "prompt"
        profile.tool_provider.servers_for_subject.return_value = []
        orch = _make_orch(session, profile=profile, work_dir_override="/custom")

        deps = self._mock_deps()
        with deps["invoke"], deps["run_val"], deps["commit"], deps[
            "write_prompt"
        ], deps["write_trace"], patch(
            "golem.orchestrator.resolve_work_dir"
        ) as m_resolve, patch.object(
            orch, "_write_report"
        ), patch.object(
            orch, "_record_run"
        ):
            await orch._run_agent_monolithic()

        m_resolve.assert_not_called()

    async def test_partial_triggers_retry(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "prompt"
        profile.tool_provider.servers_for_subject.return_value = []
        orch = _make_orch(session, profile=profile)
        orch._retry_agent = AsyncMock()

        deps = self._mock_deps()
        partial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="needs work",
        )
        with deps["resolve"], deps["invoke"], patch(
            "golem.orchestrator.run_validation", return_value=partial_verdict
        ), deps["commit"], deps["write_prompt"], deps["write_trace"], patch.object(
            orch, "_write_report"
        ), patch.object(
            orch, "_record_run"
        ):
            await orch._run_agent_monolithic()

        orch._retry_agent.assert_awaited_once()

    async def test_partial_exhausted_retries_escalates(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix", retry_count=1)
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "prompt"
        profile.tool_provider.servers_for_subject.return_value = []
        orch = _make_orch(session, profile=profile)

        deps = self._mock_deps()
        partial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.3,
            summary="still bad",
        )
        with deps["resolve"], deps["invoke"], patch(
            "golem.orchestrator.run_validation", return_value=partial_verdict
        ), deps["write_prompt"], deps["write_trace"], patch.object(
            orch, "_write_report"
        ), patch.object(
            orch, "_record_run"
        ):
            await orch._run_agent_monolithic()

        assert session.state == TaskSessionState.FAILED

    async def test_fail_verdict_escalates(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "prompt"
        profile.tool_provider.servers_for_subject.return_value = []
        orch = _make_orch(session, profile=profile)

        deps = self._mock_deps()
        fail_verdict = ValidationVerdict(verdict="FAIL", confidence=0.1, summary="bad")
        with deps["resolve"], deps["invoke"], patch(
            "golem.orchestrator.run_validation", return_value=fail_verdict
        ), deps["write_prompt"], deps["write_trace"], patch.object(
            orch, "_write_report"
        ), patch.object(
            orch, "_record_run"
        ):
            await orch._run_agent_monolithic()

        assert session.state == TaskSessionState.FAILED

    async def test_exception_triggers_handle_failure(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "prompt"
        profile.tool_provider.servers_for_subject.return_value = []
        orch = _make_orch(session, profile=profile)

        deps = self._mock_deps()
        with deps["resolve"], patch(
            "golem.orchestrator.invoke_cli_monitored", side_effect=RuntimeError("boom")
        ), deps["write_prompt"], deps["write_trace"], patch.object(
            orch, "_write_report"
        ), patch.object(
            orch, "_record_run"
        ):
            await orch._run_agent_monolithic()

        assert session.state == TaskSessionState.FAILED
        assert any("boom" in e for e in session.errors)

    async def test_worktree_cleanup_on_failure(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "prompt"
        profile.tool_provider.servers_for_subject.return_value = []
        tc = MagicMock()
        tc.supervisor_mode = False
        tc.use_worktrees = True
        tc.task_model = "sonnet"
        tc.task_timeout_seconds = 300
        tc.validation_model = "opus"
        tc.validation_budget_usd = 0.5
        tc.validation_timeout_seconds = 120
        tc.max_retries = 1
        tc.auto_commit = True
        tc.retry_budget_usd = 5.0
        orch = _make_orch(session, profile=profile, task_config=tc)

        with patch("golem.orchestrator.resolve_work_dir", return_value="/work"), patch(
            "golem.orchestrator.create_worktree", return_value="/wt"
        ), patch(
            "golem.orchestrator.invoke_cli_monitored", side_effect=RuntimeError("x")
        ), patch(
            "golem.orchestrator._write_prompt"
        ), patch(
            "golem.orchestrator._write_trace"
        ), patch(
            "golem.orchestrator.cleanup_worktree"
        ) as m_cleanup, patch.object(
            orch, "_write_report"
        ), patch.object(
            orch, "_record_run"
        ):
            await orch._run_agent_monolithic()

        m_cleanup.assert_called_once()
        assert m_cleanup.call_args[1]["keep_branch"] is True


class TestPersistTraces:
    def test_writes_prompt_and_trace(self):
        orch = _make_orch()
        result = CLIResult(trace_events=[{"t": 1}])
        with patch("golem.orchestrator._write_prompt") as m_p, patch(
            "golem.orchestrator._write_trace", return_value="/t"
        ) as m_t:
            orch._persist_traces(42, "the prompt", result)
        m_p.assert_called_once_with("golem", "golem-42", "the prompt")
        m_t.assert_called_once()
        assert orch.session.trace_file == "/t"

    def test_skips_empty_prompt(self):
        orch = _make_orch()
        with patch("golem.orchestrator._write_prompt") as m_p, patch(
            "golem.orchestrator._write_trace"
        ):
            orch._persist_traces(42, "", CLIResult())
        m_p.assert_not_called()

    def test_skips_none_result(self):
        orch = _make_orch()
        with patch("golem.orchestrator._write_prompt"), patch(
            "golem.orchestrator._write_trace"
        ) as m_t:
            orch._persist_traces(42, "prompt", None)
        m_t.assert_not_called()

    def test_skips_empty_trace_events(self):
        orch = _make_orch()
        result = CLIResult(trace_events=[])
        with patch("golem.orchestrator._write_prompt"), patch(
            "golem.orchestrator._write_trace"
        ) as m_t:
            orch._persist_traces(42, "prompt", result)
        m_t.assert_not_called()


class TestRunValidation:
    async def test_runs_validation_and_stores_verdict(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        orch = _make_orch(session, profile=profile)

        verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.9,
            summary="good",
            cost_usd=0.1,
        )
        with patch.object(orch, "_run_validation_in_executor", return_value=verdict):
            result = await orch._run_validation(42, "/work")

        assert result.verdict == "PASS"
        assert session.state == TaskSessionState.VALIDATING
        assert session.validation_verdict == "PASS"
        assert session.validation_confidence == 0.9


class TestCommitAndComplete:
    def test_pass_with_commit(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        session.validation_verdict = "PASS"
        profile = MagicMock()
        orch = _make_orch(session, profile=profile)
        verdict = ValidationVerdict(verdict="PASS", task_type="feature")

        with patch(
            "golem.orchestrator.commit_changes",
            return_value=CommitResult(committed=True, sha="abc"),
        ):
            orch._commit_and_complete(42, "/work", verdict)

        assert session.state == TaskSessionState.COMPLETED
        assert session.commit_sha == "abc"
        profile.state_backend.update_status.assert_called()

    def test_commit_error_sets_failed(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        session.validation_verdict = "PASS"
        profile = MagicMock()
        orch = _make_orch(session, profile=profile)
        verdict = ValidationVerdict(verdict="PASS", task_type="feature")

        with patch(
            "golem.orchestrator.commit_changes",
            return_value=CommitResult(committed=False, error="hook failed"),
        ):
            orch._commit_and_complete(42, "/work", verdict)

        assert session.state == TaskSessionState.FAILED
        assert any("commit failed" in e for e in session.errors)

    def test_no_commit_no_changes(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        session.validation_verdict = "PASS"
        profile = MagicMock()
        orch = _make_orch(session, profile=profile)
        verdict = ValidationVerdict(verdict="PASS", task_type="feature")

        with patch(
            "golem.orchestrator.commit_changes",
            return_value=CommitResult(committed=False),
        ):
            orch._commit_and_complete(42, "/work", verdict)

        assert session.state == TaskSessionState.COMPLETED
        assert not session.commit_sha

    def test_auto_commit_disabled(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        session.validation_verdict = "PARTIAL"
        tc = MagicMock()
        tc.auto_commit = False
        profile = MagicMock()
        orch = _make_orch(session, profile=profile, task_config=tc)
        verdict = ValidationVerdict(verdict="PARTIAL")

        with patch("golem.orchestrator.commit_changes") as m_commit:
            orch._commit_and_complete(42, "/work", verdict)

        m_commit.assert_not_called()
        assert session.state == TaskSessionState.COMPLETED

    def test_complete_comment_includes_extras(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        session.validation_verdict = "PASS"
        session.retry_count = 1
        session.total_cost_usd = 1.0
        session.duration_seconds = 120.0
        session.milestone_count = 5
        profile = MagicMock()
        orch = _make_orch(session, profile=profile)
        verdict = ValidationVerdict(verdict="PASS", task_type="fix")

        with patch(
            "golem.orchestrator.commit_changes",
            return_value=CommitResult(committed=True, sha="xyz"),
        ):
            orch._commit_and_complete(42, "/work", verdict)

        assert session.state == TaskSessionState.COMPLETED
        comment_arg = profile.state_backend.post_comment.call_args[0][1]
        assert "xyz" in comment_arg
        assert "retry" in comment_arg


class TestHandleAgentFailure:
    def test_populates_session_and_notifies(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        orch = _make_orch(session, profile=profile)
        tracker = TaskEventTracker(session_id=42)
        exc = RuntimeError("something broke")

        with patch("golem.orchestrator._write_prompt"), patch(
            "golem.orchestrator._write_trace"
        ):
            orch._handle_agent_failure(
                42, exc, time.time() - 10, tracker, None, "prompt"
            )

        assert session.state == TaskSessionState.FAILED
        assert "something broke" in session.errors
        profile.state_backend.post_comment.assert_called_once()

    def test_with_cli_result(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        orch = _make_orch(session, profile=profile)
        tracker = TaskEventTracker(session_id=42)
        result = CLIResult(cost_usd=0.5, trace_events=[{"e": 1}])
        exc = ValueError("oops")

        with patch("golem.orchestrator._write_prompt"), patch(
            "golem.orchestrator._write_trace"
        ):
            orch._handle_agent_failure(42, exc, time.time() - 5, tracker, result, "p")

        assert session.state == TaskSessionState.FAILED


class TestRetryAgent:
    async def test_retry_pass(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "retry prompt"
        orch = _make_orch(session, profile=profile)

        retry_result = CLIResult(
            cost_usd=0.3,
            trace_events=[{"r": 1}],
            output={"result": "fixed"},
        )
        retry_verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.9,
            summary="fixed",
        )
        initial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="needs work",
            concerns=["issue A"],
        )

        with patch(
            "golem.orchestrator.invoke_cli_monitored", return_value=retry_result
        ), patch("golem.orchestrator._write_prompt"), patch(
            "golem.orchestrator._write_trace", return_value="/rt"
        ), patch(
            "golem.orchestrator.run_validation", return_value=retry_verdict
        ):
            await orch._retry_agent(initial_verdict, "/work", [])

        assert session.retry_count == 1
        assert session.total_cost_usd > 0
        assert session.retry_trace_file == "/rt"

    async def test_retry_fails_escalates(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "retry prompt"
        orch = _make_orch(session, profile=profile)

        retry_result = CLIResult(cost_usd=0.3)
        retry_verdict = ValidationVerdict(
            verdict="FAIL",
            confidence=0.2,
            summary="still bad",
        )
        initial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="needs work",
        )

        with patch(
            "golem.orchestrator.invoke_cli_monitored", return_value=retry_result
        ), patch("golem.orchestrator._write_prompt"), patch(
            "golem.orchestrator._write_trace"
        ), patch(
            "golem.orchestrator.run_validation", return_value=retry_verdict
        ):
            await orch._retry_agent(initial_verdict, "/work", [])

        assert session.state == TaskSessionState.FAILED

    async def test_retry_none_result(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "retry prompt"
        orch = _make_orch(session, profile=profile)

        retry_verdict = ValidationVerdict(
            verdict="PASS",
            confidence=0.9,
            summary="ok",
        )
        initial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="needs work",
        )

        with patch("golem.orchestrator.invoke_cli_monitored", return_value=None), patch(
            "golem.orchestrator._write_prompt"
        ), patch("golem.orchestrator._write_trace"), patch(
            "golem.orchestrator.run_validation", return_value=retry_verdict
        ):
            await orch._retry_agent(initial_verdict, "/work", [])

        assert session.retry_count == 1

    async def test_retry_event_log_summary(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        session.event_log = [
            {"kind": "tool_call", "summary": "did something"} for _ in range(20)
        ]
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        profile.prompt_provider.format.return_value = "retry prompt"
        orch = _make_orch(session, profile=profile)

        retry_result = CLIResult(cost_usd=0.1)
        retry_verdict = ValidationVerdict(verdict="PASS", confidence=0.8, summary="ok")
        initial_verdict = ValidationVerdict(
            verdict="PARTIAL",
            confidence=0.5,
            summary="needs work",
            concerns=["c1", "c2"],
        )

        with patch(
            "golem.orchestrator.invoke_cli_monitored", return_value=retry_result
        ), patch("golem.orchestrator._write_prompt"), patch(
            "golem.orchestrator._write_trace"
        ), patch(
            "golem.orchestrator.run_validation", return_value=retry_verdict
        ):
            await orch._retry_agent(initial_verdict, "/work", ["mcp1"])

        assert session.retry_count == 1


class TestWriteReport:
    def test_writes_report_successfully(self):
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Fix bug",
            state=TaskSessionState.COMPLETED,
            total_cost_usd=1.0,
            duration_seconds=120.0,
            milestone_count=5,
            validation_verdict="PASS",
            validation_summary="good",
            validation_cost_usd=0.1,
            tools_called=["Read", "Write"],
            mcp_tools_called=["redmine"],
            validation_concerns=["minor"],
            errors=["warn1"],
            event_log=[
                {
                    "timestamp": "2024-01-01T00:00:00",
                    "kind": "tool_call",
                    "tool_name": "Read",
                    "summary": "read file",
                },
            ],
            trace_file="/traces/t.jsonl",
            retry_trace_file="/traces/r.jsonl",
            commit_sha="abc123",
            retry_count=1,
            created_at="2024-01-01T00:00:00Z",
        )
        orch = _make_orch(session)

        with patch("golem.orchestrator.ReportWriter") as mock_cls:
            mock_writer = MagicMock()
            mock_writer.detail_link.return_value = "[report](golem/42.md)"
            mock_cls.return_value = mock_writer
            orch._write_report()

        mock_writer.write_detail.assert_called_once()
        mock_writer.append_index.assert_called_once()
        detail_content = mock_writer.write_detail.call_args[0][1]
        assert "Fix bug" in detail_content
        assert "$1.00" in detail_content
        assert "PASS" in detail_content

    def test_report_exception_swallowed(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        orch = _make_orch(session)
        with patch("golem.orchestrator.ReportWriter", side_effect=RuntimeError("disk")):
            orch._write_report()

    def test_report_empty_fields(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        orch = _make_orch(session)
        with patch("golem.orchestrator.ReportWriter") as mock_cls:
            mock_writer = MagicMock()
            mock_writer.detail_link.return_value = "[r](x)"
            mock_cls.return_value = mock_writer
            orch._write_report()
        detail_content = mock_writer.write_detail.call_args[0][1]
        assert "none" in detail_content


class TestRecordRun:
    def test_records_completed_run(self):
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Fix",
            state=TaskSessionState.COMPLETED,
            total_cost_usd=1.0,
            duration_seconds=60.0,
            validation_verdict="PASS",
            commit_sha="abc",
            created_at="2024-01-01T00:00:00Z",
            trace_file="/t.jsonl",
        )
        tc = MagicMock()
        tc.task_model = "sonnet"
        orch = _make_orch(session, task_config=tc)

        with patch("golem.orchestrator.record_run") as m_record:
            orch._record_run()

        m_record.assert_called_once()
        rec = m_record.call_args[0][0]
        assert rec.flow == "golem"
        assert rec.success is True
        assert rec.cost_usd == 1.0

    def test_records_failed_run_with_error(self):
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Fix",
            state=TaskSessionState.FAILED,
            errors=["boom"],
        )
        tc = MagicMock()
        tc.task_model = "sonnet"
        orch = _make_orch(session, task_config=tc)

        with patch("golem.orchestrator.record_run") as m_record:
            orch._record_run()

        rec = m_record.call_args[0][0]
        assert rec.success is False
        assert rec.error == "boom"

    def test_record_exception_swallowed(self):
        session = TaskSession(parent_issue_id=42, parent_subject="Fix")
        tc = MagicMock()
        tc.task_model = "sonnet"
        orch = _make_orch(session, task_config=tc)
        with patch("golem.orchestrator.record_run", side_effect=OSError("nope")):
            orch._record_run()

    def test_records_no_errors(self):
        session = TaskSession(
            parent_issue_id=42,
            parent_subject="Fix",
            state=TaskSessionState.COMPLETED,
        )
        tc = MagicMock()
        tc.task_model = "sonnet"
        orch = _make_orch(session, task_config=tc)

        with patch("golem.orchestrator.record_run") as m_record:
            orch._record_run()

        rec = m_record.call_args[0][0]
        assert rec.error is None


class TestOnMilestone:
    def test_basic_milestone(self):
        session = TaskSession(parent_issue_id=1)
        orch = _make_orch(session)
        milestone = Milestone(kind="tool_call", tool_name="Read", summary="read file")
        ts = TrackerState(
            tools_called=["Read"],
            mcp_tools_called=[],
            errors=[],
            last_text="some text",
            milestone_count=3,
        )
        orch._on_milestone(milestone, ts)

        assert session.last_activity == "some text"
        assert session.milestone_count == 3
        assert "Read" in session.tools_called
        assert len(session.event_log) == 1
        assert session.event_log[0]["kind"] == "tool_call"

    def test_subtask_id_tagged(self):
        session = TaskSession(parent_issue_id=1, active_subtask_id=99)
        orch = _make_orch(session)
        milestone = Milestone(kind="tool_call")
        ts = TrackerState(milestone_count=1)
        orch._on_milestone(milestone, ts)

        assert session.event_log[0]["subtask_id"] == 99

    def test_no_subtask_id(self):
        session = TaskSession(parent_issue_id=1, active_subtask_id=0)
        orch = _make_orch(session)
        milestone = Milestone(kind="tool_call")
        ts = TrackerState(milestone_count=1)
        orch._on_milestone(milestone, ts)

        assert "subtask_id" not in session.event_log[0]

    def test_event_log_capped_at_500(self):
        session = TaskSession(parent_issue_id=1)
        session.event_log = [{"kind": "old"} for _ in range(500)]
        orch = _make_orch(session)
        milestone = Milestone(kind="new")
        ts = TrackerState(milestone_count=501)
        orch._on_milestone(milestone, ts)

        assert len(session.event_log) == 500
        assert session.event_log[-1]["kind"] == "new"

    def test_progress_callback_called(self):
        session = TaskSession(parent_issue_id=1)
        progress_cb = MagicMock()
        orch = _make_orch(session, on_progress=progress_cb)
        milestone = Milestone(kind="tool_call")
        ts = TrackerState(milestone_count=1)
        orch._on_milestone(milestone, ts)

        progress_cb.assert_called_once_with(session, milestone)

    def test_no_progress_callback(self):
        session = TaskSession(parent_issue_id=1)
        orch = _make_orch(session)
        milestone = Milestone(kind="tool_call")
        ts = TrackerState(milestone_count=1)
        orch._on_milestone(milestone, ts)

    def test_fallback_activity_summary(self):
        session = TaskSession(parent_issue_id=1)
        orch = _make_orch(session)
        milestone = Milestone(kind="tool_call", summary="the summary")
        ts = TrackerState(last_text="", milestone_count=1)
        orch._on_milestone(milestone, ts)
        assert session.last_activity == "the summary"

    def test_fallback_activity_kind(self):
        session = TaskSession(parent_issue_id=1)
        orch = _make_orch(session)
        milestone = Milestone(kind="result", summary="")
        ts = TrackerState(last_text="", milestone_count=1)
        orch._on_milestone(milestone, ts)
        assert session.last_activity == "result"


class TestSaveSessionsAtomicFailure:
    def test_cleanup_on_write_error(self, tmp_path):
        sessions = {1: TaskSession(parent_issue_id=1)}
        path = tmp_path / "sessions.json"

        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                save_sessions(sessions, path)

    def test_cleanup_on_fsync_error(self, tmp_path):
        sessions = {1: TaskSession(parent_issue_id=1)}
        path = tmp_path / "sessions.json"

        with patch("os.fsync", side_effect=OSError("io error")):
            with pytest.raises(OSError, match="io error"):
                save_sessions(sessions, path)

    def test_unlink_failure_suppressed(self, tmp_path):
        sessions = {1: TaskSession(parent_issue_id=1)}
        path = tmp_path / "sessions.json"

        with patch("os.replace", side_effect=OSError("disk full")), patch(
            "os.unlink", side_effect=OSError("unlink fail")
        ):
            with pytest.raises(OSError, match="disk full"):
                save_sessions(sessions, path)


class TestUpdateTask:
    def test_status_only(self):
        profile = MagicMock()
        orch = _make_orch(profile=profile)
        orch._update_task(1, status="in_progress")
        profile.state_backend.update_status.assert_called_once_with(1, "in_progress")
        profile.state_backend.update_progress.assert_not_called()
        profile.state_backend.post_comment.assert_not_called()

    def test_progress_only(self):
        profile = MagicMock()
        orch = _make_orch(profile=profile)
        orch._update_task(1, progress=50)
        profile.state_backend.update_progress.assert_called_once_with(1, 50)
        profile.state_backend.update_status.assert_not_called()

    def test_comment_only(self):
        profile = MagicMock()
        orch = _make_orch(profile=profile)
        orch._update_task(1, comment="note")
        profile.state_backend.post_comment.assert_called_once_with(1, "note")

    def test_all_fields(self):
        profile = MagicMock()
        orch = _make_orch(profile=profile)
        orch._update_task(1, status="fixed", progress=80, comment="done")
        profile.state_backend.update_status.assert_called_once()
        profile.state_backend.update_progress.assert_called_once()
        profile.state_backend.post_comment.assert_called_once()


class TestGetDescription:
    def test_delegates_to_profile(self):
        profile = MagicMock()
        profile.task_source.get_task_description.return_value = "desc"
        orch = _make_orch(profile=profile)
        assert orch._get_description(42) == "desc"


class TestGetMcpServers:
    def test_delegates_to_profile(self):
        profile = MagicMock()
        profile.tool_provider.servers_for_subject.return_value = ["s1"]
        orch = _make_orch(profile=profile)
        assert orch._get_mcp_servers("test") == ["s1"]


class TestFormatPrompt:
    def test_delegates_to_profile(self):
        profile = MagicMock()
        profile.prompt_provider.format.return_value = "formatted"
        orch = _make_orch(profile=profile)
        assert orch._format_prompt("tpl.txt", x=1) == "formatted"
