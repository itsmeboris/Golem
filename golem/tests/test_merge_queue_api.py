"""Tests for merge queue dashboard API endpoints."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from golem.core import dashboard as dash_mod
from golem.core.dashboard import mount_dashboard


def _make_app(merge_queue=None):
    app = FastAPI()
    mount_dashboard(app, merge_queue=merge_queue)
    return app


def test_get_merge_queue_empty():
    """GET /api/merge-queue returns empty snapshot when merge_queue is None."""
    app = _make_app(merge_queue=None)
    client = TestClient(app)
    resp = client.get("/api/merge-queue")
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending"] == []
    assert data["active"] is None
    assert data["deferred"] == []
    assert data["conflicts"] == []
    assert data["history"] == []


def test_get_merge_queue_returns_snapshot():
    """GET /api/merge-queue calls snapshot() on the merge queue."""
    mq = MagicMock()
    mq.snapshot.return_value = {
        "pending": [
            {
                "session_id": 1,
                "branch_name": "agent/1",
                "worktree_path": "/tmp",
                "priority": 5,
                "group_id": "",
                "queued_at": "",
                "changed_files": [],
            }
        ],
        "active": None,
        "deferred": [],
        "conflicts": [],
        "history": [],
    }
    app = _make_app(merge_queue=mq)
    client = TestClient(app)
    resp = client.get("/api/merge-queue")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["pending"]) == 1
    assert data["pending"][0]["session_id"] == 1
    mq.snapshot.assert_called_once()


async def test_post_retry_success():
    """POST /api/merge-queue/retry/{id} re-enqueues and returns ok."""
    from golem.merge_queue import MergeEntry

    mq = MagicMock()
    entry = MergeEntry(
        session_id=42,
        branch_name="agent/42",
        worktree_path="/tmp",
        base_dir="/proj",
    )
    mq.retry = AsyncMock(return_value=entry)

    app = _make_app(merge_queue=mq)
    client = TestClient(app)
    resp = client.post("/api/merge-queue/retry/42")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["session_id"] == 42


def test_post_retry_not_found():
    """POST /api/merge-queue/retry/{id} returns 404 for unknown session."""
    mq = MagicMock()
    mq.retry = AsyncMock(side_effect=ValueError("No retryable entry"))

    app = _make_app(merge_queue=mq)
    client = TestClient(app)
    resp = client.post("/api/merge-queue/retry/999")
    assert resp.status_code == 404


def test_post_retry_offline():
    """POST /api/merge-queue/retry/{id} returns 503 when merge_queue is None."""
    app = _make_app(merge_queue=None)
    client = TestClient(app)
    resp = client.post("/api/merge-queue/retry/42")
    assert resp.status_code == 503


async def test_sse_emits_merge_queue_update(tmp_path):
    """SSE stream emits merge_queue_update when sentinel file changes.

    Also exercises the post-yield sent_event/heartbeat_counter assignments
    by pulling one more event after receiving merge_queue_update.
    """
    sentinel = tmp_path / ".merge_queue_updated"
    sentinel.touch()

    with patch.object(dash_mod, "_MERGE_QUEUE_SENTINEL", sentinel):
        gen = dash_mod._sse_event_stream()

        async def delayed_touch():
            await asyncio.sleep(0.5)
            sentinel.touch()

        touch_task = asyncio.create_task(delayed_touch())
        events = []
        found_mq = False
        async for event in gen:
            events.append(event)
            if "merge_queue_update" in event:
                found_mq = True
                # Let the generator resume past the yield to cover lines 540-541,
                # then pull one more event (either another update or heartbeat).
                continue
            if found_mq or len(events) >= 20:
                break
        touch_task.cancel()

    assert any("merge_queue_update" in e for e in events)


async def test_sse_merge_queue_sentinel_oserror_init():
    """SSE init survives OSError when reading sentinel mtime (lines 499-500)."""
    # Build a mock sentinel that reports exists()=True but stat() raises OSError.
    mock_sentinel = MagicMock(spec=Path)
    mock_sentinel.exists.return_value = True
    mock_sentinel.stat.side_effect = OSError("simulated init error")

    with patch.object(dash_mod, "_MERGE_QUEUE_SENTINEL", mock_sentinel):
        gen = dash_mod._sse_event_stream()
        # Drive the generator just past init (first sleep yields control).
        # We cancel quickly so it doesn't hang.
        events = []
        try:
            async with asyncio.timeout(2):
                async for event in gen:
                    events.append(event)
                    break
        except TimeoutError:
            pass
        await gen.aclose()

    # No crash — OSError was silently suppressed.
    assert isinstance(events, list)


async def test_sse_merge_queue_sentinel_oserror_poll():
    """SSE poll loop survives OSError when reading sentinel mtime (lines 564-565)."""
    # First call (init): stat succeeds.  Second call (poll): stat raises OSError.
    init_stat = MagicMock(st_mtime=1000.0)
    poll_count = 0

    def _stat_side_effect():
        nonlocal poll_count
        poll_count += 1
        if poll_count == 1:
            return init_stat
        raise OSError("simulated poll error")

    mock_sentinel = MagicMock(spec=Path)
    mock_sentinel.exists.return_value = True
    mock_sentinel.stat.side_effect = _stat_side_effect

    # Patch asyncio.sleep to return immediately so the poll loop iterates fast
    with (
        patch.object(dash_mod, "_MERGE_QUEUE_SENTINEL", mock_sentinel),
        patch("golem.core.dashboard.asyncio.sleep", new_callable=AsyncMock),
    ):
        gen = dash_mod._sse_event_stream()
        events = []
        try:
            async with asyncio.timeout(1):
                async for event in gen:
                    events.append(event)
                    break
        except TimeoutError:
            pass
        await gen.aclose()

    # Generator survived the OSError in the poll loop.
    assert isinstance(events, list)
