# pylint: disable=too-few-public-methods
"""Tests for batch API endpoints, flow-level batch monitor integration, and CLI."""

import argparse
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from golem.core import control_api
from golem.core.config import Config, GolemFlowConfig
from golem.core.control_api import wire_control_api
from golem.orchestrator import TaskSessionState

# ---------------------------------------------------------------------------
# Helpers — same patterns as test_flow_coordination.py
# ---------------------------------------------------------------------------


def _make_test_profile():
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
        task_source=LocalFileTaskSource("/tmp/test-tasks"),
        state_backend=NullStateBackend(),
        notifier=LogNotifier(),
        tool_provider=NullToolProvider(),
        prompt_provider=FilePromptProvider(None),
    )


def _make_flow(monkeypatch, tmp_path, profile=None, **flow_kwargs):
    from golem.flow import GolemFlow

    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

    profile = profile or _make_test_profile()
    fc_kwargs = {"enabled": True, "projects": ["test-project"], "profile": "test"}
    fc_kwargs.update(flow_kwargs)
    config = Config(golem=GolemFlowConfig(**fc_kwargs))
    monkeypatch.setattr(
        "golem.flow.build_profile",
        lambda _name, _cfg: profile,
    )
    return GolemFlow(config)


# ---------------------------------------------------------------------------
# Flow-level batch monitor integration tests
# ---------------------------------------------------------------------------


class TestSubmitBatchRegistersInMonitor:
    def test_submit_batch_registers_in_monitor(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        tasks = [
            {"prompt": "task A", "subject": "A"},
            {"prompt": "task B", "subject": "B"},
        ]
        result = flow.submit_batch(tasks, group_id="grp-test")

        task_ids = [t["task_id"] for t in result["tasks"]]

        # Use the internal monitor's get() to check the registered state
        # before any update refreshes status from live sessions.
        batch_state = flow._batch_monitor.get("grp-test")
        assert batch_state is not None
        assert batch_state.task_ids == task_ids
        assert batch_state.status == "submitted"
        assert batch_state.group_id == "grp-test"


class TestListBatchesReturnsSubmitted:
    def test_list_batches_returns_submitted(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        flow.submit_batch([{"prompt": "solo task"}], group_id="grp-list")

        batches = flow.list_batches()
        assert len(batches) >= 1
        grp_list = [b for b in batches if b["group_id"] == "grp-list"]
        assert len(grp_list) == 1
        assert grp_list[0]["status"] == "submitted"


class TestGetBatchReturnsNone:
    def test_get_batch_returns_none_for_unknown(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        assert flow.get_batch("nonexistent") is None


class TestBatchMonitorUpdateOnSessionChange:
    def test_session_change_triggers_batch_update(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch(
            [{"prompt": "task A", "subject": "A"}], group_id="grp-update"
        )
        tid = result["tasks"][0]["task_id"]
        session = flow._sessions[tid]
        prev_state = session.state
        session.state = TaskSessionState.COMPLETED
        session.validation_verdict = "PASS"

        # Trigger _handle_state_transition which updates batch monitor
        flow._handle_state_transition(session, prev_state)

        batch = flow._batch_monitor.get("grp-update")
        assert batch is not None
        assert batch.status == "completed"


class TestBatchStatusUpdatesOnCompletion:
    def test_batch_status_updates_on_completion(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        tasks = [
            {"prompt": "task A", "subject": "A"},
            {"prompt": "task B", "subject": "B"},
        ]
        result = flow.submit_batch(tasks, group_id="grp-done")

        # Manually set session states to COMPLETED with validation_verdict
        for task_info in result["tasks"]:
            tid = task_info["task_id"]
            session = flow._sessions[tid]
            session.state = TaskSessionState.COMPLETED
            session.validation_verdict = "PASS"

        batch = flow.get_batch("grp-done")
        assert batch is not None
        assert batch["status"] == "completed"
        assert batch["validation_verdict"] == "PASS"


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

_SENTINEL = object()


def _make_request(headers=_SENTINEL, query_params=None, json_data=None):
    """Build a mock Request with the given attributes."""
    req = AsyncMock()
    req.headers = {"authorization": "Bearer tok"} if headers is _SENTINEL else headers
    req.query_params = query_params or {}
    req.json = AsyncMock(return_value=json_data or {})
    return req


@pytest.fixture()
def _wire_batch_deps():
    """Set up module state for batch endpoint tests."""
    pt = AsyncMock()
    pt.stop_flow = AsyncMock(return_value=True)
    pt.start_flow = AsyncMock(return_value=True)
    pt.flow_status = MagicMock(return_value={"golem": {"running": True}})
    disp = MagicMock()
    disp.get_flow.return_value = None
    gf = MagicMock()
    gf.submit_batch = MagicMock(
        return_value={
            "group_id": "grp-api",
            "tasks": [
                {"task_id": 1, "status": "submitted"},
                {"task_id": 2, "status": "submitted"},
            ],
        }
    )
    gf.get_batch = MagicMock(
        return_value={
            "group_id": "grp-api",
            "task_ids": [1, 2],
            "status": "submitted",
            "created_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "",
            "total_cost_usd": 0.0,
            "total_duration_s": 0.0,
            "task_results": {},
            "validation_verdict": "",
        }
    )
    gf.list_batches = MagicMock(
        return_value=[
            {
                "group_id": "grp-api",
                "task_ids": [1, 2],
                "status": "submitted",
                "created_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "",
                "total_cost_usd": 0.0,
                "total_duration_s": 0.0,
                "task_results": {},
                "validation_verdict": "",
            }
        ]
    )
    wire_control_api(
        polling_trigger=pt,
        dispatcher=disp,
        admin_token="tok",
        golem_flow=gf,
    )
    yield
    wire_control_api()


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestGetBatchEndpoint:
    async def test_get_batch_endpoint_returns_batch(self, _wire_batch_deps):
        from golem.core.control_api import get_batch

        result = await get_batch("grp-api")
        assert result["ok"] is True
        assert result["batch"]["group_id"] == "grp-api"
        assert result["batch"]["task_ids"] == [1, 2]

    async def test_get_batch_endpoint_404_for_unknown(self, _wire_batch_deps):
        from golem.core.control_api import get_batch

        control_api._golem_flow.get_batch = MagicMock(return_value=None)
        with pytest.raises(Exception, match="No batch found"):
            await get_batch("nonexistent")


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestListBatchesEndpoint:
    async def test_list_batches_endpoint(self, _wire_batch_deps):
        from golem.core.control_api import list_batches

        result = await list_batches()
        assert result["ok"] is True
        assert len(result["batches"]) == 1
        assert result["batches"][0]["group_id"] == "grp-api"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestBatchEndpoints503WhenNoFlow:
    async def test_get_batch_503_when_no_flow(self):
        from golem.core.control_api import get_batch

        wire_control_api()  # reset — no golem_flow
        with pytest.raises(Exception, match="Daemon not ready"):
            await get_batch("any-group")

    async def test_list_batches_503_when_no_flow(self):
        from golem.core.control_api import list_batches

        wire_control_api()  # reset — no golem_flow
        with pytest.raises(Exception, match="Daemon not ready"):
            await list_batches()


class TestCmdBatchNoSubcommand:
    def test_cmd_batch_no_subcommand(self, monkeypatch):
        from golem.cli import cmd_batch

        # Provide a minimal config so load_config doesn't touch real files
        monkeypatch.setattr(
            "golem.core.config.load_config",
            lambda _cfg=None: Config(),
        )

        args = argparse.Namespace(config=None, batch_command=None)
        result = cmd_batch(args)
        assert result == 1


class TestCmdBatchStatus:
    def test_cmd_batch_status_success(self, monkeypatch):
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr("golem.core.config.load_config", lambda _cfg=None: Config())

        response_data = json.dumps(
            {"batch": {"group_id": "grp", "status": "completed"}}
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: mock_resp)

        args = argparse.Namespace(config=None, batch_command="status", group_id="grp")
        result = cmd_batch(args)
        assert result == 0

    def test_cmd_batch_status_with_api_key(self, monkeypatch):
        import urllib.request

        from golem.cli import cmd_batch

        config = Config()
        config.dashboard.api_key = "secret-key"
        monkeypatch.setattr("golem.core.config.load_config", lambda _cfg=None: config)

        response_data = json.dumps(
            {"batch": {"group_id": "grp", "status": "completed"}}
        ).encode()
        captured_req = {}

        def mock_urlopen(req, **_kw):
            captured_req["headers"] = dict(req.headers)
            mock_resp = MagicMock()
            mock_resp.read.return_value = response_data
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

        args = argparse.Namespace(config=None, batch_command="status", group_id="grp")
        result = cmd_batch(args)
        assert result == 0
        assert captured_req["headers"].get("Authorization") == "Bearer secret-key"

    def test_cmd_batch_status_http_error(self, monkeypatch):
        import urllib.error
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr("golem.core.config.load_config", lambda _cfg=None: Config())

        def raise_http_error(*a, **kw):
            raise urllib.error.HTTPError(
                None, 404, "Not Found", {}, MagicMock(read=lambda: b"nope")
            )

        monkeypatch.setattr(urllib.request, "urlopen", raise_http_error)

        args = argparse.Namespace(config=None, batch_command="status", group_id="grp")
        result = cmd_batch(args)
        assert result == 1

    def test_cmd_batch_status_url_error(self, monkeypatch):
        import urllib.error
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr("golem.core.config.load_config", lambda _cfg=None: Config())

        def raise_url_error(*a, **kw):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)

        args = argparse.Namespace(config=None, batch_command="status", group_id="grp")
        result = cmd_batch(args)
        assert result == 1


class TestCmdBatchList:
    def test_cmd_batch_list_success(self, monkeypatch):
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr("golem.core.config.load_config", lambda _cfg=None: Config())

        batches = [{"group_id": "grp-1", "status": "completed", "task_ids": [1, 2]}]
        response_data = json.dumps({"batches": batches}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: mock_resp)

        args = argparse.Namespace(config=None, batch_command="list")
        result = cmd_batch(args)
        assert result == 0

    def test_cmd_batch_list_empty(self, monkeypatch):
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr("golem.core.config.load_config", lambda _cfg=None: Config())

        response_data = json.dumps({"batches": []}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: mock_resp)

        args = argparse.Namespace(config=None, batch_command="list")
        result = cmd_batch(args)
        assert result == 0

    def test_cmd_batch_list_http_error(self, monkeypatch):
        import urllib.error
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr("golem.core.config.load_config", lambda _cfg=None: Config())

        def raise_http_error(*a, **kw):
            raise urllib.error.HTTPError(
                None, 500, "Error", {}, MagicMock(read=lambda: b"err")
            )

        monkeypatch.setattr(urllib.request, "urlopen", raise_http_error)

        args = argparse.Namespace(config=None, batch_command="list")
        result = cmd_batch(args)
        assert result == 1

    def test_cmd_batch_list_url_error(self, monkeypatch):
        import urllib.error
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr("golem.core.config.load_config", lambda _cfg=None: Config())

        def raise_url_error(*a, **kw):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)

        args = argparse.Namespace(config=None, batch_command="list")
        result = cmd_batch(args)
        assert result == 1


# ---------------------------------------------------------------------------
# _format_batch_status tests
# ---------------------------------------------------------------------------


class TestFormatBatchStatus:
    """Tests for the _format_batch_status CLI helper."""

    def test_completed_batch_with_tasks(self, capsys):
        from golem.batch_cli import format_batch_status

        batch = {
            "group_id": "grp-fmt",
            "status": "completed",
            "task_ids": [1, 2],
            "task_results": {
                "1": {
                    "state": "completed",
                    "validation_verdict": "pass",
                    "total_cost_usd": 1.50,
                    "duration_seconds": 120.0,
                },
                "2": {
                    "state": "failed",
                    "validation_verdict": "fail",
                    "total_cost_usd": 0.75,
                    "duration_seconds": 60.0,
                },
            },
            "total_cost_usd": 2.25,
            "total_duration_s": 180.0,
            "validation_verdict": "pass",
            "created_at": "2026-01-01T00:00:00",
            "completed_at": "2026-01-01T01:00:00",
        }
        format_batch_status(batch)
        out = capsys.readouterr().out
        assert "grp-fmt" in out
        assert "COMPLETED" in out
        assert "Created:" in out
        assert "Completed:" in out
        assert "$2.25" in out
        assert "Overall verdict:" in out

    def test_running_batch_no_results(self, capsys):
        from golem.batch_cli import format_batch_status

        batch = {
            "group_id": "grp-run",
            "status": "running",
            "task_ids": [10],
            "task_results": {},
            "total_cost_usd": 0.0,
            "total_duration_s": 0.0,
            "validation_verdict": "",
            "created_at": "",
            "completed_at": "",
        }
        format_batch_status(batch)
        out = capsys.readouterr().out
        assert "grp-run" in out
        assert "RUNNING" in out
        # No Created/Completed lines when empty
        assert "Created:" not in out
        assert "Overall verdict:" not in out

    def test_failed_status_coloring(self, capsys):
        from golem.batch_cli import format_batch_status

        batch = {
            "group_id": "grp-fail",
            "status": "failed",
            "task_ids": [],
            "task_results": {},
            "total_cost_usd": 0.0,
            "total_duration_s": 0.0,
            "validation_verdict": "failed",
            "created_at": "",
            "completed_at": "",
        }
        format_batch_status(batch)
        out = capsys.readouterr().out
        assert "FAILED" in out
        assert "Overall verdict:" in out

    def test_unknown_status_no_color(self, capsys):
        from golem.batch_cli import format_batch_status

        batch = {
            "group_id": "grp-unk",
            "status": "pending",
            "task_ids": [],
            "task_results": {},
            "total_cost_usd": 0.0,
            "total_duration_s": 0.0,
            "validation_verdict": "unknown",
            "created_at": "",
            "completed_at": "",
        }
        format_batch_status(batch)
        out = capsys.readouterr().out
        assert "PENDING" in out

    def test_task_results_with_unknown_and_no_cost(self, capsys):
        from golem.batch_cli import format_batch_status

        batch = {
            "group_id": "grp-unk2",
            "status": "in_progress",
            "task_ids": [5, 6],
            "task_results": {
                "5": {
                    "state": "planning",
                    "validation_verdict": "",
                    "total_cost_usd": 0.0,
                    "duration_seconds": 0.0,
                },
                "6": {
                    "state": "unknown_state",
                    "validation_verdict": "something",
                    "total_cost_usd": 0.0,
                    "duration_seconds": 0.0,
                },
            },
            "total_cost_usd": 0.0,
            "total_duration_s": 0.0,
            "validation_verdict": "",
            "created_at": "",
            "completed_at": "",
        }
        format_batch_status(batch)
        out = capsys.readouterr().out
        assert "grp-unk2" in out

    def test_missing_fields_use_defaults(self, capsys):
        from golem.batch_cli import format_batch_status

        format_batch_status({})
        out = capsys.readouterr().out
        assert "Batch: ?" in out
        assert "$0.00" in out


# ---------------------------------------------------------------------------
# _cmd_batch_submit tests
# ---------------------------------------------------------------------------


class TestCmdBatchSubmit:
    """Tests for _cmd_batch_submit via cmd_batch with batch_command='submit'."""

    def _make_config(self):
        config = Config()
        config.dashboard.port = 9999
        config.dashboard.api_key = "test-key"
        return config

    def test_file_not_found(self, monkeypatch, tmp_path):
        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )

        args = argparse.Namespace(
            config=None,
            batch_command="submit",
            file=str(tmp_path / "nonexistent.json"),
        )
        result = cmd_batch(args)
        assert result == 1

    def test_empty_file(self, monkeypatch, tmp_path):
        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )

        f = tmp_path / "empty.json"
        f.write_text("")
        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 1

    def test_invalid_json(self, monkeypatch, tmp_path):
        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )

        f = tmp_path / "bad.json"
        f.write_text("{not valid json!!")
        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 1

    def test_invalid_yaml(self, monkeypatch, tmp_path):
        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )

        f = tmp_path / "bad.yaml"
        f.write_text("tasks:\n  - subject: ok\n  bad indent\n: :\n")
        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 1

    def test_payload_not_dict(self, monkeypatch, tmp_path):
        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )

        f = tmp_path / "list.json"
        f.write_text("[1, 2, 3]")
        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 1

    def test_missing_tasks_array(self, monkeypatch, tmp_path):
        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )

        f = tmp_path / "no_tasks.json"
        f.write_text('{"foo": "bar"}')
        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 1

    def test_empty_tasks_array(self, monkeypatch, tmp_path):
        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )

        f = tmp_path / "empty_tasks.json"
        f.write_text('{"tasks": []}')
        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 1

    def test_submit_success(self, monkeypatch, tmp_path, capsys):
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )
        monkeypatch.setattr("golem.cli._ensure_daemon", lambda *a, **kw: None)

        response_data = json.dumps(
            {
                "group_id": "grp-sub",
                "tasks": [
                    {"task_id": 100, "status": "submitted"},
                    {"task_id": 101, "status": "submitted"},
                ],
            }
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: mock_resp)

        f = tmp_path / "batch.json"
        f.write_text(
            json.dumps(
                {
                    "tasks": [
                        {"prompt": "A", "subject": "Task A", "key": "a"},
                        {
                            "prompt": "B",
                            "subject": "Task B",
                            "depends_on": ["a"],
                        },
                    ]
                }
            )
        )

        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "grp-sub" in out
        assert "#100" in out
        assert "#101" in out
        assert "key=a" in out
        assert "depends_on" in out

    def test_submit_http_error(self, monkeypatch, tmp_path):
        import urllib.error
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )
        monkeypatch.setattr("golem.cli._ensure_daemon", lambda *a, **kw: None)

        def raise_http_error(*a, **kw):
            raise urllib.error.HTTPError(
                None, 400, "Bad Request", {}, MagicMock(read=lambda: b"bad")
            )

        monkeypatch.setattr(urllib.request, "urlopen", raise_http_error)

        f = tmp_path / "batch.json"
        f.write_text(json.dumps({"tasks": [{"prompt": "A"}]}))

        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 1

    def test_submit_url_error(self, monkeypatch, tmp_path):
        import urllib.error
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )
        monkeypatch.setattr("golem.cli._ensure_daemon", lambda *a, **kw: None)

        def raise_url_error(*a, **kw):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", raise_url_error)

        f = tmp_path / "batch.json"
        f.write_text(json.dumps({"tasks": [{"prompt": "A"}]}))

        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 1

    def test_submit_yaml_file(self, monkeypatch, tmp_path, capsys):
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )
        monkeypatch.setattr("golem.cli._ensure_daemon", lambda *a, **kw: None)

        response_data = json.dumps(
            {
                "group_id": "grp-yaml",
                "tasks": [{"task_id": 200, "status": "submitted"}],
            }
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: mock_resp)

        f = tmp_path / "batch.yaml"
        f.write_text("tasks:\n  - prompt: 'hello'\n    subject: 'Test YAML'\n")

        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "grp-yaml" in out

    def test_submit_unknown_extension_tries_json(self, monkeypatch, tmp_path):
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )
        monkeypatch.setattr("golem.cli._ensure_daemon", lambda *a, **kw: None)

        response_data = json.dumps(
            {
                "group_id": "grp-unk",
                "tasks": [{"task_id": 300, "status": "submitted"}],
            }
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: mock_resp)

        f = tmp_path / "batch.txt"
        f.write_text(json.dumps({"tasks": [{"prompt": "A"}]}))

        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 0

    def test_submit_unknown_extension_falls_back_to_yaml(
        self, monkeypatch, tmp_path, capsys
    ):
        """Unknown extension with non-JSON content falls back to YAML parsing."""
        import urllib.request

        from golem.cli import cmd_batch

        monkeypatch.setattr(
            "golem.core.config.load_config", lambda _cfg=None: self._make_config()
        )
        monkeypatch.setattr("golem.cli._ensure_daemon", lambda *a, **kw: None)

        response_data = json.dumps(
            {
                "group_id": "grp-yaml-fallback",
                "tasks": [{"task_id": 400, "status": "submitted"}],
            }
        ).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: mock_resp)

        # .txt file with YAML content (not valid JSON) triggers JSON fallback to YAML
        f = tmp_path / "batch.txt"
        f.write_text("tasks:\n  - prompt: 'YAML fallback test'\n")

        args = argparse.Namespace(config=None, batch_command="submit", file=str(f))
        result = cmd_batch(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "grp-yaml-fallback" in out


# ---------------------------------------------------------------------------
# _decode_content / _parse_batch_file edge cases (no-yaml paths)
# ---------------------------------------------------------------------------


class TestDecodeContentNoYaml:
    """Tests for _decode_content when yaml is None (PyYAML not installed)."""

    def test_yaml_suffix_without_pyyaml(self):
        from golem.batch_cli import _decode_content

        with pytest.raises(ValueError, match="PyYAML not installed"):
            _decode_content("key: value", ".yaml", None)

    def test_yml_suffix_without_pyyaml(self):
        from golem.batch_cli import _decode_content

        with pytest.raises(ValueError, match="PyYAML not installed"):
            _decode_content("key: value", ".yml", None)

    def test_unknown_suffix_invalid_json_no_yaml(self):
        from golem.batch_cli import _decode_content

        with pytest.raises((json.JSONDecodeError, ValueError)):
            _decode_content("key: value", ".txt", None)


class TestParseBatchFileNoYaml:
    """Test _parse_batch_file when yaml import fails."""

    def test_yaml_import_error(self, monkeypatch, tmp_path):
        from golem.batch_cli import _parse_batch_file

        f = tmp_path / "batch.yaml"
        f.write_text('{"tasks": [{"prompt": "test"}]}')

        # Patch builtins.__import__ to make yaml import fail
        original_import = (
            __builtins__.__import__
            if hasattr(__builtins__, "__import__")  # type: ignore[union-attr]
            else __import__
        )

        def mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", mock_import)
        result = _parse_batch_file(str(f))
        assert result == 1
