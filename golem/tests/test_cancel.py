# pylint: disable=too-few-public-methods
"""Tests for per-task cancel: GolemFlow.cancel_session, API endpoint, CLI command."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.core import control_api
from golem.core.config import Config, GolemFlowConfig
from golem.core.control_api import wire_control_api
from golem.orchestrator import TaskSession, TaskSessionState


# ---------------------------------------------------------------------------
# Helpers (reuse test_flow patterns)
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


def _make_flow(monkeypatch, tmp_path, **flow_kwargs):
    from golem.flow import GolemFlow

    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

    profile = _make_test_profile()
    fc_kwargs = {"enabled": True, "projects": ["test-project"], "profile": "test"}
    fc_kwargs.update(flow_kwargs)
    config = Config(golem=GolemFlowConfig(**fc_kwargs))
    monkeypatch.setattr(
        "golem.flow.build_profile",
        lambda _name, _cfg: profile,
    )
    return GolemFlow(config)


# ---------------------------------------------------------------------------
# GolemFlow.cancel_session
# ---------------------------------------------------------------------------


class TestCancelSessionFromDetected:
    def test_cancel_detected(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=100,
            parent_subject="detected task",
            state=TaskSessionState.DETECTED,
        )
        flow._sessions[100] = session

        result = flow.cancel_session(100)

        assert result == {"task_id": 100, "status": "cancelled"}
        assert session.state == TaskSessionState.FAILED
        assert session.result_summary == "Cancelled by user"


class TestCancelSessionFromRunning:
    def test_cancel_running(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=101,
            parent_subject="running task",
            state=TaskSessionState.RUNNING,
        )
        flow._sessions[101] = session

        result = flow.cancel_session(101)

        assert result == {"task_id": 101, "status": "cancelled"}
        assert session.state == TaskSessionState.FAILED


class TestCancelSessionFromValidating:
    def test_cancel_validating(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=102,
            parent_subject="validating task",
            state=TaskSessionState.VALIDATING,
        )
        flow._sessions[102] = session

        result = flow.cancel_session(102)

        assert result == {"task_id": 102, "status": "cancelled"}
        assert session.state == TaskSessionState.FAILED


class TestCancelSessionFromRetrying:
    def test_cancel_retrying(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=103,
            parent_subject="retrying task",
            state=TaskSessionState.RETRYING,
        )
        flow._sessions[103] = session

        result = flow.cancel_session(103)

        assert result == {"task_id": 103, "status": "cancelled"}
        assert session.state == TaskSessionState.FAILED


class TestCancelSessionCancelsAsyncTask:
    async def test_cancels_running_asyncio_task(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=104,
            parent_subject="task with asyncio task",
            state=TaskSessionState.RUNNING,
        )
        flow._sessions[104] = session

        async_task = asyncio.create_task(asyncio.sleep(100))
        flow._session_tasks[104] = async_task

        flow.cancel_session(104)

        await asyncio.sleep(0)
        assert async_task.cancelled()


class TestCancelSessionNotFound:
    def test_raises_for_unknown_task(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        with pytest.raises(ValueError, match="not found"):
            flow.cancel_session(999)


class TestCancelSessionTerminalCompleted:
    def test_raises_for_completed(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=105,
            parent_subject="done task",
            state=TaskSessionState.COMPLETED,
        )
        flow._sessions[105] = session

        with pytest.raises(ValueError, match="terminal state"):
            flow.cancel_session(105)


class TestCancelSessionTerminalFailed:
    def test_raises_for_failed(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=106,
            parent_subject="failed task",
            state=TaskSessionState.FAILED,
        )
        flow._sessions[106] = session

        with pytest.raises(ValueError, match="terminal state"):
            flow.cancel_session(106)


class TestCancelSessionTriggersTransition:
    def test_calls_handle_state_transition(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=107,
            parent_subject="transition test",
            state=TaskSessionState.RUNNING,
        )
        flow._sessions[107] = session

        transitions = []
        monkeypatch.setattr(
            flow,
            "_handle_state_transition",
            lambda s, prev: transitions.append((s.state, prev)),
        )

        flow.cancel_session(107)

        assert transitions == [(TaskSessionState.FAILED, TaskSessionState.RUNNING)]


class TestCancelSessionSavesState:
    def test_persists_state(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        mock_live = MagicMock()
        monkeypatch.setattr("golem.flow.LiveState.get", lambda: mock_live)

        session = TaskSession(
            parent_issue_id=108,
            parent_subject="save test",
            state=TaskSessionState.DETECTED,
        )
        flow._sessions[108] = session

        saved = []
        monkeypatch.setattr(
            "golem.flow.save_sessions",
            lambda sessions: saved.append(True),
        )

        flow.cancel_session(108)
        assert saved


# ---------------------------------------------------------------------------
# API endpoint: POST /api/cancel/{task_id}
# ---------------------------------------------------------------------------


@pytest.fixture()
def _wire_cancel_deps():
    gf = MagicMock()
    gf.cancel_session = MagicMock(return_value={"task_id": 42, "status": "cancelled"})
    wire_control_api(golem_flow=gf)
    yield
    wire_control_api()


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestCancelEndpointSuccess:
    @pytest.mark.asyncio
    async def test_cancel_returns_ok(self, _wire_cancel_deps):
        from golem.core.control_api import cancel_task

        result = await cancel_task(42)
        assert result["ok"] is True
        assert result["task_id"] == 42
        assert result["status"] == "cancelled"
        control_api._golem_flow.cancel_session.assert_called_once_with(42)


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestCancelEndpoint404:
    @pytest.mark.asyncio
    async def test_not_found(self, _wire_cancel_deps):
        from golem.core.control_api import cancel_task

        control_api._golem_flow.cancel_session.side_effect = ValueError(
            "Task 999 not found"
        )
        with pytest.raises(Exception) as exc_info:
            await cancel_task(999)
        assert exc_info.value.status_code == 404


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestCancelEndpoint409:
    @pytest.mark.asyncio
    async def test_terminal_state(self, _wire_cancel_deps):
        from golem.core.control_api import cancel_task

        control_api._golem_flow.cancel_session.side_effect = ValueError(
            "Task 105 is in terminal state 'completed'"
        )
        with pytest.raises(Exception) as exc_info:
            await cancel_task(105)
        assert exc_info.value.status_code == 409


@pytest.mark.skipif(
    not control_api.FASTAPI_AVAILABLE,
    reason="FastAPI not installed",
)
class TestCancelEndpoint503:
    @pytest.mark.asyncio
    async def test_no_golem_flow(self, _wire_cancel_deps):
        from golem.core.control_api import cancel_task

        control_api._golem_flow = None
        with pytest.raises(Exception) as exc_info:
            await cancel_task(42)
        assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# CLI: golem cancel <task_id>
# ---------------------------------------------------------------------------


class TestCmdCancelSuccess:
    @patch("golem.cli.load_config")
    def test_prints_success(self, mock_config, capsys):
        from golem.cli import cmd_cancel

        mock_config.return_value = MagicMock()
        mock_config.return_value.dashboard.port = 8082

        response_body = json.dumps(
            {"ok": True, "task_id": 42, "status": "cancelled"}
        ).encode()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = response_body
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        args = SimpleNamespace(config=None, task_id=42)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = cmd_cancel(args)

        assert result == 0
        out = capsys.readouterr().out
        assert "cancelled" in out


class TestCmdCancelHttpError:
    @patch("golem.cli.load_config")
    def test_prints_error_on_http_error(self, mock_config, capsys):
        import urllib.error

        from golem.cli import cmd_cancel

        mock_config.return_value = MagicMock()
        mock_config.return_value.dashboard.port = 8082

        exc = urllib.error.HTTPError(
            url="http://localhost/api/cancel/999",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )
        exc.read = MagicMock(return_value=b'{"detail": "Task 999 not found"}')

        args = SimpleNamespace(config=None, task_id=999)

        with patch("urllib.request.urlopen", side_effect=exc):
            result = cmd_cancel(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "404" in err


class TestCmdCancelConnectionError:
    @patch("golem.cli.load_config")
    def test_prints_error_on_connection_failure(self, mock_config, capsys):
        import urllib.error

        from golem.cli import cmd_cancel

        mock_config.return_value = MagicMock()
        mock_config.return_value.dashboard.port = 8082

        args = SimpleNamespace(config=None, task_id=42)

        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("Connection refused"),
        ):
            result = cmd_cancel(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "Cannot reach daemon" in err


class TestCmdCancelOSError:
    @patch("golem.cli.load_config")
    def test_handles_os_error(self, mock_config, capsys):
        from golem.cli import cmd_cancel

        mock_config.return_value = MagicMock()
        mock_config.return_value.dashboard.port = 8082

        args = SimpleNamespace(config=None, task_id=42)

        with patch(
            "urllib.request.urlopen",
            side_effect=OSError("Network unreachable"),
        ):
            result = cmd_cancel(args)

        assert result == 1
        err = capsys.readouterr().err
        assert "Cannot reach daemon" in err


class TestCancelArgparse:
    @patch("golem.cli.cmd_cancel", return_value=0)
    def test_cancel_command_parsed(self, mock_cancel):
        from golem.cli import main

        with patch("sys.argv", ["golem", "cancel", "12345"]):
            result = main()
        assert result == 0
        mock_cancel.assert_called_once()
        args = mock_cancel.call_args[0][0]
        assert args.task_id == 12345
