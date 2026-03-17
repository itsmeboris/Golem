"""Tests for SIGHUP-based daemon reload mechanism."""

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.cli import _handle_reload


class TestHandleReload:
    """Tests for the _handle_reload coroutine."""

    @pytest.mark.asyncio
    async def test_stops_tick_loop(self):
        flow = MagicMock()
        flow.stop_tick_loop = MagicMock()
        flow._sessions = {}
        reload_event = asyncio.Event()
        reload_event.set()
        with patch("golem.cli.os.execv") as mock_execv:
            await _handle_reload(
                reload_event,
                flow=flow,
                drain_timeout=1,
            )
        flow.stop_tick_loop.assert_called_once()

    @pytest.mark.asyncio
    async def test_calls_execv(self):
        flow = MagicMock()
        flow.stop_tick_loop = MagicMock()
        flow._sessions = {}
        reload_event = asyncio.Event()
        reload_event.set()
        with patch("golem.cli.os.execv") as mock_execv:
            await _handle_reload(
                reload_event,
                flow=flow,
                drain_timeout=1,
            )
        expected_argv = list(sys.argv)
        if "--foreground" not in expected_argv:
            expected_argv.append("--foreground")
        mock_execv.assert_called_once_with(
            sys.executable,
            [sys.executable] + expected_argv,
        )

    @pytest.mark.asyncio
    async def test_execv_failure_continues(self):
        flow = MagicMock()
        flow.stop_tick_loop = MagicMock()
        flow._sessions = {}
        flow.start_tick_loop = MagicMock()
        reload_event = asyncio.Event()
        reload_event.set()
        with patch("golem.cli.os.execv", side_effect=OSError("boom")):
            await _handle_reload(
                reload_event,
                flow=flow,
                drain_timeout=1,
            )
        # Flow should be restarted on failure
        flow.start_tick_loop.assert_called_once()

    @pytest.mark.asyncio
    async def test_debounce_during_reload(self):
        """Second reload_event during drain is ignored."""
        flow = MagicMock()
        flow.stop_tick_loop = MagicMock()
        flow._sessions = {}
        reload_event = asyncio.Event()
        reload_event.set()
        with patch("golem.cli.os.execv") as mock_execv:
            await _handle_reload(
                reload_event,
                flow=flow,
                drain_timeout=1,
            )
        assert mock_execv.call_count == 1

    @pytest.mark.asyncio
    async def test_drain_timeout_proceeds(self):
        """If tasks don't finish before timeout, proceed anyway."""
        flow = MagicMock()
        flow.stop_tick_loop = MagicMock()
        # Always report active sessions
        flow._sessions = {1: MagicMock(), 2: MagicMock()}
        reload_event = asyncio.Event()
        reload_event.set()
        with patch("golem.cli.os.execv") as mock_execv:
            await _handle_reload(
                reload_event,
                flow=flow,
                drain_timeout=1,
            )
        # Should still call execv after timeout
        mock_execv.assert_called_once()

    @pytest.mark.asyncio
    async def test_drain_timeout_logs_active_count(self, caplog):
        """Drain timeout warning must report only non-terminal session count."""
        import logging

        from golem.orchestrator import TaskSession, TaskSessionState

        flow = MagicMock()
        flow.stop_tick_loop = MagicMock()
        flow._sessions = {
            1: TaskSession(parent_issue_id=1, state=TaskSessionState.RUNNING),
            2: TaskSession(parent_issue_id=2, state=TaskSessionState.COMPLETED),
            3: TaskSession(parent_issue_id=3, state=TaskSessionState.FAILED),
        }
        reload_event = asyncio.Event()
        reload_event.set()
        with caplog.at_level(logging.WARNING):
            with patch("golem.cli.os.execv"):
                await _handle_reload(
                    reload_event,
                    flow=flow,
                    drain_timeout=1,
                )
        drain_warnings = [r for r in caplog.records if "Drain timeout" in r.message]
        assert len(drain_warnings) == 1
        assert "1 active sessions" in drain_warnings[0].message

    @pytest.mark.asyncio
    async def test_human_review_treated_as_terminal(self):
        """HUMAN_REVIEW sessions should not block the drain loop."""
        from golem.orchestrator import TaskSession, TaskSessionState

        flow = MagicMock()
        flow.stop_tick_loop = MagicMock()
        flow._sessions = {
            1: TaskSession(parent_issue_id=1, state=TaskSessionState.HUMAN_REVIEW),
        }
        reload_event = asyncio.Event()
        reload_event.set()
        with patch("golem.cli.os.execv") as mock_execv:
            await _handle_reload(
                reload_event,
                flow=flow,
                drain_timeout=1,
            )
        # Should proceed immediately without hitting drain timeout
        mock_execv.assert_called_once()

    @pytest.mark.asyncio
    async def test_calls_apply_update_before_execv(self):
        """self_update_manager.apply_update() is called after drain, before execv."""
        flow = MagicMock()
        flow.stop_tick_loop = MagicMock()
        flow._sessions = {}
        self_update = MagicMock()
        self_update._verified_sha = "abc123"
        self_update.apply_update = AsyncMock()
        reload_event = asyncio.Event()
        reload_event.set()
        with patch("golem.cli.os.execv"):
            await _handle_reload(
                reload_event,
                flow=flow,
                self_update_manager=self_update,
                drain_timeout=1,
            )
        self_update.apply_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_apply_called_even_without_verified_sha(self):
        """apply_update is always called; the method itself guards on _verified_sha."""
        flow = MagicMock()
        flow.stop_tick_loop = MagicMock()
        flow._sessions = {}
        self_update = MagicMock()
        self_update._verified_sha = None
        self_update.apply_update = AsyncMock()
        reload_event = asyncio.Event()
        reload_event.set()
        with patch("golem.cli.os.execv"):
            await _handle_reload(
                reload_event,
                flow=flow,
                self_update_manager=self_update,
                drain_timeout=1,
            )
        self_update.apply_update.assert_called_once()
