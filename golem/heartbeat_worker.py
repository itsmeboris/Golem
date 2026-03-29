"""HeartbeatWorker — per-repo scan logic for self-directed work discovery.

One instance per attached repo.  The HeartbeatManager creates workers and
calls ``worker.tick()`` on each interval.

State is persisted to ``<state_dir>/<path_hash>.json`` so each repo has its
own independent state file.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core.cli_wrapper import CLIConfig, CLIError, CLIType, invoke_cli
from .core.config import DATA_DIR, GolemFlowConfig
from .git_utils import detect_github_remote, is_git_repo
from .types import (
    CoverageCacheDict,
    DedupEntryDict,
    HeartbeatCandidateDict,
)

logger = logging.getLogger("golem.heartbeat_worker")

_24H = 86400  # seconds

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
    """Strip markdown code fences from a JSON response."""
    match = _MD_JSON_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _coerce_task_id(value: Any) -> int | None:
    """Coerce *value* to ``int``, returning ``None`` if not possible."""
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


def _path_hash(path: str) -> str:
    """Return a 12-char hex hash of *path* for stable state filenames."""
    return hashlib.sha256(path.encode()).hexdigest()[:12]


def _content_hash(text: str) -> str:
    """Return a 12-char hex hash of *text* for stable dedup keys."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class HeartbeatWorker:
    """Owns all per-repo state and scan logic for heartbeat self-improvement.

    One instance per attached repo.  The HeartbeatManager creates workers
    and calls ``tick()`` on each heartbeat interval.
    """

    HAIKU_MODEL = "claude-haiku-4-5-20251001"

    _DEFAULT_CONFIDENCE_FLOORS: dict[str, float] = {
        "small": 0.5,
        "medium": 0.6,
        "large": 0.7,
    }

    def __init__(
        self,
        repo_path: str,
        config: GolemFlowConfig,
        state_dir: Path | None = None,
    ) -> None:
        self.repo_path = repo_path
        self._config = config
        self._state_dir = state_dir or (DATA_DIR / "heartbeat")

        # Git detection (cached at init)
        self.is_git = is_git_repo(repo_path)
        self.github_remote = detect_github_remote(repo_path) if self.is_git else None

        # State file: one file per repo, keyed by path hash
        self._state_file = self._state_dir / f"{_path_hash(repo_path)}.json"

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

        # Tier 1 promotion
        self._tier2_completions_since_tier1: int = 0
        self._tier1_owed: bool = False

        # Category-level circuit breaker
        self._category_failures: dict[str, int] = {}
        self._category_cooldown_until: dict[str, str] = {}

        # Tick-scoped cache for git-derived dedup data
        self._tick_resolved_ids: set[str] | None = None
        self._tick_recent_categories: set[str] | None = None

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
        """Remove dedup entries older than TTL."""
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
        """Return GH issue IDs with active heartbeat claims."""
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

    # -- Task completion callbacks --------------------------------------------

    def on_task_completed(self, task_id: int, success: bool) -> None:
        """Callback when a session reaches terminal state."""
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
            "Heartbeat task #%d %s (repo: %s)",
            task_id,
            "completed" if success else "failed",
            self.repo_path,
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

    # -- State persistence ----------------------------------------------------

    def save_state(self) -> None:
        """Persist worker state to disk."""
        self._state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "last_scan_at": self._last_scan_at,
            "last_scan_tier": self._last_scan_tier,
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
        """Load worker state from disk. Missing file = fresh state."""
        if not self._state_file.exists():
            return
        try:
            data: dict[str, Any] = json.loads(
                self._state_file.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load worker state: %s", exc)
            return
        self._last_scan_at = data.get("last_scan_at", "")
        self._last_scan_tier = data.get("last_scan_tier", 0)
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

    # -- Haiku integration ----------------------------------------------------

    async def _call_haiku(
        self, prompt: str, issues_json: str, record_spend: Any
    ) -> Any:
        """Call Haiku for triage via CLI wrapper. Returns parsed JSON or raw string."""
        import asyncio

        full_prompt = f"{prompt}\n\nIssues:\n{issues_json}"
        config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=self.HAIKU_MODEL,
            timeout_seconds=120,
            system_prompt="Respond with raw JSON only. No markdown, no explanation.",
            sandbox_enabled=self._config.sandbox_enabled,
            sandbox_cpu_seconds=self._config.sandbox_cpu_seconds,
            sandbox_memory_gb=self._config.sandbox_memory_gb,
        )

        def _sync_call():
            return invoke_cli(full_prompt, config)

        try:
            result = await asyncio.get_running_loop().run_in_executor(None, _sync_call)
        except CLIError as exc:
            logger.error("Haiku CLI call failed: %s", exc)
            return ""

        record_spend(result.cost_usd)

        text = result.output.get("result", "")
        text = _strip_markdown_json(text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.debug("Haiku response not valid JSON: %.200s", text)
            return text

    # -- Validation -----------------------------------------------------------

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

    def _group_candidates(
        self, candidates: list[HeartbeatCandidateDict]
    ) -> list[HeartbeatCandidateDict]:
        """Group Tier 2 candidates by category, pick the best batch."""
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

    # -- Static helpers -------------------------------------------------------

    @staticmethod
    def _content_hash(text: str) -> str:
        """Return a 12-char hex hash of *text* for stable dedup keys."""
        return hashlib.sha256(text.encode()).hexdigest()[:12]

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

    # -- Tier 2 scanners (deterministic) --------------------------------------

    def _scan_todos(self) -> list[tuple[str, str]]:
        """Find files with new TODO/FIXME since last scan via git log.

        Non-git repos return an empty list.
        Returns ``[(content_hash, description), ...]``.
        """
        if not self.is_git:
            return []

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
                cwd=self.repo_path,
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

        Returns ``[(content_hash, description), ...]``.
        """
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                cwd=self.repo_path,
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
                    cwd=self.repo_path,
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
        """Parse AGENTS.md for antipattern entries.

        Returns ``[(content_hash, description), ...]``.
        """
        agents_path = Path(self.repo_path) / "AGENTS.md"
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

    # -- Git history helpers --------------------------------------------------

    def _get_recent_batch_categories(self) -> set[str]:
        """Return the set of batch categories addressed in recent HEARTBEAT commits.

        Non-git repos return an empty set.
        """
        if not self.is_git:
            return set()

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
                cwd=self.repo_path,
            )
            if result.returncode != 0:
                return set()
            categories: set[str] = set()
            for line in result.stdout.splitlines():
                match = re.search(r"batch:(\S+)", line)
                if match:
                    raw = match.group(1)
                    category = re.split(r"[\s(]", raw)[0]
                    if category:
                        categories.add(category)
            return categories
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.debug("git log (batch categories) failed: %s", exc)
            return set()

    def _get_recently_resolved_ids(self) -> set[str]:
        """Return the set of pitfall/improvement IDs referenced in recent HEARTBEAT commits.

        Non-git repos return an empty set.
        """
        if not self.is_git:
            return set()

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
                cwd=self.repo_path,
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

    # -- Tier 1 ---------------------------------------------------------------

    async def _run_tier1(
        self, task_source: Any, record_spend: Any
    ) -> list[HeartbeatCandidateDict]:
        """Tier 1: discover untagged issues and triage via Haiku.

        Skips if no GitHub remote is configured.
        """
        if not self.github_remote:
            return []

        try:
            issues = task_source.poll_untagged_tasks(
                self._config.projects,
                self._config.detection_tag,
                limit=self._config.heartbeat_candidate_limit,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Tier 1: failed to poll untagged tasks")
            return []

        backend = self._config.profile
        new_issues = []
        for issue in issues:
            key = f"{backend}:{issue['id']}"
            if not self.is_deduped(key):
                new_issues.append(issue)

        if not new_issues:
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

        response = await self._call_haiku(prompt, issues_json, record_spend)
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

    async def _run_tier1_promoted(
        self, task_source: Any, record_spend: Any
    ) -> list[HeartbeatCandidateDict]:
        """Tier 1 scan for promotion: relaxed complexity, no reject dedup.

        Skips if no GitHub remote is configured.
        """
        if not self.github_remote:
            return []

        try:
            issues = task_source.poll_untagged_tasks(
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

        response = await self._call_haiku(prompt, issues_json, record_spend)
        candidates = self._validate_candidates(response, tier=1)

        # Only record candidates in dedup — NOT rejects
        for c in candidates:
            self.record_dedup(c["id"], "candidate")

        return candidates

    # -- Tier 2 ---------------------------------------------------------------

    async def _run_tier2(self, record_spend: Any) -> list[HeartbeatCandidateDict]:
        """Tier 2: scan for self-improvement opportunities."""
        todos = self._scan_todos()
        coverage = self._scan_coverage()
        pitfalls = self._scan_pitfalls()

        all_findings: list[tuple[str, str]] = [*todos, *coverage, *pitfalls]

        # Filter out findings already resolved in recent commits
        resolved_ids = self._get_recently_resolved_ids()
        self._tick_resolved_ids = resolved_ids
        all_findings = [(k, d) for k, d in all_findings if k not in resolved_ids]

        if not all_findings:
            return []

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

        response = await self._call_haiku(
            prompt, json.dumps(keyed_findings, indent=2), record_spend
        )
        candidates = self._validate_candidates(response, tier=2)

        # Filter out candidates whose category is in cooldown or was recently batched
        recent_categories = self._get_recent_batch_categories()
        self._tick_recent_categories = recent_categories
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

    # -- Main entry point -----------------------------------------------------

    async def tick(
        self,
        task_source: Any,
        record_spend: Any,
        budget_allows: Any,
    ) -> tuple[list[HeartbeatCandidateDict], int]:
        """Execute one scan cycle. Returns (candidates, tier).

        Manager handles submission.
        """
        # Clear tick-scoped caches from previous tick
        self._tick_resolved_ids = None
        self._tick_recent_categories = None

        if self._tier1_owed:
            promoted = await self._run_tier1_promoted(task_source, record_spend)
            if promoted:
                self._last_scan_tier = 1
                self._last_scan_at = datetime.now(timezone.utc).isoformat()
                return promoted, 1
            if not budget_allows():
                self._last_scan_at = datetime.now(timezone.utc).isoformat()
                return [], 0
            candidates = await self._run_tier2(record_spend)
            if candidates:
                self._last_scan_tier = 2
                self._last_scan_at = datetime.now(timezone.utc).isoformat()
                return candidates, 2
            self._last_scan_at = datetime.now(timezone.utc).isoformat()
            return [], 0

        if not budget_allows():
            return [], 0

        candidates = await self._run_tier1(task_source, record_spend)
        if candidates:
            self._last_scan_tier = 1
            self._last_scan_at = datetime.now(timezone.utc).isoformat()
            return candidates, 1

        candidates = await self._run_tier2(record_spend)
        if candidates:
            self._last_scan_tier = 2
            self._last_scan_at = datetime.now(timezone.utc).isoformat()
            return candidates, 2

        # No candidates found, but still record scan time
        self._last_scan_at = datetime.now(timezone.utc).isoformat()
        return [], 0
