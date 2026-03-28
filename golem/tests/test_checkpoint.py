"""Tests for golem.checkpoint — checkpoint save / load / staleness."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

import golem.checkpoint as checkpoint_mod
from golem.checkpoint import (
    delete_checkpoint,
    is_checkpoint_fresh,
    load_checkpoint,
    save_checkpoint,
)


def _make_session(issue_id: int = 42) -> MagicMock:
    """Return a mock TaskSession with a minimal to_dict() payload."""
    session = MagicMock()
    session.to_dict.return_value = {
        "parent_issue_id": issue_id,
        "state": "executing",
        "total_cost_usd": 1.5,
    }
    return session


# ---------------------------------------------------------------------------
# save / load round-trip
# ---------------------------------------------------------------------------


def test_save_load_round_trip() -> None:
    session = _make_session(issue_id=42)
    path = save_checkpoint(42, session, phase="executing")

    assert path.exists()

    data = load_checkpoint(42)
    assert data is not None
    assert data["parent_issue_id"] == 42
    assert data["state"] == "executing"
    assert data["total_cost_usd"] == 1.5
    assert data["phase"] == "executing"
    assert "saved_at" in data
    # Verify saved_at is a valid ISO timestamp
    dt = datetime.fromisoformat(data["saved_at"])
    assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# load — missing / corrupt
# ---------------------------------------------------------------------------


def test_load_no_checkpoint_returns_none() -> None:
    result = load_checkpoint(9999)
    assert result is None


def test_load_corrupt_json_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    # Manually write garbage to the checkpoint file — use the live (monkeypatched) dir
    checkpoint_path = checkpoint_mod.CHECKPOINTS_DIR / "7" / "checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text("not valid json {{{", encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="golem.checkpoint"):
        result = load_checkpoint(7)

    assert result is None
    assert any("is corrupt" in r.message for r in caplog.records)


def test_load_non_dict_json_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    """Valid JSON that is not a dict (e.g. list, null) should be rejected."""
    checkpoint_path = checkpoint_mod.CHECKPOINTS_DIR / "8" / "checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text("[1, 2, 3]", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="golem.checkpoint"):
        result = load_checkpoint(8)

    assert result is None
    assert any("not a JSON object" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# corruption resilience — ERROR logging + backup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "issue_id, corrupt_content",
    [
        (300, "not valid json {{{"),
        (301, "[1, 2, 3]"),
    ],
    ids=["invalid_json", "non_dict_json"],
)
def test_corrupt_json_logged_at_error(
    issue_id: int, corrupt_content: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Corrupt and non-dict checkpoints must be logged at ERROR level, not WARNING."""
    checkpoint_path = checkpoint_mod.CHECKPOINTS_DIR / str(issue_id) / "checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(corrupt_content, encoding="utf-8")

    with caplog.at_level(logging.ERROR, logger="golem.checkpoint"):
        result = load_checkpoint(issue_id)

    assert result is None
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error_records, "Expected at least one ERROR-level log record for corruption"


@pytest.mark.parametrize(
    "issue_id, corrupt_content",
    [
        (310, "not valid json {{{"),
        (311, "[1, 2, 3]"),
    ],
    ids=["invalid_json", "non_dict_json"],
)
def test_corrupt_json_renamed_to_dot_corrupt(
    issue_id: int, corrupt_content: str
) -> None:
    """Corrupt checkpoint files must be renamed to checkpoint.json.corrupt."""
    checkpoint_path = checkpoint_mod.CHECKPOINTS_DIR / str(issue_id) / "checkpoint.json"
    backup_path = checkpoint_path.parent / "checkpoint.json.corrupt"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(corrupt_content, encoding="utf-8")

    result = load_checkpoint(issue_id)

    assert result is None
    assert not checkpoint_path.exists(), "Original corrupt file should be renamed away"
    assert backup_path.exists(), "Backup .corrupt file must exist"


@pytest.mark.parametrize(
    "issue_id, corrupt_content",
    [
        (320, "not valid json {{{"),
        (321, "[1, 2, 3]"),
    ],
    ids=["invalid_json", "non_dict_json"],
)
def test_backup_file_contains_original_content(
    issue_id: int, corrupt_content: str
) -> None:
    """The .corrupt backup must contain the original corrupt data verbatim."""
    checkpoint_path = checkpoint_mod.CHECKPOINTS_DIR / str(issue_id) / "checkpoint.json"
    backup_path = checkpoint_path.parent / "checkpoint.json.corrupt"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(corrupt_content, encoding="utf-8")

    load_checkpoint(issue_id)

    assert backup_path.read_text("utf-8") == corrupt_content


def test_backup_corrupt_logs_debug_when_rename_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If renaming the corrupt file fails, a DEBUG message is logged and None returned."""
    checkpoint_path = checkpoint_mod.CHECKPOINTS_DIR / "330" / "checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text("not valid json {{{", encoding="utf-8")

    with (
        patch("golem.checkpoint.Path.rename", side_effect=OSError("perm denied")),
        caplog.at_level(logging.DEBUG, logger="golem.checkpoint"),
    ):
        result = load_checkpoint(330)

    assert result is None
    assert any(
        "Failed to back up corrupt checkpoint" in r.message
        for r in caplog.records
        if r.levelno == logging.DEBUG
    )


# ---------------------------------------------------------------------------
# is_checkpoint_fresh
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "age_minutes, max_age_minutes, expected",
    [
        (0, 10, True),  # just saved — fresh
        (5, 10, True),  # well within window
        (9, 10, True),  # just under limit
        (10, 10, False),  # exactly at limit (not strictly less than)
        (20, 10, False),  # well past limit
    ],
)
def test_is_checkpoint_fresh(
    age_minutes: int, max_age_minutes: int, expected: bool
) -> None:
    saved_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    checkpoint = {"saved_at": saved_at.isoformat()}
    assert is_checkpoint_fresh(checkpoint, max_age_minutes=max_age_minutes) is expected


def test_is_checkpoint_fresh_true() -> None:
    checkpoint = {"saved_at": datetime.now(timezone.utc).isoformat()}
    assert is_checkpoint_fresh(checkpoint) is True


def test_is_checkpoint_fresh_false() -> None:
    stale_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    checkpoint = {"saved_at": stale_time.isoformat()}
    assert is_checkpoint_fresh(checkpoint, max_age_minutes=10) is False


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_checkpoint_removes_files() -> None:
    session = _make_session(issue_id=55)
    path = save_checkpoint(55, session, phase="validating")
    assert path.exists()

    delete_checkpoint(55)

    assert not path.exists()
    assert not (checkpoint_mod.CHECKPOINTS_DIR / "55").exists()


def test_delete_checkpoint_noop_when_missing() -> None:
    # Should not raise
    delete_checkpoint(8888)


def test_delete_checkpoint_logs_debug_on_unlink_failure(caplog) -> None:
    """delete_checkpoint logs at debug when the checkpoint file does not exist."""
    # Issue 9999 has no checkpoint on disk, so unlink raises FileNotFoundError,
    # which is caught and logged at DEBUG level.
    with caplog.at_level(logging.DEBUG, logger="golem.checkpoint"):
        delete_checkpoint(9999)
    assert any(
        "Failed to unlink checkpoint" in r.message and r.levelno == logging.DEBUG
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# directory creation
# ---------------------------------------------------------------------------


def test_atomic_write_creates_directory() -> None:
    # Use the live (monkeypatched) CHECKPOINTS_DIR to resolve the correct path
    issue_dir = checkpoint_mod.CHECKPOINTS_DIR / "101"
    assert not issue_dir.exists()

    session = _make_session(issue_id=101)
    save_checkpoint(101, session, phase="starting")

    assert issue_dir.exists()
    assert (issue_dir / "checkpoint.json").exists()


# ---------------------------------------------------------------------------
# overwrite
# ---------------------------------------------------------------------------


def test_save_checkpoint_overwrites_existing() -> None:
    session1 = _make_session(issue_id=20)
    save_checkpoint(20, session1, phase="phase_one")

    session2 = MagicMock()
    session2.to_dict.return_value = {
        "parent_issue_id": 20,
        "state": "validating",
        "total_cost_usd": 3.0,
    }
    save_checkpoint(20, session2, phase="phase_two")

    data = load_checkpoint(20)
    assert data is not None
    assert data["phase"] == "phase_two"
    assert data["state"] == "validating"
    assert data["total_cost_usd"] == 3.0


# ---------------------------------------------------------------------------
# atomic write error path
# ---------------------------------------------------------------------------


def test_atomic_write_cleans_up_on_error() -> None:
    """Exercise the BaseException cleanup path in save_checkpoint."""
    session = _make_session(issue_id=200)
    with patch("golem.checkpoint.os.write", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            save_checkpoint(200, session, phase="failing")
    # No partial file should remain
    issue_dir = checkpoint_mod.CHECKPOINTS_DIR / "200"
    tmp_files = list(issue_dir.glob(".checkpoint_*.tmp")) if issue_dir.exists() else []
    assert not tmp_files


def test_atomic_write_cleans_up_when_unlink_fails() -> None:
    """Exercise the OSError suppression path when os.unlink also fails."""
    session = _make_session(issue_id=201)
    with (
        patch("golem.checkpoint.os.write", side_effect=OSError("disk full")),
        patch("golem.checkpoint.os.unlink", side_effect=OSError("perm")),
    ):
        with pytest.raises(OSError, match="disk full"):
            save_checkpoint(201, session, phase="failing")


# ---------------------------------------------------------------------------
# is_checkpoint_fresh — edge cases
# ---------------------------------------------------------------------------


def test_is_checkpoint_fresh_missing_saved_at() -> None:
    """Return False when saved_at key is absent."""
    assert is_checkpoint_fresh({}) is False


def test_is_checkpoint_fresh_naive_timestamp() -> None:
    """Naive (no tzinfo) timestamps are treated as UTC."""
    naive_now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    assert is_checkpoint_fresh({"saved_at": naive_now}) is True


def test_is_checkpoint_fresh_invalid_value() -> None:
    """Return False when saved_at is not a valid ISO string."""
    assert is_checkpoint_fresh({"saved_at": "not-a-date"}) is False


# ---------------------------------------------------------------------------
# config fields
# ---------------------------------------------------------------------------


def test_config_checkpoint_fields() -> None:
    from golem.core.config import GolemFlowConfig

    cfg = GolemFlowConfig()
    assert cfg.checkpoint_interval_seconds == 300
    assert cfg.checkpoint_max_age_minutes == 10
