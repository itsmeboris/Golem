"""Daemon process helpers: PID management, log tee, daemonize."""

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("golem.core.daemon")


class TeeStream:
    """Write to both the original stream and a file simultaneously."""

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data):  # pylint: disable=missing-function-docstring
        self._original.write(data)
        self._log_file.write(data)
        self._log_file.flush()

    def flush(self):  # pylint: disable=missing-function-docstring
        self._original.flush()
        self._log_file.flush()

    def fileno(self):  # pylint: disable=missing-function-docstring
        return self._original.fileno()

    def isatty(self):  # pylint: disable=missing-function-docstring
        return self._original.isatty()


def update_latest_symlink(
    log_dir: Path, log_path: Path, link_name: Path | None = None
) -> None:
    """Point a *_latest.log symlink at the given log file."""
    latest = link_name or (log_dir / "daemon_latest.log")
    try:
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(log_path.name)
    except OSError as exc:
        logger.debug("Failed to update latest symlink: %s", exc)


def setup_daemon_tee(log_dir: Path) -> tuple:
    """Tee stdout/stderr to a timestamped daemon log file.

    Returns (log_path, cleanup_fn).  Call cleanup_fn on exit to restore streams.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"daemon_{stamp}.log"
    fh = open(log_path, "a", encoding="utf-8")  # pylint: disable=consider-using-with

    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    sys.stdout = TeeStream(orig_stdout, fh)
    sys.stderr = TeeStream(orig_stderr, fh)

    for handler in logging.root.handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(
            handler, logging.FileHandler
        ):
            handler.stream = sys.stderr

    update_latest_symlink(log_dir, log_path)

    def cleanup():
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr
        fh.close()

    return log_path, cleanup


def write_pid(pid_file: Path) -> None:
    """Write current PID to file."""
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()), encoding="utf-8")


def read_pid(pid_file: Path) -> int | None:
    """Read a PID from file, returning None if absent or invalid."""
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text(encoding="utf-8").strip())
    except (ValueError, OSError) as exc:
        logger.debug("Cannot read PID file: %s", exc)
        return None


def remove_pid(pid_file: Path) -> None:
    """Remove PID file, ignoring errors."""
    try:
        pid_file.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Failed to remove PID file: %s", exc)


def daemonize(log_path: Path) -> None:
    """Double-fork to detach from the controlling terminal."""
    if os.fork() > 0:
        sys.exit(0)

    os.setsid()

    if os.fork() > 0:
        sys.exit(0)

    sys.stdin.close()
    log_fh = open(  # pylint: disable=consider-using-with
        log_path, "a", encoding="utf-8"
    )
    os.dup2(log_fh.fileno(), sys.stdout.fileno())
    os.dup2(log_fh.fileno(), sys.stderr.fileno())
    sys.stdout = log_fh
    sys.stderr = log_fh
