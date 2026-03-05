# pylint: disable=too-few-public-methods
"""Tests for batch API endpoints, flow-level batch monitor integration, and CLI."""

import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.core import control_api
from golem.core.config import Config, GolemFlowConfig
from golem.core.control_api import wire_control_api
from golem.orchestrator import TaskSession, TaskSessionState


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
    @pytest.mark.asyncio
    async def test_get_batch_endpoint_returns_batch(self, _wire_batch_deps):
        from golem.core.control_api import get_batch

        result = await get_batch("grp-api")
        assert result["ok"] is True
        assert result["batch"]["group_id"] == "grp-api"
        assert result["batch"]["task_ids"] == [1, 2]

    @pytest.mark.asyncio
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
    @pytest.mark.asyncio
    async def test_list_batches_endpoint(self, _wire_batch_deps):
        from golem.core.control_api import list_batches

        result = await list_batches()
        assert result["ok"] is True
        assert len(result["batches"]) == 1
        assert result["batches"][0]["group_id"] == "grp-api"


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestCmdBatchNoSubcommand:
    def test_cmd_batch_no_subcommand(self, monkeypatch, tmp_path):
        from golem.cli import cmd_batch

        # Provide a minimal config so load_config doesn't touch real files
        monkeypatch.setattr(
            "golem.cli.load_config",
            lambda _cfg=None: Config(),
        )

        args = argparse.Namespace(config=None, batch_command=None)
        result = cmd_batch(args)
        assert result == 1
