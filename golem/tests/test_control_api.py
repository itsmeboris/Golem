# pylint: disable=too-few-public-methods
"""Tests for golem.core.control_api — flow control and task submission endpoints."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from golem.core import control_api
from golem.core.control_api import (
    _maybe_start_tick,
    _maybe_stop_tick,
    _require_admin,
    _require_api_key,
    _require_polling,
    wire_control_api,
)


# ---------------------------------------------------------------------------
# wire_control_api
# ---------------------------------------------------------------------------


class TestWireControlApi:
    def test_sets_module_globals(self):
        pt = MagicMock()
        disp = MagicMock()
        gf = MagicMock()
        wire_control_api(
            polling_trigger=pt,
            dispatcher=disp,
            admin_token="secret",
            api_key="my-api-key",
            golem_flow=gf,
        )
        assert control_api._polling_trigger is pt
        assert control_api._dispatcher is disp
        assert control_api._admin_token == "secret"
        assert control_api._api_key == "my-api-key"
        assert control_api._golem_flow is gf

    def test_resets_start_time(self):
        before = time.time()
        wire_control_api()
        assert control_api._start_time >= before


# ---------------------------------------------------------------------------
# _maybe_start_tick / _maybe_stop_tick
# ---------------------------------------------------------------------------


class TestMaybeStartTick:
    def test_no_dispatcher(self):
        control_api._dispatcher = None
        _maybe_start_tick("golem")  # should not raise

    def test_flow_with_tick_loop(self):
        flow = MagicMock(spec=["start_tick_loop"])
        disp = MagicMock()
        disp.get_flow.return_value = flow
        control_api._dispatcher = disp
        _maybe_start_tick("golem")
        flow.start_tick_loop.assert_called_once()

    def test_flow_without_tick_loop(self):
        flow = MagicMock(spec=[])  # no start_tick_loop attribute
        disp = MagicMock()
        disp.get_flow.return_value = flow
        control_api._dispatcher = disp
        _maybe_start_tick("golem")  # should not raise

    def test_flow_not_found(self):
        disp = MagicMock()
        disp.get_flow.return_value = None
        control_api._dispatcher = disp
        _maybe_start_tick("unknown")  # should not raise


class TestMaybeStopTick:
    def test_no_dispatcher(self):
        control_api._dispatcher = None
        assert _maybe_stop_tick("golem") is False

    def test_flow_with_stop(self):
        flow = MagicMock(spec=["stop_tick_loop"])
        disp = MagicMock()
        disp.get_flow.return_value = flow
        control_api._dispatcher = disp
        assert _maybe_stop_tick("golem") is True
        flow.stop_tick_loop.assert_called_once()

    def test_flow_without_stop(self):
        flow = MagicMock(spec=[])
        disp = MagicMock()
        disp.get_flow.return_value = flow
        control_api._dispatcher = disp
        assert _maybe_stop_tick("golem") is False

    def test_flow_not_found(self):
        disp = MagicMock()
        disp.get_flow.return_value = None
        control_api._dispatcher = disp
        assert _maybe_stop_tick("x") is False


# ---------------------------------------------------------------------------
# _require_polling / _require_admin
# ---------------------------------------------------------------------------


class TestRequirePolling:
    def test_raises_when_none(self):
        control_api._polling_trigger = None
        with pytest.raises(Exception, match="not connected"):
            _require_polling()

    def test_passes_when_set(self):
        control_api._polling_trigger = MagicMock()
        _require_polling()  # should not raise


class TestRequireAdmin:
    def test_no_token_configured(self):
        control_api._admin_token = ""
        req = MagicMock()
        with pytest.raises(Exception, match="not configured"):
            _require_admin(req)

    def test_bearer_token_valid(self):
        control_api._admin_token = "secret"
        req = MagicMock()
        req.headers = {"authorization": "Bearer secret"}
        req.query_params = {}
        _require_admin(req)  # should not raise

    def test_query_param_token_valid(self):
        control_api._admin_token = "secret"
        req = MagicMock()
        req.headers = {}
        req.query_params = {"token": "secret"}
        _require_admin(req)  # should not raise

    def test_invalid_token(self):
        control_api._admin_token = "secret"
        req = MagicMock()
        req.headers = {"authorization": "Bearer wrong"}
        req.query_params = {}
        with pytest.raises(Exception, match="Invalid"):
            _require_admin(req)

    def test_missing_token(self):
        control_api._admin_token = "secret"
        req = MagicMock()
        req.headers = {}
        req.query_params = {}
        with pytest.raises(Exception, match="Invalid"):
            _require_admin(req)


class TestRequireApiKey:
    def test_no_key_configured_allows_all(self):
        control_api._api_key = ""
        req = MagicMock()
        req.headers = {}
        req.query_params = {}
        _require_api_key(req)

    def test_bearer_token_valid(self):
        control_api._api_key = "s3cret"
        req = MagicMock()
        req.headers = {"authorization": "Bearer s3cret"}
        req.query_params = {}
        _require_api_key(req)

    def test_query_param_token_valid(self):
        control_api._api_key = "s3cret"
        req = MagicMock()
        req.headers = {}
        req.query_params = {"token": "s3cret"}
        _require_api_key(req)

    def test_wrong_key_rejected(self):
        control_api._api_key = "s3cret"
        req = MagicMock()
        req.headers = {"authorization": "Bearer wrong"}
        req.query_params = {}
        with pytest.raises(Exception, match="Invalid or missing API key"):
            _require_api_key(req)

    def test_missing_key_rejected(self):
        control_api._api_key = "s3cret"
        req = MagicMock()
        req.headers = {}
        req.query_params = {}
        with pytest.raises(Exception, match="Invalid or missing API key"):
            _require_api_key(req)


# ---------------------------------------------------------------------------
# Router endpoints (when FASTAPI_AVAILABLE is True)
# ---------------------------------------------------------------------------


@pytest.fixture()
def _wire_deps():
    """Set up module state for endpoint tests."""
    pt = AsyncMock()
    pt.stop_flow = AsyncMock(return_value=True)
    pt.start_flow = AsyncMock(return_value=True)
    pt.flow_status = MagicMock(return_value={"golem": {"running": True}})
    disp = MagicMock()
    disp.get_flow.return_value = None
    gf = MagicMock()
    gf.submit_task = MagicMock(return_value={"task_id": 42, "status": "submitted"})
    wire_control_api(
        polling_trigger=pt,
        dispatcher=disp,
        admin_token="tok",
        golem_flow=gf,
    )
    yield
    # Reset to clean state
    wire_control_api()


_SENTINEL = object()


def _make_request(headers=_SENTINEL, query_params=None, json_data=None):
    """Build a mock Request with the given attributes."""
    req = AsyncMock()
    req.headers = {"authorization": "Bearer tok"} if headers is _SENTINEL else headers
    req.query_params = query_params or {}
    req.json = AsyncMock(return_value=json_data or {})
    return req


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestFlowStopEndpoint:
    @pytest.mark.asyncio
    async def test_stop_flows(self, _wire_deps):
        from golem.core.control_api import flow_stop

        req = _make_request(json_data={"flows": ["golem"]})
        result = await flow_stop(req)
        assert result["ok"] is True
        assert result["results"]["golem"] == "stopped"

    @pytest.mark.asyncio
    async def test_stop_with_tick_loop(self, _wire_deps):
        from golem.core.control_api import flow_stop

        # stop_flow returns False, but _maybe_stop_tick returns True
        control_api._polling_trigger.stop_flow = AsyncMock(return_value=False)
        flow = MagicMock(spec=["stop_tick_loop"])
        control_api._dispatcher.get_flow.return_value = flow
        req = _make_request(json_data={"flows": ["golem"]})
        result = await flow_stop(req)
        assert result["results"]["golem"] == "stopped"

    @pytest.mark.asyncio
    async def test_stop_not_running(self, _wire_deps):
        from golem.core.control_api import flow_stop

        control_api._polling_trigger.stop_flow = AsyncMock(return_value=False)
        req = _make_request(json_data={"flows": ["other"]})
        result = await flow_stop(req)
        assert result["results"]["other"] == "not_running"


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestFlowStartEndpoint:
    @pytest.mark.asyncio
    async def test_start_flows(self, _wire_deps):
        from golem.core.control_api import flow_start

        req = _make_request(json_data={"flows": ["golem"]})
        result = await flow_start(req)
        assert result["ok"] is True
        assert result["results"]["golem"] == "started"

    @pytest.mark.asyncio
    async def test_start_already_running(self, _wire_deps):
        from golem.core.control_api import flow_start

        control_api._polling_trigger.start_flow = AsyncMock(return_value=False)
        req = _make_request(json_data={"flows": ["golem"]})
        result = await flow_start(req)
        assert result["results"]["golem"] == "already_running_or_unavailable"


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestFlowStatusEndpoint:
    @pytest.mark.asyncio
    async def test_status_all(self, _wire_deps):
        from golem.core.control_api import flow_status

        req = _make_request()
        req.query_params = {}
        result = await flow_status(req)
        assert result["ok"] is True
        assert "golem" in result["flows"]

    @pytest.mark.asyncio
    async def test_status_filter(self, _wire_deps):
        from golem.core.control_api import flow_status

        req = _make_request()
        req.query_params = {"flow": "other"}
        result = await flow_status(req)
        assert "golem" not in result["flows"]

    @pytest.mark.asyncio
    async def test_status_no_trigger(self, _wire_deps):
        from golem.core.control_api import flow_status

        control_api._polling_trigger = None
        req = _make_request()
        req.query_params = {}
        result = await flow_status(req)
        assert result["flows"] == {}


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health(self, _wire_deps):
        from golem.core.control_api import health_check

        result = await health_check()
        assert result["ok"] is True
        assert "pid" in result
        assert "uptime_seconds" in result


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestSubmitEndpoint:
    @pytest.mark.asyncio
    async def test_submit_with_prompt(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = _make_request(json_data={"prompt": "Fix bugs", "subject": "test"})
        result = await submit_task(req)
        assert result["ok"] is True
        assert result["task_id"] == 42

    @pytest.mark.asyncio
    async def test_submit_with_file(self, _wire_deps, tmp_path):
        from golem.core.control_api import submit_task

        prompt_file = tmp_path / "task.md"
        prompt_file.write_text("Do something", encoding="utf-8")
        req = _make_request(json_data={"file": str(prompt_file)})
        result = await submit_task(req)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_submit_file_not_found(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = _make_request(json_data={"file": "/nonexistent/path.md"})
        with pytest.raises(Exception, match="File not found"):
            await submit_task(req)

    @pytest.mark.asyncio
    async def test_submit_no_prompt_or_file(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = _make_request(json_data={})
        with pytest.raises(Exception, match="required"):
            await submit_task(req)

    @pytest.mark.asyncio
    async def test_submit_no_golem_flow(self, _wire_deps):
        from golem.core.control_api import submit_task

        control_api._golem_flow = None
        req = _make_request(json_data={"prompt": "Fix it"})
        with pytest.raises(Exception, match="not ready"):
            await submit_task(req)

    @pytest.mark.asyncio
    async def test_submit_malformed_json(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = AsyncMock()
        req.json = AsyncMock(side_effect=json.JSONDecodeError("bad", "", 0))
        with pytest.raises(Exception, match="Invalid JSON"):
            await submit_task(req)

    @pytest.mark.asyncio
    async def test_submit_value_error_json(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = AsyncMock()
        req.json = AsyncMock(side_effect=ValueError("not json"))
        with pytest.raises(Exception, match="Invalid JSON"):
            await submit_task(req)

    @pytest.mark.asyncio
    async def test_submit_internal_error(self, _wire_deps):
        from golem.core.control_api import submit_task

        control_api._golem_flow.submit_task = MagicMock(
            side_effect=RuntimeError("boom")
        )
        req = _make_request(json_data={"prompt": "Do it"})
        with pytest.raises(Exception, match="Internal error"):
            await submit_task(req)


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestBatchSubmitEndpoint:
    @pytest.mark.asyncio
    async def test_submit_batch(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "Task A", "subject": "A"},
            {"prompt": "Task B", "depends_on": [0]},
        ]
        control_api._golem_flow.submit_batch = MagicMock(
            return_value={
                "group_id": "grp-1",
                "tasks": [
                    {"task_id": 1, "status": "submitted"},
                    {"task_id": 2, "status": "submitted"},
                ],
            }
        )
        req = _make_request(json_data={"tasks": tasks, "group_id": "grp-1"})
        result = await submit_batch(req)
        assert result["ok"] is True
        assert result["group_id"] == "grp-1"
        assert len(result["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_submit_batch_no_tasks(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={})
        with pytest.raises(Exception, match="required"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_empty_list(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": []})
        with pytest.raises(Exception, match="required"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_no_golem_flow(self, _wire_deps):
        from golem.core.control_api import submit_batch

        control_api._golem_flow = None
        req = _make_request(json_data={"tasks": [{"prompt": "x"}]})
        with pytest.raises(Exception, match="not ready"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_malformed_json(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = AsyncMock()
        req.json = AsyncMock(side_effect=json.JSONDecodeError("bad", "", 0))
        with pytest.raises(Exception, match="Invalid JSON"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_value_error_json(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = AsyncMock()
        req.json = AsyncMock(side_effect=ValueError("not json"))
        with pytest.raises(Exception, match="Invalid JSON"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_missing_prompt(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": [{"subject": "no prompt"}]})
        with pytest.raises(Exception, match="index 0.*missing.*prompt"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_empty_prompt(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": [{"prompt": "  "}]})
        with pytest.raises(Exception, match="index 0.*missing.*prompt"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_non_string_prompt(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": [{"prompt": 123}]})
        with pytest.raises(Exception, match="index 0.*missing.*prompt"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_non_dict_task(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": ["not a dict"]})
        with pytest.raises(Exception, match="index 0.*missing.*prompt"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_depends_on_forward_ref(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A", "depends_on": [1]},
            {"prompt": "B"},
        ]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 0.*invalid depends_on.*1"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_depends_on_self(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A"},
            {"prompt": "B", "depends_on": [1]},
        ]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 1.*invalid depends_on.*1"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_depends_on_negative(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [{"prompt": "A", "depends_on": [-1]}]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 0.*invalid depends_on.*-1"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_depends_on_non_int(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A"},
            {"prompt": "B", "depends_on": ["zero"]},
        ]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 1.*invalid depends_on.*zero"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_depends_on_out_of_range(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A"},
            {"prompt": "B", "depends_on": [99]},
        ]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 1.*invalid depends_on.*99"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_internal_error(self, _wire_deps):
        from golem.core.control_api import submit_batch

        control_api._golem_flow.submit_batch = MagicMock(
            side_effect=RuntimeError("kaboom")
        )
        tasks = [{"prompt": "A"}]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="Internal error"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_submit_batch_valid_depends_on(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A"},
            {"prompt": "B", "depends_on": [0]},
            {"prompt": "C", "depends_on": [0, 1]},
        ]
        control_api._golem_flow.submit_batch = MagicMock(
            return_value={
                "group_id": "g",
                "tasks": [
                    {"task_id": 1, "status": "submitted"},
                    {"task_id": 2, "status": "submitted"},
                    {"task_id": 3, "status": "submitted"},
                ],
            }
        )
        req = _make_request(json_data={"tasks": tasks})
        result = await submit_batch(req)
        assert result["ok"] is True
        assert len(result["tasks"]) == 3


# ---------------------------------------------------------------------------
# API key auth on submit endpoints
# ---------------------------------------------------------------------------


@pytest.fixture()
def _wire_deps_with_api_key():
    """Set up module state with an API key configured."""
    gf = MagicMock()
    gf.submit_task = MagicMock(return_value={"task_id": 99, "status": "submitted"})
    gf.submit_batch = MagicMock(
        return_value={
            "group_id": "g",
            "tasks": [{"task_id": 99, "status": "submitted"}],
        }
    )
    wire_control_api(golem_flow=gf, api_key="test-key")
    yield
    wire_control_api()


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestSubmitApiKeyAuth:
    @pytest.mark.asyncio
    async def test_submit_rejects_missing_key(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_task

        req = _make_request(
            headers={},
            json_data={"prompt": "hello"},
        )
        req.query_params = {}
        with pytest.raises(Exception, match="Invalid or missing API key"):
            await submit_task(req)

    @pytest.mark.asyncio
    async def test_submit_rejects_wrong_key(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_task

        req = _make_request(
            headers={"authorization": "Bearer wrong-key"},
            json_data={"prompt": "hello"},
        )
        with pytest.raises(Exception, match="Invalid or missing API key"):
            await submit_task(req)

    @pytest.mark.asyncio
    async def test_submit_accepts_valid_bearer(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_task

        req = _make_request(
            headers={"authorization": "Bearer test-key"},
            json_data={"prompt": "hello"},
        )
        result = await submit_task(req)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_submit_accepts_valid_query_param(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_task

        req = _make_request(
            headers={},
            json_data={"prompt": "hello"},
        )
        req.query_params = {"token": "test-key"}
        result = await submit_task(req)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_batch_rejects_missing_key(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_batch

        req = _make_request(
            headers={},
            json_data={"tasks": [{"prompt": "A"}]},
        )
        req.query_params = {}
        with pytest.raises(Exception, match="Invalid or missing API key"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_batch_rejects_wrong_key(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_batch

        req = _make_request(
            headers={"authorization": "Bearer wrong-key"},
            json_data={"tasks": [{"prompt": "A"}]},
        )
        with pytest.raises(Exception, match="Invalid or missing API key"):
            await submit_batch(req)

    @pytest.mark.asyncio
    async def test_batch_accepts_valid_bearer(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_batch

        req = _make_request(
            headers={"authorization": "Bearer test-key"},
            json_data={"tasks": [{"prompt": "A"}]},
        )
        result = await submit_batch(req)
        assert result["ok"] is True


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestSubmitNoApiKey:
    @pytest.mark.asyncio
    async def test_submit_open_when_no_key(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = _make_request(headers={}, json_data={"prompt": "hello"})
        req.query_params = {}
        result = await submit_task(req)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_batch_open_when_no_key(self, _wire_deps):
        from golem.core.control_api import submit_batch

        control_api._golem_flow.submit_batch = MagicMock(
            return_value={
                "group_id": "g",
                "tasks": [{"task_id": 1, "status": "submitted"}],
            }
        )
        req = _make_request(headers={}, json_data={"tasks": [{"prompt": "A"}]})
        req.query_params = {}
        result = await submit_batch(req)
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# No-FastAPI fallback
# ---------------------------------------------------------------------------


class TestNoFastapiFallback:
    def test_routers_are_none_when_unavailable(self):
        """When FASTAPI_AVAILABLE is False, routers should be None."""
        # We can't easily reload the module to test the False branch,
        # but we can verify the else-branch contract: if FASTAPI_AVAILABLE
        # were False, control_router and health_router are None.
        # This test documents the contract.
        if control_api.FASTAPI_AVAILABLE:
            assert control_api.control_router is not None
            assert control_api.health_router is not None
        else:
            assert control_api.control_router is None
            assert control_api.health_router is None
