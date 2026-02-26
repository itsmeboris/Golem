# pylint: disable=too-few-public-methods
"""Tests for golem.core.live_state — in-memory task tracking with persistence."""

import json

import pytest

from golem.core.live_state import LiveState, read_live_snapshot


@pytest.fixture(autouse=True)
def _reset_singleton():
    LiveState.reset()
    yield
    LiveState.reset()


class TestLiveSingleton:
    def test_get_returns_same_instance(self):
        a = LiveState.get()
        b = LiveState.get()
        assert a is b

    def test_reset_creates_new_instance(self):
        a = LiveState.get()
        LiveState.reset()
        b = LiveState.get()
        assert a is not b


class TestLiveStateLifecycle:
    def test_enqueue_creates_active_task(self):
        ls = LiveState.get()
        ls.enqueue("evt-1", "golem", "sonnet")
        snap = ls.snapshot()
        assert snap["active_count"] == 1
        assert snap["active_tasks"][0]["event_id"] == "evt-1"
        assert snap["active_tasks"][0]["phase"] == "preparing"

    def test_mark_queued(self):
        ls = LiveState.get()
        ls.enqueue("evt-1", "golem", "sonnet")
        ls.mark_queued("evt-1")
        snap = ls.snapshot()
        assert snap["active_tasks"][0]["phase"] == "queued"
        assert snap["queue_depth"] == 1

    def test_dequeue_start(self):
        ls = LiveState.get()
        ls.enqueue("evt-1", "golem", "sonnet")
        ls.mark_queued("evt-1")
        ls.dequeue_start("evt-1")
        snap = ls.snapshot()
        assert snap["active_tasks"][0]["phase"] == "running"
        assert snap["queue_depth"] == 0

    def test_update_phase(self):
        ls = LiveState.get()
        ls.enqueue("evt-1", "golem", "sonnet")
        ls.update_phase("evt-1", "validating")
        snap = ls.snapshot()
        assert snap["active_tasks"][0]["phase"] == "validating"

    def test_finish(self):
        ls = LiveState.get()
        ls.enqueue("evt-1", "golem", "sonnet")
        ls.dequeue_start("evt-1")
        ls.finish("evt-1", success=True, cost_usd=0.50)
        snap = ls.snapshot()
        assert snap["active_count"] == 0
        assert len(snap["recently_completed"]) == 1
        assert snap["recently_completed"][0]["success"] is True

    def test_finish_nonexistent(self):
        ls = LiveState.get()
        ls.finish("ghost", success=False)
        snap = ls.snapshot()
        assert snap["active_count"] == 0
        assert len(snap["recently_completed"]) == 0


class TestLiveStateDrain:
    def test_drain_moves_all_to_completed(self):
        ls = LiveState.get()
        for i in range(3):
            ls.enqueue(f"evt-{i}", "golem", "sonnet")
        count = ls.drain()
        assert count == 3
        snap = ls.snapshot()
        assert snap["active_count"] == 0
        assert len(snap["recently_completed"]) == 3


class TestLiveStateSnapshot:
    def test_models_active_only_counts_running(self):
        ls = LiveState.get()
        ls.enqueue("e1", "golem", "sonnet")
        ls.enqueue("e2", "golem", "opus")
        ls.dequeue_start("e1")
        snap = ls.snapshot()
        assert snap["models_active"].get("sonnet") == 1
        assert "opus" not in snap["models_active"]

    def test_recently_completed_capped(self):
        ls = LiveState.get()
        ls._max_recent = 5
        for i in range(10):
            ls.enqueue(f"e{i}", "golem", "sonnet")
            ls.finish(f"e{i}", success=True)
        snap = ls.snapshot()
        assert len(snap["recently_completed"]) <= 5

    def test_uptime_positive(self):
        ls = LiveState.get()
        snap = ls.snapshot()
        assert snap["uptime_s"] >= 0


class TestLiveStatePersistence:
    def test_persistence_writes_file(self, tmp_path):
        ls = LiveState.get()
        state_file = tmp_path / "live.json"
        ls.enable_persistence(state_file)

        ls.enqueue("evt-1", "golem", "sonnet")
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["active_count"] == 1

    def test_clear_persistence(self, tmp_path):
        ls = LiveState.get()
        state_file = tmp_path / "live.json"
        ls.enable_persistence(state_file)
        ls.enqueue("evt-1", "golem", "sonnet")
        assert state_file.exists()
        ls.clear_persistence()
        assert not state_file.exists()


class TestReadLiveSnapshot:
    def test_reads_valid_file(self, tmp_path):
        state_file = tmp_path / "live.json"
        data = {"uptime_s": 100, "active_tasks": [], "active_count": 0}
        state_file.write_text(json.dumps(data))
        snap = read_live_snapshot(state_file)
        assert snap["uptime_s"] == 100

    def test_missing_file_returns_defaults(self, tmp_path):
        snap = read_live_snapshot(tmp_path / "nope.json")
        assert snap["uptime_s"] == 0
        assert snap["active_tasks"] == []

    def test_corrupt_file_returns_defaults(self, tmp_path):
        state_file = tmp_path / "bad.json"
        state_file.write_text("not json")
        snap = read_live_snapshot(state_file)
        assert snap["uptime_s"] == 0

    def test_default_path_used_when_none(self):
        snap = read_live_snapshot(None)
        assert "uptime_s" in snap


class TestLiveStatePersistOSError:
    def test_persist_swallows_oserror(self, tmp_path):
        from unittest.mock import patch

        ls = LiveState.get()
        ls._persist_path = tmp_path / "sub" / "live.json"
        with patch(
            "golem.core.live_state.tempfile.mkstemp", side_effect=OSError("disk full")
        ):
            ls._persist()

    def test_clear_persistence_swallows_oserror(self, tmp_path):
        from unittest.mock import patch

        ls = LiveState.get()
        state_file = tmp_path / "live.json"
        state_file.write_text("{}")
        ls._persist_path = state_file
        with patch("pathlib.Path.unlink", side_effect=OSError("perm")):
            ls.clear_persistence()
        assert state_file.exists()


class TestLiveStateDrainTrimming:
    def test_drain_trims_recent_over_max(self):
        ls = LiveState.get()
        ls._max_recent = 3
        for i in range(5):
            ls.enqueue(f"e{i}", "golem", "sonnet")
            ls.finish(f"e{i}", success=True)
        for i in range(5, 10):
            ls.enqueue(f"e{i}", "golem", "sonnet")
        count = ls.drain()
        assert count == 5
        assert len(ls._recent) <= 3
