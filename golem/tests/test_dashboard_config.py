"""Tests for dashboard config API endpoints."""

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

try:
    import httpx
    from fastapi import FastAPI

    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

from golem.core.control_api import control_router, health_router, wire_control_api


def _make_client(app):
    """Return an httpx.AsyncClient wired to *app* via ASGI transport."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def _make_valid_config_data(**golem_overrides):
    """Return a minimal config dict that passes validate_config."""
    golem = {"profile": "github", "task_model": "sonnet", "projects": ["myproject"]}
    golem.update(golem_overrides)
    return {
        "flows": {"golem": golem},
        "dashboard": {"port": 8081, "admin_token": "secret"},
    }


@pytest.fixture
def config_app(tmp_path):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(control_router)
    app.include_router(health_router)
    cfg_path = tmp_path / "config.yaml"
    with open(cfg_path, "w") as f:
        yaml.safe_dump(_make_valid_config_data(), f)
    wire_control_api(admin_token="test-token", config_path=str(cfg_path))
    return app


@pytest.fixture
def config_app_no_token(tmp_path):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(control_router)
    app.include_router(health_router)
    cfg_path = tmp_path / "config.yaml"
    with open(cfg_path, "w") as f:
        yaml.safe_dump(_make_valid_config_data(), f)
    wire_control_api(admin_token="", config_path=str(cfg_path))
    return app


@pytest.fixture
def config_app_with_reload(tmp_path):
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(control_router)
    app.include_router(health_router)
    cfg_path = tmp_path / "config.yaml"
    with open(cfg_path, "w") as f:
        yaml.safe_dump(_make_valid_config_data(), f)
    reload_event = asyncio.Event()
    wire_control_api(
        admin_token="", config_path=str(cfg_path), reload_event=reload_event
    )
    return app, reload_event


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestConfigGetEndpoint:
    @pytest.mark.asyncio
    async def test_returns_categories(self, config_app):
        async with _make_client(config_app) as client:
            resp = await client.get(
                "/api/config", headers={"Authorization": "Bearer test-token"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "dashboard" in data

    @pytest.mark.asyncio
    async def test_sensitive_fields_redacted(self, config_app):
        async with _make_client(config_app) as client:
            resp = await client.get(
                "/api/config", headers={"Authorization": "Bearer test-token"}
            )
        data = resp.json()
        for field_info in data.get("dashboard", []):
            if field_info["meta"]["sensitive"]:
                assert field_info["value"] == "***"

    @pytest.mark.asyncio
    async def test_requires_admin(self, config_app):
        async with _make_client(config_app) as client:
            resp = await client.get("/api/config")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_field_has_expected_keys(self, config_app):
        async with _make_client(config_app) as client:
            resp = await client.get(
                "/api/config", headers={"Authorization": "Bearer test-token"}
            )
        data = resp.json()
        # Pick the first field from any category and verify structure
        for cat_fields in data.values():
            for fi in cat_fields:
                assert "key" in fi
                assert "value" in fi
                assert "meta" in fi
                meta = fi["meta"]
                assert "category" in meta
                assert "field_type" in meta
                assert "description" in meta
                assert "choices" in meta
                assert "min_val" in meta
                assert "max_val" in meta
                assert "sensitive" in meta
            break  # check only first category for brevity


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestConfigOpenAccess:
    @pytest.mark.asyncio
    async def test_get_open_when_no_token(self, config_app_no_token):
        async with _make_client(config_app_no_token) as client:
            resp = await client.get("/api/config")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_post_open_when_no_token(self, config_app_no_token):
        async with _make_client(config_app_no_token) as client:
            resp = await client.post(
                "/api/config/update",
                json={"golem.task_model": "opus"},
            )
        assert resp.status_code == 200


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestConfigUpdateEndpoint:
    @pytest.mark.asyncio
    async def test_valid_update(self, config_app):
        async with _make_client(config_app) as client:
            resp = await client.post(
                "/api/config/update",
                json={"golem.task_model": "opus"},
                headers={"Authorization": "Bearer test-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_invalid_update(self, config_app):
        async with _make_client(config_app) as client:
            resp = await client.post(
                "/api/config/update",
                json={"golem.task_model": "gpt4"},
                headers={"Authorization": "Bearer test-token"},
            )
        data = resp.json()
        assert data["success"] is False
        assert len(data["errors"]) > 0

    @pytest.mark.asyncio
    async def test_requires_admin(self, config_app):
        async with _make_client(config_app) as client:
            resp = await client.post(
                "/api/config/update", json={"golem.task_model": "opus"}
            )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_deferred_reload_sets_event(self, config_app_with_reload):
        app, reload_event = config_app_with_reload
        assert not reload_event.is_set()
        async with _make_client(app) as client:
            resp = await client.post(
                "/api/config/update",
                json={"golem.task_model": "opus"},
            )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # Wait for the deferred asyncio task (sleeps 0.5s then sets event)
        await asyncio.wait_for(reload_event.wait(), timeout=5.0)
        assert reload_event.is_set()


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestWireControlApiNewParams:
    def test_config_path_stored(self, tmp_path):
        from golem.core import control_api

        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text("{}\n")
        wire_control_api(config_path=str(cfg_path))
        assert control_api._config_path == str(cfg_path)

    def test_reload_event_stored(self, tmp_path):
        from golem.core import control_api

        ev = asyncio.Event()
        wire_control_api(reload_event=ev)
        assert control_api._reload_event is ev


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
class TestSelfUpdateEndpoint:
    """Tests for GET /api/self-update."""

    @pytest.fixture()
    def self_update_app(self):
        """Minimal FastAPI app with health_router, saves/restores module state."""
        import golem.core.control_api as _ctrl
        from fastapi import FastAPI

        from golem.core.control_api import health_router

        saved = _ctrl._self_update_manager
        app = FastAPI()
        if health_router is not None:
            app.include_router(health_router)
        yield app
        _ctrl._self_update_manager = saved

    @pytest.mark.asyncio
    async def test_disabled(self, self_update_app):
        """Returns {enabled: false} when no manager is wired."""
        wire_control_api(admin_token="test-token", self_update_manager=None)
        async with _make_client(self_update_app) as client:
            resp = await client.get("/api/self-update")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_with_manager(self, self_update_app):
        """Returns snapshot dict when a manager is wired."""
        mock_mgr = MagicMock()
        mock_mgr.snapshot.return_value = {
            "enabled": True,
            "branch": "master",
            "last_checked_sha": "abc123",
        }
        wire_control_api(admin_token="test-token", self_update_manager=mock_mgr)
        async with _make_client(self_update_app) as client:
            resp = await client.get("/api/self-update")
        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is True
        assert body["branch"] == "master"
        assert body["last_checked_sha"] == "abc123"
        mock_mgr.snapshot.assert_called_once()
        # Clean up
        wire_control_api(admin_token="test-token", self_update_manager=None)

    @pytest.mark.asyncio
    async def test_no_auth_required(self, self_update_app):
        """Endpoint does not require an admin token — it is read-only status info."""
        wire_control_api(admin_token="super-secret", self_update_manager=None)
        async with _make_client(self_update_app) as client:
            resp = await client.get("/api/self-update")
        assert resp.status_code == 200

    def test_self_update_manager_stored(self):
        """wire_control_api stores self_update_manager in the module global."""
        import golem.core.control_api as _ctrl

        mock_mgr = MagicMock()
        saved = _ctrl._self_update_manager
        try:
            wire_control_api(self_update_manager=mock_mgr)
            assert _ctrl._self_update_manager is mock_mgr
        finally:
            _ctrl._self_update_manager = saved
