# pylint: disable=too-few-public-methods,consider-using-with
"""Tests for golem.core.daemon_utils."""
import io
import logging
import os
import sys
from unittest.mock import patch

import pytest

from golem.core.daemon_utils import (
    TeeStream,
    read_pid,
    remove_pid,
    setup_daemon_tee,
    update_latest_symlink,
    write_pid,
)


class TestTeeStream:
    def test_write(self):
        original = io.StringIO()
        log = io.StringIO()
        tee = TeeStream(original, log)
        tee.write("hello")
        assert original.getvalue() == "hello"
        assert log.getvalue() == "hello"

    def test_flush(self):
        original = io.StringIO()
        log = io.StringIO()
        tee = TeeStream(original, log)
        tee.write("data")
        tee.flush()

    def test_fileno(self, tmp_path):
        real_file = open(tmp_path / "orig.txt", "w", encoding="utf-8")
        log = io.StringIO()
        tee = TeeStream(real_file, log)
        assert tee.fileno() == real_file.fileno()
        real_file.close()

    def test_isatty(self):
        original = io.StringIO()
        log = io.StringIO()
        tee = TeeStream(original, log)
        assert tee.isatty() is False


class TestWriteAndReadPid:
    def test_write_read_cycle(self, tmp_path):
        pid_file = tmp_path / "sub" / "test.pid"
        write_pid(pid_file)
        pid = read_pid(pid_file)
        assert pid == os.getpid()

    def test_read_nonexistent(self, tmp_path):
        assert read_pid(tmp_path / "nope.pid") is None

    def test_read_invalid(self, tmp_path):
        pid_file = tmp_path / "bad.pid"
        pid_file.write_text("not a number")
        assert read_pid(pid_file) is None


class TestRemovePid:
    def test_removes_file(self, tmp_path):
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("12345")
        remove_pid(pid_file)
        assert not pid_file.exists()

    def test_removes_nonexistent(self, tmp_path):
        remove_pid(tmp_path / "nope.pid")


class TestUpdateLatestSymlink:
    def test_creates_symlink(self, tmp_path):
        log_path = tmp_path / "daemon_20260226.log"
        log_path.touch()
        update_latest_symlink(tmp_path, log_path)
        link = tmp_path / "daemon_latest.log"
        assert link.is_symlink()
        assert link.resolve().name == "daemon_20260226.log"

    def test_replaces_existing(self, tmp_path):
        old_log = tmp_path / "old.log"
        old_log.touch()
        new_log = tmp_path / "new.log"
        new_log.touch()

        update_latest_symlink(tmp_path, old_log)
        update_latest_symlink(tmp_path, new_log)

        link = tmp_path / "daemon_latest.log"
        assert link.resolve().name == "new.log"

    def test_custom_link_name(self, tmp_path):
        log_path = tmp_path / "my.log"
        log_path.touch()
        custom_link = tmp_path / "custom_latest.log"
        update_latest_symlink(tmp_path, log_path, link_name=custom_link)
        assert custom_link.is_symlink()


class TestRemovePidOSError:
    def test_oserror_suppressed(self, tmp_path):
        pid_file = tmp_path / "test.pid"
        pid_file.write_text("12345")
        with patch.object(type(pid_file), "unlink", side_effect=OSError("perm")):
            remove_pid(pid_file)


class TestUpdateLatestSymlinkOSError:
    def test_oserror_suppressed(self, tmp_path):
        log_path = tmp_path / "daemon.log"
        log_path.touch()
        with patch("pathlib.Path.symlink_to", side_effect=OSError("read-only")):
            update_latest_symlink(tmp_path, log_path)


class TestSetupDaemonTee:
    def test_creates_log_and_tees(self, tmp_path):
        saved_stdout = sys.stdout
        saved_stderr = sys.stderr
        saved_handlers = logging.root.handlers[:]
        fake_stdout = open(tmp_path / "stdout.txt", "w", encoding="utf-8")
        fake_stderr = open(tmp_path / "stderr.txt", "w", encoding="utf-8")
        sys.stdout = fake_stdout
        sys.stderr = fake_stderr
        test_handler = logging.StreamHandler(fake_stderr)
        logging.root.handlers = [test_handler]
        try:
            log_path, cleanup = setup_daemon_tee(tmp_path / "logs")
            assert log_path.exists()
            assert isinstance(sys.stdout, TeeStream)
            assert isinstance(sys.stderr, TeeStream)
            assert isinstance(test_handler.stream, TeeStream)
            sys.stdout.write("hello tee\n")
            cleanup()
            assert not isinstance(sys.stdout, TeeStream)
        finally:
            sys.stdout = saved_stdout
            sys.stderr = saved_stderr
            logging.root.handlers = saved_handlers
            fake_stdout.close()
            fake_stderr.close()


class TestDaemonize:
    def test_first_fork_parent_exits(self, tmp_path):
        from golem.core.daemon_utils import daemonize

        log_path = tmp_path / "daemon.log"
        log_path.touch()

        with patch("os.fork", return_value=1) as mock_fork:
            with pytest.raises(SystemExit):
                daemonize(log_path)
            mock_fork.assert_called_once()

    def test_child_process_flow(self, tmp_path):
        from golem.core.daemon_utils import daemonize

        log_path = tmp_path / "daemon.log"
        log_path.touch()

        fork_calls = [0]

        def mock_fork():
            fork_calls[0] += 1
            if fork_calls[0] == 1:
                return 0
            return 1

        with patch("os.fork", side_effect=mock_fork), patch("os.setsid"):
            with pytest.raises(SystemExit):
                daemonize(log_path)

    def test_full_child_path(self, tmp_path):
        from golem.core.daemon_utils import daemonize

        log_path = tmp_path / "daemon.log"
        log_path.touch()
        (tmp_path / "fake_in.txt").touch()

        fake_stdout = open(tmp_path / "fake_out.txt", "w", encoding="utf-8")
        fake_stderr = open(tmp_path / "fake_err.txt", "w", encoding="utf-8")
        fake_stdin = open(tmp_path / "fake_in.txt", "r", encoding="utf-8")

        with patch("os.fork", return_value=0), patch("os.setsid"), patch(
            "os.dup2"
        ), patch.object(sys, "stdout", fake_stdout), patch.object(
            sys, "stderr", fake_stderr
        ), patch.object(
            sys, "stdin", fake_stdin
        ):
            daemonize(log_path)

        fake_stdout.close()
        fake_stderr.close()
        fake_stdin.close()
