"""Tests for golem.batch_monitor — batch lifecycle tracking."""

from types import SimpleNamespace

import pytest

from golem.batch_monitor import BatchMonitor, BatchState


def _make_session(
    state="completed",
    total_cost_usd=1.0,
    duration_seconds=60.0,
    validation_verdict="PASS",
    group_id="grp",
):
    """Build a lightweight mock session using SimpleNamespace."""
    return SimpleNamespace(
        state=SimpleNamespace(value=state),
        total_cost_usd=total_cost_usd,
        duration_seconds=duration_seconds,
        validation_verdict=validation_verdict,
        group_id=group_id,
    )


# ---------------------------------------------------------------------------
# BatchState dataclass
# ---------------------------------------------------------------------------


class TestBatchState:
    def test_batch_state_to_dict_round_trip(self):
        """to_dict -> from_dict produces an equivalent BatchState."""
        original = BatchState(
            group_id="g1",
            task_ids=[10, 20, 30],
            status="completed",
            created_at="2025-06-01T00:00:00+00:00",
            completed_at="2025-06-01T01:00:00+00:00",
            total_cost_usd=4.25,
            total_duration_s=300.0,
            task_results={"10": {"state": "completed"}, "20": {"state": "failed"}},
            validation_verdict="PARTIAL",
        )
        restored = BatchState.from_dict(original.to_dict())
        assert restored.group_id == original.group_id
        assert restored.task_ids == original.task_ids
        assert restored.status == original.status
        assert restored.created_at == original.created_at
        assert restored.completed_at == original.completed_at
        assert restored.total_cost_usd == original.total_cost_usd
        assert restored.total_duration_s == original.total_duration_s
        assert restored.task_results == original.task_results
        assert restored.validation_verdict == original.validation_verdict

    def test_batch_state_defaults(self):
        """Default field values are set correctly."""
        state = BatchState(group_id="g0")
        assert state.group_id == "g0"
        assert not state.task_ids
        assert state.status == "submitted"
        assert state.created_at == ""
        assert state.completed_at == ""
        assert state.total_cost_usd == 0.0
        assert state.total_duration_s == 0.0
        assert not state.task_results
        assert state.validation_verdict == ""


# ---------------------------------------------------------------------------
# BatchMonitor — register / get
# ---------------------------------------------------------------------------


class TestBatchMonitorBasic:
    def test_register_creates_batch(self):
        """register() returns a BatchState with correct fields and status."""
        mon = BatchMonitor()
        batch = mon.register("grp-1", [1, 2, 3])
        assert batch.group_id == "grp-1"
        assert batch.task_ids == [1, 2, 3]
        assert batch.status == "submitted"
        assert batch.created_at != ""

    def test_register_duplicate_overwrites(self):
        """Registering the same group_id again replaces the previous batch."""
        mon = BatchMonitor()
        first = mon.register("grp-1", [1])
        second = mon.register("grp-1", [10, 20])
        assert second.task_ids == [10, 20]
        assert mon.get("grp-1") is second
        assert mon.get("grp-1") is not first

    def test_get_returns_none_for_unknown(self):
        """get() returns None for an unregistered group_id."""
        mon = BatchMonitor()
        assert mon.get("nonexistent") is None

    def test_get_returns_registered_batch(self):
        """get() returns the batch created by register()."""
        mon = BatchMonitor()
        mon.register("grp-1", [5, 6])
        fetched = mon.get("grp-1")
        assert fetched is not None
        assert fetched.group_id == "grp-1"
        assert fetched.task_ids == [5, 6]


# ---------------------------------------------------------------------------
# BatchMonitor — update logic
# ---------------------------------------------------------------------------


class TestBatchMonitorUpdate:
    def test_update_all_completed(self):
        """All tasks completed sets status='completed', verdict='PASS', completed_at."""
        mon = BatchMonitor()
        mon.register("grp", [1, 2])
        sessions = {
            1: _make_session(state="completed", validation_verdict="PASS"),
            2: _make_session(state="completed", validation_verdict="PASS"),
        }
        batch = mon.update("grp", sessions)
        assert batch.status == "completed"
        assert batch.validation_verdict == "PASS"
        assert batch.completed_at != ""

    def test_update_some_running(self):
        """Mix of running and completed keeps status='running'."""
        mon = BatchMonitor()
        mon.register("grp", [1, 2])
        sessions = {
            1: _make_session(state="completed"),
            2: _make_session(state="running"),
        }
        batch = mon.update("grp", sessions)
        assert batch.status == "running"

    def test_update_some_failed(self):
        """One failed, rest completed sets status='failed', verdict='FAIL'."""
        mon = BatchMonitor()
        mon.register("grp", [1, 2])
        sessions = {
            1: _make_session(state="completed", validation_verdict="PASS"),
            2: _make_session(state="failed", validation_verdict="FAIL"),
        }
        batch = mon.update("grp", sessions)
        assert batch.status == "failed"
        assert batch.validation_verdict == "FAIL"

    def test_update_mixed_verdicts(self):
        """Some PASS, some FAIL sets verdict='FAIL'; PASS + PARTIAL = 'PARTIAL'."""
        mon = BatchMonitor()
        mon.register("grp", [1, 2])
        sessions = {
            1: _make_session(state="completed", validation_verdict="PASS"),
            2: _make_session(state="completed", validation_verdict="PARTIAL"),
        }
        batch = mon.update("grp", sessions)
        assert batch.validation_verdict == "PARTIAL"

    def test_update_aggregates_costs(self):
        """total_cost_usd and total_duration_s are sums of task values."""
        mon = BatchMonitor()
        mon.register("grp", [1, 2, 3])
        sessions = {
            1: _make_session(total_cost_usd=2.5, duration_seconds=100.0),
            2: _make_session(total_cost_usd=3.0, duration_seconds=200.0),
            3: _make_session(total_cost_usd=0.5, duration_seconds=50.0),
        }
        batch = mon.update("grp", sessions)
        assert batch.total_cost_usd == pytest.approx(6.0)
        assert batch.total_duration_s == pytest.approx(350.0)

    def test_update_unknown_group_raises(self):
        """update() for an unregistered group raises KeyError."""
        mon = BatchMonitor()
        with pytest.raises(KeyError):
            mon.update("missing", {})


# ---------------------------------------------------------------------------
# Persistence — save / load
# ---------------------------------------------------------------------------


class TestBatchMonitorPersistence:
    def test_save_and_load_round_trip(self, tmp_path):
        """Batches survive a save/load cycle through a new monitor."""
        mon = BatchMonitor()
        mon.register("alpha", [1, 2])
        mon.register("beta", [3, 4, 5])

        save_path = tmp_path / "batches.json"
        mon.save(save_path)

        mon2 = BatchMonitor()
        mon2.load(save_path)

        alpha = mon2.get("alpha")
        beta = mon2.get("beta")
        assert alpha is not None
        assert alpha.group_id == "alpha"
        assert alpha.task_ids == [1, 2]
        assert beta is not None
        assert beta.group_id == "beta"
        assert beta.task_ids == [3, 4, 5]

    def test_load_missing_file(self, tmp_path):
        """Loading from a nonexistent path leaves state empty (no crash)."""
        mon = BatchMonitor()
        mon.load(tmp_path / "does_not_exist.json")
        assert mon.list_batches() == []


# ---------------------------------------------------------------------------
# list_batches
# ---------------------------------------------------------------------------


class TestBatchMonitorUpdateEdgeCases:
    def test_update_skips_missing_session(self):
        """Tasks not in sessions dict are skipped (continue branch)."""
        mon = BatchMonitor()
        mon.register("grp", [1, 2])
        sessions = {
            1: _make_session(state="completed"),
            # task 2 is missing from sessions
        }
        batch = mon.update("grp", sessions)
        # Only 1 completed out of 2 total, no in-flight, no failed → submitted
        assert batch.status == "submitted"

    def test_update_no_verdicts_sets_empty(self):
        """When no tasks have verdicts, validation_verdict is empty."""
        mon = BatchMonitor()
        mon.register("grp", [1])
        sessions = {
            1: _make_session(state="completed", validation_verdict=""),
        }
        batch = mon.update("grp", sessions)
        assert batch.validation_verdict == ""

    def test_save_error_after_close_cleans_up_temp_file(self, tmp_path, monkeypatch):
        """save() cleans up temp file if os.replace fails after fd is closed."""
        mon = BatchMonitor()
        mon.register("grp", [1])

        save_path = tmp_path / "batches.json"

        def fail_replace(_src, _dst):
            raise OSError("disk full")

        monkeypatch.setattr("os.replace", fail_replace)
        with pytest.raises(OSError, match="disk full"):
            mon.save(save_path)

        # Temp file should be cleaned up
        import glob

        leftover = glob.glob(str(tmp_path / ".batches_*.tmp"))
        assert not leftover

    def test_save_error_before_close_cleans_up(self, tmp_path, monkeypatch):
        """save() closes fd and cleans temp file if os.write/fsync fails."""
        mon = BatchMonitor()
        mon.register("grp", [1])

        save_path = tmp_path / "batches.json"

        def fail_write(_fd, _data):
            raise OSError("write failed")

        monkeypatch.setattr("os.write", fail_write)
        with pytest.raises(OSError, match="write failed"):
            mon.save(save_path)

    def test_save_error_unlink_fails_silently(self, tmp_path, monkeypatch):
        """save() swallows OSError from os.unlink in cleanup."""
        mon = BatchMonitor()
        mon.register("grp", [1])

        save_path = tmp_path / "batches.json"

        def fail_replace(_src, _dst):
            raise OSError("disk full")

        def fail_unlink(_path):
            raise OSError("unlink failed")

        monkeypatch.setattr("os.replace", fail_replace)
        monkeypatch.setattr("os.unlink", fail_unlink)
        with pytest.raises(OSError, match="disk full"):
            mon.save(save_path)

    def test_save_error_unlink_fails_logs_debug(self, tmp_path, monkeypatch, caplog):
        """save() logs at debug level when os.unlink fails during cleanup."""
        import logging

        mon = BatchMonitor()
        mon.register("grp", [1])

        save_path = tmp_path / "batches.json"

        def fail_replace(_src, _dst):
            raise OSError("disk full")

        def fail_unlink(_path):
            raise OSError("unlink failed")

        monkeypatch.setattr("os.replace", fail_replace)
        monkeypatch.setattr("os.unlink", fail_unlink)
        with caplog.at_level(logging.DEBUG, logger="golem.batch_monitor"):
            with pytest.raises(OSError, match="disk full"):
                mon.save(save_path)

        assert any(
            "Failed to clean up temp file" in r.message and r.levelno == logging.DEBUG
            for r in caplog.records
        )


class TestBatchMonitorLoadCorrupt:
    """REL-008: load() must handle corrupt JSON without crashing or clearing state."""

    def test_load_corrupt_json_does_not_crash(self, tmp_path):
        """load() on corrupt JSON logs an error and returns without crashing."""
        corrupt_file = tmp_path / "batches.json"
        corrupt_file.write_text("{invalid json!!!", encoding="utf-8")

        mon = BatchMonitor()
        mon.load(corrupt_file)  # must not raise

    def test_load_corrupt_json_preserves_existing_state(self, tmp_path):
        """load() on corrupt JSON leaves pre-existing in-memory state intact."""
        corrupt_file = tmp_path / "batches.json"
        corrupt_file.write_text("{invalid json!!!", encoding="utf-8")

        mon = BatchMonitor()
        mon.register("pre-existing", [1, 2, 3])

        mon.load(corrupt_file)

        batch = mon.get("pre-existing")
        assert batch is not None
        assert batch.task_ids == [1, 2, 3]

    def test_load_corrupt_json_logged_as_error(self, tmp_path, caplog):
        """load() logs a descriptive error message when JSON is corrupt."""
        import logging

        corrupt_file = tmp_path / "batches.json"
        corrupt_file.write_text("NOTJSON", encoding="utf-8")

        mon = BatchMonitor()
        with caplog.at_level(logging.ERROR, logger="golem.batch_monitor"):
            mon.load(corrupt_file)

        assert any(
            "Corrupt" in r.message and r.levelno == logging.ERROR
            for r in caplog.records
        )


class TestBatchMonitorList:
    def test_list_batches_sorted_by_created_at(self):
        """list_batches() returns batches sorted by created_at descending."""
        mon = BatchMonitor()

        # Manually insert with controlled timestamps to avoid timing issues.
        mon._batches["old"] = BatchState(
            group_id="old",
            task_ids=[1],
            created_at="2025-01-01T00:00:00+00:00",
        )
        mon._batches["mid"] = BatchState(
            group_id="mid",
            task_ids=[2],
            created_at="2025-06-01T00:00:00+00:00",
        )
        mon._batches["new"] = BatchState(
            group_id="new",
            task_ids=[3],
            created_at="2025-12-01T00:00:00+00:00",
        )

        batches = mon.list_batches()
        ids = [b.group_id for b in batches]
        assert ids == ["new", "mid", "old"]


# ---------------------------------------------------------------------------
# REL-007: serialize() method
# ---------------------------------------------------------------------------


class TestBatchMonitorSerialize:
    """Tests for BatchMonitor.serialize() — used for two-phase atomic saves."""

    def test_serialize_returns_bytes(self):
        """serialize() returns bytes (JSON-encoded payload)."""
        mon = BatchMonitor()
        payload = mon.serialize()
        assert isinstance(payload, bytes)

    def test_serialize_empty_monitor_has_batches_key(self):
        """Empty monitor serializes with a 'batches' key."""
        mon = BatchMonitor()
        import json

        data = json.loads(mon.serialize().decode("utf-8"))
        assert "batches" in data
        assert data["batches"] == {}

    def test_serialize_includes_registered_batch(self):
        """Registered batch appears in serialized output."""
        import json

        mon = BatchMonitor()
        mon.register("grp-xyz", [11, 22])
        data = json.loads(mon.serialize().decode("utf-8"))
        assert "grp-xyz" in data["batches"]
        assert data["batches"]["grp-xyz"]["group_id"] == "grp-xyz"

    def test_serialize_round_trip_via_load(self, tmp_path):
        """serialize() output can be written to disk and loaded back."""
        mon = BatchMonitor()
        mon.register("grp-rt", [55])

        payload = mon.serialize()
        target = tmp_path / "batches.json"
        target.write_bytes(payload)

        mon2 = BatchMonitor()
        mon2.load(target)
        assert mon2.get("grp-rt") is not None
        assert mon2.get("grp-rt").group_id == "grp-rt"
