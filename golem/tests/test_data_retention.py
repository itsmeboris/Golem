"""Tests for golem.data_retention."""

import time

from golem.data_retention import cleanup_old_data


class TestCleanupOldDataNoDirectories:
    def test_returns_zeros_when_dirs_absent(self, tmp_path):
        """Returns zero counts when trace/checkpoint dirs don't exist."""
        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts == {"traces": 0, "checkpoints": 0}


class TestCleanupOldDataTraces:
    def test_removes_old_jsonl(self, tmp_path):
        """Deletes .jsonl trace files older than max_age_days."""
        traces_dir = tmp_path / "data" / "traces"
        traces_dir.mkdir(parents=True)
        old_file = traces_dir / "session.jsonl"
        old_file.write_text("trace data")
        # Set mtime to 31 days ago
        old_mtime = time.time() - (31 * 86400)
        import os

        os.utime(str(old_file), (old_mtime, old_mtime))

        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts["traces"] == 1
        assert not old_file.exists()

    def test_keeps_recent_jsonl(self, tmp_path):
        """Keeps .jsonl trace files newer than max_age_days."""
        traces_dir = tmp_path / "data" / "traces"
        traces_dir.mkdir(parents=True)
        recent_file = traces_dir / "recent.jsonl"
        recent_file.write_text("recent data")
        # File mtime is essentially now — well within any reasonable age limit

        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts["traces"] == 0
        assert recent_file.exists()

    def test_removes_old_prompt_txt(self, tmp_path):
        """Deletes .prompt.txt trace files older than max_age_days."""
        traces_dir = tmp_path / "data" / "traces"
        traces_dir.mkdir(parents=True)
        old_file = traces_dir / "task.prompt.txt"
        old_file.write_text("prompt content")
        old_mtime = time.time() - (40 * 86400)
        import os

        os.utime(str(old_file), (old_mtime, old_mtime))

        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts["traces"] == 1
        assert not old_file.exists()

    def test_removes_old_files_in_subdirs(self, tmp_path):
        """Recursively removes old trace files in subdirectories."""
        subdir = tmp_path / "data" / "traces" / "2024" / "01"
        subdir.mkdir(parents=True)
        old_file = subdir / "deep.jsonl"
        old_file.write_text("deep trace")
        old_mtime = time.time() - (35 * 86400)
        import os

        os.utime(str(old_file), (old_mtime, old_mtime))

        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts["traces"] == 1
        assert not old_file.exists()


class TestCleanupOldDataCheckpoints:
    def test_removes_old_checkpoint_json(self, tmp_path):
        """Deletes .json checkpoint files older than max_age_days."""
        checkpoints_dir = tmp_path / "data" / "checkpoints"
        checkpoints_dir.mkdir(parents=True)
        old_file = checkpoints_dir / "task-42.json"
        old_file.write_text('{"state": "completed"}')
        old_mtime = time.time() - (31 * 86400)
        import os

        os.utime(str(old_file), (old_mtime, old_mtime))

        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts["checkpoints"] == 1
        assert not old_file.exists()

    def test_keeps_recent_checkpoint(self, tmp_path):
        """Keeps .json checkpoint files newer than max_age_days."""
        checkpoints_dir = tmp_path / "data" / "checkpoints"
        checkpoints_dir.mkdir(parents=True)
        recent_file = checkpoints_dir / "recent.json"
        recent_file.write_text("{}")

        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts["checkpoints"] == 0
        assert recent_file.exists()


class TestCleanupOldDataCombined:
    def test_removes_both_old_traces_and_checkpoints(self, tmp_path):
        """Removes old files from both directories and returns combined counts."""
        import os

        old_mtime = time.time() - (32 * 86400)

        traces_dir = tmp_path / "data" / "traces"
        traces_dir.mkdir(parents=True)
        t1 = traces_dir / "a.jsonl"
        t1.write_text("t1")
        os.utime(str(t1), (old_mtime, old_mtime))
        t2 = traces_dir / "b.prompt.txt"
        t2.write_text("t2")
        os.utime(str(t2), (old_mtime, old_mtime))

        ck_dir = tmp_path / "data" / "checkpoints"
        ck_dir.mkdir(parents=True)
        c1 = ck_dir / "task.json"
        c1.write_text("{}")
        os.utime(str(c1), (old_mtime, old_mtime))

        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts["traces"] == 2
        assert counts["checkpoints"] == 1
        assert not t1.exists()
        assert not t2.exists()
        assert not c1.exists()

    def test_mixed_old_and_recent_files(self, tmp_path):
        """Only old files are removed; recent files are kept."""
        import os

        old_mtime = time.time() - (60 * 86400)

        traces_dir = tmp_path / "data" / "traces"
        traces_dir.mkdir(parents=True)
        old_file = traces_dir / "old.jsonl"
        old_file.write_text("old")
        os.utime(str(old_file), (old_mtime, old_mtime))
        recent_file = traces_dir / "recent.jsonl"
        recent_file.write_text("recent")

        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts["traces"] == 1
        assert not old_file.exists()
        assert recent_file.exists()

    def test_returns_zero_counts_when_no_old_files(self, tmp_path):
        """Returns zeros when all files are within the retention window."""
        traces_dir = tmp_path / "data" / "traces"
        traces_dir.mkdir(parents=True)
        (traces_dir / "fresh.jsonl").write_text("fresh")

        ck_dir = tmp_path / "data" / "checkpoints"
        ck_dir.mkdir(parents=True)
        (ck_dir / "fresh.json").write_text("{}")

        counts = cleanup_old_data(str(tmp_path), max_age_days=30)
        assert counts == {"traces": 0, "checkpoints": 0}


class TestCleanupOldDataLogging:
    def test_logs_when_files_deleted(self, tmp_path, caplog):
        """Logs a summary message when files are deleted."""
        import logging
        import os

        old_mtime = time.time() - (31 * 86400)
        traces_dir = tmp_path / "data" / "traces"
        traces_dir.mkdir(parents=True)
        old_file = traces_dir / "x.jsonl"
        old_file.write_text("x")
        os.utime(str(old_file), (old_mtime, old_mtime))

        with caplog.at_level(logging.INFO, logger="golem.data_retention"):
            cleanup_old_data(str(tmp_path), max_age_days=30)

        assert any("Data retention cleanup" in r.message for r in caplog.records)

    def test_no_log_when_nothing_deleted(self, tmp_path, caplog):
        """Does not log when no files are deleted."""
        import logging

        traces_dir = tmp_path / "data" / "traces"
        traces_dir.mkdir(parents=True)
        (traces_dir / "fresh.jsonl").write_text("fresh")

        with caplog.at_level(logging.INFO, logger="golem.data_retention"):
            cleanup_old_data(str(tmp_path), max_age_days=30)

        assert not any("Data retention cleanup" in r.message for r in caplog.records)
