# pylint: disable=too-few-public-methods
"""Tests for Task 6 plumbing: _touch_merge_sentinel, merge_queue param threading."""

from unittest.mock import AsyncMock, MagicMock, patch

from golem.core.config import Config, GolemFlowConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_profile(tmp_path):
    from golem.backends.local import (
        LocalFileTaskSource,
        LogNotifier,
        NullStateBackend,
        NullToolProvider,
    )
    from golem.profile import GolemProfile
    from golem.prompts import FilePromptProvider

    return GolemProfile(
        name="test",
        task_source=LocalFileTaskSource(str(tmp_path / "test-tasks")),
        state_backend=NullStateBackend(),
        notifier=LogNotifier(),
        tool_provider=NullToolProvider(),
        prompt_provider=FilePromptProvider(None),
    )


def _make_flow(monkeypatch, tmp_path):
    from golem.flow import GolemFlow

    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

    profile = _make_test_profile(tmp_path)
    config = Config(
        golem=GolemFlowConfig(enabled=True, projects=["test-project"], profile="test")
    )
    monkeypatch.setattr(
        "golem.flow.build_profile",
        lambda _name, _cfg: profile,
    )
    return GolemFlow(config)


# ---------------------------------------------------------------------------
# Step 1: _touch_merge_sentinel
# ---------------------------------------------------------------------------


class TestTouchMergeSentinel:
    def test_creates_sentinel_file(self, monkeypatch, tmp_path):
        """_touch_merge_sentinel creates the sentinel file under DATA_DIR/state."""
        data_dir = tmp_path / "data"
        monkeypatch.setattr("golem.flow.DATA_DIR", data_dir)
        flow = _make_flow(monkeypatch, tmp_path)

        flow._touch_merge_sentinel()

        sentinel = data_dir / "state" / ".merge_queue_updated"
        assert sentinel.exists()

    def test_creates_parent_dirs(self, monkeypatch, tmp_path):
        """_touch_merge_sentinel creates parent directories if missing."""
        data_dir = tmp_path / "nonexistent" / "data"
        monkeypatch.setattr("golem.flow.DATA_DIR", data_dir)
        flow = _make_flow(monkeypatch, tmp_path)

        flow._touch_merge_sentinel()

        sentinel = data_dir / "state" / ".merge_queue_updated"
        assert sentinel.exists()

    def test_idempotent_touch(self, monkeypatch, tmp_path):
        """Calling _touch_merge_sentinel twice succeeds without error."""
        data_dir = tmp_path / "data"
        monkeypatch.setattr("golem.flow.DATA_DIR", data_dir)
        flow = _make_flow(monkeypatch, tmp_path)

        flow._touch_merge_sentinel()
        flow._touch_merge_sentinel()

        sentinel = data_dir / "state" / ".merge_queue_updated"
        assert sentinel.exists()


# ---------------------------------------------------------------------------
# Step 1: MergeQueue wired with on_state_change
# ---------------------------------------------------------------------------


class TestMergeQueueOnStateChange:
    def test_merge_queue_has_on_state_change_callback(self, monkeypatch, tmp_path):
        """GolemFlow wires _touch_merge_sentinel as on_state_change callback."""
        flow = _make_flow(monkeypatch, tmp_path)
        # The private callback should be set to the method
        assert flow._merge_queue._on_state_change is not None
        assert callable(flow._merge_queue._on_state_change)

    def test_on_state_change_points_to_touch_sentinel(self, monkeypatch, tmp_path):
        """The on_state_change callback is the _touch_merge_sentinel method."""
        flow = _make_flow(monkeypatch, tmp_path)
        # The callback should be the bound method _touch_merge_sentinel
        assert flow._merge_queue._on_state_change == flow._touch_merge_sentinel


# ---------------------------------------------------------------------------
# Step 2-3: _start_dashboard_server accepts merge_queue param
# ---------------------------------------------------------------------------


class TestStartDashboardServerMergeQueueParam:
    async def test_passes_merge_queue_to_mount_dashboard(self, tmp_path):
        """_start_dashboard_server passes merge_queue to mount_dashboard."""
        from golem.cli import _start_dashboard_server

        captured = {}

        def fake_mount(
            app, _config_snapshot=None, live_state_file=None, merge_queue=None, **kwargs
        ):
            captured["merge_queue"] = merge_queue

        mock_server = MagicMock()
        mock_server.serve = AsyncMock(return_value=None)

        fake_task = MagicMock()

        mq = MagicMock()

        with (
            patch("golem.core.dashboard.mount_dashboard", fake_mount),
            patch("golem.cli.asyncio.create_task", return_value=fake_task),
        ):
            import uvicorn as _uv
            import fastapi as _fa

            with (
                patch.object(_fa, "FastAPI", return_value=MagicMock()),
                patch.object(_uv, "Config", return_value=MagicMock()),
                patch.object(_uv, "Server", return_value=mock_server),
            ):
                await _start_dashboard_server(port=5000, merge_queue=mq)

        assert captured["merge_queue"] is mq

    async def test_default_none_merge_queue(self, tmp_path):
        """_start_dashboard_server defaults merge_queue to None."""
        from golem.cli import _start_dashboard_server

        captured = {}

        def fake_mount(
            app, _config_snapshot=None, live_state_file=None, merge_queue=None, **kwargs
        ):
            captured["merge_queue"] = merge_queue

        mock_server = MagicMock()
        mock_server.serve = AsyncMock(return_value=None)

        fake_task = MagicMock()

        with (
            patch("golem.core.dashboard.mount_dashboard", fake_mount),
            patch("golem.cli.asyncio.create_task", return_value=fake_task),
        ):
            import uvicorn as _uv
            import fastapi as _fa

            with (
                patch.object(_fa, "FastAPI", return_value=MagicMock()),
                patch.object(_uv, "Config", return_value=MagicMock()),
                patch.object(_uv, "Server", return_value=mock_server),
            ):
                await _start_dashboard_server(port=5000)

        assert captured["merge_queue"] is None


# ---------------------------------------------------------------------------
# Step 5: mount_dashboard accepts merge_queue param
# ---------------------------------------------------------------------------


class TestMountDashboardMergeQueueParam:
    def test_accepts_merge_queue_param(self, tmp_path):
        """mount_dashboard accepts a merge_queue parameter without error."""
        from golem.core.dashboard import mount_dashboard

        app = MagicMock()
        mq = MagicMock()

        # Should not raise
        mount_dashboard(app, merge_queue=mq)

    def test_default_none_merge_queue(self, tmp_path):
        """mount_dashboard default merge_queue is None."""
        from golem.core.dashboard import mount_dashboard
        import inspect

        sig = inspect.signature(mount_dashboard)
        assert "merge_queue" in sig.parameters
        assert sig.parameters["merge_queue"].default is None


# ---------------------------------------------------------------------------
# Step 6: Static routes for merge_queue JS/CSS
# ---------------------------------------------------------------------------


class TestMergeQueueStaticRoutes:
    def test_merge_queue_js_cache_exists(self):
        """_merge_queue_js_cache module-level instance is defined."""
        import golem.core.dashboard as d

        assert hasattr(d, "_merge_queue_js_cache")

    def test_merge_queue_css_cache_exists(self):
        """_merge_queue_css_cache module-level instance is defined."""
        import golem.core.dashboard as d

        assert hasattr(d, "_merge_queue_css_cache")

    def test_js_route_registered(self):
        """mount_dashboard registers /dashboard/merge_queue.js route."""
        from fastapi import FastAPI

        app = FastAPI()
        from golem.core.dashboard import mount_dashboard

        mount_dashboard(app)

        routes = {r.path for r in app.routes}
        assert "/dashboard/merge_queue.js" in routes

    def test_css_route_registered(self):
        """mount_dashboard registers /dashboard/merge_queue.css route."""
        from fastapi import FastAPI

        app = FastAPI()
        from golem.core.dashboard import mount_dashboard

        mount_dashboard(app)

        routes = {r.path for r in app.routes}
        assert "/dashboard/merge_queue.css" in routes


# ---------------------------------------------------------------------------
# Step 7: _MERGE_QUEUE_SENTINEL constant
# ---------------------------------------------------------------------------


class TestMergeQueueSentinelConstant:
    def test_sentinel_constant_defined(self):
        """_MERGE_QUEUE_SENTINEL is defined in dashboard module."""
        import golem.core.dashboard as d

        assert hasattr(d, "_MERGE_QUEUE_SENTINEL")

    def test_sentinel_constant_path(self):
        """_MERGE_QUEUE_SENTINEL ends with state/.merge_queue_updated."""
        import golem.core.dashboard as d

        sentinel = d._MERGE_QUEUE_SENTINEL
        # Check it ends with the expected path components regardless of DATA_DIR
        assert sentinel.name == ".merge_queue_updated"
        assert sentinel.parent.name == "state"
