"""Tests for heartbeat→issue-close wiring via issue_mode."""

from unittest.mock import MagicMock, patch

import pytest

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


def _get_submit_kwargs(mock_flow: MagicMock) -> dict:
    """Extract kwargs from the most recent submit_task call."""
    return mock_flow.submit_task.call_args[1]


class TestSubmitTaskIssueMode:
    @pytest.mark.asyncio
    async def test_tier1_submit_passes_issue_mode(self, tmp_path):
        """Tier 1 single submission passes issue_mode=True."""
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        tier1_candidates = [
            {
                "id": "github:42",
                "category": "github",
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
            await mgr._run_heartbeat_tick()

        call_kwargs = _get_submit_kwargs(mock_flow)
        assert call_kwargs.get("issue_mode") is True

    @pytest.mark.asyncio
    async def test_tier2_submit_does_not_pass_issue_mode(self, tmp_path):
        """Tier 2 batch submission does NOT pass issue_mode."""
        mgr = _make_manager(tmp_path)
        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 999, "status": "submitted"}
        mgr._flow = mock_flow

        tier2_candidates = [
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
        ]

        with patch.object(mgr, "_run_tier1", return_value=[]):
            with patch.object(mgr, "_run_tier2", return_value=tier2_candidates):
                await mgr._run_heartbeat_tick()

        call_kwargs = _get_submit_kwargs(mock_flow)
        assert (
            "issue_mode" not in call_kwargs or call_kwargs.get("issue_mode") is not True
        )

    @pytest.mark.asyncio
    async def test_promoted_submit_passes_issue_mode(self, tmp_path):
        """Promoted Tier 1 submission passes issue_mode=True."""
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

        call_kwargs = _get_submit_kwargs(mock_flow)
        assert call_kwargs.get("issue_mode") is True
