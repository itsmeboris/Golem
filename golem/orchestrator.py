"""Durable state-machine orchestrator for golem sessions (v2).

Each ``TaskSession`` progresses through a 6-state lifecycle:
DETECTED → RUNNING → VALIDATING → COMPLETED / RETRYING → COMPLETED / FAILED.

The orchestrator spawns one Claude agent per task (not per subtask).  Real-time
visibility comes from ``TaskEventTracker`` which processes stream-json events
into structured ``Milestone`` objects.

After execution the orchestrator runs a 5-phase pipeline:
  1. Execute — invoke the agent with event-stream monitoring
  2. Persist — write JSONL traces and prompt files to disk
  3. Validate — spawn a cheap (opus) validation agent to review the work
  4. Retry or Escalate — retry once on PARTIAL, escalate on FAIL
  5. Commit — deterministic git commit for PASS verdicts with code changes

State is checkpointed to disk after every tick so the system survives daemon
restarts.  On restart, in-flight sessions are reset to DETECTED for re-spawn.
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from .core.cli_wrapper import CLIConfig, CLIResult, CLIType, invoke_cli_monitored
from .core.config import DATA_DIR, PROJECT_ROOT, GolemFlowConfig
from .core.defaults import _now_iso  # re-exported for backward compat (flow.py)
from .core.report import ReportWriter
from .core.run_log import RunRecord, format_duration, record_run
from .core.flow_base import _write_prompt, _write_trace

from .committer import commit_changes
from .core.log_context import SessionLogAdapter
from .errors import InfrastructureError
from .event_tracker import Milestone, TaskEventTracker, TrackerState
from .interfaces import TaskStatus
from .profile import GolemProfile
from .validation import ValidationVerdict, run_validation
from .workdir import resolve_work_dir
from .worktree_manager import cleanup_worktree, create_worktree

logger = logging.getLogger("golem.orchestrator")

SESSIONS_FILE = DATA_DIR / "state" / "golem_sessions.json"

# Report paths (parallel to other flows)
_REPORT_DIR = DATA_DIR / "reports" / "golem"
_REPORT_INDEX = DATA_DIR / "reports" / "golem_report.md"


class TaskSessionState(str, Enum):
    """Lifecycle states for a golem session (v2).

    DETECTED → RUNNING → VALIDATING ─── PASS ──→ COMPLETED
                                     ├── PARTIAL → RETRYING → VALIDATING → ...
                                     └── FAIL ──→ FAILED (escalate)
    """

    DETECTED = "detected"
    RUNNING = "running"
    VALIDATING = "validating"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskSession:
    """Persistent state for a single [AGENT] task orchestration (v2).

    One agent handles the full task lifecycle — no subtask records.
    Real-time tracking is provided by the event tracker.
    """

    parent_issue_id: int
    parent_subject: str = ""
    state: TaskSessionState = TaskSessionState.DETECTED
    priority: int = 5
    created_at: str = ""
    updated_at: str = ""
    grace_deadline: str = ""
    budget_usd: float = 10.0
    total_cost_usd: float = 0.0
    # Real-time tracking (populated from event_tracker)
    tools_called: list[str] = field(default_factory=list)
    mcp_tools_called: list[str] = field(default_factory=list)
    last_activity: str = ""
    errors: list[str] = field(default_factory=list)
    milestone_count: int = 0
    event_log: list[dict] = field(default_factory=list)
    # Result
    result_summary: str = ""
    duration_seconds: float = 0.0
    # Validation & commit (v2)
    validation_verdict: str = ""
    validation_confidence: float = 0.0
    validation_summary: str = ""
    validation_concerns: list[str] = field(default_factory=list)
    validation_cost_usd: float = 0.0
    retry_count: int = 0
    commit_sha: str = ""
    trace_file: str = ""
    retry_trace_file: str = ""
    # Subagent orchestration
    execution_mode: str = ""  # "subagent" | "monolithic" | "prompt"
    # "orchestrating" | "validating" | "committing"
    supervisor_phase: str = ""
    # Cross-task coordination
    depends_on: list[int] = field(default_factory=list)
    group_id: str = ""
    # Merge queue support — set by orchestrator, consumed by flow
    merge_ready: bool = False
    worktree_path: str = ""
    base_work_dir: str = ""
    infra_retry_count: int = 0
    cli_session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskSession":
        """Deserialize from a dictionary."""
        return cls(
            parent_issue_id=data["parent_issue_id"],
            parent_subject=data.get("parent_subject", ""),
            state=TaskSessionState(data["state"]),
            priority=data.get("priority", 5),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
            grace_deadline=data.get("grace_deadline", ""),
            budget_usd=data.get("budget_usd", 10.0),
            total_cost_usd=data.get("total_cost_usd", 0.0),
            tools_called=data.get("tools_called", []),
            mcp_tools_called=data.get("mcp_tools_called", []),
            last_activity=data.get("last_activity", ""),
            errors=data.get("errors", []),
            milestone_count=data.get("milestone_count", 0),
            event_log=data.get("event_log", []),
            result_summary=data.get("result_summary", ""),
            duration_seconds=data.get("duration_seconds", 0.0),
            validation_verdict=data.get("validation_verdict", ""),
            validation_confidence=data.get("validation_confidence", 0.0),
            validation_summary=data.get("validation_summary", ""),
            validation_concerns=data.get("validation_concerns", []),
            validation_cost_usd=data.get("validation_cost_usd", 0.0),
            retry_count=data.get("retry_count", 0),
            commit_sha=data.get("commit_sha", ""),
            trace_file=data.get("trace_file", ""),
            retry_trace_file=data.get("retry_trace_file", ""),
            execution_mode=data.get("execution_mode", ""),
            supervisor_phase=data.get("supervisor_phase", ""),
            depends_on=data.get("depends_on", []),
            group_id=data.get("group_id", ""),
            merge_ready=data.get("merge_ready", False),
            worktree_path=data.get("worktree_path", ""),
            base_work_dir=data.get("base_work_dir", ""),
            infra_retry_count=data.get("infra_retry_count", 0),
            cli_session_id=data.get("cli_session_id", ""),
        )


# Type alias for progress callbacks: (session, milestone) -> None
ProgressCallback = Any


class TaskOrchestrator:
    """Drives a TaskSession through its lifecycle via periodic ticks (v2).

    State machine: DETECTED → RUNNING → VALIDATING → COMPLETED / FAILED.
    After the agent finishes, the orchestrator validates, optionally retries
    once, commits code changes, and writes traces/reports/run-log entries.
    """

    def __init__(
        self,
        session: TaskSession,
        config: Any,
        task_config: GolemFlowConfig,
        *,
        on_progress: ProgressCallback | None = None,
        work_dir_lock: asyncio.Lock | None = None,
        save_callback: Any | None = None,
        profile: GolemProfile | None = None,
        event_callback: Any | None = None,
        work_dir_override: str | None = None,
    ):
        self.session = session
        self.config = config
        self.task_config = task_config
        self._on_progress = on_progress
        self._work_dir_lock = work_dir_lock or asyncio.Lock()
        self._save_callback = save_callback
        self.profile: GolemProfile = profile  # type: ignore[assignment]
        self._event_callback = event_callback
        self._work_dir_override = work_dir_override
        self._last_checkpoint_time: float = 0.0
        self._checkpoint_interval: float = 10.0  # seconds between disk writes
        self._slog = SessionLogAdapter(
            logger,
            session_id=session.parent_issue_id,
            subject=session.parent_subject,
        )

    # -- Profile-based helpers ------------------------------------------------

    def _update_task(
        self,
        task_id: int,
        *,
        status: str | None = None,
        progress: int | None = None,
        comment: str | None = None,
    ) -> None:
        """Update task via profile backend."""
        if status:
            self.profile.state_backend.update_status(task_id, status)
        if progress is not None:
            self.profile.state_backend.update_progress(task_id, progress)
        if comment:
            self.profile.state_backend.post_comment(task_id, comment)

    def _get_description(self, task_id: int) -> str:
        """Fetch task description via profile."""
        return self.profile.task_source.get_task_description(task_id)

    def _format_prompt(self, name: str, **kwargs: Any) -> str:
        """Format a prompt template via profile."""
        return self.profile.prompt_provider.format(name, **kwargs)

    def _get_mcp_servers(self, subject: str) -> list[str]:
        """Determine MCP servers via profile."""
        return self.profile.tool_provider.servers_for_subject(subject)

    def _chain_event_callback(self, tracker_callback):
        """Wrap *tracker_callback* with the optional CLI event_callback."""
        if not self._event_callback:
            return tracker_callback
        ecb = self._event_callback

        def chained(event):
            ecb(event)
            tracker_callback(event)

        return chained

    async def tick(self) -> TaskSession:
        """Advance the session state machine by one tick."""
        self.session.updated_at = _now_iso()

        if self.session.state == TaskSessionState.DETECTED:
            await self._tick_detected()
        # RUNNING is handled within _tick_detected (blocks until agent finishes)
        # COMPLETED and FAILED are terminal — no action

        return self.session

    async def run_once(self) -> TaskSession:
        """Run the full pipeline immediately.  For CLI / one-shot use.

        Skips the grace period entirely and transitions straight to RUNNING.
        """
        self.session.state = TaskSessionState.RUNNING
        self.session.updated_at = _now_iso()
        await self._run_agent()
        return self.session

    async def _tick_detected(self) -> None:
        """Wait for grace period, then spawn the agent."""
        now = datetime.now(timezone.utc)
        deadline = datetime.fromisoformat(self.session.grace_deadline)

        if now < deadline:
            return  # Still in grace period

        # Transition to RUNNING and spawn agent
        self._slog.info(
            "Grace period elapsed, spawning agent",
        )
        self.session.state = TaskSessionState.RUNNING
        self.session.updated_at = _now_iso()

        await self._run_agent()

    async def _run_agent(self) -> None:
        """Dispatch to subagent orchestration or monolithic pipeline."""
        if self.task_config.supervisor_mode:
            from .supervisor_v2_subagent import SubagentSupervisor

            sup = SubagentSupervisor(
                self.session,
                self.config,
                self.task_config,
                on_milestone=self._on_milestone,
                work_dir_lock=self._work_dir_lock,
                save_callback=self._save_callback,
                profile=self.profile,
                event_callback=self._event_callback,
                work_dir_override=self._work_dir_override,
            )
            await sup.run()
        else:
            await self._run_agent_monolithic()

    def _resolve_workdir(self, issue_id: int, description: str) -> tuple[str, str]:
        """Return ``(work_dir, worktree_path)`` for a session."""
        if self._work_dir_override:
            base_work_dir = self._work_dir_override
        else:
            base_work_dir = resolve_work_dir(
                subject=self.session.parent_subject,
                description=description,
                work_dirs=self.task_config.work_dirs,
                default_work_dir=self.task_config.default_work_dir,
                project_root=str(PROJECT_ROOT),
            )
        self.session.base_work_dir = base_work_dir
        work_dir = base_work_dir
        worktree_path = ""
        if self.task_config.use_worktrees:
            try:
                worktree_path = create_worktree(base_work_dir, issue_id)
                work_dir = worktree_path
                self.session.worktree_path = worktree_path
                self._slog.info("Using worktree at %s", work_dir)
            except RuntimeError as wt_err:
                raise InfrastructureError(
                    f"Worktree creation failed: {wt_err}"
                ) from wt_err
        return work_dir, worktree_path

    async def _run_agent_monolithic(self) -> None:  # pylint: disable=too-many-locals
        """Single-agent 5-phase pipeline."""
        issue_id = self.session.parent_issue_id
        description = self._get_description(issue_id)
        work_dir, worktree_path = self._resolve_workdir(issue_id, description)
        base_work_dir = self.session.base_work_dir

        self._preflight_check(work_dir)

        start = time.time()
        result: CLIResult | None = None
        tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )
        prompt = ""

        try:
            prompt = self._format_prompt(
                "run_task.txt",
                issue_id=issue_id,
                task_description=description,
            )
            mcp_servers = self._get_mcp_servers(self.session.parent_subject)
            cli_config = CLIConfig(
                cli_type=CLIType.CLAUDE,
                model=self.task_config.task_model,
                max_budget_usd=self.session.budget_usd,
                timeout_seconds=self.task_config.task_timeout_seconds,
                mcp_servers=mcp_servers,
                cwd=work_dir,
            )
            callback = self._chain_event_callback(tracker.handle_event)
            async with self._work_dir_lock:
                result = await asyncio.get_running_loop().run_in_executor(
                    None, invoke_cli_monitored, prompt, cli_config, callback
                )
            self._populate_session_from_tracker(tracker, result, time.time() - start)
            self._persist_traces(issue_id, prompt, result)
            self._update_task(issue_id, status=TaskStatus.FIXED, progress=80)

            verdict = await self._run_validation(issue_id, work_dir)

            if (
                verdict.verdict == "PARTIAL"
                and self.session.retry_count < self.task_config.max_retries
            ):
                await self._retry_agent(verdict, work_dir, mcp_servers)
            elif verdict.verdict != "PASS":
                self._escalate(verdict)
                return

            self._commit_and_complete(issue_id, work_dir, verdict)

            if worktree_path and self.session.commit_sha:
                self.session.merge_ready = True
                worktree_path = ""

        except InfrastructureError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._handle_agent_failure(issue_id, exc, start, tracker, result, prompt)

        finally:
            if worktree_path:
                cleanup_worktree(
                    base_work_dir,
                    worktree_path,
                    keep_branch=self.session.state == TaskSessionState.FAILED,
                )
            self._write_report()
            self._record_run()

    def _preflight_check(self, work_dir: str) -> None:
        """Validate environment before agent execution."""
        path = Path(work_dir)
        if not path.is_dir():
            raise InfrastructureError(f"Work dir does not exist: {work_dir}")
        git_dir = path / ".git"
        if not git_dir.exists():
            from .worktree_manager import _run_git

            probe = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=work_dir)
            if probe.returncode != 0:
                raise InfrastructureError(f"Not a git repo: {work_dir}")
        settings = path / ".claude" / "settings.local.json"
        if not settings.exists():
            self._copy_claude_settings(work_dir)

    @staticmethod
    def _copy_claude_settings(work_dir: str) -> None:
        """Copy .claude/settings.local.json into the work dir if available."""
        import shutil

        src = PROJECT_ROOT / ".claude" / "settings.local.json"
        if src.exists():
            dest = Path(work_dir) / ".claude"
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dest / "settings.local.json"))
            logger.debug("Copied .claude/settings.local.json into %s", work_dir)

    # -- Pipeline helpers --------------------------------------------------------

    def _persist_traces(
        self, issue_id: int, prompt: str, result: CLIResult | None
    ) -> None:
        """Phase 2: Write prompt and trace files to disk."""
        event_id = f"golem-{issue_id}"
        if prompt:
            _write_prompt("golem", event_id, prompt)
        if result and result.trace_events:
            self.session.trace_file = _write_trace(
                "golem", event_id, result.trace_events
            )

    async def _run_validation(self, issue_id: int, work_dir: str) -> ValidationVerdict:
        """Phase 3: Spawn the validation agent and store the verdict."""
        self.session.state = TaskSessionState.VALIDATING
        self.session.updated_at = _now_iso()

        tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )
        callback = self._chain_event_callback(tracker.handle_event)

        description = self._get_description(issue_id)
        verdict = await self._run_validation_in_executor(
            issue_id=issue_id,
            subject=self.session.parent_subject,
            description=description,
            session_data=self.session.to_dict(),
            work_dir=work_dir,
            model=self.task_config.validation_model,
            budget_usd=self.task_config.validation_budget_usd,
            timeout_seconds=self.task_config.validation_timeout_seconds,
            callback=callback,
        )
        self._apply_verdict(verdict)

        self._slog.info(
            "Validation verdict=%s confidence=%.2f",
            verdict.verdict,
            verdict.confidence,
        )
        return verdict

    async def _run_validation_in_executor(self, **kwargs) -> ValidationVerdict:
        from functools import partial

        return await asyncio.get_running_loop().run_in_executor(
            None, partial(run_validation, **kwargs)
        )

    def _commit_and_complete(
        self, issue_id: int, work_dir: str, verdict: ValidationVerdict
    ) -> None:
        """Phase 5: Commit changes (if applicable) and mark session COMPLETED."""
        if self.task_config.auto_commit and self.session.validation_verdict == "PASS":
            task_type = verdict.task_type if verdict.verdict == "PASS" else "other"
            cr = commit_changes(
                work_dir=work_dir,
                issue_id=issue_id,
                subject=self.session.parent_subject,
                task_type=task_type,
                summary=self.session.validation_summary,
            )
            if cr.committed:
                self.session.commit_sha = cr.sha
                self._slog.info("Committed %s", cr.sha)
            elif cr.error:
                self._slog.warning("Commit failed: %s", cr.error)
                self.session.state = TaskSessionState.FAILED
                self.session.errors.append(f"commit failed: {cr.error}")
                self._update_task(
                    issue_id,
                    status=TaskStatus.IN_PROGRESS,
                    comment=(
                        f"Agent work passed validation but commit failed "
                        f"(pre-commit hook). Worktree branch preserved for "
                        f"manual recovery.\n\nError:\n```\n{cr.error}\n```"
                    ),
                )
                return

        self.session.state = TaskSessionState.COMPLETED
        self.session.updated_at = _now_iso()

        extras = ""
        if self.session.commit_sha:
            extras += f", commit {self.session.commit_sha}"
        if self.session.retry_count:
            extras += f", {self.session.retry_count} retry"

        self._update_task(
            issue_id,
            status=TaskStatus.CLOSED,
            progress=100,
            comment=(
                f"Task completed by agent "
                f"(${self.session.total_cost_usd:.2f}, "
                f"{format_duration(self.session.duration_seconds)}, "
                f"{self.session.milestone_count} milestones, "
                f"validation={self.session.validation_verdict}{extras})"
            ),
        )
        self._slog.info(
            "Completed ($%.2f, %.0fs, verdict=%s)",
            self.session.total_cost_usd,
            self.session.duration_seconds,
            self.session.validation_verdict,
        )

    def _handle_agent_failure(
        self,
        issue_id: int,
        exc: Exception,
        start: float,
        tracker: TaskEventTracker,
        result: CLIResult | None,
        prompt: str,
    ) -> None:
        """Handle exception from the pipeline — persist state and notify."""
        elapsed = time.time() - start
        self._populate_session_from_tracker(tracker, result, elapsed)
        self.session.state = TaskSessionState.FAILED
        self.session.errors.append(str(exc))
        self._persist_traces(issue_id, prompt, result)
        self._update_task(
            issue_id,
            comment=f"Agent failed after {format_duration(elapsed)}: {exc}",
        )
        self._slog.error("Agent failed after %.0fs: %s", elapsed, exc)

    def _apply_verdict(self, verdict: ValidationVerdict) -> None:
        """Store a validation verdict into the session."""
        self.session.validation_verdict = verdict.verdict
        self.session.validation_confidence = verdict.confidence
        self.session.validation_summary = verdict.summary
        self.session.validation_concerns = verdict.concerns
        self.session.validation_cost_usd += verdict.cost_usd
        self.session.total_cost_usd += verdict.cost_usd

    async def _retry_agent(  # pylint: disable=too-many-locals
        self,
        verdict: ValidationVerdict,
        work_dir: str,
        mcp_servers: list[str],
    ) -> None:
        """Spawn a focused retry agent and re-validate."""
        issue_id = self.session.parent_issue_id
        self.session.state = TaskSessionState.RETRYING
        self.session.retry_count += 1
        self.session.updated_at = _now_iso()

        self._slog.info("Retrying (attempt %d)", self.session.retry_count)

        concerns_text = (
            "\n".join(f"- {c}" for c in verdict.concerns) or "- (none specified)"
        )

        retry_prompt = self._format_prompt(
            "retry_task.txt",
            issue_id=issue_id,
            original_summary=self.session.result_summary or "(no summary)",
            validation_verdict=verdict.verdict,
            validation_summary=verdict.summary,
            concerns=concerns_text,
            event_log_summary="\n".join(
                f"- {e.get('kind', '?')}: {e.get('summary', '')[:80]}"
                for e in self.session.event_log[-15:]
            )
            or "(no events)",
        )

        retry_tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )

        cli_config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=self.task_config.task_model,
            max_budget_usd=self.task_config.retry_budget_usd,
            timeout_seconds=self.task_config.task_timeout_seconds,
            mcp_servers=mcp_servers,
            cwd=work_dir,
        )

        retry_start = time.time()
        callback = self._chain_event_callback(retry_tracker.handle_event)
        async with self._work_dir_lock:
            retry_result = await asyncio.get_running_loop().run_in_executor(
                None,
                invoke_cli_monitored,
                retry_prompt,
                cli_config,
                callback,
            )

        retry_elapsed = time.time() - retry_start
        self.session.duration_seconds += retry_elapsed
        if retry_result:
            self.session.total_cost_usd += retry_result.cost_usd

        # Persist retry trace
        retry_event_id = f"golem-{issue_id}-retry"
        _write_prompt("golem", retry_event_id, retry_prompt)
        if retry_result and retry_result.trace_events:
            self.session.retry_trace_file = _write_trace(
                "golem", retry_event_id, retry_result.trace_events
            )

        # Re-validate after retry
        self.session.state = TaskSessionState.VALIDATING
        self.session.updated_at = _now_iso()

        retry_val_tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )
        retry_val_callback = self._chain_event_callback(retry_val_tracker.handle_event)

        description = self._get_description(issue_id)
        session_data = self.session.to_dict()

        retry_verdict = await self._run_validation_in_executor(
            issue_id=issue_id,
            subject=self.session.parent_subject,
            description=description,
            session_data=session_data,
            work_dir=work_dir,
            model=self.task_config.validation_model,
            budget_usd=self.task_config.validation_budget_usd,
            timeout_seconds=self.task_config.validation_timeout_seconds,
            callback=retry_val_callback,
        )
        self._apply_verdict(retry_verdict)

        self._slog.info(
            "Retry validation verdict=%s",
            retry_verdict.verdict,
        )

        if retry_verdict.verdict != "PASS":
            self._escalate(retry_verdict)

    def _escalate(self, verdict: ValidationVerdict) -> None:
        """Mark session FAILED and post escalation details to Redmine."""
        issue_id = self.session.parent_issue_id
        self.session.state = TaskSessionState.FAILED
        self.session.updated_at = _now_iso()

        concerns_text = "\n".join(f"- {c}" for c in verdict.concerns) or "- (none)"

        notes = (
            f"**Golem escalation — needs human review**\n\n"
            f"Verdict: {verdict.verdict} (confidence: {verdict.confidence:.0%})\n"
            f"Summary: {verdict.summary}\n\n"
            f"Concerns:\n{concerns_text}\n\n"
            f"Cost: ${self.session.total_cost_usd:.2f} | "
            f"Duration: {format_duration(self.session.duration_seconds)} | "
            f"Retries: {self.session.retry_count}"
        )

        self._update_task(
            issue_id,
            status=TaskStatus.IN_PROGRESS,
            comment=notes,
        )

        self._slog.warning(
            "Escalated (verdict=%s, retries=%d)",
            verdict.verdict,
            self.session.retry_count,
        )

    def _write_report(self) -> None:
        """Write a Markdown detail report and append to the index."""
        issue_id = self.session.parent_issue_id
        try:
            writer = ReportWriter(_REPORT_DIR, _REPORT_INDEX)

            tools_str = ", ".join(self.session.tools_called) or "none"
            mcp_str = ", ".join(self.session.mcp_tools_called) or "none"
            concerns_str = (
                "\n".join(f"- {c}" for c in self.session.validation_concerns)
                or "- (none)"
            )
            errors_str = "\n".join(f"- {e}" for e in self.session.errors) or "- (none)"
            events_str = "\n".join(
                f"| {str(e.get('timestamp', '?'))[:19]} | {e.get('kind', '?')} "
                f"| {e.get('tool_name', '')} | {str(e.get('summary', ''))[:60]} |"
                for e in self.session.event_log[-50:]
            )

            detail = (
                f"# Golem Report: #{issue_id}\n\n"
                f"**Subject**: {self.session.parent_subject}\n"
                f"**State**: {self.session.state.value}\n"
                f"**Created**: {self.session.created_at}\n\n"
                f"## Metrics\n\n"
                f"| Metric | Value |\n|---|---|\n"
                f"| Cost | ${self.session.total_cost_usd:.2f} |\n"
                f"| Duration | {format_duration(self.session.duration_seconds)} |\n"
                f"| Milestones | {self.session.milestone_count} |\n"
                f"| Retries | {self.session.retry_count} |\n"
                f"| Commit | {self.session.commit_sha or '(none)'} |\n\n"
                f"## Validation\n\n"
                f"**Verdict**: {self.session.validation_verdict or '(not run)'}\n"
                f"**Summary**: {self.session.validation_summary or '(none)'}\n"
                f"**Validation cost**: ${self.session.validation_cost_usd:.2f}\n\n"
                f"**Concerns**:\n{concerns_str}\n\n"
                f"## Tools\n\n"
                f"- **Built-in**: {tools_str}\n"
                f"- **MCP**: {mcp_str}\n\n"
                f"## Errors\n\n{errors_str}\n\n"
                f"## Event Log\n\n"
                f"| Time | Kind | Tool | Summary |\n|---|---|---|---|\n"
                f"{events_str}\n\n"
                f"## Traces\n\n"
                f"- Primary: `{self.session.trace_file or '(none)'}`\n"
                f"- Retry: `{self.session.retry_trace_file or '(none)'}`\n"
            )

            filename = f"{issue_id}.md"
            writer.write_detail(filename, detail)

            verdict_tag = self.session.validation_verdict or "-"
            link = writer.detail_link(filename)
            header = (
                "# Golem Reports\n\n"
                "| Issue | Subject | Verdict | Cost | Duration | Report |\n"
                "|---|---|---|---|---|---|\n"
            )
            row = (
                f"| #{issue_id} "
                f"| {self.session.parent_subject[:50]} "
                f"| {verdict_tag} "
                f"| ${self.session.total_cost_usd:.2f} "
                f"| {format_duration(self.session.duration_seconds)} "
                f"| {link} |\n"
            )
            writer.append_index(row, header=header)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._slog.warning("Failed to write report: %s", exc)

    def _record_run(self) -> None:
        """Append a RunRecord to runs.jsonl."""
        issue_id = self.session.parent_issue_id
        try:
            record = RunRecord(
                event_id=f"golem-{issue_id}",
                flow="golem",
                task_id=str(issue_id),
                source="orchestrator",
                started_at=self.session.created_at,
                finished_at=_now_iso(),
                duration_s=self.session.duration_seconds,
                success=self.session.state == TaskSessionState.COMPLETED,
                error=self.session.errors[-1] if self.session.errors else None,
                model=self.task_config.task_model,
                cost_usd=self.session.total_cost_usd,
                actions_taken=[
                    f"verdict:{self.session.validation_verdict}",
                    f"commit:{self.session.commit_sha or 'none'}",
                    f"retries:{self.session.retry_count}",
                ],
                verdict=self.session.validation_verdict,
                trace_file=self.session.trace_file,
            )
            record_run(record)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._slog.warning("Failed to record run: %s", exc)

    def _on_milestone(self, milestone: Milestone, tracker_state: TrackerState) -> None:
        """Called for each milestone — updates session and notifies flow layer."""
        self.session.last_activity = (
            tracker_state.last_text or milestone.summary or milestone.kind
        )
        self.session.milestone_count = tracker_state.milestone_count
        self.session.tools_called = list(tracker_state.tools_called)
        self.session.mcp_tools_called = list(tracker_state.mcp_tools_called)
        self.session.errors = list(tracker_state.errors)

        # Append milestone to session event_log for live trace view.
        # Cap at 500 entries to prevent unbounded growth.
        entry: dict = {
            "kind": milestone.kind,
            "tool_name": milestone.tool_name,
            "summary": milestone.summary,
            "timestamp": milestone.timestamp,
            "is_error": milestone.is_error,
        }
        self.session.event_log.append(entry)
        if len(self.session.event_log) > 500:
            self.session.event_log = self.session.event_log[-500:]

        if self._on_progress:
            self._on_progress(self.session, milestone)

        # Throttled disk checkpoint so the dashboard shows near-real-time
        # progress instead of waiting until the subtask/agent finishes.
        self._throttled_checkpoint()

    def _throttled_checkpoint(self) -> None:
        """Persist session to disk at most once per ``_checkpoint_interval``."""
        now = time.time()
        if now - self._last_checkpoint_time >= self._checkpoint_interval:
            self._last_checkpoint_time = now
            if self._save_callback:
                try:
                    self._save_callback()
                except Exception:  # pylint: disable=broad-exception-caught
                    self._slog.debug("Checkpoint save failed", exc_info=True)

    def _populate_session_from_tracker(
        self,
        tracker: TaskEventTracker,
        result: CLIResult | None,
        elapsed: float,
    ) -> None:
        """Copy final tracker state into the session."""
        state = tracker.state
        self.session.tools_called = list(state.tools_called)
        self.session.mcp_tools_called = list(state.mcp_tools_called)
        self.session.errors = list(state.errors)
        self.session.last_activity = state.last_text or state.last_activity
        self.session.milestone_count = state.milestone_count
        self.session.duration_seconds = elapsed
        self.session.event_log = [
            {
                "kind": m.kind,
                "tool_name": m.tool_name,
                "summary": m.summary,
                "timestamp": m.timestamp,
                "is_error": m.is_error,
            }
            for m in state.event_log
        ]

        if result is not None:
            self.session.total_cost_usd = result.cost_usd
            self.session.result_summary = str(result.output.get("result", ""))[:1000]
        else:
            self.session.total_cost_usd = state.cost_usd


# -- Session persistence -----------------------------------------------------


def load_sessions(path: Path | None = None) -> dict[int, TaskSession]:
    """Load all task sessions from disk."""
    path = path or SESSIONS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        sessions = {}
        for k, v in data.get("sessions", {}).items():
            sessions[int(k)] = TaskSession.from_dict(v)
        return sessions
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load sessions from %s: %s", path, exc)
        return {}


def save_sessions(sessions: dict[int, TaskSession], path: Path | None = None) -> None:
    """Persist all task sessions to disk (atomic write via temp + rename)."""
    path = path or SESSIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    completed_ids = [
        sid
        for sid, s in sessions.items()
        if s.state in (TaskSessionState.COMPLETED, TaskSessionState.FAILED)
    ]

    data = {
        "sessions": {str(k): v.to_dict() for k, v in sessions.items()},
        "completed_ids": completed_ids,
        "last_updated": _now_iso(),
    }
    payload = json.dumps(data, indent=2).encode("utf-8")

    # Atomic write: write to temp file, fsync, then rename over the target.
    # This prevents partial/corrupt JSON if the process crashes mid-write.
    import os
    import tempfile

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".sessions_", suffix=".tmp"
    )
    closed = False
    try:
        os.write(fd, payload)
        os.fsync(fd)
        os.close(fd)
        closed = True
        os.replace(tmp_path, str(path))
    except BaseException:
        if not closed:
            os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


_RESTARTABLE_STATES = frozenset(
    {
        TaskSessionState.RUNNING,
        TaskSessionState.VALIDATING,
        TaskSessionState.RETRYING,
    }
)


def recover_sessions(sessions: dict[int, TaskSession]) -> int:
    """Reset in-flight sessions to DETECTED after a restart.  Returns count."""
    count = 0
    for session in sessions.values():
        if session.state in _RESTARTABLE_STATES:
            session.state = TaskSessionState.DETECTED
            count += 1
    return count
