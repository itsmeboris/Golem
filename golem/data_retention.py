"""Data retention cleanup for traces and checkpoints.

Removes old trace and checkpoint files so that data/traces/ and
data/checkpoints/ do not grow unbounded over time.
"""

import logging
import time
from pathlib import Path

logger = logging.getLogger("golem.data_retention")


def cleanup_old_data(base_dir: str, max_age_days: int = 30) -> dict[str, int]:
    """Remove traces and checkpoints older than *max_age_days*.

    Scans ``<base_dir>/data/traces/`` for ``*.jsonl`` and ``*.prompt.txt``
    files, and ``<base_dir>/data/checkpoints/`` for ``*.json`` files, then
    deletes any whose modification time is older than the cutoff.

    Parameters
    ----------
    base_dir:
        Root directory that contains the ``data/`` tree.
    max_age_days:
        Files older than this many days are deleted.  Defaults to 30.

    Returns
    -------
    dict[str, int]
        ``{"traces": N, "checkpoints": M}`` with counts of deleted files.
    """
    cutoff = time.time() - (max_age_days * 86400)
    counts: dict[str, int] = {"traces": 0, "checkpoints": 0}

    traces_dir = Path(base_dir) / "data" / "traces"
    if traces_dir.exists():
        for f in traces_dir.rglob("*.jsonl"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                counts["traces"] += 1
        for f in traces_dir.rglob("*.prompt.txt"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                counts["traces"] += 1

    checkpoints_dir = Path(base_dir) / "data" / "checkpoints"
    if checkpoints_dir.exists():
        for f in checkpoints_dir.rglob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                counts["checkpoints"] += 1

    if counts["traces"] or counts["checkpoints"]:
        logger.info(
            "Data retention cleanup: removed %d trace(s) and %d checkpoint(s)"
            " older than %d days",
            counts["traces"],
            counts["checkpoints"],
            max_age_days,
        )

    return counts
