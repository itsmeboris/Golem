"""Tests for golem.heartbeat — HeartbeatManager state, budget, dedup."""

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.heartbeat import HeartbeatManager, _coerce_task_id, _strip_markdown_json
from golem.core.config import GolemFlowConfig
from golem.orchestrator import TaskSessionState


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


class TestStatePersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._daily_spend_usd = 0.05
        mgr._inflight_task_ids = [123456]
        mgr._dedup_memory["github:42"] = {
            "evaluated_at": "2026-03-15T10:00:00Z",
            "verdict": "not_automatable",
        }
        mgr.save_state()

        mgr2 = _make_manager(tmp_path)
        mgr2.load_state()
        assert mgr2._daily_spend_usd == 0.05
        assert mgr2._inflight_task_ids == [123456]
        assert "github:42" in mgr2._dedup_memory

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


class TestDedupMemory:
    def test_is_deduped_returns_false_for_new(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr.is_deduped("github:99") is False

    def test_is_deduped_returns_true_after_record(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr.record_dedup("github:99", "not_automatable")
        assert mgr.is_deduped("github:99") is True

    def test_dedup_expiry_prunes_old(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_dedup_ttl_days=30)
        mgr._dedup_memory["github:old"] = {
            "evaluated_at": "2026-01-01T00:00:00Z",  # >30 days ago
            "verdict": "not_automatable",
        }
        mgr._dedup_memory["github:new"] = {
            "evaluated_at": "2026-03-14T00:00:00Z",  # recent
            "verdict": "submitted",
        }
        mgr._prune_dedup()
        assert "github:old" not in mgr._dedup_memory
        assert "github:new" in mgr._dedup_memory

    def test_dedup_prune_handles_invalid_dates(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._dedup_memory["github:bad"] = {
            "evaluated_at": "not-a-date",
            "verdict": "not_automatable",
        }
        mgr._prune_dedup()
        assert "github:bad" not in mgr._dedup_memory

    def test_dedup_prune_handles_missing_evaluated_at(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._dedup_memory["github:missing"] = {"verdict": "not_automatable"}
        mgr._prune_dedup()
        assert "github:missing" not in mgr._dedup_memory


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
        mgr._dedup_memory["github:42"] = {
            "evaluated_at": "2026-03-15T10:00:00Z",
            "verdict": "submitted",
            "task_id": 123,
        }
        mgr.on_task_completed(123, success=True)
        assert 123 not in mgr._inflight_task_ids

    def test_on_task_completed_updates_dedup_verdict(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [123]
        mgr._dedup_memory["github:42"] = {
            "evaluated_at": "2026-03-15T10:00:00Z",
            "verdict": "submitted",
            "task_id": 123,
        }
        mgr.on_task_completed(123, success=True)
        assert mgr._dedup_memory["github:42"]["verdict"] == "completed"

    def test_on_task_completed_marks_failed(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [123]
        mgr._dedup_memory["github:42"] = {
            "evaluated_at": "2026-03-15T10:00:00Z",
            "verdict": "submitted",
            "task_id": 123,
        }
        mgr.on_task_completed(123, success=False)
        assert mgr._dedup_memory["github:42"]["verdict"] == "failed"

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
        # 1 active, limit is 2 — should be able to submit
        assert mgr.can_submit() is True

    def test_can_submit_filters_missing_sessions(self, tmp_path):
        """An ID whose session no longer exists must not count toward the limit."""
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=1)
        mgr._inflight_task_ids = [55]
        mock_flow = MagicMock()
        mock_flow.get_session.return_value = None  # session is gone
        mgr._flow = mock_flow
        # 0 active, limit is 1 — should be able to submit
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
        # 0 active (terminal doesn't count), limit is 1 — can submit
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
        mgr._dedup_memory["github:1"] = {"evaluated_at": "now", "verdict": "ok"}
        snap = mgr.snapshot()
        assert snap["daily_spend_usd"] == 0.42
        assert snap["inflight_task_ids"] == [111]
        assert snap["dedup_entry_count"] == 1


class TestIdleDetection:
    def test_is_idle_true_when_no_active_tasks(self, tmp_path):
        mgr = _make_manager(tmp_path)
        snapshot = {
            "active_count": 0,
            "queue_depth": 0,
            "active_tasks": [],
            "recently_completed": [],
        }
        assert mgr.is_idle(snapshot) is True

    def test_is_idle_false_when_active_tasks(self, tmp_path):
        mgr = _make_manager(tmp_path)
        snapshot = {"active_count": 2, "queue_depth": 0}
        assert mgr.is_idle(snapshot) is False

    def test_is_idle_true_when_only_heartbeat_tasks(self, tmp_path):
        """Idle means no external tasks. Heartbeat-own tasks don't count."""
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [111, 222]
        snapshot = {"active_count": 2, "queue_depth": 0}
        assert mgr.is_idle(snapshot) is True  # 2 active == 2 heartbeat


class TestExternalTaskDetection:
    def test_has_external_tasks_true(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [111]
        snapshot = {"active_count": 3}  # 3 active, 1 heartbeat = 2 external
        assert mgr.has_external_tasks(snapshot) is True

    def test_has_external_tasks_false_when_all_heartbeat(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [111]
        snapshot = {"active_count": 1}
        assert mgr.has_external_tasks(snapshot) is False

    def test_has_external_tasks_false_when_empty(self, tmp_path):
        mgr = _make_manager(tmp_path)
        snapshot = {"active_count": 0}
        assert mgr.has_external_tasks(snapshot) is False


class TestAsyncLoop:
    @pytest.mark.asyncio
    async def test_start_and_stop(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=1)
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = {"active_count": 5}  # not idle

        mgr.start(mock_flow)
        assert mgr._loop_task is not None
        await asyncio.sleep(0.1)
        mgr.stop()
        assert mgr._state == "idle"

    @pytest.mark.asyncio
    async def test_stop_persists_state(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=1)
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = {"active_count": 5}
        mgr._daily_spend_usd = 0.42
        mgr.start(mock_flow)
        await asyncio.sleep(0.1)
        mgr.stop()
        assert (tmp_path / "heartbeat_state.json").exists()
        data = json.loads((tmp_path / "heartbeat_state.json").read_text())
        assert data["daily_spend_usd"] == 0.42

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self, tmp_path):
        """Stopping without starting should not raise."""
        mgr = _make_manager(tmp_path)
        mgr.stop()  # Should not raise
        assert mgr._state == "idle"

    @pytest.mark.asyncio
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


class TestValidateCandidates:
    def test_filters_non_automatable(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:1",
                    "automatable": False,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "No",
                },
                {
                    "id": "github:2",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "Yes",
                },
            ]
        }
        result = mgr._validate_candidates(raw)
        assert len(result) == 1
        assert result[0]["id"] == "github:2"

    def test_filters_low_confidence(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:1",
                    "automatable": True,
                    "confidence": 0.5,
                    "complexity": "small",
                    "reason": "Low",
                },
            ]
        }
        result = mgr._validate_candidates(raw)
        assert result == []

    def test_filters_large_complexity(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:1",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "large",
                    "reason": "Big",
                },
            ]
        }
        result = mgr._validate_candidates(raw)
        assert result == []

    def test_clamps_confidence(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:1",
                    "automatable": True,
                    "confidence": 1.5,
                    "complexity": "small",
                    "reason": "Over",
                },
            ]
        }
        result = mgr._validate_candidates(raw)
        assert result[0]["confidence"] == 1.0

    def test_invalid_response_structure(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._validate_candidates("not a dict") == []
        assert mgr._validate_candidates({"no_candidates": []}) == []

    def test_non_dict_candidates_skipped(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {"candidates": ["not a dict", 42]}
        assert mgr._validate_candidates(raw) == []

    def test_non_numeric_confidence_skipped(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:1",
                    "automatable": True,
                    "confidence": "high",
                    "complexity": "small",
                    "reason": "Bad",
                },
            ]
        }
        assert mgr._validate_candidates(raw) == []

    def test_invalid_complexity_skipped(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:1",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "huge",
                    "reason": "Bad",
                },
            ]
        }
        assert mgr._validate_candidates(raw) == []

    def test_sorted_by_confidence_descending(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:1",
                    "automatable": True,
                    "confidence": 0.7,
                    "complexity": "small",
                    "reason": "Low",
                },
                {
                    "id": "github:2",
                    "automatable": True,
                    "confidence": 0.95,
                    "complexity": "small",
                    "reason": "High",
                },
                {
                    "id": "github:3",
                    "automatable": True,
                    "confidence": 0.8,
                    "complexity": "medium",
                    "reason": "Mid",
                },
            ]
        }
        result = mgr._validate_candidates(raw)
        assert [c["id"] for c in result] == ["github:2", "github:3", "github:1"]


class TestStripMarkdownJson:
    def test_plain_json_unchanged(self):
        assert _strip_markdown_json('{"a": 1}') == '{"a": 1}'

    def test_strips_json_code_fence(self):
        text = '```json\n{"candidates": []}\n```'
        assert _strip_markdown_json(text) == '{"candidates": []}'

    def test_strips_plain_code_fence(self):
        text = '```\n{"candidates": []}\n```'
        assert _strip_markdown_json(text) == '{"candidates": []}'

    def test_strips_surrounding_whitespace(self):
        assert _strip_markdown_json("  \n{}\n  ") == "{}"

    def test_extracts_from_mixed_text(self):
        text = 'Here is the result:\n```json\n{"ok": true}\n```\nDone.'
        assert _strip_markdown_json(text) == '{"ok": true}'


class TestCallHaiku:
    @pytest.mark.asyncio
    async def test_call_haiku_tracks_spend(self, tmp_path):
        from golem.core.cli_wrapper import CLIResult

        mgr = _make_manager(tmp_path)
        mock_result = CLIResult(
            output={"result": '{"candidates": []}'},
            cost_usd=0.001,
        )
        with patch("golem.heartbeat.invoke_cli", return_value=mock_result):
            result = await mgr._call_haiku("prompt", "data")

        assert mgr._daily_spend_usd == pytest.approx(0.001)
        assert result == {"candidates": []}

    @pytest.mark.asyncio
    async def test_call_haiku_handles_non_json(self, tmp_path):
        from golem.core.cli_wrapper import CLIResult

        mgr = _make_manager(tmp_path)
        mock_result = CLIResult(
            output={"result": "not json"},
            cost_usd=0.0005,
        )
        with patch("golem.heartbeat.invoke_cli", return_value=mock_result):
            result = await mgr._call_haiku("prompt", "data")

        assert result == "not json"

    @pytest.mark.asyncio
    async def test_call_haiku_handles_empty_result(self, tmp_path):
        from golem.core.cli_wrapper import CLIResult

        mgr = _make_manager(tmp_path)
        mock_result = CLIResult(output={"result": ""}, cost_usd=0.0)
        with patch("golem.heartbeat.invoke_cli", return_value=mock_result):
            result = await mgr._call_haiku("prompt", "data")

        assert result == ""

    @pytest.mark.asyncio
    async def test_call_haiku_zero_cost(self, tmp_path):
        """Spend is recorded even when cost is zero."""
        from golem.core.cli_wrapper import CLIResult

        mgr = _make_manager(tmp_path)
        mock_result = CLIResult(output={"result": "{}"}, cost_usd=0.0)
        with patch("golem.heartbeat.invoke_cli", return_value=mock_result):
            result = await mgr._call_haiku("prompt", "data")

        assert mgr._daily_spend_usd == 0.0

    @pytest.mark.asyncio
    async def test_call_haiku_cli_error(self, tmp_path):
        """Returns empty string when CLI call fails."""
        from golem.core.cli_wrapper import CLIError

        mgr = _make_manager(tmp_path)
        with patch(
            "golem.heartbeat.invoke_cli",
            side_effect=CLIError("CLI not found"),
        ):
            result = await mgr._call_haiku("prompt", "data")

        assert result == ""
        assert mgr._daily_spend_usd == 0.0

    @pytest.mark.asyncio
    async def test_call_haiku_strips_markdown_fence(self, tmp_path):
        """Haiku responses wrapped in ```json fences are parsed correctly."""
        from golem.core.cli_wrapper import CLIResult

        mgr = _make_manager(tmp_path)
        wrapped = '```json\n{"candidates": [{"id": "test:1"}]}\n```'
        mock_result = CLIResult(output={"result": wrapped}, cost_usd=0.001)
        with patch("golem.heartbeat.invoke_cli", return_value=mock_result):
            result = await mgr._call_haiku("prompt", "data")

        assert result == {"candidates": [{"id": "test:1"}]}


class TestTier1:
    @pytest.mark.asyncio
    async def test_tier1_calls_backend_and_haiku(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Fix login bug", "body": "Steps to repro"},
            {"id": 43, "subject": "Discuss roadmap", "body": "Let's plan Q2"},
        ]
        mgr._flow = mock_flow

        haiku_response = {
            "candidates": [
                {
                    "id": "github:42",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "Clear bug fix",
                },
                {
                    "id": "github:43",
                    "automatable": False,
                    "confidence": 0.2,
                    "complexity": "large",
                    "reason": "Discussion, not code",
                },
            ]
        }

        with patch.object(mgr, "_call_haiku", return_value=haiku_response):
            candidates = await mgr._run_tier1()

        assert len(candidates) == 1
        assert candidates[0]["id"] == "github:42"
        assert candidates[0]["confidence"] == 0.9

    @pytest.mark.asyncio
    async def test_tier1_skips_deduped_issues(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._dedup_memory["github:42"] = {
            "evaluated_at": "2026-03-15T10:00:00Z",
            "verdict": "not_automatable",
        }
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Fix login bug", "body": "desc"},
        ]
        mgr._flow = mock_flow

        with patch.object(mgr, "_call_haiku") as mock_haiku:
            candidates = await mgr._run_tier1()

        mock_haiku.assert_not_called()  # no new issues to evaluate
        assert candidates == []

    @pytest.mark.asyncio
    async def test_tier1_handles_malformed_haiku_response(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Bug", "body": "desc"},
        ]
        mgr._flow = mock_flow

        with patch.object(mgr, "_call_haiku", return_value="not json"):
            candidates = await mgr._run_tier1()

        assert candidates == []

    @pytest.mark.asyncio
    async def test_tier1_clamps_confidence(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Bug", "body": "desc"},
        ]
        mgr._flow = mock_flow

        haiku_response = {
            "candidates": [
                {
                    "id": "github:42",
                    "automatable": True,
                    "confidence": 1.5,  # over 1.0 — should be clamped
                    "complexity": "small",
                    "reason": "Bug fix",
                },
            ]
        }

        with patch.object(mgr, "_call_haiku", return_value=haiku_response):
            candidates = await mgr._run_tier1()

        assert candidates[0]["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_tier1_records_all_in_dedup(self, tmp_path):
        """All evaluated issues go into dedup, not just candidates."""
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Bug", "body": "desc"},
            {"id": 43, "subject": "Discussion", "body": "desc"},
        ]
        mgr._flow = mock_flow

        haiku_response = {
            "candidates": [
                {
                    "id": "github:42",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "Fix",
                },
                {
                    "id": "github:43",
                    "automatable": False,
                    "confidence": 0.2,
                    "complexity": "large",
                    "reason": "Talk",
                },
            ]
        }

        with patch.object(mgr, "_call_haiku", return_value=haiku_response):
            await mgr._run_tier1()

        assert "github:42" in mgr._dedup_memory
        assert "github:43" in mgr._dedup_memory
        assert mgr._dedup_memory["github:42"]["verdict"] == "candidate"
        assert mgr._dedup_memory["github:43"]["verdict"] == "not_automatable"

    @pytest.mark.asyncio
    async def test_tier1_handles_backend_exception(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.side_effect = OSError("fail")
        mgr._flow = mock_flow

        candidates = await mgr._run_tier1()
        assert candidates == []

    @pytest.mark.asyncio
    async def test_tier1_respects_budget(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_daily_budget_usd=0.01)
        mgr._daily_spend_usd = 0.01  # budget exhausted
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Bug", "body": "desc"},
        ]
        mgr._flow = mock_flow

        with patch.object(mgr, "_call_haiku") as mock_haiku:
            candidates = await mgr._run_tier1()

        mock_haiku.assert_not_called()
        assert candidates == []


class TestTier2:
    @pytest.mark.asyncio
    async def test_tier2_todo_scan(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._flow = MagicMock()
        mgr._last_scan_at = "2026-03-14T00:00:00Z"

        haiku_response = {
            "candidates": [
                {
                    "id": "improvement:todo:heartbeat.py",
                    "automatable": True,
                    "confidence": 0.8,
                    "complexity": "small",
                    "reason": "New TODO needs implementation",
                },
            ]
        }

        with patch.object(
            mgr,
            "_scan_todos",
            return_value=["golem/heartbeat.py: TODO: add retry logic"],
        ):
            with patch.object(mgr, "_scan_coverage", return_value=[]):
                with patch.object(mgr, "_scan_pitfalls", return_value=[]):
                    with patch.object(mgr, "_call_haiku", return_value=haiku_response):
                        candidates = await mgr._run_tier2()

        assert len(candidates) == 1
        assert candidates[0]["id"] == "improvement:todo:heartbeat.py"

    @pytest.mark.asyncio
    async def test_tier2_no_findings_returns_empty(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._flow = MagicMock()

        with patch.object(mgr, "_scan_todos", return_value=[]):
            with patch.object(mgr, "_scan_coverage", return_value=[]):
                with patch.object(mgr, "_scan_pitfalls", return_value=[]):
                    candidates = await mgr._run_tier2()

        assert candidates == []

    @pytest.mark.asyncio
    async def test_tier2_respects_budget(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_daily_budget_usd=0.01)
        mgr._daily_spend_usd = 0.01  # exhausted
        mgr._flow = MagicMock()

        with patch.object(mgr, "_scan_todos", return_value=["golem/foo.py"]):
            with patch.object(mgr, "_scan_coverage", return_value=[]):
                with patch.object(mgr, "_scan_pitfalls", return_value=[]):
                    with patch.object(mgr, "_call_haiku") as mock_haiku:
                        candidates = await mgr._run_tier2()

        mock_haiku.assert_not_called()
        assert candidates == []

    def test_scan_todos_uses_git_log(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._last_scan_at = "2026-03-14T00:00:00Z"
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="golem/heartbeat.py\ngolem/flow.py\n",
            )
            result = mgr._scan_todos()
        assert "golem/heartbeat.py" in result
        # Verify --since uses last_scan_at
        call_args = mock_run.call_args[0][0]
        assert any("2026-03-14" in str(a) for a in call_args)

    def test_scan_todos_defaults_to_7d_on_first_run(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._last_scan_at = ""  # no previous scan
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="")
            mgr._scan_todos()
        call_args = mock_run.call_args[0][0]
        assert any("7.days" in str(a) for a in call_args)

    def test_scan_todos_handles_failure(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = mgr._scan_todos()
        assert result == []

    def test_scan_todos_handles_os_error(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch("subprocess.run", side_effect=OSError("fail")):
            result = mgr._scan_todos()
        assert result == []

    def test_scan_todos_handles_timeout(self, tmp_path):
        import subprocess

        mgr = _make_manager(tmp_path)
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 30)):
            result = mgr._scan_todos()
        assert result == []

    def test_scan_coverage_uses_cache(self, tmp_path):
        from datetime import datetime, timezone

        mgr = _make_manager(tmp_path)
        mgr._coverage_cache = {
            "commit_hash": "abc123",
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "uncovered_modules": ["golem/flow.py"],
        }
        with patch("subprocess.run") as mock_run:
            # Return the same commit hash
            mock_run.return_value = MagicMock(returncode=0, stdout="abc123\n")
            result = mgr._scan_coverage()
        assert result == ["golem/flow.py"]
        # pytest should NOT have been called (cache hit)
        assert mock_run.call_count == 1  # only git rev-parse

    def test_scan_coverage_runs_pytest_on_cache_miss(self, tmp_path):
        mgr = _make_manager(tmp_path)
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "git":
                return MagicMock(returncode=0, stdout="def456\n")
            # pytest output
            return MagicMock(
                returncode=0,
                stdout="golem/flow.py   95%\ngolem/types.py   100%\n",
            )

        with patch("subprocess.run", side_effect=mock_run):
            result = mgr._scan_coverage()

        assert result == ["golem/flow.py"]
        assert len(calls) == 2  # git rev-parse + pytest

    def test_scan_coverage_handles_git_failure(self, tmp_path):
        mgr = _make_manager(tmp_path)
        with patch("subprocess.run", side_effect=OSError("fail")):
            result = mgr._scan_coverage()
        assert result == []

    def test_scan_coverage_handles_pytest_timeout(self, tmp_path):
        import subprocess

        mgr = _make_manager(tmp_path)
        call_count = [0]

        def mock_run(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return MagicMock(returncode=0, stdout="abc123\n")
            raise subprocess.TimeoutExpired("pytest", 300)

        with patch("subprocess.run", side_effect=mock_run):
            result = mgr._scan_coverage()
        assert result == []

    def test_scan_pitfalls_matches_agents_md_structure(self, tmp_path):
        """Pitfall scanner matches real AGENTS.md structure under ## Recurring Antipatterns."""
        mgr = _make_manager(tmp_path, default_work_dir=str(tmp_path))
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            "# AGENTS.md\n\n"
            "## Recurring Antipatterns\n"
            "- **Empty exception handler**: silently swallows errors <!-- seen:4 last:2026-03-15 -->\n"
            "- **String-matching control flow**: bare string comparisons <!-- seen:6 last:2026-03-15 -->\n"
            "\n## Other Section\n"
            "- This should not be matched\n"
        )
        result = mgr._scan_pitfalls()
        assert len(result) == 2
        assert "Empty exception handler" in result[0]
        assert "<!-- seen:" not in result[0]  # marker stripped

    def test_scan_pitfalls_no_agents_md(self, tmp_path):
        mgr = _make_manager(tmp_path, default_work_dir=str(tmp_path))
        result = mgr._scan_pitfalls()
        assert result == []

    def test_scan_pitfalls_handles_os_error(self, tmp_path):
        mgr = _make_manager(tmp_path, default_work_dir=str(tmp_path))
        agents_md = tmp_path / "AGENTS.md"
        agents_md.write_text(
            "## Recurring Antipatterns\n- test <!-- seen:1 last:2026 -->\n"
        )
        # Make the file unreadable by patching Path.read_text
        with patch.object(Path, "read_text", side_effect=OSError("permission denied")):
            result = mgr._scan_pitfalls()
        assert result == []

    def test_scan_pitfalls_skips_deduped(self, tmp_path):
        mgr = _make_manager(tmp_path, default_work_dir=str(tmp_path))
        agents_md = tmp_path / "AGENTS.md"
        content = (
            "## Recurring Antipatterns\n"
            "- **Bug A**: desc <!-- seen:1 last:2026-03-15 -->\n"
        )
        agents_md.write_text(content)

        # First scan: should find it
        result1 = mgr._scan_pitfalls()
        assert len(result1) == 1

        # Record it in dedup
        import hashlib

        line = "- **Bug A**: desc <!-- seen:1 last:2026-03-15 -->"
        key = f"pitfall:{hashlib.sha256(line.encode()).hexdigest()[:12]}"
        mgr.record_dedup(key, "evaluated")

        # Second scan: should be filtered
        result2 = mgr._scan_pitfalls()
        assert len(result2) == 0

    def test_scan_coverage_stale_cache(self, tmp_path):
        """Cache is stale when ran_at is >1 hour ago."""
        from datetime import datetime, timezone, timedelta

        mgr = _make_manager(tmp_path)
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        mgr._coverage_cache = {
            "commit_hash": "abc123",
            "ran_at": old_time,
            "uncovered_modules": ["golem/old.py"],
        }
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd[0])
            if cmd[0] == "git":
                return MagicMock(returncode=0, stdout="abc123\n")
            return MagicMock(returncode=0, stdout="golem/new.py   95%\n")

        with patch("subprocess.run", side_effect=mock_run):
            result = mgr._scan_coverage()

        assert result == ["golem/new.py"]
        assert len(calls) == 2  # re-ran pytest despite same commit

    def test_scan_coverage_invalid_cache_time(self, tmp_path):
        """Cache with invalid ran_at is treated as stale."""
        mgr = _make_manager(tmp_path)
        mgr._coverage_cache = {
            "commit_hash": "abc123",
            "ran_at": "not-a-date",
            "uncovered_modules": ["golem/old.py"],
        }
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd[0])
            if cmd[0] == "git":
                return MagicMock(returncode=0, stdout="abc123\n")
            return MagicMock(returncode=0, stdout="golem/new.py  95%\n")

        with patch("subprocess.run", side_effect=mock_run):
            result = mgr._scan_coverage()

        assert result == ["golem/new.py"]
        assert len(calls) == 2

    def test_scan_coverage_invalid_cache_time_logs_debug(self, tmp_path, caplog):
        """Invalid cached timestamp triggers a debug log message."""
        import logging

        mgr = _make_manager(tmp_path)
        mgr._coverage_cache = {
            "commit_hash": "abc123",
            "ran_at": "not-a-date",
            "uncovered_modules": ["golem/old.py"],
        }

        def mock_run(cmd, **kwargs):
            if cmd[0] == "git":
                return MagicMock(returncode=0, stdout="abc123\n")
            return MagicMock(returncode=0, stdout="golem/new.py  95%\n")

        with caplog.at_level(logging.DEBUG, logger="golem.heartbeat"):
            with patch("subprocess.run", side_effect=mock_run):
                mgr._scan_coverage()

        assert any(
            "Invalid cached timestamp" in r.message and r.levelno == logging.DEBUG
            for r in caplog.records
        )


class TestHeartbeatTick:
    @pytest.mark.asyncio
    async def test_tick_runs_tier1_first(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        tier1_candidates = [
            {
                "id": "github:42",
                "subject": "Fix bug",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Clear fix",
                "tier": 1,
            },
        ]

        with patch.object(mgr, "_run_tier1", return_value=tier1_candidates):
            with patch.object(mgr, "_run_tier2") as mock_t2:
                await mgr._run_heartbeat_tick()

        mock_t2.assert_not_called()  # Tier 1 found work, skip Tier 2
        mock_flow.submit_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_falls_through_to_tier2(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        tier2_candidates = [
            {
                "id": "improvement:coverage:flow",
                "subject": "Add tests for flow.py",
                "body": "desc",
                "automatable": True,
                "confidence": 0.8,
                "complexity": "small",
                "reason": "Low coverage",
                "tier": 2,
            },
        ]

        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=tier2_candidates):
                await mgr._run_heartbeat_tick()

        mock_flow.submit_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_tick_does_nothing_when_no_candidates(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mgr._flow = mock_flow

        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=[]):
                await mgr._run_heartbeat_tick()

        mock_flow.submit_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_respects_inflight_limit(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_max_inflight=1)
        mgr._inflight_task_ids = [111]  # already at max
        mock_flow = MagicMock()
        mgr._flow = mock_flow

        with patch.object(
            mgr,
            "_run_tier1",
            return_value=[
                {"id": "github:42", "confidence": 0.9},
            ],
        ):
            await mgr._run_heartbeat_tick()

        mock_flow.submit_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_tick_tags_submission_with_heartbeat(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "github:42",
                "subject": "Fix bug",
                "body": "Fix the login bug",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Clear fix",
                "tier": 1,
            },
        ]

        with patch.object(mgr, "_run_tier1", return_value=candidates):
            await mgr._run_heartbeat_tick()

        call_kwargs = mock_flow.submit_task.call_args
        # Subject should contain [HEARTBEAT]
        subject = call_kwargs.kwargs.get("subject", "")
        assert "[HEARTBEAT]" in subject

    @pytest.mark.asyncio
    async def test_tick_adds_task_to_inflight(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "github:42",
                "subject": "Fix bug",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix",
                "tier": 1,
            },
        ]

        with patch.object(mgr, "_run_tier1", return_value=candidates):
            await mgr._run_heartbeat_tick()

        assert 999 in mgr._inflight_task_ids

    @pytest.mark.asyncio
    async def test_tick_saves_state_after_submission(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "github:42",
                "subject": "Fix bug",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix",
                "tier": 1,
            },
        ]

        with patch.object(mgr, "_run_tier1", return_value=candidates):
            await mgr._run_heartbeat_tick()

        assert (tmp_path / "heartbeat_state.json").exists()

    @pytest.mark.asyncio
    async def test_tick_handles_submit_exception(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.side_effect = RuntimeError("submit failed")
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "github:42",
                "subject": "Fix bug",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix",
                "tier": 1,
            },
        ]

        with patch.object(mgr, "_run_tier1", return_value=candidates):
            await mgr._run_heartbeat_tick()  # should not raise

        assert mgr._state == "idle"

    @pytest.mark.asyncio
    async def test_tick_saves_state_when_no_candidates(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mgr._flow = mock_flow

        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=[]):
                await mgr._run_heartbeat_tick()

        assert (tmp_path / "heartbeat_state.json").exists()

    @pytest.mark.asyncio
    async def test_tick_updates_last_scan_metadata(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mgr._flow = mock_flow

        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=[]):
                await mgr._run_heartbeat_tick()

        assert mgr._last_scan_at != ""


class TestHeartbeatLoopBranches:
    """Cover the remaining branches in _heartbeat_loop."""

    @pytest.mark.asyncio
    async def test_loop_budget_exhausted_state(self, tmp_path):
        """When budget is exhausted, loop sets state to budget_exhausted."""
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=0)
        mgr._daily_spend_usd = 999.0  # over budget
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = {"active_count": 0}
        mgr.start(mock_flow)
        await asyncio.sleep(0.05)
        assert mgr._state == "budget_exhausted"
        mgr.stop()

    @pytest.mark.asyncio
    async def test_loop_idle_below_threshold(self, tmp_path):
        """When idle but below threshold, loop sets state to idle."""
        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_idle_threshold_seconds=9999,
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = {"active_count": 0}
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
        mock_flow.live.snapshot.return_value = {"active_count": 0}
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


class TestTier2CoverageAndPitfallFindings:
    """Cover lines where coverage and pitfall findings are formatted."""

    @pytest.mark.asyncio
    async def test_tier2_with_coverage_and_pitfall_findings(self, tmp_path):
        """Tier 2 formats coverage and pitfall findings for Haiku prompt."""
        mgr = _make_manager(tmp_path)
        mgr._flow = MagicMock()

        haiku_response = {
            "candidates": [
                {
                    "id": "improvement:coverage:utils",
                    "automatable": True,
                    "confidence": 0.8,
                    "complexity": "small",
                    "reason": "Add tests for utils module",
                },
            ]
        }

        with patch.object(mgr, "_scan_todos", return_value=[]):
            with patch.object(
                mgr, "_scan_coverage", return_value=["golem/utils.py (85%)"]
            ):
                with patch.object(
                    mgr,
                    "_scan_pitfalls",
                    return_value=["Avoid mocking internals"],
                ):
                    with patch.object(
                        mgr, "_call_haiku", return_value=haiku_response
                    ) as mock_haiku:
                        candidates = await mgr._run_tier2()

        # Verify coverage and pitfall findings were included in prompt
        call_args = mock_haiku.call_args
        findings_str = call_args[0][1]
        assert "Module below 100% coverage" in findings_str
        assert "Unresolved pitfall" in findings_str
        assert len(candidates) == 1


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
        mock_flow.live.snapshot.return_value = {"active_count": 0}
        mgr._daily_spend_usd = 999.0  # stay in budget_exhausted branch (fast path)

        mgr.start(mock_flow)
        loop_task = mgr._loop_task
        try:
            await asyncio.wait_for(loop_task, timeout=5.0)
        finally:
            mgr.stop()

        # wait_for completed without TimeoutError → loop exited on its own
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
        mock_flow.live.snapshot.return_value = {"active_count": 0}
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
        mock_flow.live.snapshot.return_value = {"active_count": 0}
        mgr._daily_spend_usd = 999.0  # fast budget_exhausted path

        mgr.start(mock_flow)
        loop_task = mgr._loop_task
        try:
            await asyncio.wait_for(loop_task, timeout=10.0)
        finally:
            mgr.stop()

        # wait_for completed without TimeoutError → loop exited on its own
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
        mock_flow.live.snapshot.return_value = {"active_count": 0}
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
        """heartbeat_max_ticks=0 (default) means unlimited — loop does not exit."""
        mgr = _make_manager(
            tmp_path,
            heartbeat_interval_seconds=0,
            heartbeat_max_ticks=0,  # unlimited
        )
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = {"active_count": 0}
        mgr._daily_spend_usd = 999.0  # fast budget_exhausted path

        threshold_reached = asyncio.Event()

        def counting_snapshot():
            counting_snapshot.n += 1
            if counting_snapshot.n >= 3:
                threshold_reached.set()
            return {"active_count": 0}

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
        mock_flow.live.snapshot.return_value = {"active_count": 0}
        mgr._daily_spend_usd = 999.0

        threshold_reached = asyncio.Event()

        def counting_snapshot():
            counting_snapshot.n += 1
            if counting_snapshot.n >= 3:
                threshold_reached.set()
            return {"active_count": 0}

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
        # Two string values — two warnings
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
        # One string coerced (999), one dropped (abc) — two warnings total
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
        # null → None → dropped, "55" → 55 (with warning)
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


class TestHeartbeatTickTaskIdCoercion:
    """Integration tests for _coerce_task_id in _run_heartbeat_tick()."""

    @pytest.mark.asyncio
    async def test_tick_coerces_string_task_id(self, tmp_path, caplog):
        """submit_task returning a string task_id is coerced to int."""
        import logging

        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": "999", "status": "submitted"}
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "github:42",
                "subject": "Fix bug",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix",
                "tier": 1,
            },
        ]

        with caplog.at_level(logging.WARNING, logger="golem.heartbeat"):
            with patch.object(mgr, "_run_tier1", return_value=candidates):
                await mgr._run_heartbeat_tick()

        assert 999 in mgr._inflight_task_ids
        assert any("999" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_tick_drops_non_integer_task_id(self, tmp_path):
        """submit_task returning an unconvertible task_id logs error and goes idle."""
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {
            "task_id": "not-an-int",
            "status": "submitted",
        }
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "github:42",
                "subject": "Fix bug",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix",
                "tier": 1,
            },
        ]

        with patch.object(mgr, "_run_tier1", return_value=candidates):
            await mgr._run_heartbeat_tick()

        # Should go idle and not add to inflight
        assert mgr._inflight_task_ids == []
        assert mgr._state == "idle"


class TestTrigger:
    """Tests for the force-trigger mechanism."""

    def test_trigger_before_start_returns_false(self, tmp_path):
        """trigger() returns False when loop not started (no event)."""
        mgr = _make_manager(tmp_path)
        assert mgr.trigger() is False

    @pytest.mark.asyncio
    async def test_trigger_after_start_returns_true(self, tmp_path):
        """trigger() returns True and sets event after start."""
        mgr = _make_manager(tmp_path, heartbeat_interval_seconds=999)
        mock_flow = MagicMock()
        mock_flow.live = MagicMock()
        mock_flow.live.snapshot.return_value = {"active_count": 0}
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
        mock_flow.live.snapshot.return_value = {"active_count": 5}  # not idle

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
        mock_flow.live.snapshot.return_value = {"active_count": 0}
        mgr._flow = mock_flow
        mgr._trigger_event = None

        loop_iterated = asyncio.Event()

        def _signal_side_effect(*_a, **_kw):
            loop_iterated.set()
            return {"active_count": 0}

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
