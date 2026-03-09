"""Tests for quality metrics analytics pipeline."""

import json
from unittest.mock import MagicMock, patch

import pytest

from golem.analytics import compute_analytics


class TestComputeAnalytics:
    def test_empty_runs_returns_defaults(self):
        """No run records returns zero-valued analytics."""
        result = compute_analytics([])
        assert result["total_tasks"] == 0
        assert result["pass_rate"] == 0.0
        assert result["avg_cost_usd"] == 0.0

    def test_computes_pass_rate(self):
        """Pass rate is computed correctly from verdict field."""
        runs = [
            {"verdict": "PASS", "cost_usd": 5.0, "success": True, "duration_s": 60},
            {"verdict": "PASS", "cost_usd": 3.0, "success": True, "duration_s": 45},
            {"verdict": "PARTIAL", "cost_usd": 8.0, "success": False, "duration_s": 120},
            {"verdict": "FAIL", "cost_usd": 10.0, "success": False, "duration_s": 180},
        ]
        result = compute_analytics(runs)
        assert result["total_tasks"] == 4
        assert result["pass_rate"] == 0.5
        assert result["partial_rate"] == 0.25
        assert result["fail_rate"] == 0.25

    def test_computes_average_cost(self):
        """Average cost is computed across all tasks."""
        runs = [
            {"verdict": "PASS", "cost_usd": 4.0, "success": True, "duration_s": 60},
            {"verdict": "PASS", "cost_usd": 6.0, "success": True, "duration_s": 90},
        ]
        result = compute_analytics(runs)
        assert result["avg_cost_usd"] == 5.0

    def test_computes_retry_effectiveness(self):
        """Retry effectiveness: % of retried tasks that eventually passed."""
        runs = [
            {
                "verdict": "PASS",
                "cost_usd": 5.0,
                "success": True,
                "duration_s": 60,
                "actions_taken": ["retries:1", "verdict:PASS"],
            },
            {
                "verdict": "FAIL",
                "cost_usd": 10.0,
                "success": False,
                "duration_s": 120,
                "actions_taken": ["retries:1", "verdict:FAIL"],
            },
        ]
        result = compute_analytics(runs)
        assert result["retry_effectiveness"] == 0.5

    def test_groups_failure_reasons(self):
        """Common failure categories are extracted from error field."""
        runs = [
            {
                "verdict": "FAIL",
                "cost_usd": 5.0,
                "success": False,
                "duration_s": 60,
                "error": "Worktree creation failed",
            },
            {
                "verdict": "FAIL",
                "cost_usd": 5.0,
                "success": False,
                "duration_s": 60,
                "error": "Worktree creation failed",
            },
            {
                "verdict": "FAIL",
                "cost_usd": 5.0,
                "success": False,
                "duration_s": 60,
                "error": "Agent timeout after 3600s",
            },
        ]
        result = compute_analytics(runs)
        assert result["top_failure_reasons"][0][0] == "Worktree creation failed"
        assert result["top_failure_reasons"][0][1] == 2

    def test_computes_average_duration(self):
        """Average duration is computed across all tasks."""
        runs = [
            {"verdict": "PASS", "cost_usd": 5.0, "success": True, "duration_s": 100},
            {"verdict": "PASS", "cost_usd": 5.0, "success": True, "duration_s": 200},
        ]
        result = compute_analytics(runs)
        assert result["avg_duration_s"] == 150.0

    def test_handles_missing_fields_gracefully(self):
        """Records with missing fields don't crash the analytics."""
        runs = [
            {"verdict": "PASS"},
            {"success": True},
            {},
        ]
        result = compute_analytics(runs)
        assert result["total_tasks"] == 3

    def test_retries_zero_not_counted(self):
        """A run with retries:0 is not considered retried."""
        runs = [
            {
                "verdict": "PASS",
                "cost_usd": 5.0,
                "success": True,
                "duration_s": 60,
                "actions_taken": ["retries:0"],
            },
        ]
        result = compute_analytics(runs)
        assert result["retry_effectiveness"] == 0.0

    def test_no_retried_tasks_gives_zero_effectiveness(self):
        """When no tasks were retried, retry_effectiveness is 0."""
        runs = [
            {"verdict": "PASS", "cost_usd": 5.0, "success": True, "duration_s": 60},
        ]
        result = compute_analytics(runs)
        assert result["retry_effectiveness"] == 0.0


class TestAnalyticsEndpoint:
    @pytest.fixture()
    def handlers(self):
        from golem.core.dashboard import mount_dashboard

        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(app, config_snapshot={}, live_state_file=None)
        return routes

    @pytest.mark.asyncio
    async def test_api_analytics_endpoint_exists(self, handlers):
        assert "/api/analytics" in handlers

    @pytest.mark.asyncio
    async def test_api_analytics_returns_json(self, handlers):
        with patch(
            "golem.core.dashboard.read_runs",
            return_value=[
                {"verdict": "PASS", "cost_usd": 5.0, "duration_s": 60},
            ],
        ):
            resp = await handlers["/api/analytics"]()
        body = json.loads(resp.body)
        assert body["total_tasks"] == 1
        assert body["pass_rate"] == 1.0
