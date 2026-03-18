# pylint: disable=too-few-public-methods
"""Integration smoke tests for the assembled Golem FastAPI application.

Tests require external HTTP stack and are excluded from the default pytest run.
Run with: pytest -m integration
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def app_client():
    """Assemble a real FastAPI app and return a wired httpx.AsyncClient.

    Mirrors the assembly from golem/cli.py:356-368.  Wires control_api with
    mock dependencies so all five smoke-test endpoints are exercisable.
    Tears down by resetting module state to clean defaults.
    """
    import httpx
    from fastapi import FastAPI

    import golem.core.control_api as _ctrl
    from golem.core.control_api import (
        control_router,
        health_router,
        wire_control_api,
    )
    from golem.core.dashboard import mount_dashboard

    # Save pre-existing module-level state before wiring.
    saved = (
        _ctrl._polling_trigger,
        _ctrl._dispatcher,
        _ctrl._admin_token,
        _ctrl._api_key,
        _ctrl._golem_flow,
        _ctrl._start_time,
    )

    # Build mock GolemFlow — submit_task must return a plain dict.
    mock_flow = MagicMock()
    mock_flow.submit_task.return_value = {"task_id": 42, "status": "submitted"}

    # Build mock PollingTrigger — flow_status must return a plain dict.
    mock_trigger = MagicMock()
    mock_trigger.flow_status.return_value = {"golem": "running"}

    wire_control_api(
        polling_trigger=mock_trigger,
        golem_flow=mock_flow,
    )

    app = FastAPI(title="Golem Dashboard")
    mount_dashboard(app, _config_snapshot={}, live_state_file=None)
    if control_router is not None:
        app.include_router(control_router)
    if health_router is not None:
        app.include_router(health_router)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    # Restore pre-existing module state to avoid corrupting other tests.
    (
        _ctrl._polling_trigger,
        _ctrl._dispatcher,
        _ctrl._admin_token,
        _ctrl._api_key,
        _ctrl._golem_flow,
        _ctrl._start_time,
    ) = saved


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


class TestIntegrationSmoke:
    """End-to-end HTTP smoke tests against the assembled FastAPI app."""

    @pytest.mark.integration
    async def test_health_returns_200_with_required_fields(self, app_client):
        """GET /api/health returns 200 with ok, pid, and uptime_seconds."""
        resp = await app_client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "pid" in body
        assert "uptime_seconds" in body

    @pytest.mark.integration
    async def test_submit_returns_200_with_task_id(self, app_client):
        """POST /api/submit with a valid prompt returns 200 with task_id and status."""
        resp = await app_client.post(
            "/api/submit",
            json={"prompt": "test task", "subject": "smoke"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["task_id"] == 42
        assert body["status"] == "submitted"

    @pytest.mark.integration
    async def test_dashboard_returns_html(self, app_client):
        """GET /dashboard returns 200 with text/html content-type."""
        resp = await app_client.get("/dashboard")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    @pytest.mark.integration
    async def test_ping_returns_ok(self, app_client):
        """GET /api/ping returns 200 with status=ok."""
        resp = await app_client.get("/api/ping")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    @pytest.mark.integration
    async def test_flow_status_returns_ok(self, app_client):
        """GET /api/flow/status returns 200 with ok=True."""
        resp = await app_client.get("/api/flow/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert "flows" in body
        assert body["flows"] == {"golem": "running"}
