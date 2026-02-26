"""Tests for the golem supervisor/worker architecture."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from golem.core.config import (
    GolemFlowConfig,
    _parse_golem_config,
)


# -- SubtaskResult ----------------------------------------------------------


class TestSubtaskResult:
    def test_defaults(self):
        from golem.orchestrator import SubtaskResult

        r = SubtaskResult(issue_id=100, subject="Test subtask")
        assert r.issue_id == 100
        assert r.subject == "Test subtask"
        assert r.status == ""
        assert r.verdict == ""
        assert r.cost_usd == 0.0
        assert r.duration_seconds == 0.0
        assert r.summary == ""
        assert r.retry_count == 0

    def test_custom_values(self):
        from golem.orchestrator import SubtaskResult

        r = SubtaskResult(
            issue_id=200,
            subject="Fix parser",
            status="completed",
            verdict="PASS",
            cost_usd=1.50,
            duration_seconds=120.0,
            summary="Parser fixed",
            retry_count=1,
        )
        assert r.status == "completed"
        assert r.verdict == "PASS"
        assert r.cost_usd == 1.50
        assert r.retry_count == 1


# -- Supervisor config fields -----------------------------------------------


class TestSupervisorConfig:
    def test_supervisor_defaults(self):
        config = GolemFlowConfig()
        assert config.supervisor_mode is True
        assert config.subtask_model == ""
        assert config.subtask_budget_usd == 5.0
        assert config.subtask_timeout_seconds == 900
        assert config.decompose_model == ""
        assert config.decompose_budget_usd == 1.0
        assert config.summarize_model == "haiku"
        assert config.summarize_budget_usd == 0.50
        assert config.max_subtask_retries == 1

    def test_parse_supervisor_fields(self):
        data = {
            "supervisor_mode": False,
            "subtask_model": "opus",
            "subtask_budget_usd": 8.0,
            "subtask_timeout_seconds": 1200,
            "decompose_model": "haiku",
            "decompose_budget_usd": 2.0,
            "summarize_model": "sonnet",
            "summarize_budget_usd": 1.0,
            "max_subtask_retries": 2,
        }
        config = _parse_golem_config(data)
        assert config.supervisor_mode is False
        assert config.subtask_model == "opus"
        assert config.subtask_budget_usd == 8.0
        assert config.subtask_timeout_seconds == 1200
        assert config.decompose_model == "haiku"
        assert config.decompose_budget_usd == 2.0
        assert config.summarize_model == "sonnet"
        assert config.summarize_budget_usd == 1.0
        assert config.max_subtask_retries == 2


# -- TaskSession supervisor fields ------------------------------------------


class TestTaskSessionSupervisorFields:
    def test_new_fields_defaults(self):
        from golem.orchestrator import TaskSession

        s = TaskSession(parent_issue_id=100)
        assert not s.subtask_results
        assert s.execution_mode == ""

    def test_roundtrip_with_supervisor_fields(self):
        from golem.orchestrator import TaskSession, TaskSessionState

        s = TaskSession(
            parent_issue_id=300,
            state=TaskSessionState.COMPLETED,
            execution_mode="supervisor",
            subtask_results=[
                {"issue_id": 301, "subject": "Sub 1", "status": "completed"},
            ],
        )
        d = s.to_dict()
        assert d["execution_mode"] == "supervisor"
        assert len(d["subtask_results"]) == 1

        restored = TaskSession.from_dict(d)
        assert restored.execution_mode == "supervisor"
        assert restored.subtask_results[0]["issue_id"] == 301


# -- Supervisor engine -------------------------------------------------------


class TestTaskSupervisor:
    def test_build_sibling_status_empty(self):
        from golem.supervisor import TaskSupervisor
        from golem.orchestrator import TaskSession

        session = TaskSession(parent_issue_id=100)
        sup = TaskSupervisor(session, MagicMock(), GolemFlowConfig())
        result = sup._build_sibling_status([])
        assert "No prior subtasks" in result

    def test_build_sibling_status_with_results(self):
        from golem.supervisor import TaskSupervisor
        from golem.orchestrator import SubtaskResult, TaskSession

        session = TaskSession(parent_issue_id=100)
        sup = TaskSupervisor(session, MagicMock(), GolemFlowConfig())
        results = [
            SubtaskResult(
                issue_id=201,
                subject="Fix parser",
                status="completed",
                summary="Parser was fixed",
            ),
            SubtaskResult(
                issue_id=202,
                subject="Add tests",
                status="failed",
                summary="Tests could not be added",
            ),
        ]
        text = sup._build_sibling_status(results)
        assert "#201" in text
        assert "#202" in text
        assert "completed" in text
        assert "failed" in text


# -- Orchestrator supervisor branching ----------------------------------------


class TestOrchestratorBranching:
    def test_supervisor_mode_dispatches(self):
        """When supervisor_mode=True, _run_agent delegates to TaskSupervisor."""
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

        with patch("golem.supervisor.TaskSupervisor") as mock_sup_cls:
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


# -- Worktree merge on commit -----------------------------------------------


def _mock_profile():
    """Create a mock GolemProfile with null-like backends."""
    profile = MagicMock()
    profile.name = "test"
    profile.state_backend.update_status.return_value = True
    profile.state_backend.post_comment.return_value = True
    profile.state_backend.update_progress.return_value = True
    return profile


class TestCommitAndCompleteMerge:
    """Regression: worker-committed changes must be merged even when
    the supervisor's commit_changes finds nothing to commit."""

    def test_merge_called_when_no_supervisor_commit(self):
        """If commit_changes returns committed=False (no error), the worktree
        branch should still be merged — workers may have committed directly."""
        from unittest.mock import patch
        from golem.supervisor import TaskSupervisor
        from golem.orchestrator import TaskSession
        from golem.committer import CommitResult
        from golem.validation import ValidationVerdict

        session = TaskSession(parent_issue_id=500, parent_subject="[AGENT] Test")
        task_config = GolemFlowConfig(auto_commit=True)
        sup = TaskSupervisor(session, MagicMock(), task_config, profile=_mock_profile())
        sup._base_work_dir = "/repo"
        sup._worktree_path = "/repo/data/agent/worktrees/500"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="All good", task_type="feature"
        )

        with patch(
            "golem.supervisor.commit_changes",
            return_value=CommitResult(committed=False, message="No changes to commit"),
        ), patch(
            "golem.supervisor.merge_and_cleanup",
            return_value="abc1234",
        ) as mock_merge:
            sup._commit_and_complete(500, "/repo/data/agent/worktrees/500", verdict)

        mock_merge.assert_called_once_with(
            "/repo", 500, "/repo/data/agent/worktrees/500"
        )
        assert session.commit_sha == "abc1234"
        assert sup._worktree_path == ""

    def test_merge_called_after_supervisor_commit(self):
        """If commit_changes succeeds, the worktree should still be merged."""
        from unittest.mock import patch
        from golem.supervisor import TaskSupervisor
        from golem.orchestrator import TaskSession
        from golem.committer import CommitResult
        from golem.validation import ValidationVerdict

        session = TaskSession(parent_issue_id=501, parent_subject="[AGENT] Test")
        task_config = GolemFlowConfig(auto_commit=True)
        sup = TaskSupervisor(session, MagicMock(), task_config, profile=_mock_profile())
        sup._base_work_dir = "/repo"
        sup._worktree_path = "/repo/data/agent/worktrees/501"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.95, summary="All good", task_type="feature"
        )

        with patch(
            "golem.supervisor.commit_changes",
            return_value=CommitResult(committed=True, sha="def5678"),
        ), patch(
            "golem.supervisor.merge_and_cleanup",
            return_value="fff9999",
        ) as mock_merge:
            sup._commit_and_complete(501, "/repo/data/agent/worktrees/501", verdict)

        mock_merge.assert_called_once()
        assert session.commit_sha == "fff9999"

    def test_no_merge_without_worktree(self):
        """When there's no worktree, merge_and_cleanup should not be called."""
        from unittest.mock import patch
        from golem.supervisor import TaskSupervisor
        from golem.orchestrator import TaskSession
        from golem.committer import CommitResult
        from golem.validation import ValidationVerdict

        session = TaskSession(parent_issue_id=502, parent_subject="[AGENT] Test")
        task_config = GolemFlowConfig(auto_commit=True)
        sup = TaskSupervisor(session, MagicMock(), task_config, profile=_mock_profile())
        sup._base_work_dir = "/repo"
        sup._worktree_path = ""  # No worktree

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="All good", task_type="feature"
        )

        with patch(
            "golem.supervisor.commit_changes",
            return_value=CommitResult(committed=True, sha="abc1234"),
        ), patch(
            "golem.supervisor.merge_and_cleanup",
        ) as mock_merge:
            sup._commit_and_complete(502, "/repo", verdict)

        mock_merge.assert_not_called()
        assert session.commit_sha == "abc1234"

    def test_commit_error_does_not_merge(self):
        """When commit fails with an error, merge should NOT be attempted."""
        from unittest.mock import patch
        from golem.supervisor import TaskSupervisor
        from golem.orchestrator import TaskSession, TaskSessionState
        from golem.committer import CommitResult
        from golem.validation import ValidationVerdict

        session = TaskSession(parent_issue_id=503, parent_subject="[AGENT] Test")
        task_config = GolemFlowConfig(auto_commit=True)
        sup = TaskSupervisor(session, MagicMock(), task_config, profile=_mock_profile())
        sup._base_work_dir = "/repo"
        sup._worktree_path = "/repo/data/agent/worktrees/503"

        verdict = ValidationVerdict(
            verdict="PASS", confidence=0.9, summary="All good", task_type="feature"
        )

        with patch(
            "golem.supervisor.commit_changes",
            return_value=CommitResult(committed=False, error="pre-commit hook failed"),
        ), patch(
            "golem.supervisor.merge_and_cleanup",
        ) as mock_merge:
            sup._commit_and_complete(503, "/repo/data/agent/worktrees/503", verdict)

        mock_merge.assert_not_called()
        assert session.state == TaskSessionState.FAILED


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

        # First milestone at t=0 should checkpoint
        orch._last_checkpoint_time = 0.0
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

        # Simulate last checkpoint was 15 seconds ago
        import time

        orch._last_checkpoint_time = time.time() - 15.0
        orch._on_milestone(self._make_milestone(), self._make_tracker_state())
        save.assert_called_once()

    def test_no_save_callback_no_error(self):
        orch = self._make_orchestrator(save_callback=None)
        # Should not raise
        orch._on_milestone(self._make_milestone(), self._make_tracker_state())

    def test_save_callback_exception_swallowed(self):
        save = MagicMock(side_effect=OSError("disk full"))
        orch = self._make_orchestrator(save_callback=save)
        # Should not raise
        orch._on_milestone(self._make_milestone(), self._make_tracker_state())
