"""Checkpoint persistence for crash recovery."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from golem.core.config import DATA_DIR

if TYPE_CHECKING:
    from golem.orchestrator import TaskSession

logger = logging.getLogger("golem.checkpoint")

CHECKPOINTS_DIR: Path = DATA_DIR / "state" / "checkpoints"


def save_checkpoint(issue_id: int, session: TaskSession, phase: str) -> Path:
    """Save a checkpoint for crash recovery using an atomic write.

    Args:
        issue_id: The issue ID to checkpoint.
        session: The TaskSession to persist.
        phase: The current execution phase label.

    Returns:
        The path to the written checkpoint file.
    """
    checkpoint_path = CHECKPOINTS_DIR / str(issue_id) / "checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    data: dict[str, Any] = {
        **session.to_dict(),
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
    }
    payload = json.dumps(data, indent=2).encode("utf-8")

    fd, tmp_path = tempfile.mkstemp(
        dir=str(checkpoint_path.parent), prefix=".checkpoint_", suffix=".tmp"
    )
    closed = False
    try:
        os.write(fd, payload)
        os.fsync(fd)
        os.close(fd)
        closed = True
        os.replace(tmp_path, str(checkpoint_path))
    except BaseException:
        if not closed:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info("Checkpoint saved for #%s (phase=%s)", issue_id, phase)
    return checkpoint_path


def load_checkpoint(issue_id: int) -> dict[str, Any] | None:
    """Load a checkpoint from disk.

    Args:
        issue_id: The issue ID whose checkpoint to load.

    Returns:
        The checkpoint data dict, or None if not found or corrupt.
    """
    path = CHECKPOINTS_DIR / str(issue_id) / "checkpoint.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to load checkpoint for #%s: %s", issue_id, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("Checkpoint for #%s is not a JSON object, ignoring", issue_id)
        return None

    logger.debug("Checkpoint loaded for #%s", issue_id)
    return data


def is_checkpoint_fresh(checkpoint: dict[str, Any], max_age_minutes: int = 10) -> bool:
    """Return True if the checkpoint was saved within *max_age_minutes*.

    Args:
        checkpoint: A checkpoint dict containing a ``saved_at`` ISO timestamp.
        max_age_minutes: Maximum acceptable age in minutes.

    Returns:
        True if the checkpoint age is strictly less than the threshold.
    """
    try:
        saved_at: datetime = datetime.fromisoformat(checkpoint["saved_at"])
        if saved_at.tzinfo is None:
            saved_at = saved_at.replace(tzinfo=timezone.utc)
        age_seconds: float = (datetime.now(timezone.utc) - saved_at).total_seconds()
    except (KeyError, ValueError, TypeError):
        return False
    return age_seconds < max_age_minutes * 60


def delete_checkpoint(issue_id: int) -> None:
    """Remove a checkpoint file and its parent directory.

    Args:
        issue_id: The issue ID whose checkpoint to delete.
    """
    checkpoint_path = CHECKPOINTS_DIR / str(issue_id) / "checkpoint.json"
    removed = False
    try:
        checkpoint_path.unlink()
        removed = True
    except (FileNotFoundError, OSError):
        pass

    issue_dir = CHECKPOINTS_DIR / str(issue_id)
    try:
        issue_dir.rmdir()
    except (FileNotFoundError, OSError):
        pass

    if removed:
        logger.debug("Checkpoint deleted for #%s", issue_id)
