# pylint: disable=too-few-public-methods,redefined-outer-name
"""Tests for the /api/events SSE endpoint in golem.core.dashboard."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

import golem.core.dashboard as _dashboard_module
from golem.core.dashboard import _sse_event_stream, mount_dashboard

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def app_and_paths(tmp_path):
    """Return (FastAPI app, traces_dir, sessions_file) with dashboard mounted."""
    from fastapi import FastAPI

    traces = tmp_path / "traces" / "golem"
    traces.mkdir(parents=True)
    sessions_file = tmp_path / "sessions.json"
    sessions_file.write_text(json.dumps({"sessions": {}}), encoding="utf-8")

    app = FastAPI()
    with (
        patch("golem.core.dashboard.FASTAPI_AVAILABLE", True),
        patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"),
        patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
    ):
        mount_dashboard(app)
        yield app, tmp_path / "traces", sessions_file


# ---------------------------------------------------------------------------
# HTTP-level tests — /api/events returns 200 + text/event-stream
# ---------------------------------------------------------------------------


class TestApiEventsRoute:
    """Verify the /api/events endpoint is wired up correctly."""

    @pytest.mark.asyncio
    async def test_returns_200_with_event_stream_content_type(self, app_and_paths):
        """GET /api/events returns HTTP 200 with text/event-stream content-type."""
        import httpx

        app, traces_dir, sessions_file = app_and_paths

        async def fake_sleep(_dur):
            # Immediately raise to stop the generator after one iteration
            raise asyncio.CancelledError()

        with (
            patch("golem.core.dashboard.TRACES_DIR", traces_dir),
            patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
            patch("golem.core.dashboard.asyncio") as mock_asyncio,
        ):
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as ac:
                async with ac.stream("GET", "/api/events") as resp:
                    assert resp.status_code == 200
                    assert "text/event-stream" in resp.headers["content-type"]


# ---------------------------------------------------------------------------
# Generator unit tests
# ---------------------------------------------------------------------------


class TestSseEventStreamHeartbeat:
    """Verify heartbeat events are emitted after 15 iterations."""

    @pytest.mark.asyncio
    async def test_heartbeat_emitted_after_15_iterations(self, tmp_path):
        """After 15 sleep cycles with no file changes, a heartbeat is yielded."""
        sessions_file = tmp_path / "sessions.json"
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        sleep_call_count = 0

        async def fake_sleep(_dur):
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count > 15:
                raise asyncio.CancelledError

        events = []
        with (
            patch("golem.core.dashboard.TRACES_DIR", traces_dir),
            patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
            patch("golem.core.dashboard.asyncio") as mock_asyncio,
        ):
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            try:
                async for chunk in _sse_event_stream():
                    events.append(chunk)
            except asyncio.CancelledError:
                pass

        heartbeats = [e for e in events if '"heartbeat"' in e]
        assert len(heartbeats) >= 1
        assert heartbeats[0] == 'data: {"type": "heartbeat"}\n\n'


class TestSseEventStreamSessionUpdate:
    """Verify session_update events are emitted on sessions file mtime change."""

    @pytest.mark.asyncio
    async def test_session_file_mtime_change_triggers_event(self, tmp_path):
        """Changing the sessions file mtime causes a session_update event."""
        sessions_file = tmp_path / "sessions.json"
        sessions_file.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        original_mtime = sessions_file.stat().st_mtime
        new_mtime = original_mtime + 2.0

        # Path.exists() calls stat() internally, so the init phase produces two
        # stat() calls on sessions_file before the loop: one from exists() and
        # one from the explicit .stat() call.  We return original_mtime for
        # those first two calls and new_mtime for all subsequent calls so that
        # the loop detects the change.
        stat_calls = [0]

        class _FakeStat:
            def __init__(self, mtime):
                self.st_mtime = mtime

        original_stat = Path.stat

        def patched_stat(self, *args, **kwargs):
            if self == sessions_file:
                stat_calls[0] += 1
                if stat_calls[0] < 3:
                    # First two calls are from the init phase (exists() + .stat())
                    return _FakeStat(original_mtime)
                return _FakeStat(new_mtime)
            return original_stat(self, *args, **kwargs)

        sleep_call_count = 0

        async def fake_sleep(_dur):
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count > 2:
                raise asyncio.CancelledError

        events = []
        with (
            patch("golem.core.dashboard.TRACES_DIR", traces_dir),
            patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
            patch("golem.core.dashboard.asyncio") as mock_asyncio,
            patch.object(Path, "stat", patched_stat),
        ):
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            try:
                async for chunk in _sse_event_stream():
                    events.append(chunk)
            except asyncio.CancelledError:
                pass

        session_events = [e for e in events if "session_update" in e]
        assert len(session_events) >= 1
        assert 'event: session_update\ndata: {"type": "session_update"}\n\n' in events


class TestSseEventStreamTraceUpdate:
    """Verify trace_update events are emitted when .jsonl files change."""

    @pytest.mark.asyncio
    async def test_new_trace_file_triggers_event(self, tmp_path):
        """A new .jsonl trace file triggers a trace_update with the file stem."""
        traces_dir = tmp_path / "traces" / "golem"
        traces_dir.mkdir(parents=True)
        sessions_file = tmp_path / "sessions.json"
        # sessions file should not exist to avoid extra events
        if sessions_file.exists():
            sessions_file.unlink()

        sleep_call_count = 0
        trace_file_created = False

        async def fake_sleep(_dur):
            nonlocal sleep_call_count, trace_file_created
            sleep_call_count += 1
            if sleep_call_count == 1 and not trace_file_created:
                # Create a new trace file between the first and second poll
                (traces_dir / "golem-99-20260101.jsonl").write_text(
                    "{}", encoding="utf-8"
                )
                trace_file_created = True
            if sleep_call_count > 2:
                raise asyncio.CancelledError

        events = []
        with (
            patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"),
            patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
            patch("golem.core.dashboard.asyncio") as mock_asyncio,
        ):
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            try:
                async for chunk in _sse_event_stream():
                    events.append(chunk)
            except asyncio.CancelledError:
                pass

        trace_events = [e for e in events if "trace_update" in e]
        assert len(trace_events) >= 1
        parsed = json.loads(trace_events[0].split("data: ", 1)[1].strip())
        assert parsed["type"] == "trace_update"
        assert parsed["event_id"] == "golem-99-20260101"

    @pytest.mark.asyncio
    async def test_modified_trace_file_triggers_event(self, tmp_path):
        """Modifying an existing .jsonl trace file triggers a trace_update."""
        traces_dir = tmp_path / "traces" / "golem"
        traces_dir.mkdir(parents=True)
        trace_file = traces_dir / "golem-77-20260101.jsonl"
        trace_file.write_text("{}", encoding="utf-8")
        sessions_file = tmp_path / "sessions.json"
        if sessions_file.exists():
            sessions_file.unlink()

        original_mtime = trace_file.stat().st_mtime
        new_mtime = original_mtime + 2.0

        stat_call_count = [0]

        class _FakeStat:
            def __init__(self, mtime):
                self.st_mtime = mtime

        original_stat = Path.stat

        def patched_stat(self, *args, **kwargs):
            if self == trace_file:
                stat_call_count[0] += 1
                # First call (during init) returns original mtime,
                # subsequent calls return new mtime
                if stat_call_count[0] <= 1:
                    return _FakeStat(original_mtime)
                return _FakeStat(new_mtime)
            return original_stat(self, *args, **kwargs)

        sleep_call_count = 0

        async def fake_sleep(_dur):
            nonlocal sleep_call_count
            sleep_call_count += 1
            if sleep_call_count > 2:
                raise asyncio.CancelledError

        events = []
        with (
            patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"),
            patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
            patch("golem.core.dashboard.asyncio") as mock_asyncio,
            patch.object(Path, "stat", patched_stat),
        ):
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            try:
                async for chunk in _sse_event_stream():
                    events.append(chunk)
            except asyncio.CancelledError:
                pass

        trace_events = [e for e in events if "trace_update" in e]
        assert len(trace_events) >= 1
        parsed = json.loads(trace_events[0].split("data: ", 1)[1].strip())
        assert parsed["type"] == "trace_update"
        assert parsed["event_id"] == "golem-77-20260101"


class TestSseEventStreamOsError:
    """Verify OSError exceptions during stat() are handled gracefully."""

    @pytest.mark.asyncio
    async def test_oserror_on_sessions_file_stat_during_init(self, tmp_path):
        """OSError from the explicit sessions stat() during init is handled."""
        sessions_file = tmp_path / "sessions.json"
        sessions_file.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        original_stat = Path.stat
        # Path.exists() calls stat() internally; let the first call (from
        # exists()) succeed so it returns True, then raise on the second call
        # (the explicit _SESSIONS_FILE.stat() in the init block).
        sessions_call_count = [0]

        def patched_stat(self, *args, **kwargs):
            if self == sessions_file:
                sessions_call_count[0] += 1
                if sessions_call_count[0] == 1:
                    return original_stat(self, *args, **kwargs)
                raise OSError("simulated stat error on explicit call")
            return original_stat(self, *args, **kwargs)

        async def fake_sleep(_dur):
            raise asyncio.CancelledError

        events = []
        with (
            patch("golem.core.dashboard.TRACES_DIR", traces_dir),
            patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
            patch("golem.core.dashboard.asyncio") as mock_asyncio,
            patch.object(Path, "stat", patched_stat),
        ):
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            try:
                async for chunk in _sse_event_stream():
                    events.append(chunk)
            except asyncio.CancelledError:
                pass

        # No crash; no session_update events because mtime was never captured
        assert not any("session_update" in e for e in events)

    @pytest.mark.asyncio
    async def test_oserror_on_trace_file_stat_during_init(self, tmp_path):
        """OSError from trace file stat() during init is silently skipped."""
        traces_dir = tmp_path / "traces" / "golem"
        traces_dir.mkdir(parents=True)
        trace_file = traces_dir / "golem-err-20260101.jsonl"
        trace_file.write_text("{}", encoding="utf-8")
        sessions_file = tmp_path / "sessions.json"

        original_stat = Path.stat

        def patched_stat(self, *args, **kwargs):
            if self == trace_file:
                raise OSError("simulated stat error on trace")
            return original_stat(self, *args, **kwargs)

        async def fake_sleep(_dur):
            raise asyncio.CancelledError

        events = []
        with (
            patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"),
            patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
            patch("golem.core.dashboard.asyncio") as mock_asyncio,
            patch.object(Path, "stat", patched_stat),
        ):
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            try:
                async for chunk in _sse_event_stream():
                    events.append(chunk)
            except asyncio.CancelledError:
                pass

        # No crash; no trace_update from the file that raised OSError
        assert not any("trace_update" in e for e in events)

    @pytest.mark.asyncio
    async def test_oserror_on_sessions_file_stat_during_loop(self, tmp_path):
        """OSError from sessions file stat() during the poll loop is handled."""
        sessions_file = tmp_path / "sessions.json"
        sessions_file.write_text(json.dumps({"sessions": {}}), encoding="utf-8")
        traces_dir = tmp_path / "traces"
        traces_dir.mkdir()

        original_stat = Path.stat
        # Each generator phase calls stat() twice for sessions_file:
        # once from exists() and once explicitly.  Allow the first two
        # (init phase) to succeed; raise on the third (loop exists()) and
        # beyond so that exists() returns False in the loop, preventing the
        # explicit call.  To reach lines 490-491 we need exists() to return
        # True but the explicit stat to raise, so we use call-count parity:
        # init calls are #1 (exists) and #2 (explicit); loop calls are #3
        # (exists) and #4 (explicit).  We let call #3 succeed (exists=True)
        # and raise on call #4 (explicit stat in loop).
        sessions_call_count = [0]

        def patched_stat(self, *args, **kwargs):
            if self == sessions_file:
                sessions_call_count[0] += 1
                if sessions_call_count[0] in (1, 2, 3):
                    return original_stat(self, *args, **kwargs)
                raise OSError("simulated stat error in loop explicit call")
            return original_stat(self, *args, **kwargs)

        sleep_call_count = [0]

        async def fake_sleep(_dur):
            sleep_call_count[0] += 1
            if sleep_call_count[0] > 1:
                raise asyncio.CancelledError

        events = []
        with (
            patch("golem.core.dashboard.TRACES_DIR", traces_dir),
            patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
            patch("golem.core.dashboard.asyncio") as mock_asyncio,
            patch.object(Path, "stat", patched_stat),
        ):
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            try:
                async for chunk in _sse_event_stream():
                    events.append(chunk)
            except asyncio.CancelledError:
                pass

        # No crash; OSError during loop is caught, no session_update emitted
        assert not any("session_update" in e for e in events)

    @pytest.mark.asyncio
    async def test_oserror_on_trace_file_stat_during_loop(self, tmp_path):
        """OSError from trace file stat() during the poll loop causes skip."""
        traces_dir = tmp_path / "traces" / "golem"
        traces_dir.mkdir(parents=True)
        trace_file = traces_dir / "golem-oserr-20260101.jsonl"
        trace_file.write_text("{}", encoding="utf-8")
        sessions_file = tmp_path / "sessions.json"

        original_stat = Path.stat
        init_done = [False]

        def patched_stat(self, *args, **kwargs):
            if self == trace_file:
                if not init_done[0]:
                    # Allow the init phase stat to succeed
                    return original_stat(self, *args, **kwargs)
                # Loop phase: raise OSError
                raise OSError("simulated stat error in loop")
            return original_stat(self, *args, **kwargs)

        sleep_call_count = [0]

        async def fake_sleep(_dur):
            sleep_call_count[0] += 1
            init_done[0] = True
            if sleep_call_count[0] > 1:
                raise asyncio.CancelledError

        events = []
        with (
            patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"),
            patch("golem.core.dashboard._SESSIONS_FILE", sessions_file),
            patch("golem.core.dashboard.asyncio") as mock_asyncio,
            patch.object(Path, "stat", patched_stat),
        ):
            mock_asyncio.sleep = fake_sleep
            mock_asyncio.CancelledError = asyncio.CancelledError
            try:
                async for chunk in _sse_event_stream():
                    events.append(chunk)
            except asyncio.CancelledError:
                pass

        # No crash; OSError during loop is caught via continue, no trace_update
        assert not any("trace_update" in e for e in events)
