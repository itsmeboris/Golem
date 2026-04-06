# pylint: disable=too-many-lines
"""Golem flow — orchestrates long-running tasks via independent session runners.

Detects issues tagged ``[AGENT]``, manages TaskSessions, and drives
single-agent-per-task execution with each session running in its own
``asyncio.Task``.  A shared semaphore controls API concurrency while
detection runs in a separate loop so new issues are picked up immediately.

Key exports:
- ``GolemFlow`` — the main flow class; implements ``BaseFlow``,
  ``PollableFlow``, and ``WebhookableFlow``.  Manages session lifecycle from
  detection through completion/failure and sends notifications at each
  state transition.
"""

import asyncio
import json
import logging
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .core.config import Config, DATA_DIR, GolemFlowConfig
from .data_retention import cleanup_old_data
from .sandbox import make_sandbox_preexec
from .health import STATUS_UNHEALTHY, HealthMonitor, compute_status
from .core.live_state import LiveState
from .core.triggers.base import TriggerEvent
from .core.flow_base import BaseFlow, FlowResult, PollableFlow, WebhookableFlow

from .backends.local import LocalFileTaskSource, NullStateBackend, NullToolProvider
from .log_context import clear_task_context, set_task_context
from .errors import (
    InfrastructureError,
    TaskExecutionError,
    TaskNotCancelableError,
    TaskNotFoundError,
)
from .event_tracker import Milestone, TaskEventTracker
from .merge_queue import MergeEntry, MergeQueue, MergeResult
from .merge_review import ReconciliationResult, run_merge_agent
from .worktree_manager import (
    _run_git,
    cleanup_orphaned_worktrees,
    cleanup_worktree,
    fast_forward_if_safe,
)
import os
import tempfile

from .batch_monitor import BatchMonitor
from .prompt_optimizer import PromptEvaluator, PromptOptimizer
from .orchestrator import (
    TaskOrchestrator,
    TaskSession,
    TaskSessionState,
    SESSIONS_FILE,
    load_sessions,
    recover_sessions,
    _serialize_sessions_payload,
    _now_iso,
)
from .checkpoint import is_checkpoint_fresh, load_checkpoint
from .heartbeat import HeartbeatManager
from .priority_gate import PriorityGate
from .self_update import SelfUpdateManager
from .profile import GolemProfile, build_profile
from .prompts import FilePromptProvider
from .tracing import get_tracer, trace_span
from .validation import ValidationVerdict
from .verifier import run_verification

SUBMISSIONS_DIR = DATA_DIR / "submissions"

_MAX_MERGE_RETRIES = 3

logger = logging.getLogger("golem.flow")
_tracer = get_tracer("golem.flow")


class GolemFlow(BaseFlow, PollableFlow, WebhookableFlow):
    """Detects [AGENT] issues and orchestrates single-agent-per-task execution.

    Unlike the other flows this is *not* an AIFlow — it does not follow the
    prefetch -> prompt -> Claude -> parse -> execute pipeline.  Instead it manages
    ``TaskSession`` state machines that internally invoke Claude agents for
    full task execution, monitored via event streams.
    """

    SESSIONS_DIR = DATA_DIR / "state"

    def __init__(
        self,
        config: Config,
        flow_config: GolemFlowConfig | None = None,
        reload_event: asyncio.Event | None = None,
    ):
        super().__init__(config, flow_config)
        self._task_config = self.typed_config(GolemFlowConfig)
        self._reload_event = reload_event
        self._sessions: dict[int, TaskSession] = {}
        self._trackers: dict[int, TaskEventTracker] = {}
        self._processed_ids: set[int] = set()
        self._save_lock = threading.Lock()
        self._running = False
        self._detection_task: asyncio.Task | None = None
        self._session_tasks: dict[int, asyncio.Task] = {}
        self._work_dir_lock = asyncio.Lock()
        max_concurrent = self._task_config.max_active_sessions or 3
        self._gate = PriorityGate(max_concurrent)

        # Build pluggable profile from config (always required)
        profile_name = self._task_config.profile if self._task_config else "redmine"
        self._profile: GolemProfile = build_profile(profile_name, config)

        self._merge_queue = MergeQueue(
            on_merge_agent=self._handle_merge_agent,
            on_state_change=self._touch_merge_sentinel,
            verification_timeout=self._task_config.verification_timeout_seconds,
        )
        self._max_infra_retries = getattr(self._task_config, "max_infra_retries", 2)
        self._batch_monitor = BatchMonitor()
        self._health = HealthMonitor(
            config=config.health,
            notifier=self._profile.notifier,
            merge_deferred_count_fn=self._get_deferred_merge_count,
        )
        self._last_health_alerts: list = []
        self._health_status: str = "healthy"

        # Heartbeat — self-directed work when idle
        if self._task_config.heartbeat_enabled:
            self._heartbeat: HeartbeatManager | None = HeartbeatManager(
                self._task_config
            )
        else:
            self._heartbeat = None

        # Self-update — monitors Golem's own repo for changes
        if self._task_config.self_update_enabled:
            self._self_update: SelfUpdateManager | None = SelfUpdateManager(
                self._task_config,
                reload_event=self._reload_event,
            )
        else:
            self._self_update = None

        self._verified_ref: str | None = None
        self._notified_batches: set[str] = set()

        self._submissions_dir = SUBMISSIONS_DIR
        self._submissions_dir.mkdir(parents=True, exist_ok=True)
        self._submission_source = LocalFileTaskSource(self._submissions_dir)
        self._submission_profile = self._build_submission_profile()

        # Prompt evaluation — periodic evaluation of prompt template performance
        self._detection_tick_count: int = 0
        _runs_dir = DATA_DIR / "prompt_runs"
        self._prompt_evaluator = PromptEvaluator(runs_dir=_runs_dir)
        self._prompt_optimizer = PromptOptimizer(self._prompt_evaluator)

        self._load_state()

    @property
    def name(self) -> str:
        return "golem"

    @property
    def mcp_servers(self) -> list[str]:
        return self._profile.tool_provider.base_servers()

    @property
    def health(self) -> HealthMonitor:
        return self._health

    @property
    def health_status(self) -> str:
        """Current health status: 'healthy', 'degraded', or 'unhealthy'."""
        return self._health_status

    @property
    def last_health_alerts(self) -> list:
        """Most recent alerts from the last health check."""
        return self._last_health_alerts

    @property
    def live(self) -> LiveState:
        """Expose LiveState for heartbeat access."""
        return LiveState.get()

    def _set_verified_ref(self, sha: str) -> None:
        """Record the last commit SHA that passed pre-flight verification."""
        self._verified_ref = sha
        logger.info("Updated verified ref to %s", sha)

    def _get_deferred_merge_count(self) -> int:
        """Return count of sessions with deferred merges (for health monitoring)."""
        return sum(
            1 for s in self._sessions.values() if s.merge_deferred and s.merge_branch
        )

    # -- BaseFlow interface ---------------------------------------------------

    async def handle(self, event: TriggerEvent) -> FlowResult:
        """Handle a single [AGENT] issue detection event."""
        issue_id = event.data.get("issue_id")
        if issue_id is None:
            return FlowResult(success=False, error="Missing issue_id in event data")

        issue_id = int(issue_id)

        if issue_id in self._sessions:
            return FlowResult(
                success=True,
                data={"skipped": True, "reason": "session already exists"},
            )

        if issue_id in self._processed_ids:
            return FlowResult(
                success=True,
                data={"skipped": True, "reason": "already processed"},
            )

        subject = event.data.get("subject", "")
        session = self._create_session(issue_id, subject)
        self._sessions[issue_id] = session
        self._save_state()

        logger.info("Created new task session for #%d: %s", issue_id, subject)

        self._profile.notifier.notify_started(issue_id, subject)

        if self._running:
            self._spawn_session_task(issue_id)

        return FlowResult(
            success=True,
            data={
                "session_created": True,
                "parent_issue_id": issue_id,
                "state": session.state.value,
            },
            actions_taken=["session_created"],
        )

    # -- PollableFlow interface -----------------------------------------------

    def poll_new_items(self) -> list[dict[str, Any]]:
        """Scan configured projects for [AGENT] issues not yet tracked."""
        projects = self._task_config.projects
        if not projects:
            return []

        issues = self._profile.task_source.poll_tasks(
            projects,
            detection_tag=self._task_config.detection_tag,
            timeout=self._task_config.http_timeout,
        )

        heartbeat_ids = (
            self._heartbeat.get_claimed_issue_ids()
            if self._heartbeat is not None
            else set()
        )

        # Detect reopened issues: poll_tasks only returns open issues, so
        # any completed session that reappears was reopened with new scope.
        polled_ids = {issue.get("id") for issue in issues}
        reopened = self._processed_ids & polled_ids
        for iid in reopened:
            logger.info("Issue #%d was reopened — allowing re-detection", iid)
            self._processed_ids.discard(iid)
            self._sessions.pop(iid, None)

        new_items = []
        for issue in issues:
            iid = issue.get("id")
            if (
                iid
                and iid not in self._sessions
                and iid not in self._processed_ids
                and iid not in heartbeat_ids
            ):
                new_items.append(
                    {
                        "issue_id": iid,
                        "subject": issue.get("subject", ""),
                    }
                )
        return new_items

    def generate_event_id(self, item_data: dict[str, Any]) -> str:
        issue_id = item_data.get("issue_id", "unknown")
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        return f"golem-{issue_id}-{timestamp}"

    def on_item_success(self, _item_id: Any) -> None:
        pass  # Session tracking handles dedup

    # -- WebhookableFlow interface --------------------------------------------

    def parse_webhook_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        issue = payload.get("issue", {})
        if issue:
            return {
                "issue_id": issue.get("id"),
                "subject": issue.get("subject", ""),
            }
        return {"issue_id": payload.get("issue_id")}

    def generate_webhook_event_id(self, event_data: dict[str, Any]) -> str:
        issue_id = event_data.get("issue_id", "unknown")
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"wh-golem-{issue_id}-{timestamp}"

    # -- Session runner architecture -------------------------------------------
    #
    # Each session gets its own long-lived asyncio.Task that drives the
    # orchestrator independently.  A shared semaphore gates how many sessions
    # hit the Claude API concurrently.  Detection runs in a separate periodic
    # loop so new issues are picked up immediately, regardless of how many
    # sessions are in-flight.

    def start_tick_loop(self) -> asyncio.Task:
        """Start detection loop and spawn tasks for existing active sessions."""
        if self._detection_task is not None:
            return self._detection_task
        self._running = True
        # Clean up orphaned worktrees left by any previous crashed run
        _wt_base = self._task_config.default_work_dir
        if _wt_base:
            try:
                cleaned = cleanup_orphaned_worktrees(_wt_base)
                if cleaned:
                    logger.info(
                        "Cleaned up %d orphaned worktree(s) on startup", cleaned
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Worktree orphan cleanup failed (non-fatal)", exc_info=True
                )
        try:
            cleanup_old_data(str(DATA_DIR.parent))
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Data retention cleanup failed (non-fatal)", exc_info=True)
        self._spawn_existing_sessions()
        self._detection_task = asyncio.create_task(self._detection_loop())
        if self._heartbeat is not None:
            active_ids = {
                sid
                for sid, s in self._sessions.items()
                if s.state
                in (
                    TaskSessionState.DETECTED,
                    TaskSessionState.RUNNING,
                    TaskSessionState.VERIFYING,
                    TaskSessionState.VALIDATING,
                    TaskSessionState.RETRYING,
                )
            }
            self._heartbeat.start(self)
            self._heartbeat.reconcile_inflight(active_ids)
        if self._self_update is not None:
            self._self_update.start()
        logger.info(
            "Golem started (detection_interval=%ds, max_concurrent=%d)",
            self._task_config.tick_interval,
            self._task_config.max_active_sessions or 3,
        )
        return self._detection_task

    def stop_tick_loop(self) -> None:
        """Stop detection loop, cancel all session tasks, and kill CLI subprocesses."""
        self._running = False
        if self._detection_task is not None:
            self._detection_task.cancel()
            self._detection_task = None
        for sid, task in list(self._session_tasks.items()):
            task.cancel()
            logger.info("Cancelled session task #%d", sid)
        self._session_tasks.clear()

        if self._heartbeat is not None:
            self._heartbeat.stop()

        if self._self_update is not None:
            self._self_update.stop()

        from .core.cli_wrapper import kill_all_active

        killed = kill_all_active()
        if killed:
            logger.info("Killed %d orphaned CLI subprocess(es)", killed)

    async def graceful_stop(self, timeout: float = 30.0) -> None:
        """Stop detection loop, save checkpoints, drain active tasks.

        Phases:
        1. Stop detection (no new tasks are started).
        2. Save state for all active sessions.
        3. Wait up to *timeout* seconds for active session tasks to finish.
        4. Cancel any tasks that did not finish within the timeout.
        5. Kill lingering CLI subprocesses and perform a final state save.
        """
        logger.info("Graceful shutdown initiated (timeout=%.0fs)", timeout)

        # Phase 1: stop detection (no new tasks)
        self._running = False
        if self._detection_task is not None:
            self._detection_task.cancel()
            self._detection_task = None

        if self._heartbeat is not None:
            self._heartbeat.stop()
        if self._self_update is not None:
            self._self_update.stop()

        # Phase 2: save state for all active sessions
        self._save_state()

        # Phase 3: wait for active session tasks (with timeout)
        active_tasks = list(self._session_tasks.values())
        if active_tasks:
            logger.info(
                "Waiting up to %.0fs for %d active session(s) to complete",
                timeout,
                len(active_tasks),
            )
            _done, pending = await asyncio.wait(active_tasks, timeout=timeout)
            if pending:
                logger.warning(
                    "Shutdown timeout: cancelling %d remaining session(s)",
                    len(pending),
                )
                for task in pending:
                    task.cancel()
                # Await cancelled tasks so their finally blocks run
                await asyncio.gather(*pending, return_exceptions=True)

        self._session_tasks.clear()

        # Phase 4: kill CLI subprocesses
        from .core.cli_wrapper import kill_all_active

        killed = kill_all_active()
        if killed:
            logger.info("Killed %d CLI subprocess(es) during shutdown", killed)

        # Phase 5: final state save
        self._save_state()
        logger.info("Graceful shutdown complete")

    # -- Detection loop (runs independently of session execution) -----------

    async def _detection_loop(self) -> None:
        """Periodically poll for new [AGENT] issues and spawn session tasks."""
        last_health_check = 0.0
        while self._running:
            # Health check always runs so recovery is detected
            now = time.time()
            if now - last_health_check >= self._health.check_interval:
                alerts = self._health.check()
                self._last_health_alerts = alerts
                self._health_status = compute_status(alerts)
                if self._health_status == STATUS_UNHEALTHY:
                    logger.warning(
                        "Health status UNHEALTHY — pausing new task detection; "
                        "active alerts: %s",
                        [a["type"] for a in alerts],
                    )
                last_health_check = now

            if self._health_status == STATUS_UNHEALTHY:
                await asyncio.sleep(self._task_config.tick_interval)
                continue

            try:
                with trace_span(_tracer, "flow.detection_tick"):
                    self._detect_new_issues()
                    self._check_human_feedback()
                    await self._retry_deferred_merges()
                self._health.record_poll_success()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Error in detection loop")
                self._health.record_poll_error()

            self._health.record_heartbeat()

            # Prompt evaluation — fire-and-forget so it never blocks detection
            self._detection_tick_count += 1
            if (
                self._task_config.prompt_evaluation_enabled
                and self._detection_tick_count
                % self._task_config.prompt_evaluation_interval_ticks
                == 0
            ):
                asyncio.create_task(
                    self._run_prompt_evaluation(),
                    name="prompt-evaluation",
                )

            await asyncio.sleep(self._task_config.tick_interval)

    async def _run_prompt_evaluation(self) -> None:
        """Evaluate recent prompt runs and log suggestions.

        Runs asynchronously so evaluation never blocks the detection loop.
        Errors are caught and logged — a failing evaluation must never crash
        the daemon.
        """
        try:
            runs_dir = DATA_DIR / "prompt_runs"
            runs: list[dict] = []
            if runs_dir.exists():
                for run_file in runs_dir.glob("*.json"):
                    try:
                        run_data = json.loads(run_file.read_text())
                        if isinstance(run_data, list):
                            runs.extend(run_data)
                        elif isinstance(run_data, dict):
                            runs.append(run_data)
                    except Exception:  # pylint: disable=broad-exception-caught
                        logger.debug("Skipping unreadable prompt run file %s", run_file)

            self._prompt_evaluator.evaluate(runs)
            suggestions = self._prompt_optimizer.suggest()

            if suggestions:
                report = self._prompt_optimizer.format_report(suggestions)
                logger.info(
                    "Prompt evaluation found %d suggestion(s):\n%s",
                    len(suggestions),
                    report,
                )
            else:
                logger.debug("Prompt evaluation: all prompts performing well")
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Prompt evaluation failed (non-fatal)", exc_info=True)

    def _detect_new_issues(self) -> None:
        """Poll for new [AGENT] issues and scan submissions directory."""
        live = LiveState.get()
        for item in self.poll_new_items():
            iid = item.get("issue_id")
            if not iid:
                continue
            iid = int(iid)
            subject = item.get("subject", "")
            session = self._create_session(iid, subject)
            self._sessions[iid] = session
            self._save_state()
            event_id = f"golem-{iid}"
            model = self._task_config.task_model or "sonnet"
            live.enqueue(event_id, "golem", model)
            live.update_phase(event_id, "detected")
            logger.info("Detected new task: #%d %s", iid, subject)
            self._spawn_session_task(iid)

        self._scan_submissions()

    def _check_human_feedback(self) -> None:
        """Check for human feedback on escalated (FAILED) tasks."""
        for sid, session in list(self._sessions.items()):
            if session.state != TaskSessionState.FAILED:
                continue
            # Sessions created via submit_task() use millisecond-timestamp IDs
            # that are not valid issue-tracker numbers — skip them.
            if session.execution_mode:
                continue
            try:
                comments = self._profile.task_source.get_task_comments(
                    sid, since=session.updated_at
                )
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug(
                    "Failed to fetch comments for session %s", sid, exc_info=True
                )
                continue

            human_comments = [
                c
                for c in comments
                if c.get("author", "").lower() != "golem"
                and c.get("created_at", "") > session.updated_at
            ]
            if not human_comments:
                continue

            new_feedback = "\n\n".join(
                f"**{c['author']}**: {c['body']}" for c in human_comments
            )
            session.human_feedback_at = human_comments[-1].get("created_at", "")

            # Guard: check for feedback retry limit before accepting re-attempt
            max_retries = self._task_config.max_retries
            if session.retry_count >= max_retries:
                logger.warning(
                    "Feedback retry limit reached for #%s (retry_count=%d >= max_retries=%d)",
                    sid,
                    session.retry_count,
                    max_retries,
                )
                session.previous_feedback = new_feedback
                session.human_feedback = new_feedback
                # Update updated_at so filter won't re-detect same comments
                session.updated_at = _now_iso()
                self._save_state()
                continue

            # Guard: identical feedback does not reset retry counter
            if (
                new_feedback.strip().lower()
                == session.previous_feedback.strip().lower()
            ):
                logger.warning(
                    "Identical feedback detected for #%s, not resetting retry counter",
                    sid,
                )
            else:
                session.retry_count = 0

            session.human_feedback = new_feedback
            session.previous_feedback = new_feedback
            session.state = TaskSessionState.HUMAN_REVIEW
            self._save_state()
            logger.info("Human feedback detected on #%s, queuing re-attempt", sid)
            self._spawn_session_task(sid)

    # -- Per-session task lifecycle -----------------------------------------

    def _spawn_session_task(self, session_id: int) -> None:
        """Create an independent asyncio.Task for a session."""
        if session_id in self._session_tasks:
            return
        task = asyncio.create_task(
            self._run_session(session_id),
            name=f"golem-session-{session_id}",
        )
        self._session_tasks[session_id] = task
        logger.info("Spawned task for session #%d", session_id)

    def _spawn_existing_sessions(self) -> None:
        """On startup, spawn tasks for sessions that survived a restart."""
        for sid, session in self._sessions.items():
            if session.state not in (
                TaskSessionState.COMPLETED,
                TaskSessionState.FAILED,
            ):
                self._spawn_session_task(sid)

    async def _run_session(self, session_id: int) -> None:
        """Drive a single session to completion, acquiring a priority-gated
        slot before each tick."""
        session = self._sessions[session_id]
        live = LiveState.get()
        event_id = f"golem-{session.parent_issue_id}"
        profile = (
            self._submission_profile
            if session.execution_mode == "prompt"
            else self._profile
        )
        set_task_context(str(session_id))
        with trace_span(
            _tracer,
            "flow.session",
            session_id=str(session_id),
        ):
            try:
                # Wait for dependencies before starting
                if session.depends_on:
                    await self._wait_for_dependencies(session)

                while self._running and session.state not in (
                    TaskSessionState.COMPLETED,
                    TaskSessionState.FAILED,
                ):
                    live.mark_queued(event_id)
                    async with self._gate.slot(session.priority):
                        prev_state = session.state
                        lock = (
                            asyncio.Lock()
                            if self._task_config.use_worktrees
                            else self._work_dir_lock
                        )
                        orchestrator = TaskOrchestrator(
                            session,
                            self.config,
                            self._task_config,
                            on_progress=self._on_agent_progress,
                            work_dir_lock=lock,
                            save_callback=self._save_state,
                            profile=profile,
                            verified_ref=self._verified_ref,
                            on_verified_ref=self._set_verified_ref,
                        )
                        try:
                            await orchestrator.tick()
                        except InfrastructureError as ie:
                            if session.infra_retry_count < self._max_infra_retries:
                                session.infra_retry_count += 1
                                logger.warning(
                                    "Session #%d: infra failure (%s), retrying (%d/%d)",
                                    session_id,
                                    ie,
                                    session.infra_retry_count,
                                    self._max_infra_retries,
                                )
                                session.state = TaskSessionState.DETECTED
                                self._save_state()
                                continue
                            raise
                        self._handle_state_transition(session, prev_state)
                        self._save_state()

                    if session.state not in (
                        TaskSessionState.COMPLETED,
                        TaskSessionState.FAILED,
                    ):
                        await asyncio.sleep(self._task_config.tick_interval)

                # Enqueue for merge if the session signaled merge-ready
                if session.merge_ready:
                    await self._enqueue_for_merge(session)

            except asyncio.CancelledError:
                logger.info("Session #%d cancelled", session_id)
            except TaskExecutionError as te:
                logger.error("Session #%d: %s", session_id, te)
                session.state = TaskSessionState.FAILED
                session.errors.append(str(te))
                self._handle_state_transition(session, TaskSessionState.RUNNING)
                self._save_state()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Session #%d crashed unexpectedly", session_id)
                session.state = TaskSessionState.FAILED
                session.errors.append("session task crashed")
                self._handle_state_transition(session, TaskSessionState.RUNNING)
                self._save_state()
            finally:
                clear_task_context()
                self._session_tasks.pop(session_id, None)

    async def _wait_for_dependencies(self, session: TaskSession) -> None:
        """Block until all sessions in ``depends_on`` have completed.

        Raises ``TaskExecutionError`` if any dependency failed.
        """
        while self._running:
            all_done = True
            for dep_id in session.depends_on:
                dep = self._sessions.get(dep_id)
                if dep is None:
                    logger.warning(
                        "Dependency #%d for session #%d not found; "
                        "treating as satisfied",
                        dep_id,
                        session.parent_issue_id,
                    )
                    continue
                if dep.state == TaskSessionState.FAILED:
                    raise TaskExecutionError(
                        f"Dependency #{dep_id} ({dep.parent_subject}) failed"
                    )
                if dep.state != TaskSessionState.COMPLETED:
                    all_done = False
                    break
            if all_done:
                return
            await asyncio.sleep(self._task_config.tick_interval)

    async def _enqueue_for_merge(self, session: TaskSession) -> None:
        """Enqueue a completed session into the merge queue and process."""
        issue_id = session.parent_issue_id
        branch_name = f"agent/{issue_id}"
        entry = MergeEntry(
            session_id=issue_id,
            branch_name=branch_name,
            worktree_path=session.worktree_path,
            base_dir=session.base_work_dir,
            priority=session.priority,
            group_id=session.group_id,
        )
        session.merge_queued_at = _now_iso()
        await self._merge_queue.enqueue(entry)
        results = await self._merge_queue.process_all()
        for r in results:
            self._apply_merge_result(r)
        self._save_state()

    def _apply_merge_result(self, result: MergeResult) -> None:
        """Update the session with the merge outcome."""
        session = self._sessions.get(result.session_id)
        if session is None:
            return
        if result.success:
            session.merge_ready = False
            session.merge_deferred = False
            session.merge_branch = ""
            session.files_changed = result.changed_files
            if result.merge_sha:
                session.commit_sha = result.merge_sha
            self._cleanup_session_worktree(session)
            logger.info(
                "Session %d: merge applied → %s",
                result.session_id,
                result.merge_sha or "(no changes)",
            )
        elif result.deferred:
            session.merge_deferred = True
            session.merge_branch = result.merge_branch
            session.merge_ready = False
            logger.info(
                "Session %d: merge deferred — %s (branch %s)",
                result.session_id,
                result.error,
                result.merge_branch,
            )
        elif not result.success:
            session.merge_ready = False
            session.merge_deferred = False
            session.state = TaskSessionState.FAILED
            session.errors.append(f"merge failed: {result.error}")
            self._cleanup_session_worktree(session, keep_branch=True)
            # Reopen the issue so the failure is visible on the tracker
            try:
                self._profile.state_backend.update_status(
                    session.parent_issue_id, "in_progress"
                )
            except Exception:  # pylint: disable=broad-except
                logger.debug(
                    "Session %d: could not reopen issue after merge failure",
                    result.session_id,
                )
            logger.warning(
                "Session %d: merge failed: %s", result.session_id, result.error
            )

    def _cleanup_session_worktree(
        self, session: TaskSession, *, keep_branch: bool = False
    ) -> None:
        """Remove the session's worktree if it exists."""
        if not session.worktree_path:
            return
        try:
            cleanup_worktree(
                session.base_work_dir,
                session.worktree_path,
                keep_branch=keep_branch,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Session %d: worktree cleanup failed for %s",
                session.parent_issue_id,
                session.worktree_path,
                exc_info=True,
            )
        session.worktree_path = ""

    def _handle_merge_agent(
        self,
        base_dir: str,
        issue_id: int,
        agent_diff: str,
        conflict_files: list[str],
        missing: list,
        verification_summary: str = "",
    ) -> ReconciliationResult:
        """Callback for the merge queue — spawns the unified merge agent."""
        return run_merge_agent(
            base_dir,
            issue_id,
            agent_diff,
            conflict_files=conflict_files,
            missing=missing,
            budget_usd=self._task_config.merge_review_budget_usd,
            timeout_seconds=self._task_config.merge_review_timeout,
            verification_summary=verification_summary,
            sandbox_enabled=self._task_config.sandbox_enabled,
            sandbox_cpu_seconds=self._task_config.sandbox_cpu_seconds,
            sandbox_memory_gb=self._task_config.sandbox_memory_gb,
        )

    async def _retry_deferred_merges(self) -> None:
        """Retry deferred merges when the working tree may have changed."""
        for session in list(self._sessions.values()):
            if not session.merge_deferred or not session.merge_branch:
                continue
            if session.merge_retry_count >= _MAX_MERGE_RETRIES:
                continue

            # Check if the merge branch ref still exists
            ref_check = _run_git(
                ["rev-parse", "--verify", session.merge_branch],
                cwd=session.base_work_dir,
            )
            if ref_check.returncode != 0:
                logger.error(
                    "Session %d: merge branch %s no longer exists — "
                    "marking as failed",
                    session.parent_issue_id,
                    session.merge_branch,
                )
                prev_state = session.state
                session.state = TaskSessionState.FAILED
                session.merge_deferred = False
                session.errors.append(
                    f"merge failed: branch missing ({session.merge_branch})"
                )
                self._handle_state_transition(session, prev_state)
                self._save_state()
                continue

            ok, _reason = fast_forward_if_safe(
                session.base_work_dir, session.merge_branch
            )
            if ok:
                merge_branch = session.merge_branch
                session.merge_deferred = False
                session.merge_branch = ""
                session.merge_retry_count = 0
                sha = _run_git(
                    ["rev-parse", "--short", "HEAD"],
                    cwd=session.base_work_dir,
                ).stdout.strip()
                session.commit_sha = sha
                logger.info(
                    "Session %d: deferred merge applied \u2192 %s",
                    session.parent_issue_id,
                    sha,
                )
                # Clean up branches using the captured name
                _run_git(
                    ["branch", "-D", f"agent/{session.parent_issue_id}"],
                    cwd=session.base_work_dir,
                )
                _run_git(
                    ["branch", "-D", merge_branch],
                    cwd=session.base_work_dir,
                )
            else:
                session.merge_retry_count += 1
                if session.merge_retry_count >= _MAX_MERGE_RETRIES:
                    logger.error(
                        "Session %d: deferred merge exceeded %d retries — "
                        "giving up (branch=%s, reason=%s)",
                        session.parent_issue_id,
                        _MAX_MERGE_RETRIES,
                        session.merge_branch,
                        _reason,
                    )
                    prev_state = session.state
                    session.state = TaskSessionState.FAILED
                    session.merge_deferred = False
                    session.errors.append(
                        f"merge failed: exhausted {_MAX_MERGE_RETRIES} "
                        f"retries ({_reason})"
                    )
                    self._handle_state_transition(session, prev_state)
            self._save_state()

    def _touch_merge_sentinel(self) -> None:
        """Touch the merge-queue sentinel file to trigger SSE update."""
        sentinel = Path(DATA_DIR) / "state" / ".merge_queue_updated"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()

    def _on_agent_progress(self, session: TaskSession, milestone: Milestone) -> None:
        """Central progress handler — updates LiveState and session from milestones."""
        live = LiveState.get()
        event_id = f"golem-{session.parent_issue_id}"
        phase = milestone.kind or "running"
        if milestone.tool_name:
            phase = f"tool:{milestone.tool_name}"
        live.update_phase(event_id, phase)

    def _handle_state_transition(
        self,
        session: TaskSession,
        prev_state: TaskSessionState,
    ) -> None:
        """Update LiveState and send Teams notifications on state transitions."""
        sid = session.parent_issue_id
        event_id = f"golem-{sid}"
        live = LiveState.get()

        # Started running — already enqueued at detection, now mark running
        if (
            prev_state == TaskSessionState.DETECTED
            and session.state == TaskSessionState.RUNNING
        ):
            live.dequeue_start(event_id)
            self._profile.notifier.notify_started(sid, session.parent_subject)

        # Completed — finish in LiveState and notify
        now_completed = session.state == TaskSessionState.COMPLETED
        if prev_state != TaskSessionState.COMPLETED and now_completed:
            self._processed_ids.add(sid)
            live.finish(event_id, success=True, cost_usd=session.total_cost_usd)

            self._profile.notifier.notify_completed(
                sid,
                session.parent_subject,
                cost_usd=session.total_cost_usd,
                duration_s=session.duration_seconds,
                steps=session.milestone_count,
                verdict=session.validation_verdict,
                confidence=session.validation_confidence,
                concerns=session.validation_concerns,
                commit_sha=session.commit_sha,
                retry_count=session.retry_count,
                fix_iteration=session.fix_iteration,
            )
            self._health.record_task_result(success=True)

        # Failed — finish in LiveState and notify
        if (
            prev_state != TaskSessionState.FAILED
            and session.state == TaskSessionState.FAILED
        ):
            live.finish(event_id, success=False, cost_usd=session.total_cost_usd)

            if session.validation_verdict:
                self._profile.notifier.notify_escalated(
                    sid,
                    session.parent_subject,
                    verdict=session.validation_verdict,
                    summary=session.validation_summary,
                    concerns=session.validation_concerns,
                    cost_usd=session.total_cost_usd,
                    duration_s=session.duration_seconds,
                    retry_count=session.retry_count,
                    fix_iteration=session.fix_iteration,
                )
            else:
                reason = session.errors[-1] if session.errors else "Unknown error"
                self._profile.notifier.notify_failed(
                    sid,
                    session.parent_subject,
                    reason,
                    cost_usd=session.total_cost_usd,
                    duration_s=session.duration_seconds,
                )
            self._health.record_task_result(success=False)

        # Notify heartbeat of terminal states
        if self._heartbeat is not None and session.state in (
            TaskSessionState.COMPLETED,
            TaskSessionState.FAILED,
        ):
            self._heartbeat.on_task_completed(
                sid, success=(session.state == TaskSessionState.COMPLETED)
            )

        # Update batch monitor if task belongs to a batch
        if session.group_id and self._batch_monitor.get(session.group_id):
            batch = self._batch_monitor.update(session.group_id, self._sessions)
            self._handle_batch_terminal(batch)

    def _handle_batch_terminal(self, batch: Any) -> None:
        """Notify and trigger validation when a batch reaches a terminal state."""
        if batch.status not in ("completed", "failed"):
            return
        if batch.group_id in self._notified_batches:
            return

        self._notified_batches.add(batch.group_id)
        self._profile.notifier.notify_batch_completed(
            batch.group_id,
            batch.status,
            total_cost_usd=batch.total_cost_usd,
            total_duration_s=batch.total_duration_s,
            task_count=len(batch.task_ids),
            validation_verdict=batch.validation_verdict,
        )

        if batch.status != "completed":
            return

        # Auto-trigger integration validation for successful batches
        work_dir = self._find_batch_work_dir(batch)
        if not work_dir:
            return
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.run_integration_validation(batch.group_id, work_dir))
        except RuntimeError:
            logger.debug(
                "No event loop; skipping integration validation for batch %s",
                batch.group_id,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Failed to trigger integration validation for batch %s",
                batch.group_id,
                exc_info=True,
            )

    def _find_batch_work_dir(self, batch: Any) -> str:
        """Return the work_dir from the first session in the batch that has one."""
        for tid in batch.task_ids:
            s = self._sessions.get(tid)
            if s and s.base_work_dir:
                return s.base_work_dir
        return ""

    # -- Submission support ----------------------------------------------------

    def _build_submission_profile(self) -> GolemProfile:
        """Build a profile for submitted prompt tasks."""
        mcp_enabled = self._task_config.mcp_enabled if self._task_config else False
        from .backends.mcp_tools import KeywordToolProvider

        return GolemProfile(
            name="submission",
            task_source=self._submission_source,
            state_backend=NullStateBackend(),
            notifier=self._profile.notifier,
            tool_provider=(
                KeywordToolProvider() if mcp_enabled else NullToolProvider()
            ),
            prompt_provider=FilePromptProvider(None),
        )

    def submit_task(
        self,
        prompt: str,
        subject: str = "",
        work_dir: str = "",
        _mcp: bool | None = None,
        issue_mode: bool = False,
    ) -> dict[str, Any]:
        """Write a submission file and immediately create + spawn a session.

        When *issue_mode* is True the session uses the real profile
        (with the actual state backend) so issue updates (close, comment)
        reach the tracker.  Default is False (NullStateBackend).

        Returns ``{"task_id": <int>, "status": "submitted"}``.
        """
        task_id = int(time.time() * 1000)
        if not subject:
            subject = f"[AGENT] {prompt}"

        self._submissions_dir.mkdir(parents=True, exist_ok=True)
        task_file = self._submissions_dir / f"{task_id}.json"
        task_data = {
            "id": str(task_id),
            "subject": subject,
            "description": prompt,
        }
        if work_dir:
            task_data["work_dir"] = work_dir
        task_file.write_text(json.dumps(task_data, indent=2), encoding="utf-8")

        session = self._create_session(task_id, subject)
        session.execution_mode = "issue" if issue_mode else "prompt"
        session.grace_deadline = _now_iso()
        if work_dir:
            session.base_work_dir = work_dir
        self._sessions[task_id] = session
        self._save_state()

        logger.info("Submitted task #%d: %s", task_id, subject)
        self._profile.notifier.notify_started(task_id, subject)

        if self._running:
            self._spawn_session_task(task_id)

        return {"task_id": task_id, "status": "submitted"}

    def submit_batch(
        self,
        tasks: list[dict[str, Any]],
        group_id: str = "",
    ) -> dict[str, Any]:
        """Submit multiple tasks as a batch with optional dependencies.

        Each task dict may contain ``prompt``, ``subject``, ``work_dir``,
        and ``depends_on``.  Dependencies can be **int** (0-based index)
        or **str** (the ``key`` of a preceding task in the batch).

        Returns ``{"group_id": ..., "tasks": [{"task_id": ..., "status": ...}]}``.
        """
        if not group_id:
            group_id = f"batch-{int(time.time() * 1000)}"

        results: list[dict[str, Any]] = []
        dep_map: dict[int | str, int] = {}

        for idx, task in enumerate(tasks):
            r = self.submit_task(
                prompt=task.get("prompt", ""),
                subject=task.get("subject", ""),
                work_dir=task.get("work_dir", ""),
            )
            task_id = r["task_id"]
            dep_map[idx] = task_id
            task_key = task.get("key", "")
            if task_key:
                dep_map[task_key] = task_id

            session = self._sessions[task_id]
            session.group_id = group_id
            session.depends_on = [
                dep_map[d]
                for d in task.get("depends_on", [])
                if isinstance(d, (int, str)) and d in dep_map
            ] + [
                d
                for d in task.get("depends_on", [])
                if isinstance(d, int) and d not in dep_map
            ]

            results.append({"task_id": task_id, "status": "submitted"})

        task_ids = [r["task_id"] for r in results]
        self._batch_monitor.register(group_id, task_ids)
        self._profile.notifier.notify_batch_submitted(group_id, len(tasks))
        self._save_state()
        return {"group_id": group_id, "tasks": results}

    def _bisect_merges(self, work_dir: str, ordered_shas: list[str]) -> "int | None":
        """Binary-search ordered_shas to find the first failing commit.

        For each bisect step, creates a temporary git worktree at the midpoint
        SHA, runs run_verification(), and discards the worktree.

        Returns the index of the first failing commit, or None if the search
        is inconclusive (e.g. a verification step raises an exception).
        A single-SHA list always returns 0 immediately.

        NOTE: This method is synchronous and must be dispatched to a thread
        via run_in_executor. The subprocess calls block the thread, not the
        event loop.
        """
        if not ordered_shas:
            return None
        if len(ordered_shas) == 1:
            return 0

        bisect_dir = Path(work_dir) / "data" / "agent" / "bisect-worktrees"
        lo = 0
        hi = len(ordered_shas) - 1

        while lo < hi:
            mid = (lo + hi) // 2
            sha = ordered_shas[mid]
            wt_path = str(bisect_dir / sha)
            try:
                subprocess.run(
                    ["git", "worktree", "add", "--detach", wt_path, sha],
                    cwd=work_dir,
                    check=False,
                    capture_output=True,
                    preexec_fn=make_sandbox_preexec(),
                )
                result = run_verification(wt_path)
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning("Bisect step failed for SHA %s; aborting bisect", sha)
                subprocess.run(
                    ["git", "worktree", "remove", "--force", wt_path],
                    cwd=work_dir,
                    check=False,
                    capture_output=True,
                    preexec_fn=make_sandbox_preexec(),
                )
                return None
            else:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", wt_path],
                    cwd=work_dir,
                    check=False,
                    capture_output=True,
                    preexec_fn=make_sandbox_preexec(),
                )

            if result.passed:
                # Midpoint is good → culprit is after mid
                lo = mid + 1
            else:
                # Midpoint is bad → culprit is at or before mid
                hi = mid

        # Verify the terminal SHA actually fails before blaming it
        # (guards against non-deterministic / flaky test scenarios)
        sha = ordered_shas[lo]
        wt_path = str(bisect_dir / sha)
        try:
            subprocess.run(
                ["git", "worktree", "add", "--detach", wt_path, sha],
                cwd=work_dir,
                check=False,
                capture_output=True,
                preexec_fn=make_sandbox_preexec(),
            )
            result = run_verification(wt_path)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Bisect final-verify failed for SHA %s; inconclusive", sha)
            subprocess.run(
                ["git", "worktree", "remove", "--force", wt_path],
                cwd=work_dir,
                check=False,
                capture_output=True,
                preexec_fn=make_sandbox_preexec(),
            )
            return None
        else:
            subprocess.run(
                ["git", "worktree", "remove", "--force", wt_path],
                cwd=work_dir,
                check=False,
                capture_output=True,
                preexec_fn=make_sandbox_preexec(),
            )
        return lo if not result.passed else None

    async def run_integration_validation(
        self, group_id: str, work_dir: str
    ) -> ValidationVerdict:
        """Run full validation suite on the merged result.

        Identifies which merge introduced any breakage by binary-searching
        over the merge order.
        """
        from .validation import run_validation

        group_sessions = [s for s in self._sessions.values() if s.group_id == group_id]
        if not group_sessions:
            return ValidationVerdict(
                verdict="PASS",
                confidence=1.0,
                summary="No sessions in group",
            )

        merged_desc = "\n".join(
            f"- #{s.parent_issue_id}: {s.parent_subject}" for s in group_sessions
        )

        verdict = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: run_validation(
                issue_id=0,
                subject=f"Integration validation for {group_id}",
                description=(
                    f"Cross-task integration check.\n\n"
                    f"Merged tasks:\n{merged_desc}\n\n"
                    f"Verify that black, pylint, and pytest all pass."
                ),
                session_data={},
                work_dir=work_dir,
                model=self._task_config.validation_model,
                budget_usd=self._task_config.validation_budget_usd,
                timeout_seconds=self._task_config.validation_timeout_seconds,
                sandbox_enabled=self._task_config.sandbox_enabled,
                sandbox_cpu_seconds=self._task_config.sandbox_cpu_seconds,
                sandbox_memory_gb=self._task_config.sandbox_memory_gb,
            ),
        )

        if verdict.verdict != "PASS":
            logger.warning(
                "Integration validation failed for %s: %s",
                group_id,
                verdict.summary,
            )
            verdict = await self._run_bisect_on_verdict(
                verdict, group_sessions, work_dir
            )

        return verdict

    async def _run_bisect_on_verdict(
        self,
        verdict: ValidationVerdict,
        group_sessions: list,
        work_dir: str,
    ) -> ValidationVerdict:
        """Attempt to identify the culprit merge via binary search.

        Skips bisect when there is only one session (culprit is obvious) or
        when no sessions have a commit_sha. Enriches the verdict summary and
        files_to_fix with culprit information when found.
        """
        # Sort by merge time (ISO strings sort correctly)
        ordered = sorted(group_sessions, key=lambda s: s.merge_queued_at or "")
        shas_with_session = [(s.commit_sha, s) for s in ordered if s.commit_sha]

        if not shas_with_session:
            # Cannot bisect without commit SHAs
            return verdict

        ordered_shas = [sha for sha, _ in shas_with_session]
        ordered_sessions = [s for _, s in shas_with_session]

        if len(ordered_shas) == 1:
            # Only one session — culprit is obvious, no bisect needed
            culprit = ordered_sessions[0]
            verdict.summary = (
                f"#{culprit.parent_issue_id} ({culprit.parent_subject}) "
                f"introduced the breakage. {verdict.summary}"
            )
            return verdict

        culprit_idx = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: self._bisect_merges(work_dir, ordered_shas),
        )

        if culprit_idx is None:
            return verdict

        culprit = ordered_sessions[culprit_idx]
        verdict.summary = (
            f"#{culprit.parent_issue_id} ({culprit.parent_subject}) "
            f"introduced the breakage. {verdict.summary}"
        )
        if culprit.files_changed:
            verdict.files_to_fix = list(
                dict.fromkeys(verdict.files_to_fix + culprit.files_changed)
            )

        return verdict

    def _scan_submissions(self) -> None:
        """Pick up task files from the submissions directory."""
        if not self._submissions_dir.is_dir():
            return

        done_dir = self._submissions_dir / "done"
        live = LiveState.get()

        for task_file in sorted(self._submissions_dir.iterdir()):
            if task_file.suffix not in (".json", ".yaml", ".yml"):
                continue
            if task_file.is_dir():
                continue

            task = self._submission_source._load_file(task_file)
            if task is None:
                continue

            try:
                iid = int(task.get("id", 0))
            except (ValueError, TypeError) as exc:
                logger.debug("Invalid task ID in submission: %s", exc)
                continue

            if not iid or iid in self._sessions or iid in self._processed_ids:
                # Move to done/ even when skipping — prevents re-processing
                # on daemon restart from overwriting sessions (e.g. dropping
                # group_id set by submit_batch).
                if iid:
                    done_dir.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(task_file), str(done_dir / task_file.name))
                continue

            subject = task.get("subject", "")
            session = self._create_session(iid, subject)
            session.execution_mode = "prompt"
            session.grace_deadline = _now_iso()
            self._sessions[iid] = session
            self._save_state()

            event_id = f"golem-{iid}"
            model = self._task_config.task_model or "sonnet"
            live.enqueue(event_id, "golem", model)
            live.update_phase(event_id, "detected")
            logger.info("Picked up submission: #%d %s", iid, subject)
            self._spawn_session_task(iid)

            done_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(task_file), str(done_dir / task_file.name))

    # -- Cancel support --------------------------------------------------------

    CANCELABLE_STATES = frozenset(
        {
            TaskSessionState.DETECTED,
            TaskSessionState.RUNNING,
            TaskSessionState.VALIDATING,
            TaskSessionState.RETRYING,
        }
    )

    def get_session(self, task_id: int) -> "TaskSession | None":
        return self._sessions.get(task_id)

    def get_batch(self, group_id: str) -> dict | None:
        """Return batch state as dict, or None if not found."""
        batch = self._batch_monitor.get(group_id)
        if batch is None:
            return None
        # Refresh from live sessions before returning
        batch = self._batch_monitor.update(group_id, self._sessions)
        return batch.to_dict()

    def list_batches(self) -> list[dict]:
        """Return all batches as dicts, sorted by created_at desc."""
        return [b.to_dict() for b in self._batch_monitor.list_batches()]

    def cancel_session(self, task_id: int) -> dict:
        session = self._sessions.get(task_id)
        if session is None:
            raise TaskNotFoundError(f"Task {task_id} not found")
        if session.state not in self.CANCELABLE_STATES:
            raise TaskNotCancelableError(
                f"Task {task_id} is in terminal state '{session.state.value}'"
            )
        prev_state = session.state
        session.state = TaskSessionState.FAILED
        session.result_summary = "Cancelled by user"
        running_task = self._session_tasks.get(task_id)
        if running_task is not None:
            running_task.cancel()
        self._handle_state_transition(session, prev_state)
        self._save_state()
        logger.info("Cancelled session #%d (was %s)", task_id, prev_state.value)
        return {"task_id": task_id, "status": "cancelled"}

    def clear_failed_sessions(self) -> list[int]:
        """Remove all FAILED sessions from state. Returns list of cleared IDs."""
        failed_ids = [
            sid
            for sid, s in self._sessions.items()
            if s.state == TaskSessionState.FAILED
        ]
        for sid in failed_ids:
            del self._sessions[sid]
        if failed_ids:
            self._save_state()
            logger.info("Cleared %d failed sessions: %s", len(failed_ids), failed_ids)
        return failed_ids

    # -- Session factory ------------------------------------------------------

    def _create_session(self, issue_id: int, subject: str) -> TaskSession:
        now = _now_iso()
        grace = (
            datetime.now(timezone.utc)
            + timedelta(seconds=self._task_config.grace_period_seconds)
        ).isoformat()

        return TaskSession(
            parent_issue_id=issue_id,
            parent_subject=subject,
            state=TaskSessionState.DETECTED,
            model=self._task_config.task_model or "",
            created_at=now,
            updated_at=now,
            grace_deadline=grace,
            budget_usd=self._task_config.budget_per_task_usd,
        )

    # -- Persistence ----------------------------------------------------------

    def _load_state(self) -> None:
        self._sessions = load_sessions()
        recovered = recover_sessions(self._sessions)
        if recovered:
            logger.info("Recovered %d RUNNING session(s) -> DETECTED", recovered)

        max_age = self._task_config.checkpoint_max_age_minutes
        restorations: dict[int, TaskSession] = {}
        for sid, session in self._sessions.items():
            if session.state != TaskSessionState.DETECTED:
                continue
            cp = load_checkpoint(sid)
            if cp is None or not is_checkpoint_fresh(cp, max_age_minutes=max_age):
                continue
            phase = cp.get("phase", "")
            session_data = {
                k: v for k, v in cp.items() if k not in ("phase", "saved_at")
            }
            try:
                restored_session = TaskSession.from_dict(session_data)
                restored_session.state = TaskSessionState.DETECTED
                restored_session.checkpoint_phase = phase
                restorations[sid] = restored_session
                logger.info(
                    "Restored session #%d from checkpoint (phase=%s)", sid, phase
                )
            except Exception:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Failed to restore session #%d from checkpoint", sid, exc_info=True
                )
        if restorations:
            self._sessions.update(restorations)
            logger.info("Restored %d session(s) from checkpoints", len(restorations))

        self._processed_ids = {
            sid
            for sid, s in self._sessions.items()
            if s.state in (TaskSessionState.COMPLETED, TaskSessionState.FAILED)
        }
        batch_file = self.SESSIONS_DIR / "golem_batches.json"
        self._batch_monitor.load(batch_file)

    def _save_state(self) -> None:
        """Persist sessions and batch state atomically using two-phase write.

        Phase 1 writes both payloads to temp files (no rename yet).
        Phase 2 renames both in sequence, narrowing the inconsistency window
        to a single ``os.replace`` call rather than the entire write duration.
        """
        with self._save_lock:
            sessions_path = SESSIONS_FILE
            batch_path = self.SESSIONS_DIR / "golem_batches.json"

            sessions_path.parent.mkdir(parents=True, exist_ok=True)
            batch_path.parent.mkdir(parents=True, exist_ok=True)

            # --- Phase 1: serialize both payloads and write to temp files ---
            sessions_payload = _serialize_sessions_payload(self._sessions)
            batch_payload = self._batch_monitor.serialize()

            s_fd, s_tmp = tempfile.mkstemp(
                dir=str(sessions_path.parent), prefix=".sessions_", suffix=".tmp"
            )
            b_fd, b_tmp = tempfile.mkstemp(
                dir=str(batch_path.parent), prefix=".batches_", suffix=".tmp"
            )
            s_closed = b_closed = False
            try:
                os.write(s_fd, sessions_payload)
                os.fsync(s_fd)
                os.close(s_fd)
                s_closed = True

                os.write(b_fd, batch_payload)
                os.fsync(b_fd)
                os.close(b_fd)
                b_closed = True

                # --- Phase 2: rename both (narrow crash window) ---
                os.replace(s_tmp, str(sessions_path))
                os.replace(b_tmp, str(batch_path))
            except BaseException:
                if not s_closed:
                    os.close(s_fd)
                if not b_closed:
                    os.close(b_fd)
                for tmp in (s_tmp, b_tmp):
                    try:
                        os.unlink(tmp)
                    except OSError:
                        pass
                raise

    def reset_state(self) -> None:
        """Delete all session state."""
        self._sessions.clear()
        self._trackers.clear()
        self._processed_ids.clear()
        self._batch_monitor = BatchMonitor()
        self._notified_batches.clear()
        batch_file = self.SESSIONS_DIR / "golem_batches.json"
        if batch_file.exists():
            batch_file.unlink()
        if SESSIONS_FILE.exists():
            SESSIONS_FILE.unlink()
            logger.info("Golem session state reset")
