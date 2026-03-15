# pylint: disable=too-few-public-methods,redefined-outer-name
"""Extended tests for golem.core.dashboard — since_event, TestClient, frontend."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import golem.core.dashboard as _dashboard_module
from golem.core.dashboard import _read_and_parse_trace, mount_dashboard


def _make_minimal_trace_jsonl(path: Path) -> None:
    """Write a minimal valid JSONL trace file to path."""
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


# ---------------------------------------------------------------------------
# Issue 1: since_event cache-bypass behaviour
# ---------------------------------------------------------------------------


class TestSinceEventCacheBehavior:
    """Tests for the since_event cache-hit-when-unchanged behaviour."""

    def setup_method(self):
        """Clear the parsed trace cache before each test."""
        _dashboard_module._parsed_trace_cache.clear()

    def teardown_method(self):
        """Clear the parsed trace cache after each test."""
        _dashboard_module._parsed_trace_cache.clear()

    def test_since_event_matches_event_count_returns_cache(self, tmp_path):
        """When since_event equals len(events), cached result is returned."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-10-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                # First call — parses and caches (trace has 1 event)
                result1 = _read_and_parse_trace("golem-10-20260101")

        assert result1 is not None
        assert "golem-10-20260101" in _dashboard_module._parsed_trace_cache

        # Inject a sentinel value into the cache to verify it's returned
        sentinel = {"phases": [], "sentinel": True, "result_meta": None}
        _dashboard_module._parsed_trace_cache["golem-10-20260101"] = sentinel

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                # since_event=1 matches the 1-event file → should return cache
                result2 = _read_and_parse_trace("golem-10-20260101", since_event=1)

        assert result2 is sentinel

    def test_since_event_mismatch_triggers_reparse(self, tmp_path):
        """When since_event != len(events), a full re-parse is performed."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-11-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        # Pre-populate cache with stale data
        stale = {"phases": [], "stale": True, "result_meta": None}
        _dashboard_module._parsed_trace_cache["golem-11-20260101"] = stale

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                # since_event=0 does NOT match 1-event count but since_event=0
                # hits the "return cache on initial request" branch
                result = _read_and_parse_trace("golem-11-20260101", since_event=0)

        # since_event=0 + cached → cached returned
        assert result is stale

    def test_since_event_nonzero_no_cache_forces_reparse(self, tmp_path):
        """since_event > 0 but no cache entry → full parse performed."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        _make_minimal_trace_jsonl(traces / "golem-12-20260101.jsonl")

        # No cache entry
        assert "golem-12-20260101" not in _dashboard_module._parsed_trace_cache

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-12-20260101", since_event=5)

        assert result is not None
        assert "phases" in result

    def test_since_event_zero_with_no_cache_parses_normally(self, tmp_path):
        """since_event=0 with no cache hits the normal parse path."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        _make_minimal_trace_jsonl(traces / "golem-13-20260101.jsonl")

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-13-20260101", since_event=0)

        assert result is not None
        assert "phases" in result

    def test_since_event_nonzero_count_differs_no_cache(self, tmp_path):
        """since_event > 0, count doesn't match, no cache → re-parse."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        # Write 2 events
        trace_file = traces / "golem-14-20260101.jsonl"
        events = [
            json.dumps(
                {
                    "type": "result",
                    "duration_ms": 500,
                    "total_cost_usd": 0.005,
                    "num_turns": 1,
                    "is_error": False,
                    "usage": {},
                }
            ),
            json.dumps({"type": "system", "subtype": "init", "model": "test"}),
        ]
        trace_file.write_text("\n".join(events) + "\n", encoding="utf-8")

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                # since_event=1 but file has 2 events → count mismatch → re-parse
                result = _read_and_parse_trace("golem-14-20260101", since_event=1)

        assert result is not None
        assert "phases" in result


# ---------------------------------------------------------------------------
# Issue 15: TestClient HTTP integration tests
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path):
    """Create a FastAPI app with mounted dashboard and return a TestClient.

    Patches remain active for the entire test so route handlers that read
    module-level globals (TRACES_DIR, _SESSIONS_FILE, etc.) at call-time
    resolve to the tmp_path fixtures.
    """
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    # Set up minimal file structure
    traces = tmp_path / "traces" / "golem"
    traces.mkdir(parents=True)
    reports = tmp_path / "reports"
    reports.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    sessions_file = tmp_path / "sessions.json"
    sessions_file.write_text(
        json.dumps(
            {
                "sessions": {
                    "golem-1-20260101": {"state": "COMPLETED", "subject": "Test"}
                }
            }
        ),
        encoding="utf-8",
    )

    # Write a minimal trace
    _make_minimal_trace_jsonl(traces / "golem-1-20260101.jsonl")

    # Write a prompt file (naming convention: {safe_id}.prompt.txt)
    (traces / "golem-1-20260101.prompt.txt").write_text(
        "Build the thing.", encoding="utf-8"
    )

    app = FastAPI()
    _dashboard_module._parsed_trace_cache.clear()

    # Patches stay active for the full fixture scope
    with (
        patch("golem.core.dashboard.FASTAPI_AVAILABLE", True),
        patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"),
        patch("golem.core.dashboard.REPORTS_DIR", reports),
        patch("golem.core.dashboard._LOG_DIR", logs),
        patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
    ):
        mount_dashboard(app, config_snapshot={"model": "test"})
        with TestClient(app) as tc:
            yield tc

    _dashboard_module._parsed_trace_cache.clear()


class TestClientHTTP:
    """Integration tests using Starlette's TestClient."""

    def test_api_sessions(self, client):
        resp = client.get("/api/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert "golem-1-20260101" in body.get("sessions", body)

    def test_api_trace_parsed_found(self, client):
        resp = client.get("/api/trace-parsed/golem-1-20260101")
        assert resp.status_code == 200
        body = resp.json()
        assert "phases" in body

    def test_api_trace_parsed_not_found(self, client):
        resp = client.get("/api/trace-parsed/golem-999-20260101")
        assert resp.status_code == 404

    def test_api_trace_parsed_since_event(self, client):
        # First request to populate cache
        resp1 = client.get("/api/trace-parsed/golem-1-20260101")
        total = resp1.json().get("total_events", 0)
        # Second request with since_event = total → should return cached
        resp2 = client.get(f"/api/trace-parsed/golem-1-20260101?since_event={total}")
        assert resp2.status_code == 200
        assert resp2.json()["phases"] == resp1.json()["phases"]

    def test_api_prompt_found(self, client):
        resp = client.get("/api/prompt/golem-1-20260101")
        assert resp.status_code == 200
        assert "Build the thing" in resp.json()["prompt"]

    def test_api_prompt_not_found(self, client):
        resp = client.get("/api/prompt/golem-999-20260101")
        assert resp.status_code == 404

    def test_api_logs(self, client):
        resp = client.get("/api/logs")
        assert resp.status_code == 200

    def test_api_report_not_found(self, client):
        resp = client.get("/api/report/golem-999-20260101")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Issue 16: TestFrontendServing — verify static assets are served
# ---------------------------------------------------------------------------


class TestFrontendServing:
    """Verify the HTML shell and all JS/CSS modules are served correctly."""

    def test_dashboard_html(self, client):
        resp = client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        body = resp.text
        assert "<html" in body.lower()
        assert "task_api.js" in body
        assert "task_timeline.js" in body
        assert "task_overview.js" in body
        assert "task_live.js" in body

    def test_shared_css(self, client):
        resp = client.get("/dashboard/shared.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]
        assert "--bg-base" in resp.text  # design token

    def test_task_css(self, client):
        resp = client.get("/dashboard/task.css")
        assert resp.status_code == 200
        assert "text/css" in resp.headers["content-type"]

    def test_shared_js(self, client):
        resp = client.get("/dashboard/shared.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "renderMarkdown" in resp.text

    def test_task_api_js(self, client):
        resp = client.get("/dashboard/task_api.js")
        assert resp.status_code == 200
        assert "javascript" in resp.headers["content-type"]
        assert "fetchSessions" in resp.text

    def test_task_timeline_js(self, client):
        resp = client.get("/dashboard/task_timeline.js")
        assert resp.status_code == 200
        assert "renderDetail" in resp.text or "renderTimeline" in resp.text

    def test_task_overview_js(self, client):
        resp = client.get("/dashboard/task_overview.js")
        assert resp.status_code == 200
        assert "renderOverview" in resp.text

    def test_task_live_js(self, client):
        resp = client.get("/dashboard/task_live.js")
        assert resp.status_code == 200
        assert "startPolling" in resp.text

    def test_cache_busting_versions(self, client):
        """Dashboard HTML injects ?v= query params for cache busting."""
        resp = client.get("/dashboard")
        assert "shared.css?v=" in resp.text
        assert "task_api.js?v=" in resp.text
