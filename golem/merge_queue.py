"""Sequential merge queue for cross-task coordination.

Completed sessions enter the queue instead of merging immediately.
The queue processes one merge at a time, rebasing each onto the latest
HEAD so every merge sees the freshest state.  On conflict or missing
additions, an optional merge-agent callback can attempt resolution.
"""

import asyncio
import logging
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from .worktree_manager import (
    MergeOutcome,
    _run_git,
    fast_forward_if_safe,
    get_changed_files,
    merge_in_worktree,
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
    changed_files: list[str] = field(default_factory=list)
    deferred: bool = False
    merge_branch: str = ""


OnMergeAgent = Callable[[str, int, str, list[str], list], ReconciliationResult] | None


class MergeQueue:
    """Priority-ordered, serialized merge queue.

    Entries are sorted by priority (lower = higher priority) and processed
    sequentially.  Each merge rebases onto the current HEAD first.
    """

    INFRA_RETRIES = 2
    INFRA_RETRY_DELAY = 5  # seconds

    def __init__(self, on_merge_agent: OnMergeAgent = None):
        self._queue: list[MergeEntry] = []
        self._lock = asyncio.Lock()
        self._on_merge_agent = on_merge_agent
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
        ``_on_merge_agent`` if set — if it resolves the merge proceeds;
        otherwise the entry is flagged for manual review.
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
        self,
        entry: MergeEntry,
        attempt: int,
    ) -> MergeResult | None:
        """Single merge attempt.  Returns None to signal 'retry'."""
        try:
            outcome = merge_in_worktree(entry.base_dir, entry.session_id)

            # --- Merge failed (empty sha + error) ---
            if not outcome.sha and outcome.error:
                if self._on_merge_agent and outcome.merge_branch:
                    recon = await asyncio.to_thread(
                        self._on_merge_agent,
                        entry.base_dir,
                        entry.session_id,
                        outcome.agent_diff,
                        entry.changed_files,
                        [],
                    )
                    if recon.resolved:
                        # Agent resolved — retry merge
                        outcome2 = merge_in_worktree(
                            entry.base_dir,
                            entry.session_id,
                        )
                        if outcome2.sha:
                            return self._try_ff(entry, outcome2)
                        # Retry still failed
                        logger.warning(
                            "Session %d: merge still fails after agent resolution",
                            entry.session_id,
                        )
                        return MergeResult(
                            session_id=entry.session_id,
                            success=False,
                            error=outcome2.error
                            or "merge failed after agent resolution",
                            conflict_files=entry.changed_files,
                        )

                # No agent or agent didn't resolve
                logger.warning(
                    "Session %d: merge failed, branch preserved for manual review",
                    entry.session_id,
                )
                return MergeResult(
                    session_id=entry.session_id,
                    success=False,
                    error=outcome.error or "merge failed or no changes",
                    conflict_files=entry.changed_files,
                )

            # --- Merge succeeded but has missing_additions ---
            if outcome.sha and outcome.missing_additions:
                if self._on_merge_agent:
                    logger.warning(
                        "Session %d: %d file(s) lost additions after merge",
                        entry.session_id,
                        len(outcome.missing_additions),
                    )
                    recon = await asyncio.to_thread(
                        self._on_merge_agent,
                        entry.base_dir,
                        entry.session_id,
                        outcome.agent_diff,
                        [m.file for m in outcome.missing_additions],
                        outcome.missing_additions,
                    )
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
                            conflict_files=[m.file for m in outcome.missing_additions],
                        )
                    # Reconciliation succeeded — proceed to ff
                else:
                    logger.warning(
                        "Session %d: %d file(s) lost additions after merge, "
                        "no reconciler configured",
                        entry.session_id,
                        len(outcome.missing_additions),
                    )
                    return MergeResult(
                        session_id=entry.session_id,
                        success=False,
                        merge_sha=outcome.sha,
                        error="agent additions lost during merge",
                        conflict_files=[m.file for m in outcome.missing_additions],
                    )

            # --- Merge succeeded (no missing or reconciliation passed) ---
            if outcome.sha:
                # No merge branch means no-new-commits (already on HEAD)
                if not outcome.merge_branch:
                    return MergeResult(
                        session_id=entry.session_id,
                        success=True,
                        merge_sha=outcome.sha,
                        changed_files=entry.changed_files,
                    )

                return self._try_ff(entry, outcome)

            # Empty sha with no error — no changes to merge
            logger.info(
                "Session %d: no changes to merge",
                entry.session_id,
            )
            return MergeResult(
                session_id=entry.session_id,
                success=True,
                merge_sha=outcome.sha,
                changed_files=entry.changed_files,
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
                await asyncio.sleep(self.INFRA_RETRY_DELAY)
                return None  # signal retry
            logger.error("Session %d: merge error: %s", entry.session_id, exc)
            return MergeResult(
                session_id=entry.session_id,
                success=False,
                error=str(exc),
            )

    @staticmethod
    def _try_ff(entry: MergeEntry, outcome: MergeOutcome) -> MergeResult:
        ok, reason = fast_forward_if_safe(entry.base_dir, outcome.merge_branch)
        if ok:
            # Clean up both branches
            _run_git(["branch", "-D", f"agent/{entry.session_id}"], cwd=entry.base_dir)
            _run_git(["branch", "-D", outcome.merge_branch], cwd=entry.base_dir)
            logger.info("Session %d: merged → %s", entry.session_id, outcome.sha)
            return MergeResult(
                session_id=entry.session_id,
                success=True,
                merge_sha=outcome.sha,
                changed_files=entry.changed_files,
            )
        return MergeResult(
            session_id=entry.session_id,
            success=False,
            deferred=True,
            merge_branch=outcome.merge_branch,
            merge_sha=outcome.sha,
            error=reason,
            changed_files=entry.changed_files,
        )
