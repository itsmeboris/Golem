"""Tests for the golem supervisor dispatch and related session/config fields."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from golem.core.config import (
    GolemFlowConfig,
    _parse_golem_config,
)


# -- Orchestration config fields --------------------------------------------


class TestOrchestrationConfig:
    def test_orchestration_defaults(self):
        config = GolemFlowConfig()
        assert config.supervisor_mode is True
        assert config.orchestrate_budget_usd == 15.0
        assert config.orchestrate_timeout_seconds == 3600
        assert config.orchestrate_model == "opus"
        assert config.inner_retry_max == 3
        assert config.resume_on_partial is True

    def test_parse_orchestration_fields(self):
        data = {
            "supervisor_mode": False,
            "orchestrate_budget_usd": 20.0,
            "orchestrate_timeout_seconds": 3000,
            "orchestrate_model": "opus",
            "inner_retry_max": 5,
            "resume_on_partial": False,
        }
        config = _parse_golem_config(data)
        assert config.supervisor_mode is False
        assert config.orchestrate_budget_usd == 20.0
        assert config.orchestrate_timeout_seconds == 3000
        assert config.orchestrate_model == "opus"
        assert config.inner_retry_max == 5
        assert config.resume_on_partial is False


# -- TaskSession fields -----------------------------------------------------


class TestTaskSessionFields:
    def test_new_fields_defaults(self):
        from golem.orchestrator import TaskSession

        s = TaskSession(parent_issue_id=100)
        assert s.execution_mode == ""
        assert s.cli_session_id == ""

    def test_roundtrip_with_session_fields(self):
        from golem.orchestrator import TaskSession, TaskSessionState

        s = TaskSession(
            parent_issue_id=300,
            state=TaskSessionState.COMPLETED,
            execution_mode="subagent",
            cli_session_id="sess-abc-123",
        )
        d = s.to_dict()
        assert d["execution_mode"] == "subagent"
        assert d["cli_session_id"] == "sess-abc-123"

        restored = TaskSession.from_dict(d)
        assert restored.execution_mode == "subagent"
        assert restored.cli_session_id == "sess-abc-123"


# -- Orchestrator dispatch --------------------------------------------------


class TestOrchestratorBranching:
    def test_supervisor_mode_dispatches_to_subagent(self):
        """When supervisor_mode=True, _run_agent delegates to SubagentSupervisor."""
        from golem.orchestrator import (
            TaskOrchestrator,
            TaskSession,
            TaskSessionState,
        )
        from unittest.mock import AsyncMock, patch

        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        session = TaskSession(
            parent_issue_id=100,
            parent_subject="[AGENT] Test task",
            state=TaskSessionState.DETECTED,
            grace_deadline=past,
            budget_usd=10.0,
        )

        config = MagicMock()
        task_config = GolemFlowConfig(supervisor_mode=True)
        orch = TaskOrchestrator(session, config, task_config)

        import asyncio

        with patch("golem.supervisor_v2_subagent.SubagentSupervisor") as mock_sup_cls:
            mock_sup_cls.return_value.run = AsyncMock()
            asyncio.run(orch._run_agent())

        mock_sup_cls.assert_called_once()
        mock_sup_cls.return_value.run.assert_awaited_once()

    def test_monolithic_mode_skips_supervisor(self):
        """When supervisor_mode=False, _run_agent uses monolithic pipeline."""
        from golem.orchestrator import (
            TaskOrchestrator,
            TaskSession,
            TaskSessionState,
        )
        from unittest.mock import AsyncMock, patch

        session = TaskSession(
            parent_issue_id=100,
            parent_subject="[AGENT] Test task",
            state=TaskSessionState.RUNNING,
        )

        config = MagicMock()
        task_config = GolemFlowConfig(supervisor_mode=False)
        orch = TaskOrchestrator(session, config, task_config)

        import asyncio

        with patch.object(orch, "_run_agent_monolithic", new=AsyncMock()) as mock_mono:
            asyncio.run(orch._run_agent())

        mock_mono.assert_awaited_once()


# -- Throttled checkpoint in _on_milestone -----------------------------------


class TestThrottledCheckpoint:
    """Milestones should persist the session to disk at a throttled rate
    so the dashboard shows near-real-time progress."""

    def _make_orchestrator(self, save_callback=None):
        from golem.orchestrator import TaskOrchestrator, TaskSession

        session = TaskSession(parent_issue_id=100, parent_subject="[AGENT] Test")
        orch = TaskOrchestrator(
            session,
            MagicMock(),
            GolemFlowConfig(),
            save_callback=save_callback,
        )
        return orch

    def _make_milestone(self, ts=0.0):
        from golem.event_tracker import Milestone

        return Milestone(
            kind="tool_call", tool_name="Read", summary="Read file", timestamp=ts
        )

    def _make_tracker_state(self):
        from golem.event_tracker import TrackerState

        return TrackerState(milestone_count=1)

    def test_first_milestone_triggers_checkpoint(self):
        save = MagicMock()
        orch = self._make_orchestrator(save_callback=save)
        orch._on_milestone(self._make_milestone(), self._make_tracker_state())
        save.assert_called_once()

    def test_rapid_milestones_throttled(self):
        save = MagicMock()
        orch = self._make_orchestrator(save_callback=save)
        orch._checkpoint_interval = 10.0

        import time

        orch._last_checkpoint_time = time.time()
        save.reset_mock()

        # Immediate second milestone should NOT checkpoint (within interval)
        orch._on_milestone(self._make_milestone(), self._make_tracker_state())
        save.assert_not_called()

    def test_milestone_after_interval_checkpoints(self):
        save = MagicMock()
        orch = self._make_orchestrator(save_callback=save)
        orch._checkpoint_interval = 10.0

        import time

        orch._last_checkpoint_time = time.time() - 15.0
        orch._on_milestone(self._make_milestone(), self._make_tracker_state())
        save.assert_called_once()

    def test_no_save_callback_no_error(self):
        orch = self._make_orchestrator(save_callback=None)
        orch._on_milestone(self._make_milestone(), self._make_tracker_state())

    def test_save_callback_exception_swallowed(self):
        save = MagicMock(side_effect=OSError("disk full"))
        orch = self._make_orchestrator(save_callback=save)
        orch._on_milestone(self._make_milestone(), self._make_tracker_state())
