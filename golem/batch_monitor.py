"""Batch-level state management for grouped task submissions.

Tracks groups of tasks submitted together, aggregates their results,
and reports overall batch status.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


_IN_FLIGHT_STATES = frozenset({"detected", "running", "validating", "retrying"})


@dataclass
class BatchState:
    """Persistent state for a batch of tasks submitted together."""

    group_id: str
    task_ids: list[int] = field(default_factory=list)
    status: str = "submitted"
    created_at: str = ""
    completed_at: str = ""
    total_cost_usd: float = 0.0
    total_duration_s: float = 0.0
    task_results: dict[str, dict] = field(default_factory=dict)
    validation_verdict: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "group_id": self.group_id,
            "task_ids": list(self.task_ids),
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "total_cost_usd": self.total_cost_usd,
            "total_duration_s": self.total_duration_s,
            "task_results": dict(self.task_results),
            "validation_verdict": self.validation_verdict,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BatchState:
        """Deserialize from a dictionary."""
        return cls(
            group_id=data["group_id"],
            task_ids=data.get("task_ids", []),
            status=data.get("status", "submitted"),
            created_at=data.get("created_at", ""),
            completed_at=data.get("completed_at", ""),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            total_duration_s=data.get("total_duration_s", 0.0),
            task_results=data.get("task_results", {}),
            validation_verdict=data.get("validation_verdict", ""),
        )


class BatchMonitor:
    """Tracks batch-level state across groups of tasks."""

    def __init__(self) -> None:
        self._batches: dict[str, BatchState] = {}

    def register(self, group_id: str, task_ids: list[int]) -> BatchState:
        """Create and store a new batch with status 'submitted'."""
        batch = BatchState(
            group_id=group_id,
            task_ids=list(task_ids),
            status="submitted",
            created_at=_now_iso(),
        )
        self._batches[group_id] = batch
        return batch

    def update(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self, group_id: str, sessions: dict[int, Any]
    ) -> BatchState:
        """Refresh batch state from live session objects.

        Parameters
        ----------
        group_id:
            The batch identifier.
        sessions:
            Mapping of task_id to session objects (duck-typed; expects
            ``.state``, ``.cost_usd``, ``.duration_s``,
            ``.validation_verdict``, ``.group_id`` attributes).
        """
        batch = self._batches[group_id]

        total_cost = 0.0
        total_duration = 0.0
        task_results: dict[str, dict] = {}

        completed_count = 0
        failed_count = 0
        in_flight_count = 0

        verdicts: list[str] = []

        for tid in batch.task_ids:
            session = sessions.get(tid)
            if session is None:
                continue

            # Normalise state to a plain string for comparison
            state_val = session.state
            if hasattr(state_val, "value"):
                state_val = state_val.value

            cost = getattr(session, "total_cost_usd", 0.0)
            duration = getattr(session, "duration_seconds", 0.0)
            verdict = getattr(session, "validation_verdict", "")

            total_cost += cost
            total_duration += duration

            task_results[str(tid)] = {
                "state": state_val,
                "validation_verdict": verdict,
                "total_cost_usd": cost,
                "duration_seconds": duration,
            }

            if state_val == "completed":
                completed_count += 1
            elif state_val == "failed":
                failed_count += 1
            elif state_val in _IN_FLIGHT_STATES:
                in_flight_count += 1

            if verdict:
                verdicts.append(verdict)

        batch.total_cost_usd = total_cost
        batch.total_duration_s = total_duration
        batch.task_results = task_results

        # Derive batch status
        total_tasks = len(batch.task_ids)
        if in_flight_count > 0:
            batch.status = "running"
        elif completed_count == total_tasks and total_tasks > 0:
            batch.status = "completed"
        elif failed_count > 0 and in_flight_count == 0:
            batch.status = "failed"
        else:
            batch.status = "submitted"

        # Derive aggregate validation verdict
        if not verdicts:
            batch.validation_verdict = ""
        elif all(v == "PASS" for v in verdicts):
            batch.validation_verdict = "PASS"
        elif any(v == "FAIL" for v in verdicts):
            batch.validation_verdict = "FAIL"
        else:
            batch.validation_verdict = "PARTIAL"

        # Set completed_at on terminal states
        if batch.status in ("completed", "failed") and not batch.completed_at:
            batch.completed_at = _now_iso()

        return batch

    def get(self, group_id: str) -> BatchState | None:
        """Return batch state or ``None`` if not found."""
        return self._batches.get(group_id)

    def list_batches(self) -> list[BatchState]:
        """Return all batches sorted by created_at descending."""
        return sorted(
            self._batches.values(),
            key=lambda b: b.created_at,
            reverse=True,
        )

    def serialize(self) -> bytes:
        """Return the JSON payload that would be written to disk by ``save()``."""
        data = {
            "batches": {gid: b.to_dict() for gid, b in self._batches.items()},
            "last_updated": _now_iso(),
        }
        return _json.dumps(data, indent=2).encode("utf-8")

    def save(self, path: Path) -> None:
        """Atomically persist all batches to a JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = self.serialize()

        fd, tmp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=".batches_", suffix=".tmp"
        )
        closed = False
        try:
            os.write(fd, payload)
            os.fsync(fd)
            os.close(fd)
            closed = True
            os.replace(tmp_path, str(path))
        except BaseException:
            if not closed:
                os.close(fd)
            try:
                os.unlink(tmp_path)
            except OSError as exc:
                logger.debug("Failed to clean up temp file: %s", exc)
            raise

    def load(self, path: Path) -> None:
        """Load batches from a JSON file."""
        if not path.exists():
            return
        try:
            raw = _json.loads(path.read_text(encoding="utf-8"))
        except (_json.JSONDecodeError, ValueError) as exc:
            logger.error("Corrupt batch monitor state at %s: %s", path, exc)
            return
        self._batches.clear()
        for gid, bdata in raw.get("batches", {}).items():
            self._batches[gid] = BatchState.from_dict(bdata)
