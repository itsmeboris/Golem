"""Sequential merge queue for cross-task coordination.

Completed sessions enter the queue instead of merging immediately.
The queue processes one merge at a time, rebasing each onto the latest
HEAD so every merge sees the freshest state.  On conflict, an optional
callback can spawn a reconciliation agent before falling back to manual.
"""

import asyncio
import logging
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .worktree_manager import (
    cleanup_worktree,
    get_changed_files,
    merge_and_cleanup,
    verify_merge_integrity,
)
from .merge_review import ReconciliationResult

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
OnReconcile = Callable[["MergeEntry", str, list], ReconciliationResult] | None


class MergeQueue:
    """Priority-ordered, serialized merge queue.

    Entries are sorted by priority (lower = higher priority) and processed
    sequentially.  Each merge rebases onto the current HEAD first.
    """

    INFRA_RETRIES = 2
    INFRA_RETRY_DELAY = 5  # seconds

    def __init__(
        self,
        on_conflict: OnConflict = None,
        on_reconcile: OnReconcile = None,
    ):
        self._queue: list[MergeEntry] = []
        self._lock = asyncio.Lock()
        self._on_conflict = on_conflict
        self._on_reconcile = on_reconcile
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

    @staticmethod
    def _is_transient(exc: Exception) -> bool:
        """Return True if the exception looks like a transient infra failure."""
        return isinstance(exc, (subprocess.TimeoutExpired, OSError))

    async def _merge_one(self, entry: MergeEntry) -> MergeResult:
        for attempt in range(1 + self.INFRA_RETRIES):
            result = await self._try_merge(entry, attempt)
            if result is not None:
                return result
        # Exhausted retries — should not reach here, but be safe
        return MergeResult(
            session_id=entry.session_id,
            success=False,
            error="merge retries exhausted",
        )

    async def _try_merge(  # pylint: disable=too-many-return-statements
        self, entry: MergeEntry, attempt: int,
    ) -> MergeResult | None:
        """Single merge attempt.  Returns None to signal 'retry'."""
        try:
            outcome = merge_and_cleanup(
                entry.base_dir, entry.session_id, entry.worktree_path
            )
            if outcome.sha:
                missing = outcome.missing_additions
                if missing and self._on_reconcile:
                    logger.warning(
                        "Session %d: %d file(s) lost additions after merge",
                        entry.session_id,
                        len(missing),
                    )
                    recon = self._on_reconcile(entry, outcome.agent_diff, missing)
                    if not recon.resolved:
                        logger.warning(
                            "Session %d: reconciliation failed — %s",
                            entry.session_id,
                            recon.explanation,
                        )
                        return MergeResult(
                            session_id=entry.session_id,
                            success=False,
                            merge_sha=outcome.sha,
                            error=f"reconciliation failed: {recon.explanation}",
                            conflict_files=[m.file for m in missing],
                        )

                    # Re-verify after reconciliation
                    still_missing = verify_merge_integrity(
                        entry.base_dir, outcome.agent_diff, entry.changed_files
                    )
                    if still_missing:
                        logger.warning(
                            "Session %d: reconciliation committed but "
                            "%d file(s) still missing additions",
                            entry.session_id,
                            len(still_missing),
                        )
                        return MergeResult(
                            session_id=entry.session_id,
                            success=False,
                            merge_sha=outcome.sha,
                            error="additions still missing after reconciliation",
                            conflict_files=[m.file for m in still_missing],
                        )

                elif missing:
                    logger.warning(
                        "Session %d: %d file(s) lost additions after merge, "
                        "no reconciler configured",
                        entry.session_id,
                        len(missing),
                    )
                    return MergeResult(
                        session_id=entry.session_id,
                        success=False,
                        merge_sha=outcome.sha,
                        error="agent additions lost during merge",
                        conflict_files=[m.file for m in missing],
                    )

                logger.info(
                    "Session %d: merged successfully → %s",
                    entry.session_id,
                    outcome.sha,
                )
                return MergeResult(
                    session_id=entry.session_id, success=True, merge_sha=outcome.sha
                )

            if self._on_conflict:
                resolved = self._on_conflict(entry, entry.changed_files)
                if resolved:
                    outcome_retry = merge_and_cleanup(
                        entry.base_dir, entry.session_id, entry.worktree_path
                    )
                    if outcome_retry.sha:
                        return MergeResult(
                            session_id=entry.session_id,
                            success=True,
                            merge_sha=outcome_retry.sha,
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
            if self._is_transient(exc) and attempt < self.INFRA_RETRIES:
                logger.warning(
                    "Session %d: transient merge error (attempt %d/%d): %s — retrying",
                    entry.session_id,
                    attempt + 1,
                    1 + self.INFRA_RETRIES,
                    exc,
                )
                time.sleep(self.INFRA_RETRY_DELAY)
                return None  # signal retry
            logger.error("Session %d: merge error: %s", entry.session_id, exc)
            return MergeResult(
                session_id=entry.session_id,
                success=False,
                error=str(exc),
            )
