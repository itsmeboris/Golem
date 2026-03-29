"""Subprocess sandboxing via OS resource limits.

Provides a preexec_fn that sets ulimit-style constraints on child processes:
- CPU time limit
- Memory (address space) limit
- File size limit
- Process count limit

These limits are inherited by all child processes of the sandboxed subprocess.
"""

import logging
import resource
from dataclasses import dataclass

logger = logging.getLogger("golem.sandbox")


@dataclass
class SandboxLimits:
    """Resource limits for sandboxed subprocesses."""

    cpu_seconds: int = 3600  # 1 hour CPU time
    memory_bytes: int = 4 * 1024**3  # 4 GB virtual memory
    file_size_bytes: int = 1 * 1024**3  # 1 GB max file size
    max_processes: int = 256  # max child processes
    nofile: int = 1024  # max open files


_DEFAULT_LIMITS = SandboxLimits()


def make_sandbox_preexec(limits: SandboxLimits | None = None):
    """Return a preexec_fn that sets resource limits on the child process.

    Usage:
        subprocess.run(
            cmd,
            preexec_fn=make_sandbox_preexec(),
        )
    """
    limits = limits or _DEFAULT_LIMITS

    def _apply_limits():
        _apply_rlimit(resource.RLIMIT_CPU, limits.cpu_seconds, limits.cpu_seconds)
        _apply_rlimit(resource.RLIMIT_AS, limits.memory_bytes, limits.memory_bytes)
        _apply_rlimit(
            resource.RLIMIT_FSIZE, limits.file_size_bytes, limits.file_size_bytes
        )
        _apply_rlimit(resource.RLIMIT_NPROC, limits.max_processes, limits.max_processes)
        _apply_rlimit(resource.RLIMIT_NOFILE, limits.nofile, limits.nofile)

    return _apply_limits


def _apply_rlimit(resource_id: int, soft: int, hard: int) -> None:
    """Set a single resource limit, logging debug on failure without raising."""
    try:
        resource.setrlimit(resource_id, (soft, hard))
    except (ValueError, OSError) as exc:
        # Log but don't fail — some limits may not be settable
        # (e.g., non-root users can't raise limits above hard limits)
        logger.debug("Could not set all sandbox limits: %s", exc)


def get_default_limits() -> SandboxLimits:
    """Return the default sandbox limits."""
    return SandboxLimits()
