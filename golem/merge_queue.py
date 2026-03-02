"""Sequential merge queue for cross-task coordination.

Completed sessions enter the queue instead of merging immediately.
The queue processes one merge at a time, rebasing each onto the latest
HEAD so every merge sees the freshest state.  On conflict, an optional
callback can spawn a reconciliation agent before falling back to manual.
"""

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .worktree_manager import cleanup_worktree, get_changed_files, merge_and_cleanup

logger = logging.getLogger("golem.merge_queue")


@dataclass
class MergeEntry:
    session_id: int
    branch_name: str
    worktree_path: str
    base_dir: str
    changed_files: list[str] = field(default_factory=list)
    priority: int = 5
    group_id: str = ""


@dataclass
class MergeResult:
    session_id: int
    success: bool
    merge_sha: str = ""
    conflict_files: list[str] = field(default_factory=list)
    error: str = ""


OnConflict = Callable[["MergeEntry", list[str]], bool] | None


class MergeQueue:
    """Priority-ordered, serialized merge queue.

    Entries are sorted by priority (lower = higher priority) and processed
    sequentially.  Each merge rebases onto the current HEAD first.
    """

    def __init__(self, on_conflict: OnConflict = None):
        self._queue: list[MergeEntry] = []
        self._lock = asyncio.Lock()
        self._on_conflict = on_conflict
        self._results: list[MergeResult] = []

    @property
    def pending(self) -> int:
        return len(self._queue)

    async def enqueue(self, entry: MergeEntry) -> None:
        """Add a completed session to the merge queue.

        Populates ``changed_files`` via ``git diff --name-only`` if not
        already set.
        """
        async with self._lock:
            if not entry.changed_files:
                entry.changed_files = get_changed_files(
                    entry.base_dir, entry.branch_name
                )
            self._queue.append(entry)
            logger.info(
                "Enqueued session %d for merge (%d files changed, priority=%d)",
                entry.session_id,
                len(entry.changed_files),
                entry.priority,
            )

    def detect_overlaps(self) -> dict[str, list[int]]:
        """Return ``{filepath: [session_ids]}`` for files touched by 2+ sessions."""
        file_map: dict[str, list[int]] = defaultdict(list)
        for entry in self._queue:
            for f in entry.changed_files:
                file_map[f].append(entry.session_id)
        return {f: sids for f, sids in file_map.items() if len(sids) > 1}

    async def process_all(self) -> list[MergeResult]:
        """Sort by priority, then merge sequentially.

        Each merge rebases onto current HEAD.  On conflict, invokes
        ``_on_conflict`` if set — if it returns True the conflict was
        resolved and merge proceeds; otherwise the entry is flagged
        for manual review.
        """
        async with self._lock:
            entries = sorted(self._queue, key=lambda e: e.priority)
            self._queue.clear()

        results: list[MergeResult] = []
        for entry in entries:
            result = await self._merge_one(entry)
            results.append(result)
            self._results.append(result)

        return results

    async def _merge_one(self, entry: MergeEntry) -> MergeResult:
        try:
            sha = merge_and_cleanup(
                entry.base_dir, entry.session_id, entry.worktree_path
            )
            if sha:
                logger.info(
                    "Session %d: merged successfully → %s",
                    entry.session_id,
                    sha,
                )
                return MergeResult(
                    session_id=entry.session_id, success=True, merge_sha=sha
                )

            if self._on_conflict:
                resolved = self._on_conflict(entry, entry.changed_files)
                if resolved:
                    sha_retry = merge_and_cleanup(
                        entry.base_dir, entry.session_id, entry.worktree_path
                    )
                    if sha_retry:
                        return MergeResult(
                            session_id=entry.session_id,
                            success=True,
                            merge_sha=sha_retry,
                        )

            cleanup_worktree(entry.base_dir, entry.worktree_path, keep_branch=True)
            logger.warning(
                "Session %d: merge failed, branch preserved for manual review",
                entry.session_id,
            )
            return MergeResult(
                session_id=entry.session_id,
                success=False,
                error="merge failed or no changes",
                conflict_files=entry.changed_files,
            )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Session %d: merge error: %s", entry.session_id, exc)
            return MergeResult(
                session_id=entry.session_id,
                success=False,
                error=str(exc),
            )
