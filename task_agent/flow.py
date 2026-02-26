"""Task-agent flow — orchestrates long-running tasks via a tick-driven state machine (v2).

Detects Redmine issues tagged ``[AGENT]``, manages TaskSessions, and drives
single-agent-per-task execution through periodic ticks.  Real-time monitoring
via ``TaskEventTracker`` per session.

Key exports:
- ``TaskAgentFlow`` — the main flow class; implements ``BaseFlow``,
  ``PollableFlow``, and ``WebhookableFlow``.  Manages session lifecycle from
  detection through completion/failure and sends Teams notifications at each
  state transition.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .core.config import Config, DATA_DIR, TaskAgentFlowConfig
from .core.live_state import LiveState
from .core.triggers.base import TriggerEvent
from .core.flow_base import BaseFlow, FlowResult, PollableFlow, WebhookableFlow

from .event_tracker import Milestone, TaskEventTracker
from .orchestrator import (
    TaskOrchestrator,
    TaskSession,
    TaskSessionState,
    load_sessions,
    recover_sessions,
    save_sessions,
    _now_iso,
)
from .profile import TaskAgentProfile, build_profile

logger = logging.getLogger("Tools.AgentAutomation.Flows.TaskAgent")


class TaskAgentFlow(BaseFlow, PollableFlow, WebhookableFlow):
    """Detects [AGENT] issues and orchestrates single-agent-per-task execution.

    Unlike the other flows this is *not* an AIFlow — it does not follow the
    prefetch -> prompt -> Claude -> parse -> execute pipeline.  Instead it manages
    ``TaskSession`` state machines that internally invoke Claude agents for
    full task execution, monitored via event streams.
    """

    SESSIONS_DIR = DATA_DIR / "state"

    def __init__(self, config: Config, flow_config: TaskAgentFlowConfig | None = None):
        super().__init__(config, flow_config)
        self._task_config = self.typed_config(TaskAgentFlowConfig)
        self._sessions: dict[int, TaskSession] = {}
        self._trackers: dict[int, TaskEventTracker] = {}
        self._processed_ids: set[int] = set()
        self._running = False
        self._tick_task: asyncio.Task | None = None
        self._work_dir_lock = asyncio.Lock()

        # Build pluggable profile from config (always required)
        profile_name = self._task_config.profile if self._task_config else "redmine"
        self._profile: TaskAgentProfile = build_profile(profile_name, config)

        self._load_state()

    @property
    def name(self) -> str:
        return "task_agent"

    @property
    def mcp_servers(self) -> list[str]:
        return self._profile.tool_provider.base_servers()

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

        logger.info("Created new task session for #%d: %s", issue_id, subject[:60])

        self._profile.notifier.notify_started(issue_id, subject)

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

        new_items = []
        for issue in issues:
            iid = issue.get("id")
            if iid and iid not in self._sessions and iid not in self._processed_ids:
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
        return f"task_agent-{issue_id}-{timestamp}"

    def on_item_success(self, item_id: Any) -> None:
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
        return f"wh-task_agent-{issue_id}-{timestamp}"

    # -- Tick loop ------------------------------------------------------------

    def start_tick_loop(self) -> asyncio.Task:
        """Start the background tick loop.  Returns the asyncio Task."""
        if self._tick_task is not None:
            return self._tick_task
        self._running = True
        self._tick_task = asyncio.create_task(self._session_tick_loop())
        logger.info(
            "Task-agent tick loop started (interval=%ds)",
            self._task_config.tick_interval,
        )
        return self._tick_task

    def stop_tick_loop(self) -> None:
        """Stop the background tick loop."""
        self._running = False
        if self._tick_task is not None:
            self._tick_task.cancel()
            self._tick_task = None

    async def _session_tick_loop(self) -> None:
        """Periodically detect new issues and tick all active sessions."""
        while self._running:
            try:
                self._detect_new_issues()
                await self._tick_all_sessions()
            except Exception:  # pylint: disable=broad-exception-caught
                logger.exception("Error in session tick loop")
            await asyncio.sleep(self._task_config.tick_interval)

    def _detect_new_issues(self) -> None:
        """Poll Redmine for new [AGENT] issues and create sessions."""
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
            # Register in LiveState so the dashboard shows it immediately.
            event_id = f"task_agent-{iid}"
            model = self._task_config.task_model or "sonnet"
            live.enqueue(event_id, "task_agent", model)
            live.update_phase(event_id, "detected")
            logger.info("Detected new task: #%d %s", iid, subject[:60])

    async def _tick_all_sessions(self) -> None:
        """Run one tick for every active session (concurrent up to max_active)."""
        active = {
            sid: s
            for sid, s in self._sessions.items()
            if s.state
            not in (
                TaskSessionState.COMPLETED,
                TaskSessionState.FAILED,
            )
        }

        if not active:
            return

        max_concurrent = self._task_config.max_active_sessions or 3
        sem = asyncio.Semaphore(max_concurrent)

        async def _tick_one(session: "TaskSession") -> None:
            async with sem:
                prev_state = session.state
                orchestrator = TaskOrchestrator(
                    session,
                    self.config,
                    self._task_config,
                    on_progress=self._on_agent_progress,
                    work_dir_lock=self._work_dir_lock,
                    save_callback=self._save_state,
                    profile=self._profile,
                )
                await orchestrator.tick()
                self._handle_state_transition(session, prev_state)

        await asyncio.gather(
            *(_tick_one(s) for s in active.values()),
            return_exceptions=True,
        )

        self._save_state()

    def _on_agent_progress(self, session: TaskSession, milestone: Milestone) -> None:
        """Central progress handler — updates LiveState and session from milestones."""
        live = LiveState.get()
        event_id = f"task_agent-{session.parent_issue_id}"
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
        event_id = f"task_agent-{sid}"
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
            )

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
                )
            else:
                reason = session.errors[-1] if session.errors else "Unknown error"
                self._profile.notifier.notify_failed(
                    sid,
                    session.parent_subject,
                    reason[:200],
                    cost_usd=session.total_cost_usd,
                    duration_s=session.duration_seconds,
                )

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
        self._processed_ids = {
            sid
            for sid, s in self._sessions.items()
            if s.state in (TaskSessionState.COMPLETED, TaskSessionState.FAILED)
        }

    def _save_state(self) -> None:
        save_sessions(self._sessions)

    def reset_state(self) -> None:
        """Delete all session state."""
        self._sessions.clear()
        self._trackers.clear()
        self._processed_ids.clear()
        from .orchestrator import SESSIONS_FILE

        if SESSIONS_FILE.exists():
            SESSIONS_FILE.unlink()
            logger.info("Task-agent session state reset")
