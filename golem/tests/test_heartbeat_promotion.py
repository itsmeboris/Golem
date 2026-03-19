"""Tests for heartbeat Tier 1 promotion feature."""

import json
from unittest.mock import MagicMock, patch

from golem.heartbeat import HeartbeatManager
from golem.core.config import GolemFlowConfig


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
        heartbeat_batch_size=5,
        heartbeat_dedup_ttl_days=30,
        heartbeat_tier1_every_n=3,
    )
    defaults.update(overrides)
    return GolemFlowConfig(**defaults)


def _make_manager(tmp_path, **config_overrides) -> HeartbeatManager:
    cfg = _make_config(**config_overrides)
    return HeartbeatManager(cfg, state_dir=tmp_path)


class TestTier1PromotionConfig:
    def test_default_value(self):
        cfg = GolemFlowConfig()
        assert cfg.heartbeat_tier1_every_n == 3

    def test_parsed_from_yaml(self, tmp_path):
        from golem.core.config import load_config

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "flows:\n"
            "  golem:\n"
            "    projects: [test/repo]\n"
            "    heartbeat_enabled: true\n"
            "    heartbeat_tier1_every_n: 5\n"
        )
        config = load_config(cfg_file)
        assert config.golem.heartbeat_tier1_every_n == 5

    def test_validation_rejects_zero(self):
        from golem.core.config import Config, validate_config

        cfg = Config(
            golem=GolemFlowConfig(
                projects=["test/repo"],
                heartbeat_enabled=True,
                heartbeat_tier1_every_n=0,
            )
        )
        errors = validate_config(cfg)
        assert any("heartbeat_tier1_every_n" in e for e in errors)


class TestTier1PromotionState:
    def test_initial_state(self, tmp_path):
        mgr = _make_manager(tmp_path)
        assert mgr._tier2_completions_since_tier1 == 0
        assert mgr._tier1_owed is False

    def test_save_load_round_trip(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._tier2_completions_since_tier1 = 2
        mgr._tier1_owed = True
        mgr.save_state()

        mgr2 = _make_manager(tmp_path)
        mgr2.load_state()
        assert mgr2._tier2_completions_since_tier1 == 2
        assert mgr2._tier1_owed is True

    def test_load_missing_fields_defaults(self, tmp_path):
        state_file = tmp_path / "heartbeat_state.json"
        state_file.write_text(json.dumps({"daily_spend_usd": 0.1}))

        mgr = _make_manager(tmp_path)
        mgr.load_state()
        assert mgr._tier2_completions_since_tier1 == 0
        assert mgr._tier1_owed is False


class TestTier1PromotionCounter:
    def test_tier2_success_increments_counter(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_tier1_every_n=3)
        mgr._inflight_task_ids = [100]
        mgr._dedup_memory["improvement:eh:fix1"] = {
            "evaluated_at": "2026-03-16T10:00:00Z",
            "verdict": "submitted",
            "task_id": 100,
        }
        mgr.on_task_completed(100, success=True)
        assert mgr._tier2_completions_since_tier1 == 1

    def test_tier2_failure_does_not_increment(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_tier1_every_n=3)
        mgr._inflight_task_ids = [100]
        mgr._dedup_memory["improvement:eh:fix1"] = {
            "evaluated_at": "2026-03-16T10:00:00Z",
            "verdict": "submitted",
            "task_id": 100,
        }
        mgr.on_task_completed(100, success=False)
        assert mgr._tier2_completions_since_tier1 == 0

    def test_tier1_success_does_not_increment(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_tier1_every_n=3)
        mgr._inflight_task_ids = [100]
        mgr._dedup_memory["github:42"] = {
            "evaluated_at": "2026-03-16T10:00:00Z",
            "verdict": "submitted",
            "task_id": 100,
        }
        mgr.on_task_completed(100, success=True)
        assert mgr._tier2_completions_since_tier1 == 0

    def test_counter_sets_owed_flag_at_threshold(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_tier1_every_n=2)
        mgr._tier2_completions_since_tier1 = 1

        mgr._inflight_task_ids = [200]
        mgr._dedup_memory["improvement:rel:fix2"] = {
            "evaluated_at": "2026-03-16T10:00:00Z",
            "verdict": "submitted",
            "task_id": 200,
        }
        mgr.on_task_completed(200, success=True)
        assert mgr._tier2_completions_since_tier1 == 2
        assert mgr._tier1_owed is True

    def test_counter_does_not_set_owed_below_threshold(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_tier1_every_n=3)

        mgr._inflight_task_ids = [100]
        mgr._dedup_memory["improvement:eh:fix1"] = {
            "evaluated_at": "2026-03-16T10:00:00Z",
            "verdict": "submitted",
            "task_id": 100,
        }
        mgr.on_task_completed(100, success=True)
        assert mgr._tier2_completions_since_tier1 == 1
        assert mgr._tier1_owed is False

    def test_batch_completion_increments_by_one(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_tier1_every_n=3)
        mgr._inflight_task_ids = [300]
        for suffix in ("a", "b", "c"):
            mgr._dedup_memory[f"improvement:eh:{suffix}"] = {
                "evaluated_at": "2026-03-16T10:00:00Z",
                "verdict": "submitted",
                "task_id": 300,
            }
        mgr.on_task_completed(300, success=True)
        assert mgr._tier2_completions_since_tier1 == 1


class TestRunTier1Promoted:
    async def test_accepts_large_complexity(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Big feature", "body": "desc"},
        ]
        mgr._flow = mock_flow

        haiku_response = {
            "candidates": [
                {
                    "id": "github:42",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "large",
                    "reason": "Big but doable",
                },
            ]
        }

        with patch.object(mgr, "_call_haiku", return_value=haiku_response):
            candidates = await mgr._run_tier1_promoted()

        assert len(candidates) == 1
        assert candidates[0]["complexity"] == "large"

    async def test_does_not_dedup_non_candidates(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Good", "body": "desc"},
            {"id": 43, "subject": "Bad", "body": "desc"},
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
                    "reason": "No",
                },
            ]
        }

        with patch.object(mgr, "_call_haiku", return_value=haiku_response):
            await mgr._run_tier1_promoted()

        assert "github:42" in mgr._dedup_memory
        assert "github:43" not in mgr._dedup_memory

    async def test_returns_empty_when_no_issues(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = []
        mgr._flow = mock_flow

        candidates = await mgr._run_tier1_promoted()
        assert candidates == []

    async def test_handles_backend_exception(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.side_effect = OSError("fail")
        mgr._flow = mock_flow

        candidates = await mgr._run_tier1_promoted()
        assert candidates == []

    async def test_respects_budget(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_daily_budget_usd=0.01)
        mgr._daily_spend_usd = 0.01
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Bug", "body": "desc"},
        ]
        mgr._flow = mock_flow

        with patch.object(mgr, "_call_haiku") as mock_haiku:
            candidates = await mgr._run_tier1_promoted()

        mock_haiku.assert_not_called()
        assert candidates == []

    async def test_skips_deduped_issues(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._dedup_memory["github:42"] = {
            "evaluated_at": "2026-03-16T10:00:00Z",
            "verdict": "promoted",
        }
        mock_flow = MagicMock()
        mock_flow._profile.task_source.poll_untagged_tasks.return_value = [
            {"id": 42, "subject": "Already done", "body": "desc"},
        ]
        mgr._flow = mock_flow

        candidates = await mgr._run_tier1_promoted()
        assert candidates == []


class TestTier1PromotionTick:
    async def test_owed_tick_runs_promoted_scan(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._tier1_owed = True
        mgr._tier2_completions_since_tier1 = 3
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        promoted_candidates = [
            {
                "id": "github:42",
                "category": "github",
                "subject": "Big feature",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "large",
                "reason": "Doable",
            },
        ]

        with patch.object(mgr, "_run_tier1_promoted", return_value=promoted_candidates):
            with patch.object(mgr, "_run_tier2", return_value=[]):
                await mgr._run_heartbeat_tick()

        assert mock_flow.submit_task.called
        subject = mock_flow.submit_task.call_args[1].get("subject", "")
        assert "[PROMOTED]" in subject
        assert mgr._tier1_owed is False
        assert mgr._tier2_completions_since_tier1 == 0

    async def test_owed_tick_promoted_tracked_in_inflight(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._tier1_owed = True
        mgr._tier2_completions_since_tier1 = 3
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        promoted_candidates = [
            {
                "id": "github:42",
                "category": "github",
                "subject": "Feature",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix",
            },
        ]

        with patch.object(mgr, "_run_tier1_promoted", return_value=promoted_candidates):
            with patch.object(mgr, "_run_tier2", return_value=[]):
                await mgr._run_heartbeat_tick()

        assert 999 in mgr._inflight_task_ids

    async def test_owed_tick_records_promoted_in_dedup(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._tier1_owed = True
        mgr._tier2_completions_since_tier1 = 3
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        promoted_candidates = [
            {
                "id": "github:42",
                "category": "github",
                "subject": "Feature",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix",
            },
        ]

        with patch.object(mgr, "_run_tier1_promoted", return_value=promoted_candidates):
            with patch.object(mgr, "_run_tier2", return_value=[]):
                await mgr._run_heartbeat_tick()

        assert mgr._dedup_memory["github:42"]["verdict"] == "promoted"

    async def test_owed_tick_no_candidates_continues_tier2(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._tier1_owed = True
        mgr._tier2_completions_since_tier1 = 3
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        tier2_candidates = [
            {
                "id": "improvement:eh:a",
                "category": "error-handling",
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix",
                "subject": "Fix",
                "body": "desc",
                "automatable": True,
                "tier": 2,
            },
        ]

        with patch.object(mgr, "_run_tier1_promoted", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=tier2_candidates):
                with patch.object(mgr, "_run_tier1") as mock_normal_t1:
                    await mgr._run_heartbeat_tick()

        assert not mock_normal_t1.called
        assert mock_flow.submit_task.called
        assert mgr._tier1_owed is True

    async def test_owed_tick_skips_normal_tier1(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._tier1_owed = True
        mock_flow = MagicMock()
        mgr._flow = mock_flow

        with patch.object(mgr, "_run_tier1_promoted", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=[]):
                with patch.object(mgr, "_run_tier1") as mock_t1:
                    await mgr._run_heartbeat_tick()

        mock_t1.assert_not_called()

    async def test_not_owed_runs_normal_flow(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._tier1_owed = False
        mock_flow = MagicMock()
        mgr._flow = mock_flow

        with patch.object(mgr, "_run_tier1_promoted") as mock_promoted:
            with patch.object(mgr, "_run_tier1", return_value=[]):
                with patch.object(mgr, "_run_tier2", return_value=[]):
                    await mgr._run_heartbeat_tick()

        mock_promoted.assert_not_called()

    async def test_submit_promoted_exception_does_not_crash(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._tier1_owed = True
        mgr._tier2_completions_since_tier1 = 3
        mock_flow = MagicMock()
        mock_flow.submit_task.side_effect = RuntimeError("boom")
        mgr._flow = mock_flow

        promoted_candidates = [
            {
                "id": "github:42",
                "category": "github",
                "subject": "Feature",
                "body": "desc",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix",
            },
        ]

        with patch.object(mgr, "_run_tier1_promoted", return_value=promoted_candidates):
            with patch.object(mgr, "_run_tier2", return_value=[]):
                await mgr._run_heartbeat_tick()

        # Flag NOT cleared on failure
        assert mgr._tier1_owed is True


class TestPromotedTaskTracking:
    """Promoted tasks must store task_id and track via inflight so
    on_task_completed() can transition the verdict."""

    async def test_submit_promoted_stores_task_id_in_dedup(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 777, "status": "submitted"}
        mgr._flow = mock_flow

        candidate = {
            "id": "github:50",
            "category": "github",
            "subject": "Feature X",
            "body": "desc",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "Useful",
        }
        mgr._submit_promoted(candidate)

        entry = mgr._dedup_memory["github:50"]
        assert entry["verdict"] == "promoted"
        assert entry["task_id"] == 777

    async def test_submit_promoted_adds_to_inflight(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 777, "status": "submitted"}
        mgr._flow = mock_flow

        candidate = {
            "id": "github:50",
            "category": "github",
            "subject": "Feature X",
            "body": "desc",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "Useful",
        }
        mgr._submit_promoted(candidate)

        assert 777 in mgr._inflight_task_ids

    async def test_on_task_completed_transitions_promoted_verdict(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [777]
        mgr._dedup_memory["github:50"] = {
            "evaluated_at": "2026-03-17T00:00:00+00:00",
            "verdict": "promoted",
            "task_id": 777,
        }

        mgr.on_task_completed(777, success=True)

        assert mgr._dedup_memory["github:50"]["verdict"] == "completed"
        assert 777 not in mgr._inflight_task_ids

    async def test_submit_promoted_returns_early_on_invalid_task_id(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {
            "task_id": "not-an-int",
            "status": "submitted",
        }
        mgr._flow = mock_flow
        mgr._tier1_owed = True

        candidate = {
            "id": "github:50",
            "category": "github",
            "subject": "Feature X",
            "body": "desc",
            "automatable": True,
            "confidence": 0.9,
            "complexity": "small",
            "reason": "Useful",
        }
        mgr._submit_promoted(candidate)

        assert "github:50" not in mgr._dedup_memory
        assert mgr._inflight_task_ids == []
        # tier1_owed not cleared on failure
        assert mgr._tier1_owed is True

    async def test_on_task_completed_transitions_promoted_to_failed(self, tmp_path):
        mgr = _make_manager(tmp_path)
        mgr._inflight_task_ids = [777]
        mgr._dedup_memory["github:50"] = {
            "evaluated_at": "2026-03-17T00:00:00+00:00",
            "verdict": "promoted",
            "task_id": 777,
        }

        mgr.on_task_completed(777, success=False)

        assert mgr._dedup_memory["github:50"]["verdict"] == "failed"
        assert 777 not in mgr._inflight_task_ids
