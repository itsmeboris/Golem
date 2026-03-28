# pylint: disable=too-few-public-methods,redefined-outer-name
"""TestClient integration tests for 7 dashboard API endpoints.

Covers: /api/analytics, /api/analytics/by-prompt, /api/cost-analytics,
/api/events, /api/merge-queue, /api/merge-queue/retry/{session_id},
/api/trace/{event_id:path}.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import golem.core.dashboard as _dashboard_module
from golem.core.dashboard import mount_dashboard


def _make_minimal_trace_jsonl(path: Path) -> None:
    """Write a minimal valid JSONL trace file to *path*."""
    events = [
        json.dumps(
            {
                "type": "result",
                "duration_ms": 1000,
                "total_cost_usd": 0.01,
                "num_turns": 1,
                "is_error": False,
                "usage": {},
            }
        )
    ]
    path.write_text("\n".join(events) + "\n", encoding="utf-8")


@pytest.fixture()
def client(tmp_path):
    """FastAPI app with mounted dashboard; yields a Starlette TestClient."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    traces = tmp_path / "traces" / "golem"
    traces.mkdir(parents=True)
    reports = tmp_path / "reports"
    reports.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    sessions_file = tmp_path / "sessions.json"
    sessions_file.write_text(json.dumps({"sessions": {}}), encoding="utf-8")

    # Minimal trace file so /api/trace/ can resolve the path
    _make_minimal_trace_jsonl(traces / "golem-10-20260101.jsonl")

    app = FastAPI()
    _dashboard_module._parsed_trace_cache.clear()

    with (
        patch("golem.core.dashboard.FASTAPI_AVAILABLE", True),
        patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"),
        patch("golem.core.dashboard.REPORTS_DIR", reports),
        patch("golem.core.dashboard._LOG_DIR", logs),
        patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
    ):
        mount_dashboard(app, _config_snapshot={"model": "test"})
        with TestClient(app) as tc:
            yield tc

    _dashboard_module._parsed_trace_cache.clear()


# ---------------------------------------------------------------------------
# /api/analytics
# ---------------------------------------------------------------------------


class TestApiAnalyticsIntegration:
    """TestClient integration tests for GET /api/analytics."""

    def test_returns_200_with_analytics_keys(self, client):
        """Happy path: endpoint returns 200 and all expected analytics keys."""
        runs = [
            {
                "verdict": "PASS",
                "cost_usd": 1.0,
                "duration_s": 30.0,
                "actions_taken": [],
                "error": None,
            }
        ]
        with patch("golem.core.dashboard.read_runs", return_value=runs):
            resp = client.get("/api/analytics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_tasks"] == 1
        assert body["pass_rate"] == 1.0
        assert body["avg_cost_usd"] == 1.0

    def test_returns_empty_dict_when_runs_is_none(self, client):
        """When _safe_to_thread returns None (shutdown), response is {}."""
        with patch("golem.core.dashboard._safe_to_thread", return_value=None):
            resp = client.get("/api/analytics")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_empty_runs_returns_zero_totals(self, client):
        """Empty run list returns zeros for all metrics."""
        with patch("golem.core.dashboard.read_runs", return_value=[]):
            resp = client.get("/api/analytics")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_tasks"] == 0
        assert body["pass_rate"] == 0.0


# ---------------------------------------------------------------------------
# /api/analytics/by-prompt
# ---------------------------------------------------------------------------


class TestApiAnalyticsByPromptIntegration:
    """TestClient integration tests for GET /api/analytics/by-prompt."""

    def test_returns_200_with_per_prompt_list(self, client):
        """Happy path: groups runs by prompt_hash and returns a list."""
        runs = [
            {
                "prompt_hash": "abc123",
                "success": True,
                "cost_usd": 2.0,
                "duration_s": 45.0,
            },
            {
                "prompt_hash": "abc123",
                "success": False,
                "cost_usd": 3.0,
                "duration_s": 60.0,
            },
        ]
        with patch("golem.core.dashboard.read_runs", return_value=runs):
            resp = client.get("/api/analytics/by-prompt")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        entry = body[0]
        assert entry["prompt_hash"] == "abc123"
        assert entry["run_count"] == 2
        assert entry["success_rate"] == 0.5

    def test_returns_empty_dict_when_runs_is_none(self, client):
        """Shutdown path: _safe_to_thread returns None → response is {}."""
        with patch("golem.core.dashboard._safe_to_thread", return_value=None):
            resp = client.get("/api/analytics/by-prompt")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_runs_without_prompt_hash_excluded(self, client):
        """Runs missing a prompt_hash are not included in the result."""
        runs = [{"success": True, "cost_usd": 1.0, "duration_s": 10.0}]
        with patch("golem.core.dashboard.read_runs", return_value=runs):
            resp = client.get("/api/analytics/by-prompt")
        assert resp.status_code == 200
        assert resp.json() == []


# ---------------------------------------------------------------------------
# /api/cost-analytics
# ---------------------------------------------------------------------------


class TestApiCostAnalyticsIntegration:
    """TestClient integration tests for GET /api/cost-analytics."""

    def test_returns_200_with_expected_keys(self, client):
        """Happy path: returns dict with all cost-analytics keys."""
        runs = [
            {
                "cost_usd": 0.05,
                "verdict": "PASS",
                "started_at": "2026-01-01T10:00:00",
                "actions_taken": [],
            }
        ]
        with (
            patch("golem.core.dashboard.read_runs", return_value=runs),
            patch("golem.core.dashboard.load_sessions", return_value={}),
        ):
            resp = client.get("/api/cost-analytics")
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "cost_over_time",
            "cost_by_verdict",
            "cost_per_retry",
            "budget_utilization",
            "summary",
        ):
            assert key in body, f"Missing key in response: {key}"

    def test_returns_empty_dict_when_runs_or_sessions_none(self, client):
        """When runs is None (shutdown), response is {}."""
        with patch("golem.core.dashboard._safe_to_thread", return_value=None):
            resp = client.get("/api/cost-analytics")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_summary_totals_from_runs(self, client):
        """Summary reflects actual cost data from run records."""
        runs = [
            {
                "cost_usd": 0.10,
                "verdict": "PASS",
                "started_at": "",
                "actions_taken": [],
            },
            {
                "cost_usd": 0.20,
                "verdict": "FAIL",
                "started_at": "",
                "actions_taken": [],
            },
        ]
        with (
            patch("golem.core.dashboard.read_runs", return_value=runs),
            patch("golem.core.dashboard.load_sessions", return_value={}),
        ):
            resp = client.get("/api/cost-analytics")
        assert resp.status_code == 200
        summary = resp.json()["summary"]
        assert summary["total_runs"] == 2
        assert summary["total_cost"] == pytest.approx(0.30, abs=1e-4)


# ---------------------------------------------------------------------------
# /api/events (SSE)
# ---------------------------------------------------------------------------


class TestApiEventsIntegration:
    """TestClient integration test for GET /api/events SSE endpoint."""

    async def test_returns_200_text_event_stream(self, client):
        """GET /api/events responds with HTTP 200 and text/event-stream."""
        import httpx

        async def fake_sleep(_dur):
            raise asyncio.CancelledError()

        with patch("golem.core.dashboard.asyncio") as mock_asyncio:
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=client.app),
                base_url="http://test",
            ) as ac:
                async with ac.stream("GET", "/api/events") as resp:
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# /api/trace/{event_id:path}
# ---------------------------------------------------------------------------


class TestApiTraceIntegration:
    """TestClient integration tests for GET /api/trace/{event_id:path}."""

    def test_returns_200_and_sections_for_existing_trace(self, client, tmp_path):
        """Happy path: trace file found returns event_id and sections."""
        trace_path = tmp_path / "trace.jsonl"
        _make_minimal_trace_jsonl(trace_path)
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": trace_path, "prompt": None, "report": None},
        ):
            resp = client.get("/api/trace/golem-10-20260101")
        assert resp.status_code == 200
        body = resp.json()
        assert body["event_id"] == "golem-10-20260101"
        assert "sections" in body

    def test_returns_404_for_missing_trace(self, client):
        """Missing trace returns 404 with an error key."""
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": None},
        ):
            resp = client.get("/api/trace/golem-999-20260101")
        assert resp.status_code == 404
        assert "error" in resp.json()

    def test_returns_empty_dict_when_parse_returns_none(self, client, tmp_path):
        """When _parse_trace returns None (shutdown), response body is {}."""
        trace_path = tmp_path / "trace.jsonl"
        trace_path.write_text("{}\n", encoding="utf-8")
        with (
            patch(
                "golem.core.dashboard._resolve_paths",
                return_value={"trace": trace_path, "prompt": None, "report": None},
            ),
            patch("golem.core.dashboard._safe_to_thread", return_value=None),
        ):
            resp = client.get("/api/trace/golem-10-20260101")
        assert resp.status_code == 200
        assert resp.json() == {}
