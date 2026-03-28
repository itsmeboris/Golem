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
from golem.errors import TaskNotCancelableError, TaskNotFoundError

try:
    from fastapi import HTTPException
except ImportError:
    HTTPException = None  # type: ignore[assignment,misc]

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
    async def test_stop_flows(self, _wire_deps):
        from golem.core.control_api import flow_stop

        req = _make_request(json_data={"flows": ["golem"]})
        result = await flow_stop(req)
        assert result["ok"] is True
        assert result["results"]["golem"] == "stopped"

    async def test_stop_with_tick_loop(self, _wire_deps):
        from golem.core.control_api import flow_stop

        # stop_flow returns False, but _maybe_stop_tick returns True
        control_api._polling_trigger.stop_flow = AsyncMock(return_value=False)
        flow = MagicMock(spec=["stop_tick_loop"])
        control_api._dispatcher.get_flow.return_value = flow
        req = _make_request(json_data={"flows": ["golem"]})
        result = await flow_stop(req)
        assert result["results"]["golem"] == "stopped"

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
    async def test_start_flows(self, _wire_deps):
        from golem.core.control_api import flow_start

        req = _make_request(json_data={"flows": ["golem"]})
        result = await flow_start(req)
        assert result["ok"] is True
        assert result["results"]["golem"] == "started"

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
    async def test_status_all(self, _wire_deps):
        from golem.core.control_api import flow_status

        req = _make_request()
        req.query_params = {}
        result = await flow_status(req)
        assert result["ok"] is True
        assert "golem" in result["flows"]

    async def test_status_filter(self, _wire_deps):
        from golem.core.control_api import flow_status

        req = _make_request()
        req.query_params = {"flow": "other"}
        result = await flow_status(req)
        assert "golem" not in result["flows"]

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
    async def test_submit_with_prompt(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = _make_request(json_data={"prompt": "Fix bugs", "subject": "test"})
        result = await submit_task(req)
        assert result["ok"] is True
        assert result["task_id"] == 42

    async def test_submit_with_file(self, _wire_deps, tmp_path):
        from golem.core.control_api import submit_task

        prompt_file = tmp_path / "task.md"
        prompt_file.write_text("Do something", encoding="utf-8")
        # Provide work_dir so the file is within an allowed directory.
        req = _make_request(
            json_data={"file": str(prompt_file), "work_dir": str(tmp_path)}
        )
        result = await submit_task(req)
        assert result["ok"] is True

    async def test_submit_file_not_found(self, _wire_deps, tmp_path):
        from golem.core.control_api import submit_task

        # File is within allowed work_dir but does not exist on disk.
        req = _make_request(
            json_data={
                "file": str(tmp_path / "nonexistent.md"),
                "work_dir": str(tmp_path),
            }
        )
        with pytest.raises(Exception, match="File not found"):
            await submit_task(req)

    async def test_submit_no_prompt_or_file(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = _make_request(json_data={})
        with pytest.raises(Exception, match="required"):
            await submit_task(req)

    async def test_submit_no_golem_flow(self, _wire_deps):
        from golem.core.control_api import submit_task

        control_api._golem_flow = None
        req = _make_request(json_data={"prompt": "Fix it"})
        with pytest.raises(Exception, match="not ready"):
            await submit_task(req)

    async def test_submit_malformed_json(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = AsyncMock()
        req.json = AsyncMock(side_effect=json.JSONDecodeError("bad", "", 0))
        with pytest.raises(Exception, match="Invalid JSON"):
            await submit_task(req)

    async def test_submit_value_error_json(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = AsyncMock()
        req.json = AsyncMock(side_effect=ValueError("not json"))
        with pytest.raises(Exception, match="Invalid JSON"):
            await submit_task(req)

    async def test_submit_internal_error(self, _wire_deps):
        from golem.core.control_api import submit_task

        control_api._golem_flow.submit_task = MagicMock(
            side_effect=RuntimeError("boom")
        )
        req = _make_request(json_data={"prompt": "Do it"})
        with pytest.raises(Exception, match="Internal server error"):
            await submit_task(req)


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestSubmitFilePathTraversal:
    """SEC-001: path traversal prevention on the file= parameter."""

    @pytest.mark.parametrize(
        "file_arg",
        [
            "/etc/passwd",
            "/etc/shadow",
            "/root/.ssh/id_rsa",
        ],
        ids=["etc_passwd", "etc_shadow", "root_ssh_key"],
    )
    async def test_absolute_path_outside_cwd_rejected(self, _wire_deps, file_arg):
        from golem.core.control_api import submit_task

        req = _make_request(json_data={"file": file_arg})
        with pytest.raises(Exception) as exc_info:
            await submit_task(req)
        assert exc_info.value.status_code == 403
        assert "outside allowed" in exc_info.value.detail
        # Path must not be echoed back (information disclosure prevention).
        assert file_arg not in exc_info.value.detail

    async def test_file_within_work_dir_allowed(self, _wire_deps, tmp_path):
        from golem.core.control_api import submit_task

        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("Fix the bug", encoding="utf-8")
        req = _make_request(
            json_data={"file": str(prompt_file), "work_dir": str(tmp_path)}
        )
        result = await submit_task(req)
        assert result["ok"] is True

    async def test_file_within_cwd_allowed(self, _wire_deps, tmp_path, monkeypatch):
        from golem.core.control_api import submit_task

        # Patch CWD to tmp_path so the file resolves inside it.
        monkeypatch.chdir(tmp_path)
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("Do something useful", encoding="utf-8")
        req = _make_request(json_data={"file": str(prompt_file)})
        result = await submit_task(req)
        assert result["ok"] is True

    async def test_path_traversal_via_dotdot_rejected(self, _wire_deps, tmp_path):
        from golem.core.control_api import submit_task

        # work_dir is tmp_path/subdir, but file tries to escape via ../../etc/passwd.
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        traversal = str(subdir / ".." / ".." / "etc" / "passwd")
        req = _make_request(json_data={"file": traversal, "work_dir": str(subdir)})
        with pytest.raises(Exception) as exc_info:
            await submit_task(req)
        assert exc_info.value.status_code == 403

    async def test_file_within_registered_repo_allowed(self, _wire_deps, tmp_path):
        from unittest.mock import patch

        from golem.core.control_api import submit_task

        prompt_file = tmp_path / "task.md"
        prompt_file.write_text("Repo task", encoding="utf-8")

        mock_registry = MagicMock()
        mock_registry.return_value.list_repos.return_value = [{"path": str(tmp_path)}]
        with patch("golem.repo_registry.RepoRegistry", mock_registry):
            req = _make_request(json_data={"file": str(prompt_file)})
            result = await submit_task(req)
        assert result["ok"] is True

    async def test_registry_load_failure_falls_back_gracefully(
        self, _wire_deps, tmp_path
    ):
        """If RepoRegistry raises, the request still proceeds with CWD/work_dir check."""
        from unittest.mock import patch

        from golem.core.control_api import submit_task

        prompt_file = tmp_path / "task.md"
        prompt_file.write_text("Fallback task", encoding="utf-8")

        with patch(
            "golem.repo_registry.RepoRegistry", side_effect=RuntimeError("db error")
        ):
            # File is within work_dir, so it should still be allowed.
            req = _make_request(
                json_data={"file": str(prompt_file), "work_dir": str(tmp_path)}
            )
            result = await submit_task(req)
        assert result["ok"] is True

    async def test_file_outside_all_allowed_dirs_rejected(
        self, _wire_deps, tmp_path, monkeypatch
    ):
        """A file outside CWD, work_dir, and all repos is rejected."""
        from unittest.mock import patch

        from golem.core.control_api import submit_task

        # Chdir to tmp_path so CWD is known.
        monkeypatch.chdir(tmp_path)
        # Target a path outside tmp_path — use /tmp itself if it resolves differently.
        other_dir = tmp_path.parent
        target_file = other_dir / "secret.txt"

        mock_registry = MagicMock()
        mock_registry.return_value.list_repos.return_value = []
        with patch("golem.repo_registry.RepoRegistry", mock_registry):
            req = _make_request(
                json_data={"file": str(target_file), "work_dir": str(tmp_path)}
            )
            with pytest.raises(Exception) as exc_info:
                await submit_task(req)
        assert exc_info.value.status_code == 403


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestBatchSubmitEndpoint:
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

    async def test_submit_batch_no_tasks(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={})
        with pytest.raises(Exception, match="required"):
            await submit_batch(req)

    async def test_submit_batch_empty_list(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": []})
        with pytest.raises(Exception, match="required"):
            await submit_batch(req)

    async def test_submit_batch_no_golem_flow(self, _wire_deps):
        from golem.core.control_api import submit_batch

        control_api._golem_flow = None
        req = _make_request(json_data={"tasks": [{"prompt": "x"}]})
        with pytest.raises(Exception, match="not ready"):
            await submit_batch(req)

    async def test_submit_batch_malformed_json(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = AsyncMock()
        req.json = AsyncMock(side_effect=json.JSONDecodeError("bad", "", 0))
        with pytest.raises(Exception, match="Invalid JSON"):
            await submit_batch(req)

    async def test_submit_batch_value_error_json(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = AsyncMock()
        req.json = AsyncMock(side_effect=ValueError("not json"))
        with pytest.raises(Exception, match="Invalid JSON"):
            await submit_batch(req)

    async def test_submit_batch_missing_prompt(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": [{"subject": "no prompt"}]})
        with pytest.raises(Exception, match="index 0.*missing.*prompt"):
            await submit_batch(req)

    async def test_submit_batch_empty_prompt(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": [{"prompt": "  "}]})
        with pytest.raises(Exception, match="index 0.*missing.*prompt"):
            await submit_batch(req)

    async def test_submit_batch_non_string_prompt(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": [{"prompt": 123}]})
        with pytest.raises(Exception, match="index 0.*missing.*prompt"):
            await submit_batch(req)

    async def test_submit_batch_non_dict_task(self, _wire_deps):
        from golem.core.control_api import submit_batch

        req = _make_request(json_data={"tasks": ["not a dict"]})
        with pytest.raises(Exception, match="index 0.*missing.*prompt"):
            await submit_batch(req)

    async def test_submit_batch_depends_on_forward_ref(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A", "depends_on": [1]},
            {"prompt": "B"},
        ]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 0.*invalid depends_on.*1"):
            await submit_batch(req)

    async def test_submit_batch_depends_on_self(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A"},
            {"prompt": "B", "depends_on": [1]},
        ]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 1.*invalid depends_on.*1"):
            await submit_batch(req)

    async def test_submit_batch_depends_on_negative(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [{"prompt": "A", "depends_on": [-1]}]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 0.*invalid depends_on.*-1"):
            await submit_batch(req)

    async def test_submit_batch_depends_on_unknown_key(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A"},
            {"prompt": "B", "depends_on": ["nonexistent"]},
        ]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(
            Exception, match="index 1.*unknown depends_on key.*nonexistent"
        ):
            await submit_batch(req)

    async def test_submit_batch_depends_on_non_int_non_str(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A"},
            {"prompt": "B", "depends_on": [1.5]},
        ]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 1.*invalid depends_on"):
            await submit_batch(req)

    async def test_submit_batch_depends_on_out_of_range(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A"},
            {"prompt": "B", "depends_on": [99]},
        ]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="index 1.*invalid depends_on.*99"):
            await submit_batch(req)

    async def test_submit_batch_internal_error(self, _wire_deps):
        from golem.core.control_api import submit_batch

        control_api._golem_flow.submit_batch = MagicMock(
            side_effect=RuntimeError("kaboom")
        )
        tasks = [{"prompt": "A"}]
        req = _make_request(json_data={"tasks": tasks})
        with pytest.raises(Exception, match="Internal server error"):
            await submit_batch(req)

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

    async def test_submit_batch_valid_key_depends_on(self, _wire_deps):
        from golem.core.control_api import submit_batch

        tasks = [
            {"prompt": "A", "key": "setup"},
            {"prompt": "B", "depends_on": ["setup"]},
            {"prompt": "C", "depends_on": ["setup", 1]},
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
# GET /api/sessions/{task_id}
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestGetSessionEndpoint:
    async def test_session_found(self, _wire_deps):
        from golem.core.control_api import get_session

        session = MagicMock()
        session.to_dict.return_value = {
            "parent_issue_id": 42,
            "state": "running",
            "total_cost_usd": 1.23,
            "validation_cost_usd": 0.45,
        }
        control_api._golem_flow.get_session = MagicMock(return_value=session)
        result = await get_session(42)
        assert result["ok"] is True
        assert result["session"]["parent_issue_id"] == 42
        assert result["session"]["total_cost_usd"] == 1.23

    async def test_session_not_found(self, _wire_deps):
        from golem.core.control_api import get_session

        control_api._golem_flow.get_session = MagicMock(return_value=None)
        with pytest.raises(Exception, match="No session found"):
            await get_session(999)

    async def test_session_no_golem_flow(self, _wire_deps):
        from golem.core.control_api import get_session

        control_api._golem_flow = None
        with pytest.raises(Exception, match="not ready"):
            await get_session(1)


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
    async def test_submit_rejects_missing_key(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_task

        req = _make_request(
            headers={},
            json_data={"prompt": "hello"},
        )
        req.query_params = {}
        with pytest.raises(Exception, match="Invalid or missing API key"):
            await submit_task(req)

    async def test_submit_rejects_wrong_key(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_task

        req = _make_request(
            headers={"authorization": "Bearer wrong-key"},
            json_data={"prompt": "hello"},
        )
        with pytest.raises(Exception, match="Invalid or missing API key"):
            await submit_task(req)

    async def test_submit_accepts_valid_bearer(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_task

        req = _make_request(
            headers={"authorization": "Bearer test-key"},
            json_data={"prompt": "hello"},
        )
        result = await submit_task(req)
        assert result["ok"] is True

    async def test_submit_accepts_valid_query_param(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_task

        req = _make_request(
            headers={},
            json_data={"prompt": "hello"},
        )
        req.query_params = {"token": "test-key"}
        result = await submit_task(req)
        assert result["ok"] is True

    async def test_batch_rejects_missing_key(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_batch

        req = _make_request(
            headers={},
            json_data={"tasks": [{"prompt": "A"}]},
        )
        req.query_params = {}
        with pytest.raises(Exception, match="Invalid or missing API key"):
            await submit_batch(req)

    async def test_batch_rejects_wrong_key(self, _wire_deps_with_api_key):
        from golem.core.control_api import submit_batch

        req = _make_request(
            headers={"authorization": "Bearer wrong-key"},
            json_data={"tasks": [{"prompt": "A"}]},
        )
        with pytest.raises(Exception, match="Invalid or missing API key"):
            await submit_batch(req)

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
    async def test_submit_open_when_no_key(self, _wire_deps):
        from golem.core.control_api import submit_task

        req = _make_request(headers={}, json_data={"prompt": "hello"})
        req.query_params = {}
        result = await submit_task(req)
        assert result["ok"] is True

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
# POST /cancel/{task_id}
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestCancelEndpoint:
    async def test_cancel_success(self, _wire_deps):
        from golem.core.control_api import cancel_task

        gf = control_api._golem_flow
        gf.cancel_session = MagicMock(return_value={"state": "cancelled"})
        result = await cancel_task(task_id=42)
        assert result["ok"] is True
        gf.cancel_session.assert_called_once_with(42)

    async def test_cancel_not_found(self, _wire_deps):
        from golem.core.control_api import cancel_task

        gf = control_api._golem_flow
        gf.cancel_session = MagicMock(side_effect=TaskNotFoundError("No task 99"))
        with pytest.raises(HTTPException) as exc_info:
            await cancel_task(task_id=99)
        assert exc_info.value.status_code == 404

    async def test_cancel_not_cancelable(self, _wire_deps):
        from golem.core.control_api import cancel_task

        gf = control_api._golem_flow
        gf.cancel_session = MagicMock(
            side_effect=TaskNotCancelableError("Task already completed")
        )
        with pytest.raises(HTTPException) as exc_info:
            await cancel_task(task_id=42)
        assert exc_info.value.status_code == 409

    async def test_cancel_no_flow(self):
        from golem.core.control_api import cancel_task

        wire_control_api()  # reset — no flow
        with pytest.raises(HTTPException) as exc_info:
            await cancel_task(task_id=1)
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# POST /api/sessions/clear-failed
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestClearFailedEndpoint:
    async def test_clear_failed_success(self, _wire_deps):
        from golem.core.control_api import clear_failed_sessions

        gf = control_api._golem_flow
        gf.clear_failed_sessions = MagicMock(return_value=[1, 5])
        result = await clear_failed_sessions()
        assert result["ok"] is True
        assert result["cleared"] == [1, 5]
        gf.clear_failed_sessions.assert_called_once()

    async def test_clear_failed_no_flow(self):
        from golem.core.control_api import clear_failed_sessions

        wire_control_api()  # reset — no flow
        with pytest.raises(HTTPException) as exc_info:
            await clear_failed_sessions()
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# GET /batch/{group_id} and GET /batches
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestBatchEndpoints:
    async def test_get_batch_success(self, _wire_deps):
        from golem.core.control_api import get_batch

        gf = control_api._golem_flow
        gf.get_batch = MagicMock(return_value={"id": "b1", "status": "running"})
        result = await get_batch(group_id="b1")
        assert result["ok"] is True
        assert result["batch"]["id"] == "b1"
        gf.get_batch.assert_called_once_with("b1")

    async def test_get_batch_not_found(self, _wire_deps):
        from golem.core.control_api import get_batch

        gf = control_api._golem_flow
        gf.get_batch = MagicMock(return_value=None)
        with pytest.raises(HTTPException) as exc_info:
            await get_batch(group_id="nope")
        assert exc_info.value.status_code == 404

    async def test_get_batch_no_flow(self):
        from golem.core.control_api import get_batch

        wire_control_api()  # reset — no flow
        with pytest.raises(HTTPException) as exc_info:
            await get_batch(group_id="b1")
        assert exc_info.value.status_code == 503

    async def test_list_batches_success(self, _wire_deps):
        from golem.core.control_api import list_batches

        gf = control_api._golem_flow
        gf.list_batches = MagicMock(return_value=[{"id": "b1"}, {"id": "b2"}])
        result = await list_batches()
        assert result["ok"] is True
        assert len(result["batches"]) == 2

    async def test_list_batches_no_flow(self):
        from golem.core.control_api import list_batches

        wire_control_api()  # reset — no flow
        with pytest.raises(HTTPException) as exc_info:
            await list_batches()
        assert exc_info.value.status_code == 503


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
