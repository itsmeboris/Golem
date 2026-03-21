"""Heartbeat — self-directed work discovery when Golem is idle.

Runs on its own async timer, discovers untagged issues (Tier 1) and
self-improvement opportunities (Tier 2), and submits work via
``GolemFlow.submit_task()``.

State is persisted to ``data/heartbeat_state.json``.  Budget limits are
read from ``GolemFlowConfig`` (read-only at runtime).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.cli_wrapper import CLIConfig, CLIError, CLIType, invoke_cli
from .core.config import DATA_DIR, GolemFlowConfig
from .orchestrator import TaskSessionState
from .types import (
    CoverageCacheDict,
    DedupEntryDict,
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

_MD_JSON_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?\s*```", re.DOTALL)

_DEDUP_REQUIRED_KEYS: frozenset[str] = frozenset({"evaluated_at", "verdict"})
_CANDIDATE_REQUIRED_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "subject",
        "body",
        "automatable",
        "confidence",
        "complexity",
        "reason",
        "tier",
    }
)


def _strip_markdown_json(text: str) -> str:
    """Strip markdown code fences from a JSON response.

    Models often wrap JSON in ```json ... ``` blocks.  This extracts
    the inner content so ``json.loads`` can parse it.
    """
    match = _MD_JSON_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


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
    Mutable state (spend, dedup, inflight) is persisted to *state_dir*.
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

        # Dedup memory — keyed by "backend:id" string
        self._dedup_memory: dict[str, DedupEntryDict] = {}

        # Candidates — overwritten each scan
        self._candidates: list[HeartbeatCandidateDict] = []

        # Coverage cache
        self._coverage_cache: CoverageCacheDict | None = None

        # Scan metadata
        self._last_scan_at: str = ""
        self._last_scan_tier: int = 0

        # Runtime state
        self._state: str = (
            "idle"  # idle | scanning | submitted | paused | budget_exhausted
        )
        self._loop_task: Any = None  # asyncio.Task, set in start()
        self._flow: Any = None  # set in start()
        self._next_tick_at: float = 0.0  # epoch when next tick fires
        self._trigger_event: Any = None  # asyncio.Event for force-trigger

        # Tier 1 promotion
        self._tier2_completions_since_tier1: int = 0
        self._tier1_owed: bool = False

        # Category-level circuit breaker
        self._category_failures: dict[str, int] = {}
        self._category_cooldown_until: dict[str, str] = {}

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

    # -- Dedup ----------------------------------------------------------------

    def is_deduped(self, issue_key: str) -> bool:
        """Return True if *issue_key* has already been evaluated."""
        return issue_key in self._dedup_memory

    def record_dedup(
        self, issue_key: str, verdict: str, task_id: int | None = None
    ) -> None:
        """Record an evaluation in dedup memory."""
        entry: DedupEntryDict = {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict,
        }
        if task_id is not None:
            entry["task_id"] = task_id
        self._dedup_memory[issue_key] = entry

    def _prune_dedup(self) -> None:
        """Remove dedup entries older than TTL.

        ``not_automatable`` entries use a shorter TTL so GH issues get
        re-evaluated as Golem's capabilities improve.
        """
        default_ttl = self._config.heartbeat_dedup_ttl_days * _24H
        na_ttl = self._config.heartbeat_not_automatable_ttl_days * _24H
        now = time.time()
        to_remove = []
        for key, entry in self._dedup_memory.items():
            try:
                evaluated = datetime.fromisoformat(entry["evaluated_at"])
                ttl = (
                    na_ttl if entry.get("verdict") == "not_automatable" else default_ttl
                )
                if evaluated.timestamp() < now - ttl:
                    to_remove.append(key)
            except (KeyError, ValueError):
                to_remove.append(key)
        for key in to_remove:
            del self._dedup_memory[key]

    def get_claimed_issue_ids(self) -> set[int]:
        """Return GH issue IDs with active heartbeat claims.

        Extracts numeric IDs from dedup keys like ``"github:40"`` where the
        verdict is still active (submitted, candidate, or promoted).
        """
        active_verdicts = {"submitted", "candidate", "promoted"}
        ids: set[int] = set()
        for key, entry in self._dedup_memory.items():
            if entry.get("verdict") not in active_verdicts:
                continue
            parts = key.split(":", 1)
            if len(parts) != 2 or parts[0] == "improvement":
                continue
            try:
                ids.add(int(parts[1]))
            except ValueError:
                pass
        return ids

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

        # Collect categories from dedup entries for this task
        task_categories: set[str] = set()
        for key, entry in self._dedup_memory.items():
            if entry.get("task_id") == task_id:
                entry["verdict"] = "completed" if success else "failed"
                task_categories.add(self._extract_category_from_id(key))
        task_categories.discard("")

        logger.info(
            "Heartbeat task #%d %s", task_id, "completed" if success else "failed"
        )

        # Category circuit breaker
        for cat in task_categories:
            if success:
                self._category_failures.pop(cat, None)
                self._category_cooldown_until.pop(cat, None)
            else:
                count = self._category_failures.get(cat, 0) + 1
                self._category_failures[cat] = count
                threshold = self._config.heartbeat_category_failure_threshold
                if count >= threshold:
                    cooldown_h = self._config.heartbeat_category_cooldown_hours
                    until = datetime.now(timezone.utc).timestamp() + cooldown_h * 3600
                    until_iso = datetime.fromtimestamp(
                        until, tz=timezone.utc
                    ).isoformat()
                    self._category_cooldown_until[cat] = until_iso
                    logger.warning(
                        "Category %r hit %d failures — cooldown until %s",
                        cat,
                        count,
                        until_iso,
                    )

        # Tier 1 promotion counter — count Tier 2 successes
        if success:
            is_tier2 = any(
                key.startswith("improvement:")
                for key, entry in self._dedup_memory.items()
                if entry.get("task_id") == task_id
            )
            if is_tier2:
                self._tier2_completions_since_tier1 += 1
                if (
                    self._tier2_completions_since_tier1
                    >= self._config.heartbeat_tier1_every_n
                ):
                    self._tier1_owed = True
                    logger.info(
                        "Tier 1 promotion owed after %d Tier 2 completions",
                        self._tier2_completions_since_tier1,
                    )

    def is_category_cooled_down(self, category: str) -> bool:
        """Return True if *category* is in a failure cooldown period."""
        until_iso = self._category_cooldown_until.get(category)
        if not until_iso:
            return False
        try:
            until_ts = datetime.fromisoformat(until_iso).timestamp()
        except ValueError:
            return False
        if time.time() >= until_ts:
            # Cooldown expired — clear state
            self._category_cooldown_until.pop(category, None)
            self._category_failures.pop(category, None)
            return False
        return True

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

    # -- State persistence ----------------------------------------------------

    def save_state(self) -> None:
        """Persist heartbeat state to disk."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "last_scan_at": self._last_scan_at,
            "last_scan_tier": self._last_scan_tier,
            "daily_spend_usd": self._daily_spend_usd,
            "daily_spend_reset_at": self._daily_spend_reset_at,
            "inflight_task_ids": self._inflight_task_ids,
            "dedup_memory": self._dedup_memory,
            "coverage_cache": (
                self._coverage_cache if self._coverage_cache is not None else {}
            ),
            "candidates": self._candidates,
            "tier2_completions_since_tier1": self._tier2_completions_since_tier1,
            "tier1_owed": self._tier1_owed,
            "category_failures": self._category_failures,
            "category_cooldown_until": self._category_cooldown_until,
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
        self._last_scan_at = data.get("last_scan_at", "")
        self._last_scan_tier = data.get("last_scan_tier", 0)
        self._daily_spend_usd = data.get("daily_spend_usd", 0.0)
        self._daily_spend_reset_at = data.get("daily_spend_reset_at", time.time())
        raw_ids = data.get("inflight_task_ids", [])
        coerced: list[int] = []
        for raw in raw_ids:
            tid = _coerce_task_id(raw)
            if tid is not None:
                coerced.append(tid)
        self._inflight_task_ids = coerced
        raw_dedup = data.get("dedup_memory", {})
        if not isinstance(raw_dedup, dict):
            logger.warning(
                "dedup_memory is not a dict (%r) — resetting to empty",
                type(raw_dedup).__name__,
            )
            raw_dedup = {}
        validated_dedup: dict[str, DedupEntryDict] = {}
        for key, value in raw_dedup.items():
            if not isinstance(value, dict) or not _DEDUP_REQUIRED_KEYS.issubset(
                value.keys()
            ):
                logger.warning(
                    "Dropping invalid dedup_memory entry %r — missing required keys",
                    key,
                )
            else:
                validated_dedup[key] = value
        self._dedup_memory = validated_dedup
        raw_cache = data.get("coverage_cache")
        if (
            isinstance(raw_cache, dict)
            and "commit_hash" in raw_cache
            and "ran_at" in raw_cache
            and "uncovered_modules" in raw_cache
        ):
            self._coverage_cache = raw_cache
        else:
            self._coverage_cache = None
        raw_candidates = data.get("candidates", [])
        validated_candidates: list[HeartbeatCandidateDict] = []
        for i, entry in enumerate(raw_candidates):
            if not isinstance(entry, dict) or not _CANDIDATE_REQUIRED_KEYS.issubset(
                entry.keys()
            ):
                logger.warning(
                    "Dropping invalid candidates entry at index %d — missing required keys",
                    i,
                )
            else:
                validated_candidates.append(entry)
        self._candidates = validated_candidates
        self._prune_dedup()
        self._tier2_completions_since_tier1 = data.get(
            "tier2_completions_since_tier1", 0
        )
        self._tier1_owed = data.get("tier1_owed", False)
        self._category_failures = data.get("category_failures", {})
        self._category_cooldown_until = data.get("category_cooldown_until", {})

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

    # -- Haiku integration ----------------------------------------------------

    HAIKU_MODEL = "claude-haiku-4-5-20251001"

    async def _call_haiku(self, prompt: str, issues_json: str) -> Any:
        """Call Haiku for triage via CLI wrapper. Returns parsed JSON or raw string."""
        import asyncio

        full_prompt = f"{prompt}\n\nIssues:\n{issues_json}"
        config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=self.HAIKU_MODEL,
            timeout_seconds=120,
            system_prompt="Respond with raw JSON only. No markdown, no explanation.",
        )

        def _sync_call():
            return invoke_cli(full_prompt, config)

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _sync_call)
        except CLIError as exc:
            logger.error("Haiku CLI call failed: %s", exc)
            return ""

        self.record_spend(result.cost_usd)

        text = result.output.get("result", "")
        text = _strip_markdown_json(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Haiku response not valid JSON: %.200s", text)
            return text

    _DEFAULT_CONFIDENCE_FLOORS: dict[str, float] = {
        "small": 0.5,
        "medium": 0.6,
        "large": 0.7,
    }

    def _validate_candidates(
        self,
        raw: Any,
        valid_complexities: tuple[str, ...] = ("small", "medium", "large"),
        confidence_floors: dict[str, float] | None = None,
        tier: int = 0,
    ) -> list[HeartbeatCandidateDict]:
        """Validate and filter Haiku response per the output contract."""
        if not isinstance(raw, dict) or "candidates" not in raw:
            logger.warning("Haiku returned invalid response structure")
            return []

        floors = confidence_floors or self._DEFAULT_CONFIDENCE_FLOORS

        valid: list[HeartbeatCandidateDict] = []
        for c in raw["candidates"]:
            if not isinstance(c, dict):
                continue
            if not c.get("automatable", False):
                continue

            # Validate id is a non-empty string
            candidate_id = c.get("id", "")
            if not isinstance(candidate_id, str) or not candidate_id:
                logger.warning("Candidate has missing or empty id — skipping")
                continue

            conf = c.get("confidence", 0.0)
            if not isinstance(conf, (int, float)):
                continue
            conf = max(0.0, min(1.0, float(conf)))

            complexity = c.get("complexity", "")
            if complexity not in ("small", "medium", "large"):
                continue
            if complexity not in valid_complexities:
                continue

            # Category: use explicit field, fall back to ID prefix
            category = c.get("category", "")
            if not category or not isinstance(category, str):
                category = self._extract_category_from_id(candidate_id)
            if not category:
                logger.warning(
                    "Candidate %r has no category and unparseable ID — skipping",
                    candidate_id,
                )
                continue
            category = category.lower()

            reason = c.get("reason", "")
            min_conf = floors.get(complexity, 0.7)
            if conf >= min_conf:
                validated: HeartbeatCandidateDict = {
                    "id": candidate_id,
                    "subject": c.get("subject", reason),
                    "body": c.get("body", reason),
                    "automatable": True,
                    "confidence": conf,
                    "complexity": complexity,
                    "reason": reason,
                    "tier": tier,
                    "category": category,
                }
                valid.append(validated)

        return sorted(valid, key=lambda x: x["confidence"], reverse=True)

    @staticmethod
    def _extract_category_from_id(candidate_id: str) -> str:
        """Extract category from candidate ID.

        - ``improvement:<category>:<name>`` -> ``<category>``
        - ``<backend>:<id>`` (e.g. ``github:42``) -> ``<backend>``
        """
        parts = candidate_id.split(":")
        if len(parts) >= 3 and parts[0] == "improvement":
            return parts[1]
        if len(parts) >= 2:
            return parts[0]
        return ""

    def _group_candidates(
        self, candidates: list[HeartbeatCandidateDict]
    ) -> list[HeartbeatCandidateDict]:
        """Group Tier 2 candidates by category, pick the best batch.

        1. Group by ``category`` field
        2. Cap each group at ``heartbeat_batch_size``
        3. Return the largest group (ties broken by highest avg confidence)
        """
        if not candidates:
            return []

        groups: dict[str, list[HeartbeatCandidateDict]] = {}
        for c in candidates:
            cat = c.get("category", "")
            if not cat:
                cat = self._extract_category_from_id(c["id"])
            if not cat:
                logger.warning(
                    "Candidate %r has no category — skipping for batching",
                    c["id"],
                )
                continue
            groups.setdefault(cat, []).append(c)

        if not groups:
            return []

        batch_size = self._config.heartbeat_batch_size

        # Pick largest group; tie-break by highest average confidence
        best_cat = max(
            groups,
            key=lambda cat: (
                min(len(groups[cat]), batch_size),
                sum(c["confidence"] for c in groups[cat]) / len(groups[cat]),
            ),
        )

        return groups[best_cat][:batch_size]

    # -- Tier 1 ---------------------------------------------------------------

    async def _run_tier1(self) -> list[HeartbeatCandidateDict]:
        """Tier 1: discover untagged issues and triage via Haiku."""
        try:
            issues = self._flow._profile.task_source.poll_untagged_tasks(
                self._config.projects,
                self._config.detection_tag,
                limit=self._config.heartbeat_candidate_limit,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Tier 1: failed to poll untagged tasks")
            return []

        # Filter already-evaluated issues
        backend = self._config.profile
        new_issues = []
        for issue in issues:
            key = f"{backend}:{issue['id']}"
            if not self.is_deduped(key):
                new_issues.append(issue)

        if not new_issues:
            return []

        if not self.budget_allows():
            return []

        issues_json = json.dumps(
            [
                {"id": i["id"], "subject": i["subject"], "body": i.get("body", "")}
                for i in new_issues
            ],
            indent=2,
        )
        prompt = (
            "You are triaging issues for Golem, an autonomous coding agent that can:\n"
            "- Read and modify any file in the codebase\n"
            "- Run the full test suite (pytest with 100% coverage gate)\n"
            "- Run linters (black, pylint)\n"
            "- Make multi-file changes across the project\n"
            "- Create new modules and test files\n"
            "- Work with Python, JavaScript, HTML/CSS, shell scripts\n\n"
            "Be generous in assessing automatability. If an issue can be decomposed "
            "into concrete code changes, mark it automatable even if it requires "
            "significant work. Only mark not_automatable if the issue requires human "
            "judgment that cannot be derived from the codebase (UX design decisions, "
            "unspecified business requirements, external service integration without "
            "docs).\n\n"
            "Respond with JSON matching this schema:\n"
            '{"candidates": [{"id": "' + backend + ':<number>", "automatable": bool, '
            '"confidence": float (0-1), "complexity": "small"|"medium"|"large", '
            '"reason": "..."}]}'
        )

        response = await self._call_haiku(prompt, issues_json)
        candidates = self._validate_candidates(response, tier=1)

        # Record all evaluated issues in dedup
        evaluated_ids = {f"{backend}:{i['id']}" for i in new_issues}
        candidate_ids = {c["id"] for c in candidates}
        for key in evaluated_ids:
            if key in candidate_ids:
                self.record_dedup(key, "candidate")
            else:
                self.record_dedup(key, "not_automatable")

        return candidates

    async def _run_tier1_promoted(self) -> list[HeartbeatCandidateDict]:
        """Tier 1 scan for promotion: relaxed complexity, no reject dedup."""
        try:
            issues = self._flow._profile.task_source.poll_untagged_tasks(
                self._config.projects,
                self._config.detection_tag,
                limit=self._config.heartbeat_candidate_limit,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Promoted Tier 1: failed to poll untagged tasks")
            return []

        backend = self._config.profile
        new_issues = [
            issue for issue in issues if not self.is_deduped(f"{backend}:{issue['id']}")
        ]

        if not new_issues:
            return []

        if not self.budget_allows():
            return []

        issues_json = json.dumps(
            [
                {"id": i["id"], "subject": i["subject"], "body": i.get("body", "")}
                for i in new_issues
            ],
            indent=2,
        )
        prompt = (
            "You are triaging issues for Golem, an autonomous coding agent that can:\n"
            "- Read and modify any file in the codebase\n"
            "- Run the full test suite (pytest with 100% coverage gate)\n"
            "- Run linters (black, pylint)\n"
            "- Make multi-file changes across the project\n"
            "- Create new modules and test files\n"
            "- Work with Python, JavaScript, HTML/CSS, shell scripts\n\n"
            "Be generous in assessing automatability. If an issue can be decomposed "
            "into concrete code changes, mark it automatable even if it requires "
            "significant work. Only mark not_automatable if the issue requires human "
            "judgment that cannot be derived from the codebase (UX design decisions, "
            "unspecified business requirements, external service integration without "
            "docs).\n\n"
            "Respond with JSON matching this schema:\n"
            '{"candidates": [{"id": "' + backend + ':<number>", "automatable": bool, '
            '"confidence": float (0-1), "complexity": "small"|"medium"|"large", '
            '"reason": "..."}]}'
        )

        response = await self._call_haiku(prompt, issues_json)
        candidates = self._validate_candidates(response, tier=1)

        # Only record candidates in dedup — NOT rejects
        for c in candidates:
            self.record_dedup(c["id"], "candidate")

        return candidates

    # -- Tier 2 scanners (deterministic) --------------------------------------

    @staticmethod
    def _content_hash(text: str) -> str:
        """Return a 12-char hex hash of *text* for stable dedup keys."""
        return hashlib.sha256(text.encode()).hexdigest()[:12]

    def _scan_todos(self) -> list[tuple[str, str]]:
        """Find files with new TODO/FIXME since last scan via git log.

        Returns ``[(content_hash, description), ...]`` with stable keys
        derived from the finding content rather than Haiku-invented names.
        """
        import subprocess

        work_dir = self._config.default_work_dir or None
        since = self._last_scan_at if self._last_scan_at else "7.days.ago"
        try:
            result = subprocess.run(
                [
                    "git",
                    "log",
                    f"--since={since}",
                    "--diff-filter=A",
                    "-G",
                    "TODO|FIXME",
                    "--name-only",
                    "--format=",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                cwd=work_dir,
            )
            if result.returncode != 0:
                return []
            files = sorted(
                set(f.strip() for f in result.stdout.strip().split("\n") if f.strip())
            )
            findings = []
            for f in files:
                key = f"todo:{self._content_hash(f)}"
                if not self.is_deduped(key):
                    findings.append((key, f"TODO/FIXME found in: {f}"))
            return findings
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("git diff-tree scan failed: %s", exc)
            return []

    def _scan_coverage(self) -> list[tuple[str, str]]:
        """Return uncovered modules. Uses cached results keyed by HEAD hash.

        Returns ``[(content_hash, description), ...]`` with stable keys.
        """
        import subprocess

        work_dir = self._config.default_work_dir or None
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                cwd=work_dir,
            ).stdout.strip()
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("git rev-parse HEAD failed: %s", exc)
            return []

        uncovered: list[str] | None = None
        # Use cache if same commit and ran within the last hour
        if self._coverage_cache is not None:
            cached_hash = self._coverage_cache["commit_hash"]
            cached_at = self._coverage_cache["ran_at"]
            if cached_hash == head and cached_at:
                try:
                    ran_ts = datetime.fromisoformat(cached_at).timestamp()
                    if time.time() - ran_ts < 3600:
                        uncovered = self._coverage_cache["uncovered_modules"]
                except ValueError as exc:
                    logger.debug(
                        "Invalid cached timestamp, re-running coverage: %s", exc
                    )

        if uncovered is None:
            # Run pytest --cov with timeout
            try:
                result = subprocess.run(
                    [
                        "pytest",
                        "golem/tests/",
                        "--cov=golem",
                        "--cov-report=term-missing",
                        "-q",
                        "--no-header",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                    cwd=work_dir,
                )
            except (OSError, subprocess.TimeoutExpired):
                logger.warning("Tier 2: coverage scan timed out")
                return []

            # Parse output for modules below 100%
            uncovered = []
            for line in result.stdout.split("\n"):
                if "%" in line and "100%" not in line:
                    parts = line.split()
                    if parts and parts[0].startswith("golem/"):
                        uncovered.append(parts[0])

            self._coverage_cache = {
                "commit_hash": head,
                "ran_at": datetime.now(timezone.utc).isoformat(),
                "uncovered_modules": uncovered,
            }

        # Filter through dedup and return keyed findings
        findings = []
        for mod in uncovered:
            key = f"coverage:{self._content_hash(mod)}"
            if not self.is_deduped(key):
                findings.append((key, f"Module below 100% coverage: {mod}"))
        return findings

    def _scan_pitfalls(self) -> list[tuple[str, str]]:
        """Parse AGENTS.md for antipattern entries under '## Recurring Antipatterns'.

        Real entries look like:
          - **Empty exception handler**: description <!-- seen:4 last:2026-03-15 -->

        Returns ``[(content_hash, description), ...]`` with stable keys.
        """

        work_dir = self._config.default_work_dir or "."
        agents_path = Path(work_dir) / "AGENTS.md"
        if not agents_path.exists():
            return []

        try:
            content = agents_path.read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug("Failed to read AGENTS.md: %s", exc)
            return []

        pitfalls: list[tuple[str, str]] = []
        in_section = False
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## Recurring Antipatterns"):
                in_section = True
                continue
            if in_section and stripped.startswith("## "):
                break  # next section
            if not in_section:
                continue
            if stripped.startswith("- ") and "<!-- seen:" in stripped:
                clean = re.sub(r"\s*<!--.*?-->", "", stripped)
                key = f"pitfall:{self._content_hash(clean)}"
                if not self.is_deduped(key):
                    pitfalls.append((key, f"Unresolved pitfall: {clean}"))
        return pitfalls

    # -- Git history helpers ---------------------------------------------------

    def _get_recent_batch_categories(self) -> set[str]:
        """Return the set of batch categories addressed in recent HEARTBEAT commits.

        Runs ``git log --oneline -5 --grep=[HEARTBEAT] batch:`` and parses the
        category name from each matching line (e.g. ``batch:dead-code``).
        Returns an empty set on any subprocess failure.
        """
        import subprocess

        work_dir = self._config.default_work_dir or None
        try:
            result = subprocess.run(
                [
                    "git",
                    "log",
                    "--oneline",
                    f"-{self._config.heartbeat_recent_commits_lookback}",
                    "--fixed-strings",
                    "--grep=[HEARTBEAT] batch:",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                cwd=work_dir,
            )
            if result.returncode != 0:
                return set()
            categories: set[str] = set()
            for line in result.stdout.splitlines():
                match = re.search(r"batch:(\S+)", line)
                if match:
                    # Strip trailing punctuation like " (1 items)" prefix spaces
                    raw = match.group(1)
                    # Remove trailing non-category characters such as spaces or parens
                    # The category ends at the first space or paren
                    category = re.split(r"[\s(]", raw)[0]
                    if category:
                        categories.add(category)
            return categories
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("git log (batch categories) failed: %s", exc)
            return set()

    def _get_recently_resolved_ids(self) -> set[str]:
        """Return the set of pitfall/improvement IDs referenced in recent HEARTBEAT commits.

        Runs ``git log -5 --grep=[HEARTBEAT] --format=%B`` and extracts all
        IDs matching ``pitfall:<hash>`` or ``improvement:<cat>:<name>`` patterns.
        Returns an empty set on any subprocess failure.
        """
        import subprocess

        work_dir = self._config.default_work_dir or None
        try:
            result = subprocess.run(
                [
                    "git",
                    "log",
                    f"-{self._config.heartbeat_recent_commits_lookback}",
                    "--fixed-strings",
                    "--grep=[HEARTBEAT]",
                    "--format=%B",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
                cwd=work_dir,
            )
            if result.returncode != 0:
                return set()
            ids: set[str] = set()
            pattern = re.compile(r"\[?(pitfall:[a-f0-9]+|improvement:[^\]\s]+)\]?")
            for match in pattern.finditer(result.stdout):
                ids.add(match.group(1))
            return ids
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("git log (recently resolved IDs) failed: %s", exc)
            return set()

    # -- Tier 2 ---------------------------------------------------------------

    async def _run_tier2(self) -> list[HeartbeatCandidateDict]:
        """Tier 2: scan for self-improvement opportunities."""
        todos = self._scan_todos()
        coverage = self._scan_coverage()
        pitfalls = self._scan_pitfalls()

        # All scanners now return (key, description) tuples
        all_findings: list[tuple[str, str]] = [*todos, *coverage, *pitfalls]

        # Filter out findings already resolved in recent commits
        resolved_ids = self._get_recently_resolved_ids()
        all_findings = [(k, d) for k, d in all_findings if k not in resolved_ids]

        if not all_findings:
            return []

        if not self.budget_allows():
            return []

        # Build keyed findings list for Haiku, including stable content hashes
        keyed_findings = [{"key": key, "finding": desc} for key, desc in all_findings]

        prompt = (
            "Rank these improvement opportunities by impact and estimated effort. "
            "For each, assess if a coding agent can complete it autonomously.\n"
            "Respond with JSON matching this schema:\n"
            '{"candidates": [{"id": "<key from input>", '
            '"category": "<category>", "automatable": bool, '
            '"confidence": float (0-1), "complexity": "small"|"medium"|"large", '
            '"reason": "..."}]}\n'
            "IMPORTANT: Use the exact 'key' from the input as the 'id' field. "
            "Always include the 'category' field (e.g. 'error-handling', "
            "'reliability', 'coverage', 'dead-code'). This is used for batching."
        )

        response = await self._call_haiku(prompt, json.dumps(keyed_findings, indent=2))
        candidates = self._validate_candidates(response, tier=2)

        # Filter out candidates whose category is in cooldown, was recently batched,
        # or ID was recently resolved
        recent_categories = self._get_recent_batch_categories()
        if recent_categories:
            before = len(candidates)
            candidates = [
                c for c in candidates if c.get("category", "") not in recent_categories
            ]
            filtered = before - len(candidates)
            if filtered:
                logger.info(
                    "Tier 2: filtered %d candidate(s) whose category was recently batched",
                    filtered,
                )
        return [
            c
            for c in candidates
            if not self.is_category_cooled_down(c.get("category", ""))
            and c["id"] not in resolved_ids
        ]

    async def _run_heartbeat_tick(self) -> None:
        """Execute one heartbeat cycle: promotion -> Tier 1 -> Tier 2 -> submit."""
        if not self.can_submit():
            logger.debug("Heartbeat tick skipped — inflight limit reached")
            return

        self._state = "scanning"

        # Tier 1 promotion: when owed, run relaxed scan first
        if self._tier1_owed:
            promoted = await self._run_tier1_promoted()
            if promoted:
                self._submit_promoted(promoted[0])
                self.save_state()
            # Skip normal Tier 1 (promoted scan already ran with relaxed criteria)
            candidates = await self._run_tier2()
            if candidates:
                self._last_scan_tier = 2
        else:
            # Normal flow: Tier 1 -> Tier 2
            candidates = await self._run_tier1()
            if candidates:
                self._last_scan_tier = 1
            else:
                candidates = await self._run_tier2()
                if candidates:
                    self._last_scan_tier = 2

        self._last_scan_at = datetime.now(timezone.utc).isoformat()
        self._candidates = candidates

        if not candidates or not self.can_submit():
            self._state = "idle"
            self.save_state()
            return

        # Tier 2: batch by category; Tier 1: single submission
        if self._last_scan_tier == 2:
            batch = self._group_candidates(candidates)
            if not batch:
                self._state = "idle"
                self.save_state()
                return
            self._submit_batch(batch)
        else:
            self._submit_single(candidates[0])

        self.save_state()

    def _submit_single(self, candidate: HeartbeatCandidateDict) -> None:
        """Submit a single candidate as a heartbeat task."""
        subject = f"[HEARTBEAT] {candidate['subject']}"
        body = candidate["body"]
        prompt = (
            f"{body}\n\n"
            f"Source: heartbeat tier {self._last_scan_tier}\n"
            f"Confidence: {candidate['confidence']}\n"
            f"Complexity: {candidate['complexity']}\n"
            f"Reason: {candidate['reason']}"
        )

        try:
            is_issue = not candidate["id"].startswith("improvement:")
            result = self._flow.submit_task(
                prompt=prompt, subject=subject, issue_mode=is_issue
            )
            task_id = result["task_id"]
            coerced = _coerce_task_id(task_id)
            if coerced is None:
                logger.error("submit_task returned non-integer task_id: %r", task_id)
                self._state = "idle"
                return
            task_id = coerced
            self._inflight_task_ids.append(task_id)
            self.record_dedup(candidate["id"], "submitted", task_id=task_id)
            self._state = "submitted"
            logger.info(
                "Heartbeat submitted task #%d: %s (tier=%d, confidence=%.2f)",
                task_id,
                subject,
                self._last_scan_tier,
                candidate["confidence"],
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Heartbeat failed to submit task")
            self._state = "idle"

    def _submit_batch(self, batch: list[HeartbeatCandidateDict]) -> None:
        """Submit a batch of Tier 2 candidates as a single heartbeat task."""
        category = batch[0].get("category", "improvement")

        # Skip if this category was already addressed in a recent commit
        recent_categories = self._get_recent_batch_categories()
        if category in recent_categories:
            logger.warning(
                "Skipping batch submission — category %r was recently addressed",
                category,
            )
            return

        subject = f"[HEARTBEAT] batch:{category} ({len(batch)} items)"

        items_text = []
        for i, c in enumerate(batch, 1):
            items_text.append(f"{i}. [{c['id']}] {c['reason']}")

        prompt = (
            f"Fix the following {len(batch)} related issues "
            f"(category: {category}):\n\n"
            + "\n".join(items_text)
            + f"\n\nSource: heartbeat tier {self._last_scan_tier}\n"
            f"Batch size: {len(batch)}\n"
            f"Category: {category}"
        )

        try:
            result = self._flow.submit_task(prompt=prompt, subject=subject)
            task_id = result["task_id"]
            coerced = _coerce_task_id(task_id)
            if coerced is None:
                logger.error("submit_task returned non-integer task_id: %r", task_id)
                self._state = "idle"
                return
            task_id = coerced
            self._inflight_task_ids.append(task_id)
            for c in batch:
                self.record_dedup(c["id"], "submitted", task_id=task_id)
            self._state = "submitted"
            logger.info(
                "Heartbeat submitted batch task #%d: %s (tier=%d, items=%d)",
                task_id,
                subject,
                self._last_scan_tier,
                len(batch),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Heartbeat failed to submit batch task")
            self._state = "idle"

    def _submit_promoted(self, candidate: HeartbeatCandidateDict) -> None:
        """Submit a promoted GH issue directly, bypassing heartbeat limits."""
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
                prompt=prompt, subject=subject, issue_mode=True
            )
            task_id = _coerce_task_id(result["task_id"])
            if task_id is None:
                logger.error(
                    "submit_task returned non-integer task_id: %r",
                    result["task_id"],
                )
                return
            self._inflight_task_ids.append(task_id)
            self.record_dedup(candidate["id"], "promoted", task_id=task_id)
            self._tier1_owed = False
            self._tier2_completions_since_tier1 = 0
            logger.info(
                "Promoted GH issue submitted: %s (confidence=%.2f)",
                subject,
                candidate["confidence"],
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to submit promoted GH issue")

    # -- Snapshot (dashboard) -------------------------------------------------

    def snapshot(self) -> HeartbeatSnapshotDict:
        """Return a dashboard-safe snapshot of heartbeat state."""
        remaining = max(0.0, self._next_tick_at - time.time())
        return {
            "enabled": self._config.heartbeat_enabled,
            "state": self._state,
            "last_scan_at": self._last_scan_at,
            "last_scan_tier": self._last_scan_tier,
            "daily_spend_usd": round(self._daily_spend_usd, 4),
            "daily_budget_usd": self._config.heartbeat_daily_budget_usd,
            "inflight_task_ids": list(self._inflight_task_ids),
            "candidate_count": len(self._candidates),
            "dedup_entry_count": len(self._dedup_memory),
            "next_tick_seconds": round(remaining),
        }
