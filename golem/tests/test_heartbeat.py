"""Tests for golem.heartbeat — HeartbeatManager state, budget, scheduling."""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.heartbeat import HeartbeatManager, _coerce_task_id
from golem.core.config import GolemFlowConfig
from golem.orchestrator import TaskSessionState
from golem.types import LiveSnapshotDict


def _make_config(**overrides) -> GolemFlowConfig:
    defaults = dict(
        profile="github",
        projects=["test/repo"],
        heartbeat_enabled=True,
        heartbeat_interval_seconds=60,
        heartbeat_idle_threshold_seconds=120,
        heartbeat_daily_budget_usd=1.0,
        heartbeat_max_inflight=1,
        heartbeat_candidate_limit=5,
        heartbeat_dedup_ttl_days=30,
    )
    defaults.update(overrides)
    return GolemFlowConfig(**defaults)


def _make_manager(tmp_path, **config_overrides) -> HeartbeatManager:
    cfg = _make_config(**config_overrides)
    return HeartbeatManager(cfg, state_dir=tmp_path)


def _make_snapshot(**overrides) -> LiveSnapshotDict:
    """Build a complete ``LiveSnapshotDict`` with sensible defaults."""
    base: LiveSnapshotDict = {
        "uptime_s": 0.0,
        "active_tasks": [],
        "active_count": 0,
        "queue_depth": 0,
        "queued_event_ids": [],
        "models_active": {},
        "recently_completed": [],
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


class TestStatePersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._daily_spend_usd = 0.05
        mgr._inflight_task_ids = [123456]
        mgr._repo_index = 2
        mgr.save_state()

        mgr2 = _make_manager(tmp_path)
        mgr2.load_state()
        assert mgr2._daily_spend_usd == 0.05
        assert mgr2._inflight_task_ids == [123456]
        assert mgr2._repo_index == 2

    def test_load_state_missing_file(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.load_state()  # should not raise
        assert mgr._daily_spend_usd == 0.0

    def test_state_file_location(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.save_state()
        assert (tmp_path / "heartbeat_state.json").exists()

    def test_load_state_corrupt_json(self, tmp_path):
        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text("not valid json {{{", encoding="utf-8")
        mgr = _make_manager(tmp_path)
        mgr.load_state()  # should not raise
        assert mgr._daily_spend_usd == 0.0

    def test_load_state_preserves_repo_index(self, tmp_path):
        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text(json.dumps({"repo_index": 5}), encoding="utf-8")
        mgr = _make_manager(tmp_path)
        mgr.load_state()
        assert mgr._repo_index == 5

    def test_load_state_missing_fields_default(self, tmp_path):
        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text(json.dumps({}), encoding="utf-8")
        mgr = _make_manager(tmp_path)
        mgr.load_state()
        assert mgr._daily_spend_usd == 0.0
        assert mgr._inflight_task_ids == []
        assert mgr._repo_index == 0


class TestBudgetTracking:
    def test_budget_allows_when_under_limit(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_daily_budget_usd=1.0)
        mgr._daily_spend_usd = 0.5
        assert mgr.budget_allows() is True

    def test_budget_blocks_when_at_limit(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_daily_budget_usd=1.0)
        mgr._daily_spend_usd = 1.0
        assert mgr.budget_allows() is False

    def test_budget_blocks_when_over_limit(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_daily_budget_usd=1.0)
        mgr._daily_spend_usd = 1.5
        assert mgr.budget_allows() is False

    def test_record_spend_accumulates(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.record_spend(0.02)
        mgr.record_spend(0.03)
        assert mgr._daily_spend_usd == pytest.approx(0.05)

    def test_budget_resets_after_24h(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._daily_spend_usd = 0.99
        mgr._daily_spend_reset_at = time.time() - 86401  # >24h ago
        mgr._maybe_reset_budget()
        assert mgr._daily_spend_usd == 0.0


class TestInflightTracking:
    def test_can_submit_when_under_max(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=1)
        assert mgr.can_submit() is True

    def test_cannot_submit_when_at_max(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=1)
        mgr._inflight_task_ids = [123]
        assert mgr.can_submit() is False

    def test_on_task_completed_removes_id(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [123]
        mgr.on_task_completed(123, success=True)
        assert 123 not in mgr._inflight_task_ids

    def test_on_task_completed_ignores_unknown_id(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [123]
        mgr.on_task_completed(999, success=True)  # should not raise
        assert mgr._inflight_task_ids == [123]

    def test_reconcile_removes_stale_ids(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [100, 200, 300]
        active_session_ids = {200}  # only 200 still exists
        mgr.reconcile_inflight(active_session_ids)
        assert mgr._inflight_task_ids == [200]

    def test_can_submit_filters_terminal_sessions(self, tmp_path):
        """A RUNNING session counts; a COMPLETED session does not."""
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=2)
        mgr._inflight_task_ids = [10, 20]
        mock_flow = MagicMock()
        running_session = MagicMock()
        running_session.state = TaskSessionState.RUNNING
        completed_session = MagicMock()
        completed_session.state = TaskSessionState.COMPLETED
        mock_flow.get_session.side_effect = lambda tid: (
            running_session if tid == 10 else completed_session
        )
        mgr._flow = mock_flow
        # 1 active, limit is 2 -- should be able to submit
        assert mgr.can_submit() is True

    def test_can_submit_filters_missing_sessions(self, tmp_path):
        """An ID whose session no longer exists must not count toward the limit."""
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=1)
        mgr._inflight_task_ids = [55]
        mock_flow = MagicMock()
        mock_flow.get_session.return_value = None  # session is gone
        mgr._flow = mock_flow
        # 0 active, limit is 1 -- should be able to submit
        assert mgr.can_submit() is True

    def test_can_submit_removes_stale_ids_as_side_effect(self, tmp_path):
        """Calling can_submit() must clean up terminal/missing IDs from _inflight_task_ids."""
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=3)
        mgr._inflight_task_ids = [1, 2, 3]
        mock_flow = MagicMock()
        running_session = MagicMock()
        running_session.state = TaskSessionState.RUNNING
        terminal_session = MagicMock()
        terminal_session.state = TaskSessionState.COMPLETED
        mock_flow.get_session.side_effect = lambda tid: (
            running_session if tid == 1 else terminal_session if tid == 2 else None
        )
        mgr._flow = mock_flow
        mgr.can_submit()
        # Only the RUNNING session (id=1) should remain
        assert mgr._inflight_task_ids == [1]

    def test_can_submit_no_flow_fallback(self, tmp_path):
        """Before start() is called (_flow is None), count all inflight IDs."""
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=1)
        mgr._inflight_task_ids = [99]
        assert mgr._flow is None
        assert mgr.can_submit() is False

    @pytest.mark.parametrize(
        "terminal_state",
        [
            TaskSessionState.COMPLETED,
            TaskSessionState.FAILED,
            TaskSessionState.HUMAN_REVIEW,
        ],
    )
    def test_can_submit_filters_all_terminal_states(self, tmp_path, terminal_state):
        """COMPLETED, FAILED, and HUMAN_REVIEW sessions must not count toward the limit."""
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=1)
        mgr._inflight_task_ids = [77]
        mock_flow = MagicMock()
        terminal_session = MagicMock()
        terminal_session.state = terminal_state
        mock_flow.get_session.return_value = terminal_session
        mgr._flow = mock_flow
        # 0 active (terminal doesn't count), limit is 1 -- can submit
        assert mgr.can_submit() is True


class TestSnapshot:
    def test_snapshot_returns_dict(self, tmp_path):
        mgr = _make_manager(tmp_path)
        snap = mgr.snapshot()
        assert snap["enabled"] is True
        assert snap["state"] == "idle"
        assert snap["daily_spend_usd"] == 0.0
        assert snap["daily_budget_usd"] == 1.0
        assert snap["inflight_task_ids"] == []
        assert snap["candidate_count"] == 0
        assert snap["dedup_entry_count"] == 0

    def test_snapshot_reflects_state_changes(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._daily_spend_usd = 0.42
        mgr._inflight_task_ids = [111]
        snap = mgr.snapshot()
        assert snap["daily_spend_usd"] == 0.42
        assert snap["inflight_task_ids"] == [111]


class TestIdleDetection:
    def test_is_idle_true_when_no_active_tasks(self, tmp_path):
        mgr = _make_manager(tmp_path)
        snapshot = _make_snapshot(active_count=0, queue_depth=0)
        assert mgr.is_idle(snapshot) is True

    def test_is_idle_false_when_active_tasks(self, tmp_path):
        mgr = _make_manager(tmp_path)
        snapshot = _make_snapshot(active_count=2, queue_depth=0)
        assert mgr.is_idle(snapshot) is False

    def test_is_idle_true_when_only_heartbeat_tasks(self, tmp_path):
        """Idle means no external tasks. Heartbeat-own tasks don't count."""
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [111, 222]
        snapshot = _make_snapshot(active_count=2, queue_depth=0)
        assert mgr.is_idle(snapshot) is True  # 2 active == 2 heartbeat


class TestExternalTaskDetection:
    def test_has_external_tasks_true(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [111]
        snapshot = _make_snapshot(active_count=3)  # 3 active, 1 heartbeat = 2 external
        assert mgr.has_external_tasks(snapshot) is True

    def test_has_external_tasks_false_when_all_heartbeat(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [111]
        snapshot = _make_snapshot(active_count=1)
        assert mgr.has_external_tasks(snapshot) is False

    def test_has_external_tasks_false_when_empty(self, tmp_path):
        mgr = _make_manager(tmp_path)
        snapshot = _make_snapshot(active_count=0)
        assert mgr.has_external_tasks(snapshot) is False


class TestAsyncLoop:
    async def test_start_and_stop(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=1)
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(
            active_count=5
        )  # not idle

        mgr.start(mock_flow)
        assert mgr._loop_task is not None
        await asyncio.sleep(0.1)
        mgr.stop()
        assert mgr._state == "idle"

    async def test_stop_persists_state(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=1)
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=5)
        mgr._daily_spend_usd = 0.42
        mgr.start(mock_flow)
        await asyncio.sleep(0.1)
        mgr.stop()
        assert (tmp_path / "heartbeat_state.json").exists()
        data = json.loads((tmp_path / "heartbeat_state.json").read_text())
        assert data["daily_spend_usd"] == 0.42

    async def test_stop_when_not_started(self, tmp_path):
        """Stopping without starting should not raise."""
        mgr = _make_manager(tmp_path)
        mgr.stop()  # Should not raise
        assert mgr._state == "idle"

    async def test_loop_handles_exception(self, tmp_path):
        """Loop continues after non-CancelledError exceptions."""
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=0)
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.side_effect = RuntimeError("test error")
        mgr.start(mock_flow)
        await asyncio.sleep(0.05)
        # Loop should still be running
        assert mgr._loop_task is not None
        assert not mgr._loop_task.done()
        mgr.stop()


class TestHeartbeatLoopBranches:
    """Cover the remaining branches in _heartbeat_loop."""

    async def test_loop_budget_exhausted_state(self, tmp_path):
        """When budget is exhausted, loop sets state to budget_exhausted."""
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=0)
        mgr._daily_spend_usd = 999.0  # over budget
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr.start(mock_flow)
        await asyncio.sleep(0.05)
        assert mgr._state == "budget_exhausted"
        mgr.stop()

    async def test_loop_idle_below_threshold(self, tmp_path):
        """When idle but below threshold, loop sets state to idle."""
        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_idle_threshold_seconds=9999,
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr.start(mock_flow)
        await asyncio.sleep(0.05)
        assert mgr._state == "idle"
        mgr.stop()

    async def test_loop_idle_triggers_tick(self, tmp_path):
        """When idle above threshold, loop triggers heartbeat tick."""
        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_idle_threshold_seconds=0,
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mock_flow.submit_task = AsyncMock(return_value=None)

        tick_called = asyncio.Event()

        async def _tick_signal():
            tick_called.set()

        with patch.object(mgr, "_run_heartbeat_tick", new=_tick_signal):
            mgr.start(mock_flow)
            try:
                await asyncio.wait_for(tick_called.wait(), timeout=2.0)
            finally:
                mgr.stop()

        assert tick_called.is_set(), "heartbeat tick was never called"

    async def test_loop_cancelled_error_breaks(self, tmp_path):
        """CancelledError in loop breaks cleanly."""
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=0)
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.side_effect = asyncio.CancelledError()
        mgr.start(mock_flow)
        await asyncio.sleep(0.05)
        # Task should have completed (broken out of loop)
        assert mgr._loop_task.done()
        mgr.stop()


class TestHeartbeatLoopGuards:
    """Tests for max-ticks and max-duration loop exit guards."""

    async def test_loop_exits_after_max_ticks(self, tmp_path):
        """_heartbeat_loop exits after heartbeat_max_ticks iterations."""
        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_max_ticks=2,
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr._daily_spend_usd = 999.0  # stay in budget_exhausted branch (fast path)

        mgr.start(mock_flow)
        loop_task = mgr._loop_task
        try:
            await asyncio.wait_for(loop_task, timeout=5.0)
        finally:
            mgr.stop()

        # wait_for completed without TimeoutError -> loop exited on its own
        assert loop_task.done()

    async def test_loop_exits_after_max_ticks_logs_message(self, tmp_path, caplog):
        """_heartbeat_loop logs exit message when stopping due to max_ticks."""
        import logging

        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_max_ticks=1,
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr._daily_spend_usd = 999.0  # fast budget_exhausted path

        with caplog.at_level(logging.INFO, logger="golem.heartbeat"):
            mgr.start(mock_flow)
            try:
                await asyncio.wait_for(mgr._loop_task, timeout=5.0)
            finally:
                mgr.stop()

        assert any(
            "max_ticks" in r.message or "ticks" in r.message for r in caplog.records
        )

    async def test_loop_exits_after_max_duration(self, tmp_path):
        """_heartbeat_loop exits after heartbeat_max_duration_seconds elapsed."""
        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_max_duration_seconds=1,
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr._daily_spend_usd = 999.0  # fast budget_exhausted path

        mgr.start(mock_flow)
        loop_task = mgr._loop_task
        try:
            await asyncio.wait_for(loop_task, timeout=10.0)
        finally:
            mgr.stop()

        # wait_for completed without TimeoutError -> loop exited on its own
        assert loop_task.done()

    async def test_loop_exits_after_max_duration_logs_message(self, tmp_path, caplog):
        """_heartbeat_loop logs exit message when stopping due to max_duration."""
        import logging

        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_max_duration_seconds=1,
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr._daily_spend_usd = 999.0

        with caplog.at_level(logging.INFO, logger="golem.heartbeat"):
            mgr.start(mock_flow)
            try:
                await asyncio.wait_for(mgr._loop_task, timeout=10.0)
            finally:
                mgr.stop()

        assert any(
            "max_duration" in r.message or "duration" in r.message
            for r in caplog.records
        )

    async def test_zero_max_ticks_means_unlimited(self, tmp_path):
        """heartbeat_max_ticks=0 (default) means unlimited -- loop does not exit."""
        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_max_ticks=0,  # unlimited
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr._daily_spend_usd = 999.0  # fast budget_exhausted path

        threshold_reached = asyncio.Event()

        def counting_snapshot():
            counting_snapshot.n += 1
            if counting_snapshot.n >= 3:
                threshold_reached.set()
            return _make_snapshot(active_count=0)

        counting_snapshot.n = 0
        mock_flow.live.snapshot.side_effect = counting_snapshot

        mgr.start(mock_flow)
        try:
            await asyncio.wait_for(threshold_reached.wait(), timeout=5.0)
        finally:
            mgr.stop()

        # Loop ran at least 3 times and did NOT exit on its own
        assert counting_snapshot.n >= 3
        assert mgr._loop_task is None  # was stopped via stop()

    async def test_zero_max_duration_means_unlimited(self, tmp_path):
        """heartbeat_max_duration_seconds=0 (default) means unlimited."""
        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_max_duration_seconds=0,  # unlimited
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr._daily_spend_usd = 999.0

        threshold_reached = asyncio.Event()

        def counting_snapshot():
            counting_snapshot.n += 1
            if counting_snapshot.n >= 3:
                threshold_reached.set()
            return _make_snapshot(active_count=0)

        counting_snapshot.n = 0
        mock_flow.live.snapshot.side_effect = counting_snapshot

        mgr.start(mock_flow)
        try:
            await asyncio.wait_for(threshold_reached.wait(), timeout=5.0)
        finally:
            mgr.stop()

        assert counting_snapshot.n >= 3
        assert mgr._loop_task is None


class TestInterfacesDefaultPollUntagged:
    """Cover the default poll_untagged_tasks return in interfaces.py."""

    def test_protocol_default_poll_untagged(self):
        """TaskSource.poll_untagged_tasks default returns empty list."""
        from golem.interfaces import TaskSource

        # Call the default implementation directly on the Protocol class
        result = TaskSource.poll_untagged_tasks(None, [], "tag")
        assert result == []


class TestConfigHeartbeatValidationEdgeCases:
    """Cover heartbeat validation error branches in config.py."""

    def test_invalid_idle_threshold(self, tmp_path):
        """Validation catches non-positive idle_threshold."""
        from golem.core.config import validate_config, load_config

        config_data = {
            "flows": {
                "golem": {
                    "heartbeat_enabled": True,
                    "heartbeat_interval_seconds": 300,
                    "heartbeat_idle_threshold_seconds": 0,
                    "heartbeat_daily_budget_usd": 1.0,
                    "heartbeat_max_inflight": 1,
                }
            }
        }
        import yaml

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data))
        cfg = load_config(config_path)
        errors = validate_config(cfg)
        assert any("idle_threshold" in e for e in errors)

    def test_invalid_max_inflight(self, tmp_path):
        """Validation catches max_inflight < 1."""
        from golem.core.config import validate_config, load_config

        config_data = {
            "flows": {
                "golem": {
                    "heartbeat_enabled": True,
                    "heartbeat_interval_seconds": 300,
                    "heartbeat_idle_threshold_seconds": 900,
                    "heartbeat_daily_budget_usd": 1.0,
                    "heartbeat_max_inflight": 0,
                }
            }
        }
        import yaml

        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data))
        cfg = load_config(config_path)
        errors = validate_config(cfg)
        assert any("max_inflight" in e for e in errors)


class TestRedminePollUntagged:
    """Cover Redmine poll_untagged_tasks stub."""

    def test_redmine_poll_untagged_returns_empty(self):
        from golem.backends.redmine import RedmineTaskSource

        src = RedmineTaskSource.__new__(RedmineTaskSource)
        result = src.poll_untagged_tasks(["proj"], "tag")
        assert result == []


class TestCoerceTaskId:
    """Unit tests for the _coerce_task_id module-level helper."""

    @pytest.mark.parametrize(
        "value,expected",
        [
            (42, 42),
            (0, 0),
            (-1, -1),
        ],
    )
    def test_int_values_returned_as_is(self, value, expected):
        assert _coerce_task_id(value) == expected

    def test_int_value_no_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            _coerce_task_id(42)
        assert caplog.records == []

    @pytest.mark.parametrize(
        "value,expected_int",
        [
            ("123", 123),
            ("0", 0),
            ("-5", -5),
        ],
    )
    def test_string_ints_coerced(self, value, expected_int):
        assert _coerce_task_id(value) == expected_int

    def test_string_coercion_logs_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            _coerce_task_id("123")
        assert len(caplog.records) == 1
        assert "123" in caplog.records[0].message
        assert "str" in caplog.records[0].message

    @pytest.mark.parametrize(
        "value",
        ["abc", "12.5", "", "None", "[]"],
    )
    def test_unconvertible_strings_return_none(self, value):
        assert _coerce_task_id(value) is None

    def test_unconvertible_string_logs_warning(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            _coerce_task_id("abc")
        assert len(caplog.records) == 1
        assert "abc" in caplog.records[0].message
        assert "str" in caplog.records[0].message

    @pytest.mark.parametrize(
        "value",
        [3.14, 1.0, None, [], {}, True, False],
    )
    def test_non_int_non_str_returns_none(self, value):
        assert _coerce_task_id(value) is None

    def test_bool_true_returns_none_with_warning(self, caplog):
        """bool is a subclass of int but must NOT be treated as int."""
        import logging

        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            result = _coerce_task_id(True)
        assert result is None
        assert len(caplog.records) == 1

    def test_bool_false_returns_none_with_warning(self, caplog):
        """False must also be rejected."""
        import logging

        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            result = _coerce_task_id(False)
        assert result is None
        assert len(caplog.records) == 1

    def test_warning_includes_type_name(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            _coerce_task_id(3.14)
        assert "float" in caplog.records[0].message


class TestLoadStateTaskIdCoercion:
    """Integration tests for _coerce_task_id in load_state()."""

    def test_load_state_coerces_string_ids(self, tmp_path, caplog):
        import logging

        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text('{"inflight_task_ids": ["123", "456"]}', encoding="utf-8")
        mgr = _make_manager(tmp_path)
        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            mgr.load_state()
        assert mgr._inflight_task_ids == [123, 456]
        # Two string values -- two warnings
        assert len(caplog.records) == 2

    def test_load_state_drops_invalid_ids(self, tmp_path, caplog):
        import logging

        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text(
            '{"inflight_task_ids": [123, "abc", "999"]}', encoding="utf-8"
        )
        mgr = _make_manager(tmp_path)
        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            mgr.load_state()
        assert mgr._inflight_task_ids == [123, 999]
        # One string coerced (999), one dropped (abc) -- two warnings total
        assert len(caplog.records) == 2

    def test_load_state_all_int_no_warnings(self, tmp_path, caplog):
        import logging

        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text('{"inflight_task_ids": [1, 2, 3]}', encoding="utf-8")
        mgr = _make_manager(tmp_path)
        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            mgr.load_state()
        assert mgr._inflight_task_ids == [1, 2, 3]
        assert caplog.records == []

    def test_load_state_mixed_drops_none_type(self, tmp_path, caplog):
        import logging

        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text(
            '{"inflight_task_ids": [42, null, "55"]}', encoding="utf-8"
        )
        mgr = _make_manager(tmp_path)
        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            mgr.load_state()
        # null -> None -> dropped, "55" -> 55 (with warning)
        assert mgr._inflight_task_ids == [42, 55]
        # One warning for null (non-int/non-str), one for string coercion
        assert len(caplog.records) == 2


class TestOnTaskCompletedCoercion:
    """Integration tests for _coerce_task_id in on_task_completed()."""

    def test_on_task_completed_string_id_matches_inflight(self, tmp_path, caplog):
        """String task_id that corresponds to an inflight int is handled."""
        import logging

        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [123]
        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            mgr.on_task_completed("123", success=True)
        assert 123 not in mgr._inflight_task_ids
        # Should log a coercion warning for the string input
        assert any("123" in r.message for r in caplog.records)

    def test_on_task_completed_unconvertible_returns_early(self, tmp_path, caplog):
        """Unconvertible task_id returns without modifying state."""
        import logging

        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [123]
        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            mgr.on_task_completed("abc", success=True)
        # State unchanged
        assert mgr._inflight_task_ids == [123]
        assert len(caplog.records) == 1

    def test_on_task_completed_int_no_warning(self, tmp_path, caplog):
        """Plain int task_id passes through with no coercion warning."""
        import logging

        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [123]
        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            mgr.on_task_completed(123, success=True)
        assert caplog.records == []
        assert 123 not in mgr._inflight_task_ids


class TestTrigger:
    """Tests for the force-trigger mechanism."""

    def test_trigger_before_start_returns_false(self, tmp_path):
        """trigger() returns False when loop not started (no event)."""
        mgr = _make_manager(tmp_path)
        assert mgr.trigger() is False

    async def test_trigger_after_start_returns_true(self, tmp_path):
        """trigger() returns True and sets event after start."""
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=999)
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr.start(mock_flow)
        assert mgr.trigger() is True
        assert mgr._trigger_event.is_set()
        mgr.stop()

    async def test_force_trigger_calls_tick(self, tmp_path):
        """Force-trigger bypasses idle threshold and calls tick."""
        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_idle_threshold_seconds=9999,  # would never fire normally
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(
            active_count=5
        )  # not idle

        tick_called = asyncio.Event()

        async def _tick_signal():
            tick_called.set()

        with patch.object(mgr, "_run_heartbeat_tick", new=_tick_signal):
            mgr.start(mock_flow)
            mgr.trigger()
            try:
                await asyncio.wait_for(tick_called.wait(), timeout=2.0)
            finally:
                mgr.stop()

        assert tick_called.is_set(), "force-trigger did not call tick"

    async def test_loop_without_trigger_event_uses_sleep(self, tmp_path):
        """When _trigger_event is None, loop falls back to asyncio.sleep."""
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=0)
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = _make_snapshot(active_count=0)
        mgr._flow = mock_flow
        mgr._trigger_event = None

        loop_iterated = asyncio.Event()

        def _signal_side_effect(*_a, **_kw):
            loop_iterated.set()
            return _make_snapshot(active_count=0)

        mock_flow.live.snapshot.side_effect = _signal_side_effect

        loop_coro = mgr._heartbeat_loop()
        task = asyncio.create_task(loop_coro)
        try:
            await asyncio.wait_for(loop_iterated.wait(), timeout=2.0)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert loop_iterated.is_set(), "loop never iterated without trigger event"


class TestSnapshotNextTick:
    """Tests for next_tick_seconds in snapshot."""

    def test_next_tick_seconds_zero_before_start(self, tmp_path):
        mgr = _make_manager(tmp_path)
        snap = mgr.snapshot()
        assert snap["next_tick_seconds"] == 0

    def test_next_tick_seconds_reflects_timer(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._next_tick_at = time.time() + 120
        snap = mgr.snapshot()
        assert 118 <= snap["next_tick_seconds"] <= 120


class TestLiveSnapshotDictTyping:
    """Verify is_idle/has_external_tasks accept LiveSnapshotDict input."""

    def test_is_idle_with_full_live_snapshot(self, tmp_path):
        """is_idle must work with a fully-typed LiveSnapshotDict."""
        mgr = _make_manager(tmp_path)
        snapshot: LiveSnapshotDict = {
            "uptime_s": 100.0,
            "active_tasks": [],
            "active_count": 0,
            "queue_depth": 0,
            "queued_event_ids": [],
            "models_active": {},
            "recently_completed": [],
        }
        assert mgr.is_idle(snapshot) is True

    def test_has_external_tasks_with_full_live_snapshot(self, tmp_path):
        """has_external_tasks must work with a fully-typed LiveSnapshotDict."""
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [111]
        snapshot: LiveSnapshotDict = {
            "uptime_s": 50.0,
            "active_tasks": [],
            "active_count": 3,
            "queue_depth": 1,
            "queued_event_ids": [],
            "models_active": {},
            "recently_completed": [],
        }
        assert mgr.has_external_tasks(snapshot) is True

    def test_is_idle_uses_active_count_key_directly(self, tmp_path):
        """Regression: active_count must be read via direct key access."""
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [5]
        snapshot: LiveSnapshotDict = {
            "uptime_s": 0.0,
            "active_tasks": [],
            "active_count": 1,
            "queue_depth": 0,
            "queued_event_ids": [],
            "models_active": {},
            "recently_completed": [],
        }
        # 1 active == 1 heartbeat -> idle
        assert mgr.is_idle(snapshot) is True


class TestMultiRepoScheduler:
    """HeartbeatManager as a multi-repo scheduler."""

    def test_sync_workers_creates_from_registry(self, tmp_path):
        """Workers are created for each repo in the registry."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        reg.attach("/fake/repo2")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        assert len(mgr._workers) == 2
        assert "/fake/repo1" in mgr._workers
        assert "/fake/repo2" in mgr._workers

    def test_sync_workers_removes_detached(self, tmp_path):
        """Workers for detached repos are removed on sync."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        reg.attach("/fake/repo2")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
            assert len(mgr._workers) == 2
            reg.detach("/fake/repo1")
            mgr._sync_workers()
            assert len(mgr._workers) == 1
            assert "/fake/repo2" in mgr._workers

    def test_sync_workers_preserves_existing(self, tmp_path):
        """Existing workers are not recreated on re-sync."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
            first_worker = mgr._workers["/fake/repo1"]
            mgr._sync_workers()
            assert mgr._workers["/fake/repo1"] is first_worker

    def test_next_worker_round_robins(self, tmp_path):
        """_next_worker cycles through workers in round-robin order."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        reg.attach("/fake/repo2")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        paths = []
        for _ in range(4):
            w = mgr._next_worker()
            if w:
                paths.append(w.repo_path)
        assert len(set(paths)) == 2
        # Each repo appears twice in 4 calls
        assert paths.count(paths[0]) == 2

    def test_next_worker_returns_none_when_no_workers(self, tmp_path):
        """_next_worker returns None when there are no workers."""
        mgr = _make_manager(tmp_path)
        assert mgr._next_worker() is None

    def test_on_task_completed_routes_to_worker(self, tmp_path):
        """on_task_completed routes to the correct worker."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        worker = mgr._workers["/fake/repo1"]
        worker._inflight_task_ids.append(42)
        worker._dedup_memory["improvement:coverage:mod1"] = {
            "evaluated_at": "2026-01-01T00:00:00+00:00",
            "verdict": "submitted",
            "task_id": 42,
        }
        mgr._inflight_task_ids.append(42)

        mgr.on_task_completed(42, success=True)
        assert 42 not in worker._inflight_task_ids
        assert 42 not in mgr._inflight_task_ids

    def test_on_task_completed_no_worker_match(self, tmp_path):
        """on_task_completed handles case when no worker claims the task."""
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids.append(99)
        mgr.on_task_completed(99, success=True)
        assert 99 not in mgr._inflight_task_ids

    def test_get_claimed_issue_ids_includes_workers(self, tmp_path):
        """get_claimed_issue_ids includes IDs from all workers."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        worker = mgr._workers["/fake/repo1"]
        worker._dedup_memory["github:55"] = {
            "evaluated_at": "2026-01-01T00:00:00+00:00",
            "verdict": "submitted",
            "task_id": 55,
        }

        ids = mgr.get_claimed_issue_ids()
        assert 55 in ids

    def test_submit_single_for_worker(self, tmp_path):
        """_submit_single_for_worker passes work_dir to flow.submit_task."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 100}
        mgr._flow = mock_flow

        worker = mgr._workers["/fake/repo1"]
        candidate = {
            "id": "github:10",
            "subject": "Fix something",
            "body": "Body text",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "Easy fix",
            "tier": 1,
            "category": "github",
        }
        mgr._submit_single_for_worker(worker, candidate)

        mock_flow.submit_task.assert_called_once()
        call_kwargs = mock_flow.submit_task.call_args
        assert call_kwargs[1]["work_dir"] == "/fake/repo1"
        assert 100 in mgr._inflight_task_ids
        assert 100 in worker._inflight_task_ids
        assert "github:10" in worker._dedup_memory

    def test_submit_batch_for_worker(self, tmp_path):
        """_submit_batch_for_worker passes work_dir to flow.submit_task."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 200}
        mgr._flow = mock_flow

        worker = mgr._workers["/fake/repo1"]
        batch = [
            {
                "id": "improvement:coverage:mod1",
                "subject": "Fix coverage 1",
                "body": "Body 1",
                "automatable": True,
                "confidence": 0.8,
                "complexity": "small",
                "reason": "Coverage gap",
                "tier": 2,
                "category": "coverage",
            },
            {
                "id": "improvement:coverage:mod2",
                "subject": "Fix coverage 2",
                "body": "Body 2",
                "automatable": True,
                "confidence": 0.7,
                "complexity": "small",
                "reason": "Coverage gap",
                "tier": 2,
                "category": "coverage",
            },
        ]
        mgr._submit_batch_for_worker(worker, batch)

        mock_flow.submit_task.assert_called_once()
        call_kwargs = mock_flow.submit_task.call_args
        assert call_kwargs[1]["work_dir"] == "/fake/repo1"
        assert 200 in mgr._inflight_task_ids
        assert 200 in worker._inflight_task_ids

    def test_submit_promoted_for_worker(self, tmp_path):
        """_submit_promoted_for_worker passes work_dir to flow.submit_task."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 300}
        mgr._flow = mock_flow

        worker = mgr._workers["/fake/repo1"]
        candidate = {
            "id": "github:20",
            "subject": "Promoted issue",
            "body": "Body text",
            "automatable": True,
            "confidence": 0.95,
            "complexity": "medium",
            "reason": "Important fix",
            "tier": 1,
            "category": "github",
        }
        mgr._submit_promoted_for_worker(worker, candidate)

        mock_flow.submit_task.assert_called_once()
        call_kwargs = mock_flow.submit_task.call_args
        assert call_kwargs[1]["work_dir"] == "/fake/repo1"
        assert 300 in mgr._inflight_task_ids
        assert 300 in worker._inflight_task_ids

    async def test_run_multi_repo_tick_delegates_to_worker(self, tmp_path):
        """_run_multi_repo_tick calls worker.tick and submits results."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        worker = mgr._workers["/fake/repo1"]
        candidate = {
            "id": "github:30",
            "subject": "Test issue",
            "body": "Body",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "Test",
            "tier": 1,
            "category": "github",
        }
        worker.tick = AsyncMock(return_value=([candidate], 1))

        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 400}
        mock_flow._profile = MagicMock()
        mgr._flow = mock_flow

        await mgr._run_multi_repo_tick()

        worker.tick.assert_awaited_once()
        mock_flow.submit_task.assert_called_once()
        assert mgr._state == "idle"

    async def test_run_heartbeat_tick_delegates_to_multi_repo(self, tmp_path):
        """_run_heartbeat_tick always delegates to _run_multi_repo_tick."""
        mgr = _make_manager(tmp_path)
        mgr._run_multi_repo_tick = AsyncMock()
        mock_flow = MagicMock()
        mgr._flow = mock_flow

        await mgr._run_heartbeat_tick()

        mgr._run_multi_repo_tick.assert_awaited_once()

    def test_submit_single_for_worker_coercion_failure(self, tmp_path):
        """_submit_single_for_worker handles non-integer task_id from submit_task."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        mgr._flow = MagicMock()
        mgr._flow.submit_task.return_value = {"task_id": "not_an_int!!!"}

        candidate = {
            "id": "github:42",
            "subject": "test",
            "body": "test body",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "easy",
            "tier": 1,
        }
        mgr._submit_single_for_worker(worker, candidate)
        assert mgr._state == "idle"

    def test_submit_single_for_worker_exception(self, tmp_path):
        """_submit_single_for_worker handles submit_task exceptions."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        mgr._flow = MagicMock()
        mgr._flow.submit_task.side_effect = RuntimeError("boom")

        candidate = {
            "id": "github:42",
            "subject": "test",
            "body": "test body",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "easy",
            "tier": 1,
        }
        mgr._submit_single_for_worker(worker, candidate)
        assert mgr._state == "idle"

    def test_submit_batch_for_worker_recent_category_skip(self, tmp_path):
        """_submit_batch_for_worker skips recently addressed category."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        mgr._flow = MagicMock()

        batch = [
            {
                "id": "improvement:coverage:a",
                "category": "coverage",
                "confidence": 0.9,
                "subject": "a",
                "body": "a",
                "automatable": True,
                "complexity": "small",
                "reason": "a",
                "tier": 2,
            }
        ]
        mgr._submit_batch_for_worker(
            worker, batch, recent_categories={"coverage"}, resolved_ids=set()
        )
        mgr._flow.submit_task.assert_not_called()

    def test_submit_batch_for_worker_all_resolved_skip(self, tmp_path):
        """_submit_batch_for_worker skips when all items already resolved."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        mgr._flow = MagicMock()

        batch = [
            {
                "id": "improvement:coverage:a",
                "category": "coverage",
                "confidence": 0.9,
                "subject": "a",
                "body": "a",
                "automatable": True,
                "complexity": "small",
                "reason": "a",
                "tier": 2,
            }
        ]
        mgr._submit_batch_for_worker(
            worker,
            batch,
            recent_categories=set(),
            resolved_ids={"improvement:coverage:a"},
        )
        mgr._flow.submit_task.assert_not_called()

    def test_submit_batch_for_worker_coercion_failure(self, tmp_path):
        """_submit_batch_for_worker handles non-integer task_id."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        mgr._flow = MagicMock()
        mgr._flow.submit_task.return_value = {"task_id": None}

        batch = [
            {
                "id": "improvement:coverage:a",
                "category": "coverage",
                "confidence": 0.9,
                "subject": "a",
                "body": "a",
                "automatable": True,
                "complexity": "small",
                "reason": "a",
                "tier": 2,
            }
        ]
        mgr._submit_batch_for_worker(
            worker, batch, recent_categories=set(), resolved_ids=set()
        )
        assert mgr._state == "idle"

    def test_submit_batch_for_worker_exception(self, tmp_path):
        """_submit_batch_for_worker handles exceptions."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        mgr._flow = MagicMock()
        mgr._flow.submit_task.side_effect = RuntimeError("boom")

        batch = [
            {
                "id": "improvement:coverage:a",
                "category": "coverage",
                "confidence": 0.9,
                "subject": "a",
                "body": "a",
                "automatable": True,
                "complexity": "small",
                "reason": "a",
                "tier": 2,
            }
        ]
        mgr._submit_batch_for_worker(
            worker, batch, recent_categories=set(), resolved_ids=set()
        )
        assert mgr._state == "idle"

    def test_submit_promoted_for_worker_coercion_failure(self, tmp_path):
        """_submit_promoted_for_worker handles non-integer task_id."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        mgr._flow = MagicMock()
        mgr._flow.submit_task.return_value = {"task_id": True}

        candidate = {
            "id": "github:42",
            "subject": "test",
            "body": "test body",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "easy",
            "tier": 1,
        }
        result = mgr._submit_promoted_for_worker(worker, candidate)
        assert result is False
        assert not worker._inflight_task_ids

    def test_submit_promoted_for_worker_exception(self, tmp_path):
        """_submit_promoted_for_worker handles exceptions."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        mgr._flow = MagicMock()
        mgr._flow.submit_task.side_effect = RuntimeError("boom")

        candidate = {
            "id": "github:42",
            "subject": "test",
            "body": "test body",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "easy",
            "tier": 1,
        }
        result = mgr._submit_promoted_for_worker(worker, candidate)
        assert result is False
        assert not worker._inflight_task_ids

    async def test_run_multi_repo_tick_promoted_failure_preserves_debt(self, tmp_path):
        """Failed promoted submission preserves tier1_owed state."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        worker._tier1_owed = True
        worker._tier2_completions_since_tier1 = 3

        candidate = {
            "id": "github:42",
            "subject": "test",
            "body": "body",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "easy",
            "tier": 1,
            "category": "coverage",
        }
        worker.tick = AsyncMock(return_value=([candidate], 1))
        mgr._flow = MagicMock()
        mgr._flow._profile = MagicMock()
        mgr._flow.submit_task.side_effect = RuntimeError("boom")

        await mgr._run_multi_repo_tick()
        # Promotion debt must be preserved on failure
        assert worker._tier1_owed is True
        assert worker._tier2_completions_since_tier1 == 3

    async def test_run_multi_repo_tick_promoted_path(self, tmp_path):
        """_run_multi_repo_tick handles tier1_owed promotion path."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        worker._tier1_owed = True

        candidate = {
            "id": "github:42",
            "subject": "test",
            "body": "body",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "easy",
            "tier": 1,
            "category": "coverage",
        }
        worker.tick = AsyncMock(return_value=([candidate], 1))
        mgr._flow = MagicMock()
        mgr._flow._profile = MagicMock()
        mgr._flow.submit_task.return_value = {"task_id": 999}

        await mgr._run_multi_repo_tick()
        mgr._flow.submit_task.assert_called_once()
        assert worker._tier1_owed is False
        assert worker._tier2_completions_since_tier1 == 0

    async def test_run_multi_repo_tick_tier2_path(self, tmp_path):
        """_run_multi_repo_tick handles tier 2 batch path."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]

        candidates = [
            {
                "id": "improvement:coverage:a",
                "category": "coverage",
                "subject": "a",
                "body": "a",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "a",
                "tier": 2,
            },
        ]
        worker.tick = AsyncMock(return_value=(candidates, 2))
        worker._tick_recent_categories = set()
        worker._tick_resolved_ids = set()
        mgr._flow = MagicMock()
        mgr._flow._profile = MagicMock()
        mgr._flow.submit_task.return_value = {"task_id": 998}

        await mgr._run_multi_repo_tick()
        mgr._flow.submit_task.assert_called_once()

    async def test_run_multi_repo_tick_next_worker_none(self, tmp_path):
        """_run_multi_repo_tick handles _next_worker returning None mid-loop."""
        mgr = _make_manager(tmp_path)
        # Workers dict is non-empty so loop enters, but _next_worker returns None
        mgr._workers = {"/fake": MagicMock()}
        mgr._next_worker = MagicMock(return_value=None)
        mgr._flow = MagicMock()
        await mgr._run_multi_repo_tick()
        assert mgr._state == "idle"

    async def test_run_multi_repo_tick_no_candidates_skips(self, tmp_path):
        """_run_multi_repo_tick skips workers with no candidates."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()
        worker = mgr._workers["/fake/repo1"]
        worker.tick = AsyncMock(return_value=([], 0))
        mgr._flow = MagicMock()
        mgr._flow._profile = MagicMock()

        await mgr._run_multi_repo_tick()
        assert mgr._state == "idle"

    async def test_run_heartbeat_tick_skips_when_cannot_submit(self, tmp_path):
        """_run_heartbeat_tick returns early when can_submit is False."""
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=1)
        mgr._inflight_task_ids = [999]
        mgr._flow = MagicMock()
        await mgr._run_heartbeat_tick()
        assert mgr._state != "scanning"

    def test_snapshot_aggregates_from_workers(self, tmp_path):
        """snapshot() aggregates scan metadata from workers."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")

        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        worker = mgr._workers["/fake/repo1"]
        worker._last_scan_at = "2026-03-28T12:00:00+00:00"
        worker._last_scan_tier = 2
        worker._candidates = [{"id": "test"}]
        worker._dedup_memory = {"key1": {"evaluated_at": "x", "verdict": "y"}}

        snap = mgr.snapshot()
        assert snap["last_scan_at"] == "2026-03-28T12:00:00+00:00"
        assert snap["last_scan_tier"] == 2
        assert snap["candidate_count"] == 1
        assert snap["dedup_entry_count"] == 1

    async def test_run_multi_repo_tick_advances_past_dry_worker(self, tmp_path):
        """Multi-repo tick skips a dry worker and submits from the next."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/dry_repo")
        reg.attach("/fake/productive_repo")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        # First worker returns nothing, second returns a candidate
        dry_worker = mgr._workers["/fake/dry_repo"]
        dry_worker.tick = AsyncMock(return_value=([], 0))

        productive_worker = mgr._workers["/fake/productive_repo"]
        candidate = {
            "id": "github:99",
            "subject": "fix",
            "body": "body",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "easy",
            "tier": 1,
            "category": "bugfix",
        }
        productive_worker.tick = AsyncMock(return_value=([candidate], 1))

        mgr._flow = MagicMock()
        mgr._flow._profile = MagicMock()
        mgr._flow.submit_task.return_value = {"task_id": 555}

        await mgr._run_multi_repo_tick()

        # Dry worker was tried but produced nothing
        dry_worker.tick.assert_awaited_once()
        # Productive worker was tried and submitted
        productive_worker.tick.assert_awaited_once()
        mgr._flow.submit_task.assert_called_once()
        assert 555 in mgr._inflight_task_ids

    def test_reconcile_inflight_cleans_worker_lists(self, tmp_path):
        """reconcile_inflight removes stale IDs from worker lists too."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        worker = mgr._workers["/fake/repo1"]
        worker._inflight_task_ids = [100, 200]
        mgr._inflight_task_ids = [100, 200]

        # Only session 100 is still active
        mgr.reconcile_inflight({100})
        assert mgr._inflight_task_ids == [100]
        assert worker._inflight_task_ids == [100]

    def test_sync_workers_saves_state_before_removal(self, tmp_path):
        """_sync_workers saves worker state before removing detached repos."""
        reg_path = tmp_path / "registry.json"
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=reg_path)
        reg.attach("/fake/repo1")
        mgr = _make_manager(tmp_path)
        mgr._registry = reg
        with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
            mgr._sync_workers()

        worker = mgr._workers["/fake/repo1"]
        worker.save_state = MagicMock()

        reg.detach("/fake/repo1")
        mgr._sync_workers()

        worker.save_state.assert_called_once()
        assert "/fake/repo1" not in mgr._workers
