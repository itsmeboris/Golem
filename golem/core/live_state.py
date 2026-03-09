"""In-memory runtime state for live dashboard visibility.

Supports optional file-backed persistence so a standalone dashboard process
can read live state written by the daemon.
"""

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from golem.types import LiveSnapshotDict

logger = logging.getLogger("golem.core.live_state")

DEFAULT_LIVE_STATE_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "live_state.json"
)


@dataclass
class ActiveTask:
    """A task currently being processed or waiting in queue."""

    event_id: str
    flow: str
    model: str
    started_at: float
    phase: str = "queued"


@dataclass
class CompletedTask:
    """A recently finished task kept for the live feed."""

    event_id: str
    flow: str
    model: str
    started_at: float
    finished_at: float
    success: bool
    cost_usd: float = 0.0


def read_live_snapshot(path: Path | None = None) -> LiveSnapshotDict:
    """Read a live-state snapshot from a JSON file on disk.

    Returns an empty-state dict if the file is missing or corrupt.
    """
    if path is None:
        path = DEFAULT_LIVE_STATE_FILE
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {
            "uptime_s": 0,
            "active_tasks": [],
            "active_count": 0,
            "queue_depth": 0,
            "queued_event_ids": [],
            "models_active": {},
            "recently_completed": [],
        }


class LiveState:
    """Thread-safe singleton tracking in-flight tasks and queue depth."""

    _instance: "LiveState | None" = None
    _lock = threading.Lock()

    def __init__(self):
        self._active: dict[str, ActiveTask] = {}
        self._queue: dict[str, float] = {}
        self._recent: list[CompletedTask] = []
        self._mu = threading.Lock()
        self._max_recent = (
            50  # ring buffer size for the dashboard's recent-completions feed
        )
        self._boot_time = time.time()
        self._persist_path: Path | None = None

    @classmethod
    def get(cls) -> "LiveState":
        """Return the global LiveState instance (created on first call)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Discard the singleton so the next get() creates a fresh instance."""
        with cls._lock:
            cls._instance = None

    def enable_persistence(self, path: Path | None = None) -> None:
        """Turn on automatic file persistence on every state change."""
        self._persist_path = path or DEFAULT_LIVE_STATE_FILE
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)

    def _persist(self) -> None:
        if self._persist_path is None:
            return
        try:
            data = json.dumps(self.snapshot()).encode("utf-8")
            fd, tmp = tempfile.mkstemp(dir=self._persist_path.parent, suffix=".tmp")
            try:
                os.write(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, self._persist_path)
        except OSError:
            logger.debug("Failed to persist live state", exc_info=True)

    def enqueue(self, event_id: str, flow: str, model: str) -> None:
        """Register a new task in the 'preparing' phase (prefetch/prompt)."""
        with self._mu:
            self._active[event_id] = ActiveTask(
                event_id=event_id,
                flow=flow,
                model=model,
                started_at=time.time(),
                phase="preparing",
            )
        self._persist()

    def mark_queued(self, event_id: str) -> None:
        """Mark a task as waiting for a concurrency slot."""
        with self._mu:
            task = self._active.get(event_id)
            if task:
                self._queue[event_id] = time.time()
                task.phase = "queued"
        self._persist()

    def dequeue_start(self, event_id: str) -> None:
        """Move a task from queued to running."""
        with self._mu:
            self._queue.pop(event_id, None)
            task = self._active.get(event_id)
            if task:
                task.phase = "running"
                task.started_at = time.time()
        self._persist()

    def update_phase(self, event_id: str, phase: str) -> None:
        """Update the processing phase of an active task."""
        with self._mu:
            task = self._active.get(event_id)
            if task:
                task.phase = phase
        self._persist()

    def finish(self, event_id: str, success: bool, cost_usd: float = 0.0) -> None:
        """Mark a task as complete and move it to the recent-completions ring."""
        with self._mu:
            self._queue.pop(event_id, None)
            task = self._active.pop(event_id, None)
            if task:
                self._recent.append(
                    CompletedTask(
                        event_id=task.event_id,
                        flow=task.flow,
                        model=task.model,
                        started_at=task.started_at,
                        finished_at=time.time(),
                        success=success,
                        cost_usd=cost_usd,
                    )
                )
                if len(self._recent) > self._max_recent:
                    self._recent = self._recent[-self._max_recent :]
        self._persist()

    def drain(self) -> int:
        """Move all active/queued tasks to recently-completed as interrupted.

        Returns the number of tasks drained.
        """
        with self._mu:
            count = len(self._active)
            now = time.time()
            for task in self._active.values():
                self._recent.append(
                    CompletedTask(
                        event_id=task.event_id,
                        flow=task.flow,
                        model=task.model,
                        started_at=task.started_at,
                        finished_at=now,
                        success=False,
                        cost_usd=0.0,
                    )
                )
            self._active.clear()
            self._queue.clear()
            if len(self._recent) > self._max_recent:
                self._recent = self._recent[-self._max_recent :]
        self._persist()
        return count

    def clear_persistence(self) -> None:
        """Remove the live-state JSON file from disk."""
        if self._persist_path and self._persist_path.exists():
            try:
                self._persist_path.unlink()
            except OSError:
                pass

    def snapshot(self) -> LiveSnapshotDict:  # pylint: disable=too-many-locals
        """Return a JSON-serialisable dict of the current live state."""
        now = time.time()
        with self._mu:
            active = [
                {
                    "event_id": t.event_id,
                    "flow": t.flow,
                    "model": t.model,
                    "phase": t.phase,
                    "elapsed_s": round(now - t.started_at, 1),
                }
                for t in self._active.values()
            ]

            queued = [eid for eid, _ in sorted(self._queue.items(), key=lambda x: x[1])]

            models_active: dict[str, int] = {}
            for t in self._active.values():
                if t.phase not in ("queued", "preparing"):
                    models_active[t.model] = models_active.get(t.model, 0) + 1

            recent = [
                {
                    "event_id": c.event_id,
                    "flow": c.flow,
                    "success": c.success,
                    "duration_s": round(c.finished_at - c.started_at, 1),
                    "cost_usd": round(c.cost_usd, 4),
                    "finished_ago_s": round(now - c.finished_at, 0),
                }
                for c in reversed(self._recent[-10:])
            ]

        return {
            "uptime_s": round(now - self._boot_time, 0),
            "active_tasks": active,
            "active_count": len(active),
            "queue_depth": len(queued),
            "queued_event_ids": queued,
            "models_active": models_active,
            "recently_completed": recent,
        }
