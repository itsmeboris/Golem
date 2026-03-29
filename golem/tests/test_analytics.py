"""Tests for quality metrics analytics pipeline."""

import json
from unittest.mock import MagicMock, patch

import pytest

from golem.analytics import compute_analytics, compute_prompt_analytics


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
            {
                "verdict": "PARTIAL",
                "cost_usd": 8.0,
                "success": False,
                "duration_s": 120,
            },
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
    @pytest.fixture(autouse=True)
    def _bypass_api_key(self):
        with patch("golem.core.dashboard._require_api_key"):
            yield

    @pytest.fixture()
    def handlers(self):
        from golem.core.dashboard import mount_dashboard

        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **_kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(app, _config_snapshot={}, live_state_file=None)
        return routes

    async def test_api_analytics_endpoint_exists(self, handlers):
        assert "/api/analytics" in handlers

    async def test_api_analytics_returns_json(self, handlers):
        with patch(
            "golem.core.dashboard.read_runs",
            return_value=[
                {"verdict": "PASS", "cost_usd": 5.0, "duration_s": 60},
            ],
        ):
            resp = await handlers["/api/analytics"](MagicMock())
        body = json.loads(resp.body)
        assert body["total_tasks"] == 1
        assert body["pass_rate"] == 1.0

    async def test_api_analytics_by_prompt_endpoint_exists(self, handlers):
        assert "/api/analytics/by-prompt" in handlers

    async def test_api_analytics_by_prompt_returns_json(self, handlers):
        with patch(
            "golem.core.dashboard.read_runs",
            return_value=[
                {
                    "prompt_hash": "abc123def456",
                    "success": True,
                    "cost_usd": 4.0,
                    "duration_s": 60,
                },
                {
                    "prompt_hash": "abc123def456",
                    "success": False,
                    "cost_usd": 6.0,
                    "duration_s": 90,
                },
            ],
        ):
            resp = await handlers["/api/analytics/by-prompt"](MagicMock())
        body = json.loads(resp.body)
        assert len(body) == 1
        assert body[0]["prompt_hash"] == "abc123def456"
        assert body[0]["run_count"] == 2
        assert body[0]["success_rate"] == 0.5


class TestComputePromptAnalytics:
    def test_empty_runs_returns_empty_list(self):
        assert compute_prompt_analytics([]) == []

    def test_runs_without_prompt_hash_excluded(self):
        runs = [
            {"success": True, "cost_usd": 1.0, "duration_s": 10},
            {"prompt_hash": "", "success": True, "cost_usd": 1.0, "duration_s": 10},
        ]
        assert compute_prompt_analytics(runs) == []

    def test_groups_by_prompt_hash(self):
        runs = [
            {
                "prompt_hash": "aaa",
                "success": True,
                "cost_usd": 2.0,
                "duration_s": 20,
            },
            {
                "prompt_hash": "bbb",
                "success": False,
                "cost_usd": 4.0,
                "duration_s": 40,
            },
        ]
        result = compute_prompt_analytics(runs)
        hashes = {r["prompt_hash"] for r in result}
        assert hashes == {"aaa", "bbb"}

    def test_correct_stats_per_group(self):
        runs = [
            {
                "prompt_hash": "abc",
                "success": True,
                "cost_usd": 2.0,
                "duration_s": 20,
            },
            {
                "prompt_hash": "abc",
                "success": False,
                "cost_usd": 4.0,
                "duration_s": 40,
            },
        ]
        result = compute_prompt_analytics(runs)
        assert len(result) == 1
        entry = result[0]
        assert entry["run_count"] == 2
        assert entry["success_rate"] == 0.5
        assert entry["avg_cost_usd"] == 3.0
        assert entry["avg_duration_s"] == 30.0

    def test_sorted_by_run_count_descending(self):
        runs = [
            {"prompt_hash": "one", "success": True, "cost_usd": 1.0, "duration_s": 10},
            {
                "prompt_hash": "three",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10,
            },
            {
                "prompt_hash": "three",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10,
            },
            {
                "prompt_hash": "three",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10,
            },
            {"prompt_hash": "two", "success": True, "cost_usd": 1.0, "duration_s": 10},
            {"prompt_hash": "two", "success": True, "cost_usd": 1.0, "duration_s": 10},
        ]
        result = compute_prompt_analytics(runs)
        assert result[0]["prompt_hash"] == "three"
        assert result[1]["prompt_hash"] == "two"
        assert result[2]["prompt_hash"] == "one"

    def test_single_hash_all_success(self):
        runs = [
            {"prompt_hash": "xyz", "success": True, "cost_usd": 5.0, "duration_s": 50},
        ]
        result = compute_prompt_analytics(runs)
        assert len(result) == 1
        assert result[0]["success_rate"] == 1.0
        assert result[0]["avg_cost_usd"] == 5.0
        assert result[0]["avg_duration_s"] == 50.0

    def test_missing_fields_default_to_zero(self):
        runs = [{"prompt_hash": "xyz"}]
        result = compute_prompt_analytics(runs)
        assert result[0]["run_count"] == 1
        assert result[0]["success_rate"] == 0.0
        assert result[0]["avg_cost_usd"] == 0.0
        assert result[0]["avg_duration_s"] == 0.0

    def test_none_values_treated_as_zero(self):
        runs = [
            {"prompt_hash": "xyz", "cost_usd": None, "duration_s": None},
        ]
        result = compute_prompt_analytics(runs)
        assert result[0]["avg_cost_usd"] == 0.0
        assert result[0]["avg_duration_s"] == 0.0
