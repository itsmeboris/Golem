# pylint: disable=too-few-public-methods
"""Tests for golem.flow cross-task coordination — infra retry, deps, merge, batch."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from golem.core.config import Config, GolemFlowConfig
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


class TestInfraRetryInRunSession:
    async def test_infra_error_retries_then_succeeds(self, monkeypatch, tmp_path):
        from golem.errors import InfrastructureError

        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        session = TaskSession(
            parent_issue_id=9900,
            parent_subject="infra retry",
            state=TaskSessionState.DETECTED,
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[9900] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        call_count = 0

        async def sometimes_infra(self_orch):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise InfrastructureError("worktree gone")
            self_orch.session.state = TaskSessionState.COMPLETED

        monkeypatch.setattr(TaskOrchestrator, "tick", sometimes_infra)

        await flow._run_session(9900)
        assert call_count == 2
        assert session.infra_retry_count == 1
        assert session.state == TaskSessionState.COMPLETED

    async def test_infra_error_exhausted_crashes(self, monkeypatch, tmp_path):
        from golem.errors import InfrastructureError

        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True
        flow._max_infra_retries = 1

        session = TaskSession(
            parent_issue_id=9901,
            parent_subject="infra exhaust",
            state=TaskSessionState.DETECTED,
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[9901] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        async def always_infra(self_orch):
            raise InfrastructureError("stuck")

        monkeypatch.setattr(TaskOrchestrator, "tick", always_infra)

        transitions = []
        monkeypatch.setattr(
            flow,
            "_handle_state_transition",
            lambda s, prev: transitions.append((s.state, prev)),
        )

        await flow._run_session(9901)
        assert session.state == TaskSessionState.FAILED
        assert session.infra_retry_count == 1


class TestDependencyWaiting:
    async def test_waits_for_dependencies(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path, tick_interval=0)
        flow._running = True

        dep_session = TaskSession(
            parent_issue_id=100,
            parent_subject="dep",
            state=TaskSessionState.RUNNING,
        )
        flow._sessions[100] = dep_session

        session = TaskSession(
            parent_issue_id=101,
            parent_subject="waiter",
            state=TaskSessionState.DETECTED,
            depends_on=[100],
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[101] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        tick_count = 0

        async def completing_tick(self_orch):
            nonlocal tick_count
            tick_count += 1
            self_orch.session.state = TaskSessionState.COMPLETED

        monkeypatch.setattr(TaskOrchestrator, "tick", completing_tick)

        async def complete_dep():
            await asyncio.sleep(0.01)
            dep_session.state = TaskSessionState.COMPLETED

        asyncio.create_task(complete_dep())

        await flow._run_session(101)
        assert tick_count >= 1
        assert session.state == TaskSessionState.COMPLETED

    async def test_deps_none_skipped(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path, tick_interval=0)
        flow._running = True

        session = TaskSession(
            parent_issue_id=102,
            parent_subject="dep on unknown",
            state=TaskSessionState.DETECTED,
            depends_on=[999],
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[102] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        async def completing_tick(self_orch):
            self_orch.session.state = TaskSessionState.COMPLETED

        monkeypatch.setattr(TaskOrchestrator, "tick", completing_tick)

        await flow._run_session(102)
        assert session.state == TaskSessionState.COMPLETED

    async def test_dep_failed_raises(self, monkeypatch, tmp_path):
        from golem.errors import TaskExecutionError

        flow = _make_flow(monkeypatch, tmp_path, tick_interval=0)
        flow._running = True

        dep_session = TaskSession(
            parent_issue_id=150,
            parent_subject="broken dep",
            state=TaskSessionState.FAILED,
        )
        flow._sessions[150] = dep_session

        session = TaskSession(
            parent_issue_id=151,
            parent_subject="depends on broken",
            depends_on=[150],
        )

        import pytest as _pt

        with _pt.raises(TaskExecutionError, match="Dependency #150"):
            await flow._wait_for_dependencies(session)

    async def test_dep_failed_session_transitions_to_failed(
        self, monkeypatch, tmp_path
    ):
        flow = _make_flow(monkeypatch, tmp_path, tick_interval=0)
        flow._running = True

        dep_session = TaskSession(
            parent_issue_id=160,
            parent_subject="failing dep",
            state=TaskSessionState.FAILED,
        )
        flow._sessions[160] = dep_session

        session = TaskSession(
            parent_issue_id=161,
            parent_subject="blocked by failure",
            state=TaskSessionState.DETECTED,
            depends_on=[160],
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[161] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        await flow._run_session(161)
        assert session.state == TaskSessionState.FAILED
        assert any("Dependency #160" in e for e in session.errors)

    async def test_deps_not_done_waits(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path, tick_interval=0)
        flow._running = True

        dep_session = TaskSession(
            parent_issue_id=200,
            parent_subject="dep",
            state=TaskSessionState.RUNNING,
        )
        flow._sessions[200] = dep_session

        session = TaskSession(
            parent_issue_id=201,
            parent_subject="blocked",
            depends_on=[200],
        )

        wait_count = 0
        orig_wait = flow._wait_for_dependencies

        async def counting_wait(s):
            nonlocal wait_count
            wait_count += 1
            dep_session.state = TaskSessionState.COMPLETED
            await orig_wait(s)

        monkeypatch.setattr(flow, "_wait_for_dependencies", counting_wait)

        await flow._wait_for_dependencies(session)
        assert wait_count >= 1


class TestEnqueueForMerge:
    async def test_enqueues_and_processes(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=300,
            parent_subject="merge me",
            state=TaskSessionState.COMPLETED,
            merge_ready=True,
            worktree_path="/wt/300",
            base_work_dir="/repo",
        )
        flow._sessions[300] = session

        from golem.merge_queue import MergeResult

        mock_queue = MagicMock()
        mock_queue.enqueue = AsyncMock()
        mock_queue.process_all = AsyncMock(
            return_value=[MergeResult(session_id=300, success=True, merge_sha="abc")]
        )
        flow._merge_queue = mock_queue

        await flow._enqueue_for_merge(session)
        assert session.commit_sha == "abc"
        assert session.merge_ready is False


class TestApplyMergeResult:
    def test_success(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        session = TaskSession(parent_issue_id=400, parent_subject="m")
        flow._sessions[400] = session

        from golem.merge_queue import MergeResult

        flow._apply_merge_result(
            MergeResult(session_id=400, success=True, merge_sha="xyz")
        )
        assert session.commit_sha == "xyz"
        assert session.merge_ready is False

    def test_failure(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        session = TaskSession(parent_issue_id=401, parent_subject="m")
        session.merge_ready = True
        flow._sessions[401] = session

        from golem.merge_queue import MergeResult

        flow._apply_merge_result(
            MergeResult(session_id=401, success=False, error="conflict")
        )
        assert "merge failed: conflict" in session.errors
        assert session.merge_ready is False

    def test_unknown_session(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        from golem.merge_queue import MergeResult

        flow._apply_merge_result(
            MergeResult(session_id=999, success=True, merge_sha="a")
        )


class TestMergeReadyInRunSession:
    async def test_merge_ready_session_enqueued(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        flow._running = True

        session = TaskSession(
            parent_issue_id=500,
            parent_subject="merge test",
            state=TaskSessionState.DETECTED,
            grace_deadline=(
                datetime.now(timezone.utc) - timedelta(seconds=10)
            ).isoformat(),
        )
        flow._sessions[500] = session

        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        from golem.orchestrator import TaskOrchestrator

        async def completing_tick(self_orch):
            self_orch.session.state = TaskSessionState.COMPLETED
            self_orch.session.merge_ready = True

        monkeypatch.setattr(TaskOrchestrator, "tick", completing_tick)

        enqueued = []

        async def fake_enqueue(s):
            enqueued.append(s.parent_issue_id)

        monkeypatch.setattr(flow, "_enqueue_for_merge", fake_enqueue)

        await flow._run_session(500)
        assert 500 in enqueued


class TestSubmitBatch:
    def test_creates_batch_with_deps(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        tasks = [
            {"prompt": "task A", "subject": "A"},
            {"prompt": "task B", "subject": "B", "depends_on": [0]},
        ]
        result = flow.submit_batch(tasks, group_id="grp-1")

        assert result["group_id"] == "grp-1"
        assert len(result["tasks"]) == 2

        t0_id = result["tasks"][0]["task_id"]
        t1_id = result["tasks"][1]["task_id"]

        assert flow._sessions[t0_id].group_id == "grp-1"
        assert flow._sessions[t1_id].group_id == "grp-1"
        assert flow._sessions[t1_id].depends_on == [t0_id]

    def test_auto_group_id(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch([{"prompt": "solo"}])
        assert result["group_id"].startswith("batch-")

    def test_creates_batch_with_key_deps(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        tasks = [
            {"prompt": "task A", "subject": "A", "key": "task-a"},
            {"prompt": "task B", "subject": "B", "depends_on": ["task-a"]},
        ]
        result = flow.submit_batch(tasks, group_id="grp-key")

        t0_id = result["tasks"][0]["task_id"]
        t1_id = result["tasks"][1]["task_id"]

        assert flow._sessions[t1_id].depends_on == [t0_id]

    def test_mixed_key_and_index_deps(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        tasks = [
            {"prompt": "task A", "subject": "A", "key": "first"},
            {"prompt": "task B", "subject": "B"},
            {"prompt": "task C", "subject": "C", "depends_on": ["first", 1]},
        ]
        result = flow.submit_batch(tasks, group_id="grp-mix")

        t0_id = result["tasks"][0]["task_id"]
        t1_id = result["tasks"][1]["task_id"]
        t2_id = result["tasks"][2]["task_id"]

        assert flow._sessions[t2_id].depends_on == [t0_id, t1_id]

    def test_external_dep_id(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch([{"prompt": "x", "depends_on": [42]}])
        t_id = result["tasks"][0]["task_id"]
        assert flow._sessions[t_id].depends_on == [42]


class TestIntegrationValidation:
    async def test_no_sessions_passes(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        verdict = await flow.run_integration_validation("nonexistent", "/work")
        assert verdict.verdict == "PASS"

    async def test_runs_validation(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        s1 = TaskSession(parent_issue_id=601, parent_subject="task 1", group_id="grp")
        s2 = TaskSession(parent_issue_id=602, parent_subject="task 2", group_id="grp")
        flow._sessions[601] = s1
        flow._sessions[602] = s2

        from golem.validation import ValidationVerdict

        with patch(
            "golem.validation.run_validation",
            return_value=ValidationVerdict(
                verdict="PASS", confidence=0.9, summary="all good"
            ),
        ):
            verdict = await flow.run_integration_validation("grp", "/work")
        assert verdict.verdict == "PASS"

    async def test_failing_validation_logs_warning(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        s1 = TaskSession(parent_issue_id=701, parent_subject="task 1", group_id="grp2")
        flow._sessions[701] = s1

        from golem.validation import ValidationVerdict

        with patch(
            "golem.validation.run_validation",
            return_value=ValidationVerdict(
                verdict="FAIL", confidence=0.2, summary="lint errors"
            ),
        ):
            verdict = await flow.run_integration_validation("grp2", "/work")
        assert verdict.verdict == "FAIL"
