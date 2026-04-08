"""Self-update manager — monitors Golem's own repo for changes.

Polls a configured branch, reviews changes with a Claude agent,
runs verification in a worktree, and triggers a daemon reload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from golem.sandbox import make_sandbox_preexec

logger = logging.getLogger(__name__)

_STATE_FILE = "self_update_state.json"
_MAX_HISTORY = 50
_MAX_DIFF_CHARS = 50_000


class SelfUpdateManager:
    """Async manager that polls for code updates and applies them."""

    def __init__(
        self,
        config: Any,  # GolemFlowConfig
        state_dir: Path | None = None,
        reload_event: asyncio.Event | None = None,
    ) -> None:
        self._config = config
        self._state_dir = Path(state_dir or "data")
        self._reload_event = reload_event
        self._task: asyncio.Task | None = None

        # Persisted state
        self._last_checked_sha: str = ""
        self._last_check_timestamp: str = ""
        self._last_update_sha: str = ""
        self._last_update_timestamp: str = ""
        self._last_review_verdict: str = ""
        self._last_review_reasoning: str = ""
        self._pre_update_sha: str | None = None
        self._last_startup_timestamp: str | None = None
        self._consecutive_crash_count: int = 0
        self._update_history: list[dict[str, Any]] = []
        # Internal
        self._verified_sha: str | None = None

    # -- state persistence --

    @property
    def _state_path(self) -> Path:
        return self._state_dir / _STATE_FILE

    def save_state(self) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        state = {
            "last_checked_sha": self._last_checked_sha,
            "last_check_timestamp": self._last_check_timestamp,
            "last_update_sha": self._last_update_sha,
            "last_update_timestamp": self._last_update_timestamp,
            "last_review_verdict": self._last_review_verdict,
            "last_review_reasoning": self._last_review_reasoning,
            "pre_update_sha": self._pre_update_sha,
            "last_startup_timestamp": self._last_startup_timestamp,
            "consecutive_crash_count": self._consecutive_crash_count,
            "update_history": self._update_history[-_MAX_HISTORY:],
        }
        with open(self._state_path, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)

    def load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            with open(self._state_path, "r", encoding="utf-8") as fh:
                state = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Cannot load self-update state: %s", exc)
            return
        self._last_checked_sha = state.get("last_checked_sha", "")
        self._last_check_timestamp = state.get("last_check_timestamp", "")
        self._last_update_sha = state.get("last_update_sha", "")
        self._last_update_timestamp = state.get("last_update_timestamp", "")
        self._last_review_verdict = state.get("last_review_verdict", "")
        self._last_review_reasoning = state.get("last_review_reasoning", "")
        self._pre_update_sha = state.get("pre_update_sha")
        self._last_startup_timestamp = state.get("last_startup_timestamp")
        self._consecutive_crash_count = state.get("consecutive_crash_count", 0)
        self._update_history = state.get("update_history", [])[-_MAX_HISTORY:]

    # -- lifecycle --

    def start(self) -> None:
        self.load_state()
        self._check_crash_loop()
        self._last_startup_timestamp = datetime.now(timezone.utc).isoformat()
        self.save_state()
        self._task = asyncio.create_task(self._update_loop())
        logger.info(
            "SelfUpdateManager started (branch=%s, strategy=%s)",
            self._config.self_update_branch,
            self._config.self_update_strategy,
        )

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
        self.save_state()
        logger.info("SelfUpdateManager stopped")

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self._config.self_update_enabled,
            "branch": self._config.self_update_branch,
            "strategy": self._config.self_update_strategy,
            "last_checked_sha": self._last_checked_sha,
            "last_check_timestamp": self._last_check_timestamp,
            "last_review_verdict": self._last_review_verdict,
            "last_review_reasoning": self._last_review_reasoning,
            "current_sha": self._get_head_sha(),
            "update_history": self._update_history[-10:],
        }

    # -- crash loop detection --

    def _check_crash_loop(self) -> None:
        if not self._pre_update_sha or not self._last_startup_timestamp:
            return
        try:
            last_ts = datetime.fromisoformat(self._last_startup_timestamp)
            elapsed = (datetime.now(timezone.utc) - last_ts).total_seconds()
        except (ValueError, TypeError) as exc:
            logger.debug("Invalid startup timestamp: %s", exc)
            return
        if elapsed < 60:
            self._consecutive_crash_count += 1
            logger.warning(
                "Quick restart detected (%ds). crash_count=%d",
                int(elapsed),
                self._consecutive_crash_count,
            )
            if self._consecutive_crash_count >= 2:
                logger.critical(
                    "Crash loop detected — rolling back to %s",
                    self._pre_update_sha,
                )
                self._rollback_to(self._pre_update_sha)
                self._consecutive_crash_count = 0
                self._pre_update_sha = None
                self.save_state()
        else:
            self._consecutive_crash_count = 0
            self._pre_update_sha = None

    def _rollback_to(self, sha: str) -> None:
        try:
            subprocess.run(
                ["git", "reset", "--hard", sha],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
                preexec_fn=make_sandbox_preexec(),
            )
            logger.info("Rolled back to %s", sha)
        except subprocess.CalledProcessError as exc:
            logger.error("Rollback to %s failed: %s", sha, exc.stderr)

    # -- git helpers --

    def _get_head_sha(self) -> str:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                preexec_fn=make_sandbox_preexec(),
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as exc:
            logger.debug("git rev-parse HEAD failed: %s", exc)
            return ""

    def _get_remote_sha(self) -> str:
        branch = self._config.self_update_branch
        try:
            result = subprocess.run(
                ["git", "rev-parse", "origin/%s" % branch],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                preexec_fn=make_sandbox_preexec(),
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as exc:
            logger.debug("git rev-parse remote failed: %s", exc)
            return ""

    def _fetch(self) -> bool:
        try:
            subprocess.run(
                ["git", "fetch", "origin", "--quiet"],
                check=True,
                capture_output=True,
                text=True,
                timeout=60,
                preexec_fn=make_sandbox_preexec(),
            )
            return True
        except subprocess.CalledProcessError as exc:
            logger.warning("git fetch failed: %s", exc.stderr)
            return False

    def _is_fast_forward(self, remote_sha: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", "HEAD", remote_sha],
                capture_output=True,
                text=True,
                timeout=30,
                preexec_fn=make_sandbox_preexec(),
            )
            return result.returncode == 0
        except subprocess.CalledProcessError as exc:
            logger.debug("git merge-base check failed: %s", exc)
            return False

    def _get_diff(self, remote_sha: str) -> str:
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD..%s" % remote_sha],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                preexec_fn=make_sandbox_preexec(),
            )
            return result.stdout
        except subprocess.CalledProcessError as exc:
            logger.debug("git diff failed: %s", exc)
            return ""

    def _get_commit_log(self, remote_sha: str) -> str:
        try:
            result = subprocess.run(
                ["git", "log", "HEAD..%s" % remote_sha, "--oneline"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
                preexec_fn=make_sandbox_preexec(),
            )
            return result.stdout
        except subprocess.CalledProcessError as exc:
            logger.debug("git log failed: %s", exc)
            return ""

    # -- update loop --

    async def _update_loop(self) -> None:
        interval = self._config.self_update_interval_seconds
        while True:
            try:
                await asyncio.sleep(interval)
                await self._check_for_updates()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Self-update check failed")

    async def _check_for_updates(self) -> None:
        if not self._fetch():
            return

        head = self._get_head_sha()
        remote = self._get_remote_sha()
        self._last_checked_sha = remote
        self._last_check_timestamp = datetime.now(timezone.utc).isoformat()

        if head == remote or not remote:
            return

        # Strategy filter
        if self._config.self_update_strategy == "merged_only":
            if not self._is_fast_forward(remote):
                logger.warning(
                    "Remote %s is not a fast-forward from HEAD — skipping "
                    "(possible force-push/rebase)",
                    remote[:8],
                )
                return

        diff = self._get_diff(remote)
        commit_log = self._get_commit_log(remote)

        if not diff:
            return

        logger.info(
            "New commits detected on %s: %s",
            self._config.self_update_branch,
            commit_log.strip(),
        )

        # Review gate
        verdict, reasoning = await self._review(diff, commit_log)
        self._last_review_verdict = verdict
        self._last_review_reasoning = reasoning
        self.save_state()

        if verdict != "ACCEPT":
            self._update_history.append(
                {
                    "sha": remote,
                    "timestamp": self._last_check_timestamp,
                    "verdict": verdict,
                    "reason": reasoning,
                    "applied": False,
                }
            )
            self.save_state()
            return

        # Verification gate
        passed = await self._verify_in_worktree(remote)
        if not passed:
            self._update_history.append(
                {
                    "sha": remote,
                    "timestamp": self._last_check_timestamp,
                    "verdict": "VERIFY_FAILED",
                    "applied": False,
                }
            )
            self.save_state()
            return

        # Apply
        self._verified_sha = remote
        self._pre_update_sha = head
        self._update_history.append(
            {
                "sha": remote,
                "timestamp": self._last_check_timestamp,
                "verdict": "ACCEPT",
                "applied": True,
            }
        )
        self.save_state()

        if self._reload_event:
            self._reload_event.set()

    async def _review(self, diff: str, commit_log: str) -> tuple[str, str]:
        """Dispatch a Claude agent to review the diff. Returns (verdict, reasoning)."""
        diff_text = diff[:_MAX_DIFF_CHARS]
        if len(diff) > _MAX_DIFF_CHARS:
            diff_text += (
                "\n\n--- TRUNCATED: diff was %d characters, showing first %d. "
                "Review the remaining %d characters manually before accepting. ---"
                % (len(diff), _MAX_DIFF_CHARS, len(diff) - _MAX_DIFF_CHARS)
            )
            logger.warning(
                "Self-update diff truncated: %d chars → %d chars",
                len(diff),
                _MAX_DIFF_CHARS,
            )
        prompt = (
            "You are reviewing a code update to the Golem daemon. "
            "Assess whether this change is safe to apply.\n\n"
            "Commits:\n%s\n\nDiff:\n%s\n\n"
            "Evaluate: Does it break existing functionality? "
            "Does it introduce bugs? Is it safe?\n"
            "Respond with exactly ACCEPT or REJECT on the first line, "
            "followed by your reasoning." % (commit_log, diff_text)
        )
        try:
            result = await asyncio.to_thread(self._run_review_agent, prompt)
            lines = result.strip().split("\n", 1)
            verdict = lines[0].strip().upper()
            reasoning = lines[1].strip() if len(lines) > 1 else ""
            if verdict not in ("ACCEPT", "REJECT"):
                return "REJECT", "Ambiguous response: %s" % lines[0]
            return verdict, reasoning
        except Exception as exc:
            logger.warning("Review agent failed: %s", exc)
            return "REJECT", "Agent error: %s" % exc

    def _run_review_agent(self, prompt: str) -> str:
        """Invoke Claude CLI for review. Blocking — called via asyncio.to_thread."""
        result = subprocess.run(
            ["claude", "--model", "sonnet", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=300,
            preexec_fn=make_sandbox_preexec(),
        )
        if result.returncode != 0:
            raise RuntimeError("Claude review failed: %s" % result.stderr)
        return result.stdout

    async def _verify_in_worktree(self, sha: str) -> bool:
        """Run verification in a temporary git worktree."""
        worktree_path = str(Path(tempfile.gettempdir()) / f"golem-verify-{sha[:8]}")
        try:
            # Create worktree
            await asyncio.to_thread(
                subprocess.run,
                ["git", "worktree", "add", worktree_path, sha],
                check=True,
                capture_output=True,
                text=True,
                preexec_fn=make_sandbox_preexec(),
            )
            # Run verification
            from golem.verifier import run_verification

            result = await asyncio.to_thread(
                run_verification,
                worktree_path,
            )
            return result.passed
        except Exception as exc:
            logger.warning("Worktree verification failed: %s", exc)
            return False
        finally:
            try:
                await asyncio.to_thread(
                    subprocess.run,
                    ["git", "worktree", "remove", "--force", worktree_path],
                    capture_output=True,
                    text=True,
                    preexec_fn=make_sandbox_preexec(),
                )
            except Exception as exc:
                logger.debug("Failed to remove verify worktree: %s", exc)

    async def apply_update(self) -> None:
        """Called after drain completes to merge the verified SHA."""
        if not self._verified_sha:
            logger.info("apply_update called but no verified SHA — skipping")
            return
        strategy = self._config.self_update_strategy
        sha = self._verified_sha
        logger.info("Applying update %s (strategy=%s)", sha[:8], strategy)
        try:
            if strategy == "merged_only":
                await asyncio.to_thread(
                    subprocess.run,
                    ["git", "merge", "--ff-only", sha],
                    check=True,
                    capture_output=True,
                    text=True,
                    preexec_fn=make_sandbox_preexec(),
                )
            else:
                await asyncio.to_thread(
                    subprocess.run,
                    ["git", "reset", "--hard", sha],
                    check=True,
                    capture_output=True,
                    text=True,
                    preexec_fn=make_sandbox_preexec(),
                )
            self._last_update_sha = sha
            self._last_update_timestamp = datetime.now(timezone.utc).isoformat()
            self.save_state()
            logger.info("Applied update to %s", sha[:8])
        except subprocess.CalledProcessError as exc:
            logger.error("Failed to apply update %s: %s", sha[:8], exc.stderr)
            self._verified_sha = None
