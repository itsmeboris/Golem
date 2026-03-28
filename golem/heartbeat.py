"""Heartbeat — self-directed work discovery when Golem is idle.

Runs on its own async timer, discovers untagged issues (Tier 1) and
self-improvement opportunities (Tier 2), and submits work via
``GolemFlow.submit_task()``.

State is persisted to ``data/heartbeat_state.json``.  Budget limits are
read from ``GolemFlowConfig`` (read-only at runtime).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .core.config import DATA_DIR, GolemFlowConfig
from .heartbeat_worker import HeartbeatWorker
from .orchestrator import TaskSessionState
from .repo_registry import RepoRegistry
from .types import (
    HeartbeatCandidateDict,
    HeartbeatSnapshotDict,
    LiveSnapshotDict,
)

logger = logging.getLogger("golem.heartbeat")

_24H = 86400  # seconds

_TERMINAL_STATES = frozenset(
    {
        TaskSessionState.COMPLETED,
        TaskSessionState.FAILED,
        TaskSessionState.HUMAN_REVIEW,
    }
)


def _coerce_task_id(value: Any) -> int | None:
    """Coerce *value* to ``int``, returning ``None`` if not possible.

    - Returns *value* unchanged when it is already an ``int`` (but not a
      ``bool`` — ``bool`` is a subclass of ``int`` and must be rejected).
    - Attempts ``int(value)`` for ``str`` inputs and logs a warning on
      success.
    - Logs a warning and returns ``None`` for any value that cannot be
      converted (including ``bool``, ``float``, ``None``, …).

    All warnings include the original value and its type for diagnostics.
    """
    if isinstance(value, bool):
        logger.warning(
            "Cannot coerce task ID %r (type %s) to int — skipping",
            value,
            type(value).__name__,
        )
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            coerced = int(value)
            logger.warning(
                "Coerced string task ID %r (type %s) to int",
                value,
                type(value).__name__,
            )
            return coerced
        except (ValueError, TypeError):
            logger.warning(
                "Cannot coerce task ID %r (type %s) to int — skipping",
                value,
                type(value).__name__,
            )
            return None
    logger.warning(
        "Cannot coerce task ID %r (type %s) to int — skipping",
        value,
        type(value).__name__,
    )
    return None


class HeartbeatManager:
    """Discovers work when Golem is idle via a two-tier scanning system.

    Budget limits come from *config* and are read-only at runtime.
    Mutable state (spend, inflight) is persisted to *state_dir*.
    Per-repo scan state lives on HeartbeatWorker instances.
    """

    def __init__(
        self,
        config: GolemFlowConfig,
        state_dir: Path | None = None,
    ) -> None:
        self._config = config
        self._state_dir = state_dir or DATA_DIR
        self._state_file = self._state_dir / "heartbeat_state.json"

        # Budget — limits from config (read-only), spend tracked here
        self._daily_spend_usd: float = 0.0
        self._daily_spend_reset_at: float = time.time()

        # Inflight tracking
        self._inflight_task_ids: list[int] = []

        # Runtime state
        self._state: str = (
            "idle"  # idle | scanning | submitted | paused | budget_exhausted
        )
        self._loop_task: Any = None  # asyncio.Task, set in start()
        self._flow: Any = None  # set in start()
        self._next_tick_at: float = 0.0  # epoch when next tick fires
        self._trigger_event: Any = None  # asyncio.Event for force-trigger

        # Multi-repo scheduler state
        self._registry: RepoRegistry | None = None
        self._workers: dict[str, HeartbeatWorker] = {}
        self._repo_index: int = 0

    # -- Budget ---------------------------------------------------------------

    def budget_allows(self) -> bool:
        """Check if daily budget has remaining capacity."""
        self._maybe_reset_budget()
        return self._daily_spend_usd < self._config.heartbeat_daily_budget_usd

    def record_spend(self, amount_usd: float) -> None:
        """Add *amount_usd* to the daily spend counter."""
        self._daily_spend_usd += amount_usd

    def _maybe_reset_budget(self) -> None:
        """Reset daily spend counter if 24h have elapsed."""
        now = time.time()
        if now - self._daily_spend_reset_at >= _24H:
            self._daily_spend_usd = 0.0
            self._daily_spend_reset_at = now

    # -- Inflight -------------------------------------------------------------

    def can_submit(self) -> bool:
        """Return True if we're under the max inflight limit.

        When a flow is available, filters out sessions that are in a terminal
        state (COMPLETED, FAILED, HUMAN_REVIEW) or no longer exist.  Stale IDs
        are removed from ``_inflight_task_ids`` as a side effect so the list
        stays tidy over time.
        """
        if self._flow is None:
            return len(self._inflight_task_ids) < self._config.heartbeat_max_inflight

        active_ids = []
        stale_ids = []
        for tid in self._inflight_task_ids:
            session = self._flow.get_session(tid)
            if session is None or session.state in _TERMINAL_STATES:
                stale_ids.append(tid)
            else:
                active_ids.append(tid)

        if stale_ids:
            logger.info("Removing %s stale inflight IDs: %s", len(stale_ids), stale_ids)
            self._inflight_task_ids = active_ids

        return len(active_ids) < self._config.heartbeat_max_inflight

    def on_task_completed(self, task_id: int, success: bool) -> None:
        """Callback from GolemFlow when a session reaches terminal state."""
        coerced = _coerce_task_id(task_id)
        if coerced is None:
            return
        task_id = coerced
        if task_id not in self._inflight_task_ids:
            return
        self._inflight_task_ids.remove(task_id)

        # Route to the owning worker
        for worker in self._workers.values():
            if task_id in worker._inflight_task_ids:
                worker.on_task_completed(task_id, success)
                worker.save_state()
                break

        self.save_state()

    def get_claimed_issue_ids(self) -> set[int]:
        """Return GH issue IDs with active heartbeat claims.

        Aggregates from all attached workers.
        """
        ids: set[int] = set()
        for worker in self._workers.values():
            ids.update(worker.get_claimed_issue_ids())
        return ids

    def reconcile_inflight(self, active_session_ids: set[int]) -> None:
        """Remove inflight IDs not present in active sessions (startup recovery)."""
        before = len(self._inflight_task_ids)
        self._inflight_task_ids = [
            tid for tid in self._inflight_task_ids if tid in active_session_ids
        ]
        removed = before - len(self._inflight_task_ids)
        if removed:
            logger.info(
                "Heartbeat reconciliation: removed %d stale inflight IDs", removed
            )

        # Also reconcile worker inflight lists
        for worker in self._workers.values():
            w_before = len(worker._inflight_task_ids)
            worker._inflight_task_ids = [
                tid for tid in worker._inflight_task_ids if tid in active_session_ids
            ]
            if len(worker._inflight_task_ids) < w_before:
                worker.save_state()

    # -- State persistence ----------------------------------------------------

    def save_state(self) -> None:
        """Persist heartbeat state to disk."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "daily_spend_usd": self._daily_spend_usd,
            "daily_spend_reset_at": self._daily_spend_reset_at,
            "inflight_task_ids": self._inflight_task_ids,
            "repo_index": self._repo_index,
        }
        self._state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load_state(self) -> None:
        """Load heartbeat state from disk. Missing file = fresh state."""
        if not self._state_file.exists():
            return
        try:
            data: dict[str, Any] = json.loads(
                self._state_file.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load heartbeat state: %s", exc)
            return
        self._daily_spend_usd = data.get("daily_spend_usd", 0.0)
        self._daily_spend_reset_at = data.get("daily_spend_reset_at", time.time())
        raw_ids = data.get("inflight_task_ids", [])
        coerced: list[int] = []
        for raw in raw_ids:
            tid = _coerce_task_id(raw)
            if tid is not None:
                coerced.append(tid)
        self._inflight_task_ids = coerced
        self._repo_index = data.get("repo_index", 0)

    # -- Idle detection -------------------------------------------------------

    def is_idle(self, snapshot: LiveSnapshotDict) -> bool:
        """Return True if there are no external tasks active."""
        active = snapshot["active_count"]
        heartbeat_count = len(self._inflight_task_ids)
        return active <= heartbeat_count

    def has_external_tasks(self, snapshot: LiveSnapshotDict) -> bool:
        """Return True if non-heartbeat tasks are active."""
        active = snapshot["active_count"]
        return active > len(self._inflight_task_ids)

    # -- Async loop -----------------------------------------------------------

    def start(self, flow: Any) -> None:
        """Start the heartbeat async loop."""
        import asyncio

        self._flow = flow
        self.load_state()
        self._trigger_event = asyncio.Event()
        self._loop_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(
            "Heartbeat started (interval=%ds, idle_threshold=%ds, budget=$%.2f/day)",
            self._config.heartbeat_interval_seconds,
            self._config.heartbeat_idle_threshold_seconds,
            self._config.heartbeat_daily_budget_usd,
        )

    def stop(self) -> None:
        """Stop the heartbeat loop and persist state."""
        if self._loop_task is not None:
            self._loop_task.cancel()
            self._loop_task = None
        self._state = "idle"
        self.save_state()
        logger.info("Heartbeat stopped")

    def trigger(self) -> bool:
        """Force an immediate heartbeat tick.  Returns True if triggered."""
        if self._trigger_event is None:
            return False
        self._trigger_event.set()
        return True

    async def _heartbeat_loop(self) -> None:
        """Main async loop — runs on its own interval."""
        import asyncio

        idle_since: float | None = None
        tick_count: int = 0
        loop_start: float = time.time()
        while True:
            try:
                forced = (
                    self._trigger_event is not None and self._trigger_event.is_set()
                )
                if self._trigger_event is not None:
                    self._trigger_event.clear()

                snapshot = self._flow.live.snapshot()

                if not self.budget_allows():
                    self._state = "budget_exhausted"
                elif forced:
                    logger.info("Heartbeat force-triggered")
                    await self._run_heartbeat_tick()
                elif self.has_external_tasks(snapshot):
                    self._state = "paused"
                    idle_since = None
                elif self.is_idle(snapshot):
                    if idle_since is None:
                        idle_since = time.time()
                    idle_duration = time.time() - idle_since
                    if idle_duration >= self._config.heartbeat_idle_threshold_seconds:
                        await self._run_heartbeat_tick()
                    else:
                        self._state = "idle"
            except asyncio.CancelledError:
                break
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Error in heartbeat loop")

            interval = self._config.heartbeat_interval_seconds
            self._next_tick_at = time.time() + interval

            tick_count += 1
            max_ticks = self._config.heartbeat_max_ticks
            if max_ticks > 0 and tick_count >= max_ticks:
                logger.info("Heartbeat loop exiting: reached max_ticks=%d", max_ticks)
                break

            max_duration = self._config.heartbeat_max_duration_seconds
            if max_duration > 0 and (time.time() - loop_start) >= max_duration:
                logger.info(
                    "Heartbeat loop exiting: reached max_duration=%ds", max_duration
                )
                break

            # Sleep but wake early on force-trigger
            if self._trigger_event is not None:
                try:
                    await asyncio.wait_for(self._trigger_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(interval)

    # -- Multi-repo scheduling -------------------------------------------------

    def _sync_workers(self) -> None:
        """Reload registry and create/remove workers as needed."""
        if self._registry is None:
            return

        self._registry.load()
        current_paths = {r["path"] for r in self._registry.heartbeat_repos()}

        # Remove workers for detached repos (save state first)
        for path in list(self._workers):
            if path not in current_paths:
                self._workers[path].save_state()
                del self._workers[path]

        # Create workers for new repos
        for path in current_paths:
            if path not in self._workers:
                worker = HeartbeatWorker(
                    repo_path=path,
                    config=self._config,
                    state_dir=self._state_dir / "heartbeat",
                )
                worker.load_state()
                self._workers[path] = worker

    def _next_worker(self) -> HeartbeatWorker | None:
        """Return the next worker in round-robin order, or None if empty."""
        if not self._workers:
            return None
        paths = sorted(self._workers.keys())
        self._repo_index = self._repo_index % len(paths)
        worker = self._workers[paths[self._repo_index]]
        self._repo_index = (self._repo_index + 1) % len(paths)
        return worker

    def _submit_single_for_worker(
        self, worker: HeartbeatWorker, candidate: HeartbeatCandidateDict
    ) -> None:
        """Submit a single candidate, routing to worker's repo."""
        subject = f"[HEARTBEAT] {candidate['subject']}"
        body = candidate["body"]
        prompt = (
            f"{body}\n\n"
            f"Source: heartbeat tier {candidate.get('tier', 0)}\n"
            f"Confidence: {candidate['confidence']}\n"
            f"Complexity: {candidate['complexity']}\n"
            f"Reason: {candidate['reason']}"
        )

        try:
            is_issue = not candidate["id"].startswith("improvement:")
            result = self._flow.submit_task(
                prompt=prompt,
                subject=subject,
                issue_mode=is_issue,
                work_dir=worker.repo_path,
            )
            task_id = result["task_id"]
            coerced = _coerce_task_id(task_id)
            if coerced is None:
                logger.error("submit_task returned non-integer task_id: %r", task_id)
                self._state = "idle"
                return
            task_id = coerced
            self._inflight_task_ids.append(task_id)
            worker._inflight_task_ids.append(task_id)
            worker.record_dedup(candidate["id"], "submitted", task_id=task_id)
            self._state = "submitted"
            logger.info(
                "Heartbeat submitted task #%d: %s (tier=%d, confidence=%.2f, repo=%s)",
                task_id,
                subject,
                candidate.get("tier", 0),
                candidate["confidence"],
                worker.repo_path,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Heartbeat failed to submit task for worker")
            self._state = "idle"

    def _submit_batch_for_worker(
        self,
        worker: HeartbeatWorker,
        batch: list[HeartbeatCandidateDict],
        recent_categories: set[str] | None = None,
        resolved_ids: set[str] | None = None,
    ) -> None:
        """Submit a batch of Tier 2 candidates for a worker's repo."""
        category = batch[0].get("category", "improvement")

        if recent_categories is None:
            recent_categories = worker._get_recent_batch_categories()
        if category in recent_categories:
            logger.warning(
                "Skipping batch submission — category %r was recently addressed",
                category,
            )
            return

        if resolved_ids is None:
            resolved_ids = worker._get_recently_resolved_ids()
        if resolved_ids and all(c["id"] in resolved_ids for c in batch):
            logger.warning(
                "Skipping batch submission — all %d item(s) already resolved",
                len(batch),
            )
            return

        tier = batch[0].get("tier", 2)
        subject = f"[HEARTBEAT] batch:{category} ({len(batch)} items)"

        items_text = []
        for i, c in enumerate(batch, 1):
            items_text.append(f"{i}. [{c['id']}] {c['reason']}")

        prompt = (
            f"Fix the following {len(batch)} related issues "
            f"(category: {category}):\n\n"
            + "\n".join(items_text)
            + f"\n\nSource: heartbeat tier {tier}\n"
            f"Batch size: {len(batch)}\n"
            f"Category: {category}"
        )

        try:
            result = self._flow.submit_task(
                prompt=prompt,
                subject=subject,
                work_dir=worker.repo_path,
            )
            task_id = result["task_id"]
            coerced = _coerce_task_id(task_id)
            if coerced is None:
                logger.error("submit_task returned non-integer task_id: %r", task_id)
                self._state = "idle"
                return
            task_id = coerced
            self._inflight_task_ids.append(task_id)
            worker._inflight_task_ids.append(task_id)
            for c in batch:
                worker.record_dedup(c["id"], "submitted", task_id=task_id)
            self._state = "submitted"
            logger.info(
                "Heartbeat submitted batch task #%d: %s "
                "(tier=%d, items=%d, repo=%s)",
                task_id,
                subject,
                tier,
                len(batch),
                worker.repo_path,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Heartbeat failed to submit batch task for worker")
            self._state = "idle"

    def _submit_promoted_for_worker(
        self, worker: HeartbeatWorker, candidate: HeartbeatCandidateDict
    ) -> bool:
        """Submit a promoted GH issue for a worker's repo.

        Returns True on successful submission, False on failure.
        """
        subject = f"[PROMOTED] {candidate['subject']}"
        body = candidate["body"]
        prompt = (
            f"{body}\n\n"
            f"Source: heartbeat tier 1 promotion\n"
            f"Confidence: {candidate['confidence']}\n"
            f"Complexity: {candidate['complexity']}\n"
            f"Reason: {candidate['reason']}"
        )

        try:
            result = self._flow.submit_task(
                prompt=prompt,
                subject=subject,
                issue_mode=True,
                work_dir=worker.repo_path,
            )
            task_id = _coerce_task_id(result["task_id"])
            if task_id is None:
                logger.error(
                    "submit_task returned non-integer task_id: %r",
                    result["task_id"],
                )
                return False
            self._inflight_task_ids.append(task_id)
            worker._inflight_task_ids.append(task_id)
            worker.record_dedup(candidate["id"], "promoted", task_id=task_id)
            logger.info(
                "Promoted GH issue submitted: %s (confidence=%.2f, repo=%s)",
                subject,
                candidate["confidence"],
                worker.repo_path,
            )
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to submit promoted GH issue for worker")
            return False

    async def _run_multi_repo_tick(self) -> None:
        """Multi-repo tick: round-robin across workers."""
        tried = 0
        total = len(self._workers)
        while tried < total:
            worker = self._next_worker()
            if worker is None:
                break
            tried += 1

            candidates, tier = await worker.tick(
                task_source=self._flow._profile.task_source,
                record_spend=self.record_spend,
                budget_allows=self.budget_allows,
            )

            if not candidates or not self.can_submit():
                continue

            # Handle promoted tier 1
            if tier == 1 and worker._tier1_owed:
                if self._submit_promoted_for_worker(worker, candidates[0]):
                    worker._tier1_owed = False
                    worker._tier2_completions_since_tier1 = 0
                worker.save_state()
                break

            if tier == 1:
                self._submit_single_for_worker(worker, candidates[0])
                worker.save_state()
                break

            if tier == 2:
                batch = worker._group_candidates(candidates)
                if batch:
                    self._submit_batch_for_worker(
                        worker,
                        batch,
                        recent_categories=worker._tick_recent_categories,
                        resolved_ids=worker._tick_resolved_ids,
                    )
                worker.save_state()
                break

        self._state = "idle"

    # -- Tick dispatch --------------------------------------------------------

    async def _run_heartbeat_tick(self) -> None:
        """Execute one heartbeat cycle via multi-repo workers."""
        if not self.can_submit():
            logger.debug("Heartbeat tick skipped — inflight limit reached")
            return

        self._state = "scanning"
        self._sync_workers()
        await self._run_multi_repo_tick()
        self.save_state()

    # -- Snapshot (dashboard) -------------------------------------------------

    def snapshot(self) -> HeartbeatSnapshotDict:
        """Return a dashboard-safe snapshot of heartbeat state."""
        remaining = max(0.0, self._next_tick_at - time.time())

        # Aggregate scan metadata from workers
        last_scan_at = ""
        last_scan_tier = 0
        candidate_count = 0
        dedup_entry_count = 0
        for worker in self._workers.values():
            if worker._last_scan_at > last_scan_at:
                last_scan_at = worker._last_scan_at
                last_scan_tier = worker._last_scan_tier
            candidate_count += len(worker._candidates)
            dedup_entry_count += len(worker._dedup_memory)

        return {
            "enabled": self._config.heartbeat_enabled,
            "state": self._state,
            "last_scan_at": last_scan_at,
            "last_scan_tier": last_scan_tier,
            "daily_spend_usd": round(self._daily_spend_usd, 4),
            "daily_budget_usd": self._config.heartbeat_daily_budget_usd,
            "inflight_task_ids": list(self._inflight_task_ids),
            "candidate_count": candidate_count,
            "dedup_entry_count": dedup_entry_count,
            "next_tick_seconds": round(remaining),
        }
