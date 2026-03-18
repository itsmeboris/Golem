"""Tests for heartbeat Tier 2 batching — _group_candidates, category validation, batch submission."""

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
    )
    defaults.update(overrides)
    return GolemFlowConfig(**defaults)


def _make_manager(tmp_path, **config_overrides) -> HeartbeatManager:
    cfg = _make_config(**config_overrides)
    return HeartbeatManager(cfg, state_dir=tmp_path)


class TestExtractCategoryFromId:
    def test_improvement_id(self):
        assert HeartbeatManager._extract_category_from_id("improvement:eh:fix1") == "eh"

    def test_backend_id(self):
        assert HeartbeatManager._extract_category_from_id("github:42") == "github"

    def test_bare_string(self):
        assert HeartbeatManager._extract_category_from_id("badid") == ""


class TestGroupCandidates:
    def test_groups_by_category_field(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        candidates = [
            {
                "id": "improvement:error-handling:a",
                "category": "error-handling",
                "confidence": 0.9,
                "reason": "A",
            },
            {
                "id": "improvement:error-handling:b",
                "category": "error-handling",
                "confidence": 0.8,
                "reason": "B",
            },
            {
                "id": "improvement:reliability:c",
                "category": "reliability",
                "confidence": 0.85,
                "reason": "C",
            },
        ]
        batch = mgr._group_candidates(candidates)
        assert len(batch) == 2
        assert all(c["category"] == "error-handling" for c in batch)

    def test_caps_at_batch_size(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=2)
        candidates = [
            {
                "id": f"improvement:eh:{i}",
                "category": "error-handling",
                "confidence": 0.9,
                "reason": f"Fix {i}",
            }
            for i in range(5)
        ]
        batch = mgr._group_candidates(candidates)
        assert len(batch) == 2

    def test_picks_largest_group(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        candidates = [
            {
                "id": "improvement:eh:1",
                "category": "error-handling",
                "confidence": 0.9,
                "reason": "A",
            },
            {
                "id": "improvement:rel:1",
                "category": "reliability",
                "confidence": 0.9,
                "reason": "B",
            },
            {
                "id": "improvement:rel:2",
                "category": "reliability",
                "confidence": 0.85,
                "reason": "C",
            },
            {
                "id": "improvement:rel:3",
                "category": "reliability",
                "confidence": 0.8,
                "reason": "D",
            },
        ]
        batch = mgr._group_candidates(candidates)
        assert len(batch) == 3
        assert all(c["category"] == "reliability" for c in batch)

    def test_tie_breaks_by_highest_avg_confidence(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        candidates = [
            {
                "id": "improvement:eh:1",
                "category": "error-handling",
                "confidence": 0.9,
                "reason": "A",
            },
            {
                "id": "improvement:eh:2",
                "category": "error-handling",
                "confidence": 0.8,
                "reason": "B",
            },
            {
                "id": "improvement:rel:1",
                "category": "reliability",
                "confidence": 0.7,
                "reason": "C",
            },
            {
                "id": "improvement:rel:2",
                "category": "reliability",
                "confidence": 0.75,
                "reason": "D",
            },
        ]
        batch = mgr._group_candidates(candidates)
        assert len(batch) == 2
        assert all(c["category"] == "error-handling" for c in batch)

    def test_falls_back_to_id_prefix_when_no_category(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        candidates = [
            {"id": "improvement:error-handling:a", "confidence": 0.9, "reason": "A"},
            {"id": "improvement:error-handling:b", "confidence": 0.8, "reason": "B"},
            {"id": "improvement:reliability:c", "confidence": 0.85, "reason": "C"},
        ]
        batch = mgr._group_candidates(candidates)
        assert len(batch) == 2

    def test_skips_candidates_with_no_category_and_bad_id(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        candidates = [
            {"id": "badid", "confidence": 0.9, "reason": "A"},
            {
                "id": "improvement:eh:a",
                "category": "eh",
                "confidence": 0.8,
                "reason": "B",
            },
        ]
        batch = mgr._group_candidates(candidates)
        assert len(batch) == 1
        assert batch[0]["category"] == "eh"

    def test_empty_candidates_returns_empty(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        assert mgr._group_candidates([]) == []

    def test_all_unparseable_returns_empty(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        batch = mgr._group_candidates(
            [
                {"id": "bad1", "confidence": 0.9, "reason": "A"},
                {"id": "bad2", "confidence": 0.8, "reason": "B"},
            ]
        )
        assert batch == []


class TestValidateCandidatesCategory:
    def test_category_preserved_when_present(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "improvement:eh:1",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "Fix",
                    "category": "error-handling",
                }
            ]
        }
        result = mgr._validate_candidates(raw)
        assert result[0]["category"] == "error-handling"

    def test_category_extracted_from_id_when_missing(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "improvement:error-handling:fix1",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "Fix",
                }
            ]
        }
        result = mgr._validate_candidates(raw)
        assert result[0]["category"] == "error-handling"

    def test_category_normalized_to_lowercase(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "improvement:eh:1",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "Fix",
                    "category": "Error-Handling",
                }
            ]
        }
        result = mgr._validate_candidates(raw)
        assert result[0]["category"] == "error-handling"

    def test_candidate_skipped_when_unparseable(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "nocolon",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "Fix",
                }
            ]
        }
        result = mgr._validate_candidates(raw)
        assert result == []

    def test_empty_category_triggers_fallback(self, tmp_path):
        mgr = _make_manager(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "improvement:reliability:fix1",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "Fix",
                    "category": "",
                }
            ]
        }
        result = mgr._validate_candidates(raw)
        assert result[0]["category"] == "reliability"


class TestTier2BatchSubmission:
    async def test_tier2_submits_batch(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "improvement:eh:a",
                "category": "error-handling",
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix A",
                "subject": "Fix A",
                "body": "desc A",
                "automatable": True,
                "tier": 2,
            },
            {
                "id": "improvement:eh:b",
                "category": "error-handling",
                "confidence": 0.85,
                "complexity": "small",
                "reason": "Fix B",
                "subject": "Fix B",
                "body": "desc B",
                "automatable": True,
                "tier": 2,
            },
        ]
        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=candidates):
                await mgr._run_heartbeat_tick()

        subject = mock_flow.submit_task.call_args.kwargs.get("subject", "")
        assert "batch:" in subject
        assert "2 items" in subject

    async def test_tier2_batch_dedups_all(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "improvement:eh:a",
                "category": "error-handling",
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix A",
                "subject": "Fix A",
                "body": "A",
                "automatable": True,
                "tier": 2,
            },
            {
                "id": "improvement:eh:b",
                "category": "error-handling",
                "confidence": 0.85,
                "complexity": "small",
                "reason": "Fix B",
                "subject": "Fix B",
                "body": "B",
                "automatable": True,
                "tier": 2,
            },
        ]
        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=candidates):
                await mgr._run_heartbeat_tick()

        assert "improvement:eh:a" in mgr._dedup_memory
        assert "improvement:eh:b" in mgr._dedup_memory
        assert mgr._dedup_memory["improvement:eh:a"]["task_id"] == 999

    async def test_tier2_prompt_lists_items(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "improvement:eh:a",
                "category": "error-handling",
                "confidence": 0.9,
                "complexity": "small",
                "reason": "Fix A",
                "subject": "A",
                "body": "A",
                "automatable": True,
                "tier": 2,
            },
            {
                "id": "improvement:eh:b",
                "category": "error-handling",
                "confidence": 0.85,
                "complexity": "small",
                "reason": "Fix B",
                "subject": "B",
                "body": "B",
                "automatable": True,
                "tier": 2,
            },
        ]
        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=candidates):
                await mgr._run_heartbeat_tick()

        prompt = mock_flow.submit_task.call_args.kwargs.get("prompt", "")
        assert "Fix A" in prompt
        assert "Fix B" in prompt

    async def test_tier1_still_single(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "github:42",
                "category": "github",
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

        subject = mock_flow.submit_task.call_args.kwargs.get("subject", "")
        assert "batch:" not in subject
        assert "[HEARTBEAT]" in subject

    async def test_tier2_empty_batch_goes_idle(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        mock_flow = MagicMock()
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "badid",
                "confidence": 0.9,
                "complexity": "small",
                "reason": "A",
                "automatable": True,
                "tier": 2,
            }
        ]
        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=candidates):
                await mgr._run_heartbeat_tick()

        mock_flow.submit_task.assert_not_called()
        assert mgr._state == "idle"

    async def test_batch_submit_bad_task_id(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {
            "task_id": "not-an-int",
            "status": "submitted",
        }
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "improvement:eh:a",
                "category": "error-handling",
                "confidence": 0.9,
                "complexity": "small",
                "reason": "A",
                "automatable": True,
                "tier": 2,
            }
        ]
        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=candidates):
                await mgr._run_heartbeat_tick()

        assert mgr._inflight_task_ids == []
        assert mgr._state == "idle"

    async def test_batch_submit_exception(self, tmp_path):
        mgr = _make_manager(tmp_path, heartbeat_batch_size=5)
        mock_flow = MagicMock()
        mock_flow.submit_task.side_effect = RuntimeError("boom")
        mgr._flow = mock_flow

        candidates = [
            {
                "id": "improvement:eh:a",
                "category": "error-handling",
                "confidence": 0.9,
                "complexity": "small",
                "reason": "A",
                "automatable": True,
                "tier": 2,
            }
        ]
        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=candidates):
                await mgr._run_heartbeat_tick()

        assert mgr._state == "idle"


class TestBatchSizeConfig:
    def test_default_value(self):
        cfg = GolemFlowConfig()
        assert cfg.heartbeat_batch_size == 5

    def test_parsed_from_yaml(self, tmp_path):
        from golem.core.config import load_config

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "flows:\n"
            "  golem:\n"
            "    projects: [test/repo]\n"
            "    heartbeat_enabled: true\n"
            "    heartbeat_batch_size: 3\n"
        )
        config = load_config(cfg_file)
        assert config.golem.heartbeat_batch_size == 3

    def test_validation_rejects_zero(self):
        from golem.core.config import Config, validate_config

        cfg = Config(
            golem=GolemFlowConfig(
                projects=["test/repo"],
                heartbeat_enabled=True,
                heartbeat_batch_size=0,
            )
        )
        errors = validate_config(cfg)
        assert any("heartbeat_batch_size" in e for e in errors)
