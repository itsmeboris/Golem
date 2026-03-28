"""Sequential merge queue for cross-task coordination.

Completed sessions enter the queue instead of merging immediately.
The queue processes one merge at a time, rebasing each onto the latest
HEAD so every merge sees the freshest state.  On conflict or missing
additions, an optional merge-agent callback can attempt resolution.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .types import MergeQueueSnapshotDict

from .worktree_manager import (
    MergeOutcome,
    _run_git,
    fast_forward_if_safe,
    get_changed_files,
    merge_in_worktree,
)
from .merge_review import ReconciliationResult
from .verifier import VerificationResult, run_verification

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
    queued_at: str = ""


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
    timestamp: str = ""


OnMergeAgent = Callable[[str, int, str, list[str], list], ReconciliationResult] | None


class MergeQueue:
    """Priority-ordered, serialized merge queue.

    Entries are sorted by priority (lower = higher priority) and processed
    sequentially.  Each merge rebases onto the current HEAD first.
    """

    INFRA_RETRIES = 2
    INFRA_RETRY_DELAY = 5  # seconds

    def __init__(
        self,
        on_merge_agent: OnMergeAgent = None,
        on_state_change: "Callable[[], None] | None" = None,
    ):
        self._queue: list[MergeEntry] = []
        self._processing: list[MergeEntry] = []
        self._lock = asyncio.Lock()
        self._thread_lock = threading.Lock()
        self._on_merge_agent = on_merge_agent
        self._on_state_change = on_state_change
        self._history: deque[tuple[MergeEntry, MergeResult]] = deque(maxlen=50)
        self._active: MergeEntry | None = None

    @property
    def pending(self) -> int:
        with self._thread_lock:
            return len(self._queue) + len(self._processing)

    async def enqueue(self, entry: MergeEntry) -> None:
        """Add a completed session to the merge queue.

        Populates ``changed_files`` via ``git diff --name-only`` if not
        already set.
        """
        async with self._lock:
            if not entry.changed_files:
                entry.changed_files = await asyncio.to_thread(
                    get_changed_files, entry.base_dir, entry.branch_name
                )
            if not entry.queued_at:
                entry.queued_at = datetime.now(timezone.utc).isoformat()
            with self._thread_lock:
                self._queue.append(entry)
            logger.info(
                "Enqueued session %d for merge (%d files changed, priority=%d)",
                entry.session_id,
                len(entry.changed_files),
                entry.priority,
            )
        self._notify()

    def detect_overlaps(self) -> dict[str, list[int]]:
        """Return ``{filepath: [session_ids]}`` for files touched by 2+ sessions."""
        with self._thread_lock:
            entries = [*self._queue, *self._processing]
        file_map: dict[str, list[int]] = defaultdict(list)
        for entry in entries:
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
            with self._thread_lock:
                entries = sorted(self._queue, key=lambda e: e.priority)
                self._queue.clear()
                self._processing = list(entries)

        results: list[MergeResult] = []
        for entry in entries:
            async with self._lock:
                with self._thread_lock:
                    self._active = entry
            result = await self._merge_one(entry)
            async with self._lock:
                with self._thread_lock:
                    self._active = None
                    self._processing = [e for e in self._processing if e is not entry]
            result.timestamp = datetime.now(timezone.utc).isoformat()
            async with self._lock:
                with self._thread_lock:
                    self._history.append((entry, result))
            self._notify()
            results.append(result)

        return results

    def _notify(self) -> None:
        """Invoke the state-change callback if set."""
        if self._on_state_change:
            self._on_state_change()

    def snapshot(self) -> MergeQueueSnapshotDict:
        """Serialize current queue state for the dashboard API."""
        from .types import (
            MergeEntryDict,
            MergeHistoryEntryDict,
            MergeQueueSnapshotDict,
        )

        def _entry_dict(e: MergeEntry) -> MergeEntryDict:
            return {
                "session_id": e.session_id,
                "branch_name": e.branch_name,
                "worktree_path": e.worktree_path,
                "priority": e.priority,
                "group_id": e.group_id,
                "queued_at": e.queued_at,
                "changed_files": list(e.changed_files),
            }

        def _history_dict(_e: MergeEntry, r: MergeResult) -> MergeHistoryEntryDict:
            return {
                "session_id": r.session_id,
                "success": r.success,
                "merge_sha": r.merge_sha,
                "conflict_files": list(r.conflict_files),
                "error": r.error,
                "changed_files": list(r.changed_files),
                "deferred": r.deferred,
                "merge_branch": r.merge_branch,
                "timestamp": r.timestamp,
            }

        # Snapshot all shared state under thread lock to prevent torn reads
        with self._thread_lock:
            queue_copy = list(self._queue)
            processing_copy = list(self._processing)
            active_copy = self._active
            history_copy = list(self._history)

        # Build latest-index map for dedup
        latest_idx: dict[int, int] = {}
        for i, (e, _r) in enumerate(history_copy):
            latest_idx[e.session_id] = i

        # Derive deferred/conflicts from history, only latest entry per session
        deferred = [
            _entry_dict(e)
            for i, (e, r) in enumerate(history_copy)
            if r.deferred and not r.success and latest_idx[e.session_id] == i
        ]
        conflicts = [
            _entry_dict(e)
            for i, (e, r) in enumerate(history_copy)
            if r.conflict_files
            and not r.success
            and not r.deferred
            and latest_idx[e.session_id] == i
        ]

        active = _entry_dict(active_copy) if active_copy else None
        pending = [
            _entry_dict(e)
            for e in [*queue_copy, *processing_copy]
            if e is not active_copy
        ]
        history = [_history_dict(e, r) for e, r in history_copy]

        return MergeQueueSnapshotDict(
            pending=pending,
            active=active,
            deferred=deferred,
            conflicts=conflicts,
            history=history,
        )

    async def retry(self, session_id: int) -> MergeEntry:
        """Re-enqueue a failed/deferred/conflicted entry from history.

        Raises ``ValueError`` if no retryable entry is found.
        """
        async with self._lock:
            with self._thread_lock:
                history_snapshot = list(self._history)
            for entry, result in reversed(history_snapshot):
                if entry.session_id == session_id and not result.success:
                    new_entry = MergeEntry(
                        session_id=entry.session_id,
                        branch_name=entry.branch_name,
                        worktree_path=entry.worktree_path,
                        base_dir=entry.base_dir,
                        changed_files=list(entry.changed_files),
                        priority=entry.priority,
                        group_id=entry.group_id,
                    )
                    break
            else:
                raise ValueError(
                    "No retryable entry found for session_id %d" % session_id
                )
        await self.enqueue(new_entry)
        return new_entry

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
            outcome = await asyncio.to_thread(
                merge_in_worktree, entry.base_dir, entry.session_id
            )

            # --- Merge failed (empty sha + error) ---
            if not outcome.sha and outcome.error:
                if self._on_merge_agent and outcome.merge_branch:
                    try:
                        recon = await asyncio.to_thread(
                            self._on_merge_agent,
                            entry.base_dir,
                            entry.session_id,
                            outcome.agent_diff,
                            entry.changed_files,
                            [],
                        )
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        logger.error(
                            "Session %d: merge agent callback failed: %s",
                            entry.session_id,
                            exc,
                        )
                        recon = ReconciliationResult(
                            resolved=False,
                            explanation="merge agent error: %s" % exc,
                        )
                    if recon.resolved:
                        # Agent resolved — retry merge
                        outcome2 = await asyncio.to_thread(
                            merge_in_worktree,
                            entry.base_dir,
                            entry.session_id,
                        )
                        if outcome2.sha:
                            vr = await asyncio.to_thread(
                                self._verify_merge,
                                entry.base_dir,
                                outcome2.merge_branch,
                                entry.session_id,
                            )
                            if not vr.passed:
                                logger.warning(
                                    "Session %d: post-merge verification failed"
                                    " after conflict resolution",
                                    entry.session_id,
                                )
                                return MergeResult(
                                    session_id=entry.session_id,
                                    success=False,
                                    merge_sha=outcome2.sha,
                                    merge_branch=outcome2.merge_branch,
                                    error="post-merge verification failed",
                                    changed_files=entry.changed_files,
                                )
                            return await asyncio.to_thread(
                                self._try_ff, entry, outcome2
                            )
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
                    # Agent callback returned unresolved (or raised)
                    return MergeResult(
                        session_id=entry.session_id,
                        success=False,
                        error=recon.explanation or outcome.error or "merge failed",
                        conflict_files=entry.changed_files,
                    )

                # No agent configured
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
                    try:
                        recon = await asyncio.to_thread(
                            self._on_merge_agent,
                            entry.base_dir,
                            entry.session_id,
                            outcome.agent_diff,
                            [m.file for m in outcome.missing_additions],
                            outcome.missing_additions,
                        )
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        logger.error(
                            "Session %d: merge agent callback failed: %s",
                            entry.session_id,
                            exc,
                        )
                        recon = ReconciliationResult(
                            resolved=False,
                            explanation="merge agent error: %s" % exc,
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
                    # Reconciliation succeeded — proceed to verify + ff
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

                # Always verify post-merge before fast-forwarding
                vr = await asyncio.to_thread(
                    self._verify_merge,
                    entry.base_dir,
                    outcome.merge_branch,
                    entry.session_id,
                )
                if not vr.passed:
                    logger.warning(
                        "Session %d: post-merge verification failed",
                        entry.session_id,
                    )
                    return MergeResult(
                        session_id=entry.session_id,
                        success=False,
                        merge_sha=outcome.sha,
                        merge_branch=outcome.merge_branch,
                        error="post-merge verification failed",
                        changed_files=entry.changed_files,
                    )
                return await asyncio.to_thread(self._try_ff, entry, outcome)

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
    def _verify_merge(
        base_dir: str, merge_branch: str, session_id: int
    ) -> VerificationResult:
        """Run verification in a temporary worktree created from the merge branch.

        Creates a disposable worktree, runs black+pylint+pytest, and cleans up.
        Returns the VerificationResult.
        """
        verify_wt_path = str(
            Path(base_dir) / "data" / "agent" / "verify-worktrees" / str(session_id)
        )
        Path(verify_wt_path).parent.mkdir(parents=True, exist_ok=True)

        # Clean stale worktree
        if Path(verify_wt_path).exists():
            _run_git(
                ["worktree", "remove", "--force", verify_wt_path],
                cwd=base_dir,
                timeout=120,
            )

        wt_result = _run_git(
            ["worktree", "add", "--detach", verify_wt_path, merge_branch],
            cwd=base_dir,
        )
        if wt_result.returncode != 0:
            logger.error(
                "Session %d: failed to create verification worktree: %s",
                session_id,
                wt_result.stderr.strip(),
            )
            return VerificationResult(
                passed=False,
                black_ok=False,
                black_output="verification worktree creation failed",
                pylint_ok=False,
                pylint_output="",
                pytest_ok=False,
                pytest_output="",
            )

        try:
            return run_verification(verify_wt_path)
        finally:
            _run_git(
                ["worktree", "remove", "--force", verify_wt_path],
                cwd=base_dir,
                timeout=120,
            )
            _run_git(["worktree", "prune"], cwd=base_dir)

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
