# pylint: disable=too-many-lines
"""Durable state-machine orchestrator for golem sessions (v2).

Each ``TaskSession`` progresses through a 7-state lifecycle:
DETECTED → RUNNING → VERIFYING → VALIDATING → COMPLETED / RETRYING → COMPLETED / FAILED.

The orchestrator spawns one Claude agent per task (not per subtask).  Real-time
visibility comes from ``TaskEventTracker`` which processes stream-json events
into structured ``Milestone`` objects.

After execution the orchestrator runs a 6-phase pipeline:
  1. Execute — invoke the agent with event-stream monitoring
  2. Persist — write JSONL traces and prompt files to disk
  3. Verify — run deterministic checks (black, pylint, pytest) as a hard gate
  4. Validate — spawn a cheap (opus) validation agent to review the work
  5. Retry or Escalate — retry once on PARTIAL, escalate on FAIL
  6. Commit — deterministic git commit for PASS verdicts with code changes

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

from .types import FileRoleDict, MilestoneDict, PhaseHandoffDict
from .handoff import create_handoff, validate_handoff

from .core.cli_wrapper import CLIConfig, CLIResult, CLIType, invoke_cli_monitored
from .core.config import DATA_DIR, PROJECT_ROOT, GolemFlowConfig
from .core.defaults import _now_iso  # re-exported for backward compat (flow.py)
from .core.report import ReportWriter
from .core.run_log import RunRecord, record_run
from .prompts import compute_prompt_hash, load_prompt
from .utils import format_duration
from .core.flow_base import _StreamingTraceWriter, _write_prompt, _write_trace

from .checkpoint import delete_checkpoint, save_checkpoint
from .committer import commit_changes
from .core.log_context import SessionLogAdapter
from .log_context import clear_task_context, set_task_context
from .errors import InfrastructureError
from .event_tracker import Milestone, TaskEventTracker, TrackerState
from .interfaces import TaskStatus
from .profile import GolemProfile
from .observation_hooks import (
    SignalAccumulator,
    compare_retry_signatures,
    mine_validation_signals,
    mine_verification_signals,
)
from .instinct_store import InstinctStore
from .pitfall_extractor import classify_pitfall, extract_pitfalls
from .pitfall_writer import update_agents_md_from_instincts
from .tracing import get_tracer, trace_span
from .validation import ValidationVerdict, run_validation
from .verifier import VerificationResult, run_verification
from .workdir import resolve_work_dir
from .worktree_manager import cleanup_worktree, create_worktree

logger = logging.getLogger("golem.orchestrator")
_tracer = get_tracer("golem.orchestrator")

SESSIONS_FILE = DATA_DIR / "state" / "golem_sessions.json"

# Report paths (parallel to other flows)
_REPORT_DIR = DATA_DIR / "reports" / "golem"
_REPORT_INDEX = DATA_DIR / "reports" / "golem_report.md"


class TaskSessionState(str, Enum):
    """Lifecycle states for a golem session (v2).

    DETECTED → RUNNING → VERIFYING → VALIDATING ─── PASS ──→ COMPLETED
                                                  ├── PARTIAL → RETRYING → ...
                                                  └── FAIL ──→ FAILED (escalate)
                                                                └── feedback → HUMAN_REVIEW
    """

    DETECTED = "detected"
    RUNNING = "running"
    VERIFYING = "verifying"
    VALIDATING = "validating"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    HUMAN_REVIEW = "human_review"


class RootCause(str, Enum):
    """Named root causes for task escalation."""

    IDENTICAL_FAILURES = "identical_failures"
    BUDGET_EXCEEDED = "budget_exceeded"


def _parse_root_cause(val: str) -> str:
    """Convert root_cause string to RootCause enum, falling back to raw string."""
    if not val:
        return ""
    try:
        return RootCause(val)
    except ValueError:
        return val


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
    event_log: list[MilestoneDict] = field(default_factory=list)
    # Result
    result_summary: str = ""
    duration_seconds: float = 0.0
    # Validation & commit (v2)
    validation_verdict: str = ""
    validation_confidence: float = 0.0
    validation_summary: str = ""
    validation_concerns: list[str] = field(default_factory=list)
    validation_files_to_fix: list[str] = field(default_factory=list)
    validation_test_failures: list[str] = field(default_factory=list)
    validation_cost_usd: float = 0.0
    verification_result: dict | None = None  # VerificationResultDict
    retry_count: int = 0
    fix_iteration: int = 0
    commit_sha: str = ""
    trace_file: str = ""
    retry_trace_file: str = ""
    fix_trace_files: list[str] = field(default_factory=list)
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
    checkpoint_phase: str = ""
    merge_deferred: bool = False
    merge_branch: str = ""
    merge_retry_count: int = 0
    # Dashboard enrichment
    model: str = ""  # task_model used for this session (e.g. "sonnet", "opus")
    started_at: str = ""
    files_changed: list[str] = field(default_factory=list)
    prompt_hash: str = ""
    merge_queued_at: str = ""
    # Human feedback re-attempt
    human_feedback: str = ""
    human_feedback_at: str = ""
    previous_feedback: str = ""
    # Phase-to-phase structured context
    phase_handoffs: list[PhaseHandoffDict] = field(default_factory=list)
    # Stall / abort root cause
    root_cause: str = ""
    # Promoted observation signals (patterns seen >= threshold times)
    promoted_signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        d = asdict(self)
        d["state"] = self.state.value
        if isinstance(self.root_cause, RootCause):
            d["root_cause"] = self.root_cause.value
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
            validation_files_to_fix=data.get("validation_files_to_fix", []),
            validation_test_failures=data.get("validation_test_failures", []),
            validation_cost_usd=data.get("validation_cost_usd", 0.0),
            verification_result=data.get("verification_result"),
            retry_count=data.get("retry_count", 0),
            fix_iteration=data.get("fix_iteration", 0),
            commit_sha=data.get("commit_sha", ""),
            trace_file=data.get("trace_file", ""),
            retry_trace_file=data.get("retry_trace_file", ""),
            fix_trace_files=data.get("fix_trace_files", []),
            execution_mode=data.get("execution_mode", ""),
            supervisor_phase=data.get("supervisor_phase", ""),
            depends_on=data.get("depends_on", []),
            group_id=data.get("group_id", ""),
            merge_ready=data.get("merge_ready", False),
            worktree_path=data.get("worktree_path", ""),
            base_work_dir=data.get("base_work_dir", ""),
            infra_retry_count=data.get("infra_retry_count", 0),
            cli_session_id=data.get("cli_session_id", ""),
            checkpoint_phase=data.get("checkpoint_phase", ""),
            merge_deferred=data.get("merge_deferred", False),
            merge_branch=data.get("merge_branch", ""),
            model=data.get("model", ""),
            started_at=data.get("started_at", ""),
            files_changed=data.get("files_changed", []),
            merge_queued_at=data.get("merge_queued_at", ""),
            human_feedback=data.get("human_feedback", ""),
            human_feedback_at=data.get("human_feedback_at", ""),
            previous_feedback=data.get("previous_feedback", ""),
            phase_handoffs=data.get("phase_handoffs", []),
            root_cause=_parse_root_cause(data.get("root_cause", "")),
            promoted_signals=data.get("promoted_signals", []),
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
        verified_ref: str | None = None,
        on_verified_ref: Any | None = None,
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
        self._verified_ref = verified_ref
        self._on_verified_ref = on_verified_ref
        self._last_checkpoint_time: float = 0.0
        self._checkpoint_interval: float = 10.0  # seconds between disk writes
        self._signal_accumulator = SignalAccumulator(
            DATA_DIR / "observation_signals.json"
        )
        self._instinct_store = InstinctStore(DATA_DIR / "instinct_store.json")
        self._last_verification: VerificationResult | None = None
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

    def _record_handoff(
        self,
        from_phase: str,
        to_phase: str,
        context: list[str],
        files: list[FileRoleDict],
    ) -> None:
        """Create, validate, and store a phase handoff document."""
        handoff = create_handoff(
            from_phase=from_phase,
            to_phase=to_phase,
            context=context,
            files=files,
            open_questions=[],
            warnings=[],
        )
        valid, reasons = validate_handoff(handoff)
        if not valid:
            self._slog.warning(
                "Handoff %s\u2192%s validation failed — rejected: %s",
                from_phase,
                to_phase,
                reasons,
            )
            return
        self.session.phase_handoffs.append(handoff)

    async def tick(self) -> TaskSession:
        """Advance the session state machine by one tick."""
        self.session.updated_at = _now_iso()

        with trace_span(
            _tracer,
            "orchestrator.tick",
            session_id=str(self.session.parent_issue_id),
            state=self.session.state.value,
        ):
            if self.session.state == TaskSessionState.DETECTED:
                await self._tick_detected()
            elif self.session.state == TaskSessionState.HUMAN_REVIEW:
                await self._tick_human_review()
            # RUNNING is handled within _tick_detected (blocks until agent finishes)
            # COMPLETED and FAILED are terminal — no action

        return self.session

    async def run_once(self) -> TaskSession:
        """Run the full pipeline immediately.  For CLI / one-shot use.

        Skips the grace period entirely and transitions straight to RUNNING.
        """
        self.session.state = TaskSessionState.RUNNING
        self.session.updated_at = _now_iso()
        self.session.started_at = self.session.updated_at
        await self._run_agent()
        return self.session

    async def _tick_detected(self) -> None:
        """Wait for grace period, then spawn the agent."""
        if self.session.grace_deadline:
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
        self.session.started_at = self.session.updated_at
        set_task_context(str(self.session.parent_issue_id), phase="BUILD")

        with trace_span(
            _tracer,
            "orchestrator.build",
            session_id=str(self.session.parent_issue_id),
        ):
            await self._run_agent()

    async def _tick_human_review(self) -> None:
        """Re-attempt a task using human feedback as guidance."""
        set_task_context(str(self.session.parent_issue_id), phase="BUILD")
        self._slog.info("Processing human feedback re-attempt")
        self.session.state = TaskSessionState.RUNNING
        self.session.updated_at = _now_iso()
        self.session.started_at = self.session.updated_at
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
                verified_ref=self._verified_ref,
                on_verified_ref=self._on_verified_ref,
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
                self._slog.error(
                    "Worktree creation failed: %s. base_dir=%s, branch=agent/%s",
                    wt_err,
                    base_work_dir,
                    issue_id,
                )
                raise InfrastructureError(
                    f"Worktree creation failed for task #{issue_id}: {wt_err}"
                ) from wt_err
        return work_dir, worktree_path

    # pylint: disable-next=too-many-locals,too-many-branches,too-many-statements
    async def _run_agent_monolithic(self) -> None:
        """Single-agent 5-phase pipeline."""
        issue_id = self.session.parent_issue_id
        description = self._get_description(issue_id)
        work_dir, worktree_path = self._resolve_workdir(issue_id, description)
        base_work_dir = self.session.base_work_dir

        try:
            save_checkpoint(issue_id, self.session, phase="executing")
        except Exception:  # pylint: disable=broad-exception-caught
            self._slog.debug("save_checkpoint failed", exc_info=True)

        self._preflight_check(work_dir)

        start = time.time()
        result: CLIResult | None = None
        tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )
        prompt = ""
        trace_writer: _StreamingTraceWriter | None = None

        try:
            prompt = self._format_prompt(
                "run_task.txt",
                issue_id=issue_id,
                task_description=description,
            )
            self.session.prompt_hash = compute_prompt_hash(load_prompt("run_task.txt"))
            with trace_span(
                _tracer,
                "orchestrator.invoke_agent",
                session_id=str(issue_id),
            ) as invoke_span:
                result, trace_writer, mcp_servers = await self._invoke_agent(
                    issue_id,
                    prompt,
                    work_dir,
                    tracker,
                )
                invoke_span.set_attribute(
                    "gen_ai.usage.input_tokens", result.input_tokens
                )
                invoke_span.set_attribute(
                    "gen_ai.usage.output_tokens", result.output_tokens
                )
                invoke_span.set_attribute("gen_ai.usage.cost_usd", result.cost_usd)
            self._populate_session_from_tracker(tracker, result, time.time() - start)
            self._update_task(issue_id, status=TaskStatus.FIXED, progress=80)
            self._record_handoff(
                from_phase="executing",
                to_phase="verifying",
                context=[
                    "Agent execution completed",
                    "Tools called: %s" % ", ".join(self.session.tools_called[:10]),
                    "Errors: %d" % len(self.session.errors),
                    "Result: %s"
                    % (
                        self.session.result_summary[:200]
                        if self.session.result_summary
                        else "N/A"
                    ),
                ],
                files=[],
            )

            # Phase 3: Deterministic verification (hard gate before reviewer)
            _prev_verification = self._last_verification
            verification = await self._run_verification(work_dir)
            self._record_handoff(
                from_phase="verifying",
                to_phase="validating",
                context=[
                    "Verification %s" % ("passed" if verification.passed else "failed"),
                    "black=%s pylint=%s pytest=%s"
                    % (
                        verification.black_ok,
                        verification.pylint_ok,
                        verification.pytest_ok,
                    ),
                    "Tests: %d, Coverage: %.1f%%"
                    % (
                        verification.test_count,
                        verification.coverage_pct,
                    ),
                ],
                files=[],
            )
            if not verification.passed:
                feedback = self._format_verification_feedback(verification)
                # Guard 1: identical failures — stall detected, abort immediately
                if (
                    _prev_verification is not None
                    and frozenset(verification.failures)
                    and frozenset(verification.failures)
                    == frozenset(_prev_verification.failures)
                ):
                    self._slog.warning(
                        "Identical verification failures detected, aborting retry (issue=%s)",
                        self.session.parent_issue_id,
                    )
                    synth_verdict = ValidationVerdict(
                        verdict="FAIL",
                        confidence=0.0,
                        summary=f"Identical failures on retry: {feedback[:200]}",
                        concerns=[feedback],
                    )
                    self._apply_verdict(synth_verdict)
                    self._escalate(
                        synth_verdict, root_cause=RootCause.IDENTICAL_FAILURES
                    )
                    return
                # Guard 2: budget exceeded — abort instead of retrying
                if self.session.total_cost_usd >= self.session.budget_usd:
                    self._slog.warning(
                        "Budget exceeded (cost=%.2f >= budget=%.2f), aborting retry (issue=%s)",
                        self.session.total_cost_usd,
                        self.session.budget_usd,
                        self.session.parent_issue_id,
                    )
                    synth_verdict = ValidationVerdict(
                        verdict="FAIL",
                        confidence=0.0,
                        summary=f"Budget exceeded, cannot retry: {feedback[:200]}",
                        concerns=[feedback],
                    )
                    self._apply_verdict(synth_verdict)
                    self._escalate(synth_verdict, root_cause=RootCause.BUDGET_EXCEEDED)
                    return
                if self.session.retry_count < self.task_config.max_retries:
                    # Build a synthetic PARTIAL verdict from verification failure
                    synth_verdict = ValidationVerdict(
                        verdict="PARTIAL",
                        confidence=0.0,
                        summary=f"Verification failed: {feedback[:200]}",
                        concerns=[feedback],
                    )
                    self._apply_verdict(synth_verdict)
                    await self._retry_agent(synth_verdict, work_dir, mcp_servers)
                    # _retry_agent re-validates internally; commit if PASS
                    if self.session.state != TaskSessionState.FAILED:
                        await self._commit_and_complete(
                            issue_id, work_dir, synth_verdict
                        )
                    return
                synth_verdict = ValidationVerdict(
                    verdict="FAIL",
                    confidence=0.0,
                    summary=f"Verification failed after retries: {feedback[:200]}",
                    concerns=[feedback],
                )
                self._apply_verdict(synth_verdict)
                # Guard 3: promoted signals — systematic issue detected, escalate with context
                if self._check_promoted_and_escalate(synth_verdict):
                    return
                self._escalate(synth_verdict)
                return

            verdict = await self._run_validation(issue_id, work_dir)
            self._record_handoff(
                from_phase="validating",
                to_phase="committing",
                context=[
                    "Validation verdict: %s" % verdict.verdict,
                    "Confidence: %.0f%%" % (verdict.confidence * 100),
                    "Summary: %s" % verdict.summary[:200],
                ],
                files=[],
            )

            if (
                verdict.verdict == "PARTIAL"
                and self.session.retry_count < self.task_config.max_retries
            ):
                await self._retry_agent(verdict, work_dir, mcp_servers)
            elif verdict.verdict != "PASS":
                self._escalate(verdict)
                return

            await self._commit_and_complete(issue_id, work_dir, verdict)

            if worktree_path and self.session.commit_sha:
                self.session.merge_ready = True
                worktree_path = ""

        except InfrastructureError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._handle_agent_failure(issue_id, exc, start, tracker, result, prompt)

        finally:
            if trace_writer:
                trace_writer.close()
            if self.session.state in (
                TaskSessionState.COMPLETED,
                TaskSessionState.FAILED,
            ):
                try:
                    delete_checkpoint(issue_id)
                except Exception:  # pylint: disable=broad-exception-caught
                    self._slog.debug("delete_checkpoint failed", exc_info=True)
            if worktree_path:
                cleanup_worktree(
                    base_work_dir,
                    worktree_path,
                    keep_branch=self.session.state == TaskSessionState.FAILED,
                )
            self._write_report()
            self._record_run()
            clear_task_context()

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

        # Pre-flight verification: ensure base branch is healthy
        if getattr(self.task_config, "preflight_verify", True):
            self._slog.info("Running pre-flight verification on base branch...")
            vr = run_verification(
                work_dir,
                timeout=getattr(self.task_config, "verification_timeout_seconds", 120),
            )
            if not vr.passed:
                failures = []
                if not vr.black_ok:
                    failures.append(f"black: {vr.black_output[:200]}")
                if not vr.pylint_ok:
                    failures.append(f"pylint: {vr.pylint_output[:200]}")
                if not vr.pytest_ok:
                    failures.append(f"pytest: {vr.pytest_output[:200]}")
                detail = "; ".join(failures)
                raise InfrastructureError(
                    f"Base branch verification failed — aborting to save budget. {detail}"
                )
            self._slog.info("Pre-flight verification passed (%.1fs)", vr.duration_s)

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

    async def _invoke_agent(
        self,
        issue_id: int,
        prompt: str,
        work_dir: str,
        tracker: TaskEventTracker,
    ) -> tuple[CLIResult, _StreamingTraceWriter, list[str]]:
        """Prepare traces, invoke the CLI, and return the result + writer."""
        event_id = f"golem-{issue_id}"
        _write_prompt("golem", event_id, prompt)

        trace_writer = _StreamingTraceWriter("golem", event_id)
        self.session.trace_file = trace_writer.relative_path

        def _streaming_callback(event: dict) -> None:
            trace_writer.append(event)
            tracker.handle_event(event)

        mcp_servers = self._get_mcp_servers(self.session.parent_subject)

        system_prompt = ""
        if self.task_config.context_injection:
            from .context_injection import (
                build_system_prompt,
            )  # pylint: disable=import-outside-toplevel

            system_prompt = build_system_prompt(
                work_dir,
                max_tokens=self.task_config.context_budget_tokens,
                subject=self.session.parent_subject,
            )

        cli_config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=self.task_config.task_model,
            max_budget_usd=self.session.budget_usd,
            timeout_seconds=self.task_config.task_timeout_seconds,
            mcp_servers=mcp_servers,
            cwd=work_dir,
            system_prompt=system_prompt,
            sandbox_enabled=self.task_config.sandbox_enabled,
            sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
            sandbox_memory_gb=self.task_config.sandbox_memory_gb,
        )
        callback = self._chain_event_callback(_streaming_callback)
        try:
            async with self._work_dir_lock:
                result = await asyncio.get_running_loop().run_in_executor(
                    None, invoke_cli_monitored, prompt, cli_config, callback
                )
        except BaseException:
            trace_writer.close()
            raise
        trace_writer.close()
        return result, trace_writer, mcp_servers

    # -- Pipeline helpers --------------------------------------------------------

    async def _run_validation(self, issue_id: int, work_dir: str) -> ValidationVerdict:
        """Phase 3: Spawn the validation agent and store the verdict."""
        self.session.state = TaskSessionState.VALIDATING
        self.session.updated_at = _now_iso()
        set_task_context(str(issue_id), phase="REVIEW")

        tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )
        callback = self._chain_event_callback(tracker.handle_event)

        description = self._get_description(issue_id)
        with trace_span(
            _tracer,
            "orchestrator.review",
            session_id=str(issue_id),
        ) as span:
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
                sandbox_enabled=self.task_config.sandbox_enabled,
                sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
                sandbox_memory_gb=self.task_config.sandbox_memory_gb,
            )
            span.set_attribute("review.verdict", verdict.verdict)
            span.set_attribute("review.confidence", verdict.confidence)
            span.set_attribute("gen_ai.usage.cost_usd", verdict.cost_usd)
        self._apply_verdict(verdict)

        # Mine validation output for observation signals
        val_signals = mine_validation_signals(verdict)
        if val_signals:
            self._signal_accumulator.record(val_signals)
            self._slog.info(
                "Mined %d observation signal(s) from validation",
                len(val_signals),
            )

        # Update session with any newly promoted signals
        promoted = self._signal_accumulator.get_promoted()
        for key in promoted:
            if key not in self.session.promoted_signals:
                self.session.promoted_signals.append(key)

        try:
            save_checkpoint(issue_id, self.session, phase="validated")
        except Exception:  # pylint: disable=broad-exception-caught
            self._slog.debug("save_checkpoint failed", exc_info=True)

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

    async def _run_verification(self, work_dir: str) -> VerificationResult:
        """Phase 3: Run deterministic verification (black, pylint, pytest)."""
        self.session.state = TaskSessionState.VERIFYING
        self.session.updated_at = _now_iso()
        set_task_context(str(self.session.parent_issue_id), phase="VERIFY")

        self._slog.info("Running deterministic verification")
        with trace_span(
            _tracer,
            "orchestrator.verify",
            session_id=str(self.session.parent_issue_id),
        ) as span:
            result = await asyncio.get_running_loop().run_in_executor(
                None, run_verification, work_dir
            )
            span.set_attribute("verify.passed", result.passed)
            span.set_attribute("verify.black_ok", result.black_ok)
            span.set_attribute("verify.pylint_ok", result.pylint_ok)
            span.set_attribute("verify.pytest_ok", result.pytest_ok)
            span.set_attribute("verify.duration_s", result.duration_s)

        self.session.verification_result = result.to_dict()

        # Mine verification output for observation signals
        signals = mine_verification_signals(result)
        if signals:
            self._signal_accumulator.record(signals)
            self._slog.info(
                "Mined %d observation signal(s) from verification", len(signals)
            )

        # Compare with previous verification for retry pattern detection
        if self._last_verification is not None:
            retry_signals = compare_retry_signatures(result, self._last_verification)
            if retry_signals:
                self._signal_accumulator.record(retry_signals)
                self._slog.info(
                    "Detected %d identical retry pattern(s)", len(retry_signals)
                )
        self._last_verification = result

        # Update session with any newly promoted signals
        promoted = self._signal_accumulator.get_promoted()
        for key in promoted:
            if key not in self.session.promoted_signals:
                self.session.promoted_signals.append(key)

        try:
            save_checkpoint(
                self.session.parent_issue_id, self.session, phase="verified"
            )
        except Exception:  # pylint: disable=broad-exception-caught
            self._slog.debug("save_checkpoint failed", exc_info=True)

        self._slog.info(
            "Verification %s: black=%s pylint=%s pytest=%s (%.1fs)",
            "PASSED" if result.passed else "FAILED",
            result.black_ok,
            result.pylint_ok,
            result.pytest_ok,
            result.duration_s,
        )
        return result

    def _format_verification_feedback(self, result: VerificationResult) -> str:
        """Format verification failures into structured feedback for retry."""
        parts = ["Independent verification failed:"]

        # Generic verification path — use command_results when present
        if result.command_results:
            for cr in result.command_results:
                if not cr["passed"]:
                    parts.append(
                        "\n%s (%s): FAILED\n%s"
                        % (cr["role"], cr["cmd"], cr["output"][:2000])
                    )
            if result.error:
                parts.append("\n%s" % result.error)
            return "\n".join(parts)

        # Legacy Python verification path
        if not result.black_ok:
            parts.append(f"\nblack --check: FAILED\n{result.black_output}")
        if not result.pylint_ok:
            parts.append(f"\npylint: FAILED\n{result.pylint_output}")
        if not result.pytest_ok:
            parts.append(f"\npytest: FAILED ({len(result.failures)} failures)")
            for f in result.failures:
                parts.append(f"  - {f}")
            if result.pytest_output:
                parts.append(f"\n{result.pytest_output[-2000:]}")
        if result.error:
            parts.append(f"\n{result.error}")
        return "\n".join(parts)

    async def _commit_and_complete(
        self, issue_id: int, work_dir: str, verdict: ValidationVerdict
    ) -> None:
        """Phase 5: Commit changes (if applicable) and mark session COMPLETED."""
        if self.task_config.auto_commit and self.session.validation_verdict == "PASS":
            task_type = verdict.task_type if verdict.verdict == "PASS" else "other"
            cr = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: commit_changes(
                    work_dir=work_dir,
                    issue_id=issue_id,
                    subject=self.session.parent_subject,
                    task_type=task_type,
                    summary=self.session.validation_summary,
                ),
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
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._extract_and_write_pitfalls)
        except Exception:  # pylint: disable=broad-exception-caught
            self._slog.debug("Pitfall extraction failed", exc_info=True)

    def _handle_agent_failure(
        self,
        issue_id: int,
        exc: Exception,
        start: float,
        tracker: TaskEventTracker,
        result: CLIResult | None,
        _prompt: str,
    ) -> None:
        """Handle exception from the pipeline — persist state and notify."""
        elapsed = time.time() - start
        self._populate_session_from_tracker(tracker, result, elapsed)
        self.session.state = TaskSessionState.FAILED
        self.session.errors.append(str(exc))
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
        self.session.validation_files_to_fix = verdict.files_to_fix
        self.session.validation_test_failures = verdict.test_failures
        self.session.validation_cost_usd += verdict.cost_usd
        self.session.total_cost_usd += verdict.cost_usd

    def _extract_and_write_pitfalls(self) -> None:
        """Extract pitfalls from session and write to AGENTS.md via instinct store."""
        session_dict = asdict(self.session)
        pitfalls = extract_pitfalls([session_dict])

        # Also collect promoted observation signals
        promoted = self._signal_accumulator.get_promoted()
        if promoted:
            pitfalls.extend(promoted)
            self._signal_accumulator.clear_promoted()
            self._slog.info(
                "Promoted %d observation signal(s) to pitfalls", len(promoted)
            )

        for pitfall in pitfalls:
            category = classify_pitfall(pitfall)
            self._instinct_store.add(pitfall, category)

        self._instinct_store.prune()
        update_agents_md_from_instincts(self._instinct_store)

        if pitfalls:
            self._slog.info("Added %d pitfall(s) to instinct store", len(pitfalls))

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

        try:
            save_checkpoint(issue_id, self.session, phase="retrying")
        except Exception:  # pylint: disable=broad-exception-caught
            self._slog.debug("save_checkpoint failed", exc_info=True)

        self._slog.info("Retrying (attempt %d)", self.session.retry_count)

        concerns_text = (
            "\n".join(f"- {c}" for c in verdict.concerns) or "- (none specified)"
        )
        files_to_fix_text = (
            "\n".join(f"- {f}" for f in verdict.files_to_fix) or "- (none identified)"
        )
        test_failures_text = (
            "\n".join(f"- {t}" for t in verdict.test_failures) or "- (none identified)"
        )

        retry_prompt = self._format_prompt(
            "retry_task.txt",
            issue_id=issue_id,
            original_summary=self.session.result_summary or "(no summary)",
            validation_verdict=verdict.verdict,
            validation_summary=verdict.summary,
            concerns=concerns_text,
            files_to_fix=files_to_fix_text,
            test_failures=test_failures_text,
            event_log_summary="\n".join(
                f"- {e.get('kind', '?')}: {e.get('summary', '')}"
                for e in self.session.event_log
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
            sandbox_enabled=self.task_config.sandbox_enabled,
            sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
            sandbox_memory_gb=self.task_config.sandbox_memory_gb,
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
            sandbox_enabled=self.task_config.sandbox_enabled,
            sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
            sandbox_memory_gb=self.task_config.sandbox_memory_gb,
        )
        self._apply_verdict(retry_verdict)

        self._slog.info(
            "Retry validation verdict=%s",
            retry_verdict.verdict,
        )

        if retry_verdict.verdict != "PASS":
            self._escalate(retry_verdict)

    def _check_promoted_and_escalate(self, verdict: ValidationVerdict) -> bool:
        """Check for promoted signals and escalate if any exist.

        Returns True if escalation was triggered (caller should stop processing),
        False if no promoted signals are present.
        """
        if not self.session.promoted_signals:
            return False
        self._slog.warning(
            "Promoted signals detected, escalating: %s",
            ", ".join(self.session.promoted_signals),
        )
        self._escalate(verdict)
        return True

    def _escalate(self, verdict: ValidationVerdict, root_cause: str = "") -> None:
        """Mark session FAILED and post escalation details to Redmine."""
        issue_id = self.session.parent_issue_id
        self.session.state = TaskSessionState.FAILED
        self.session.updated_at = _now_iso()
        if root_cause:
            self.session.root_cause = root_cause

        concerns_text = "\n".join(f"- {c}" for c in verdict.concerns) or "- (none)"
        files_text = "\n".join(f"- {f}" for f in verdict.files_to_fix) or "- (none)"
        failures_text = "\n".join(f"- {t}" for t in verdict.test_failures) or "- (none)"

        root_cause_line = f"Root cause: {root_cause}\n\n" if root_cause else ""
        notes = (
            f"**Golem escalation — needs human review**\n\n"
            f"Verdict: {verdict.verdict} (confidence: {verdict.confidence:.0%})\n"
            f"Summary: {verdict.summary}\n\n"
            f"{root_cause_line}"
            f"Concerns:\n{concerns_text}\n\n"
            f"Files to fix:\n{files_text}\n\n"
            f"Test failures:\n{failures_text}\n\n"
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

    def _write_report(self) -> None:  # pylint: disable=too-many-locals
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
            files_to_fix_str = (
                "\n".join(f"- {f}" for f in self.session.validation_files_to_fix)
                or "- (none)"
            )
            test_failures_str = (
                "\n".join(f"- {t}" for t in self.session.validation_test_failures)
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
                f"**Files to fix**:\n{files_to_fix_str}\n\n"
                f"**Test failures**:\n{test_failures_str}\n\n"
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
                prompt_hash=self.session.prompt_hash,
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
        entry: MilestoneDict = {
            "kind": milestone.kind,
            "tool_name": milestone.tool_name,
            "summary": milestone.summary,
            **({"full_text": milestone.full_text} if milestone.full_text else {}),
            "timestamp": milestone.timestamp,
            "is_error": milestone.is_error,
        }
        self.session.event_log.append(entry)

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
                **({"full_text": m.full_text} if m.full_text else {}),
                "timestamp": m.timestamp,
                "is_error": m.is_error,
            }
            for m in state.event_log
        ]

        if result is not None:
            self.session.total_cost_usd = result.cost_usd
            self.session.result_summary = str(result.output.get("result", ""))
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


def _serialize_sessions_payload(sessions: dict[int, TaskSession]) -> bytes:
    """Serialize *sessions* to a JSON-encoded byte payload.

    Extracted so callers can obtain the bytes without performing a rename,
    enabling two-phase atomic saves that commit multiple files together.
    """
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
    return json.dumps(data, indent=2).encode("utf-8")


def save_sessions(sessions: dict[int, TaskSession], path: Path | None = None) -> None:
    """Persist all task sessions to disk (atomic write via temp + rename)."""
    import os
    import tempfile

    path = path or SESSIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = _serialize_sessions_payload(sessions)

    # Atomic write: write to temp file, fsync, then rename over the target.
    # This prevents partial/corrupt JSON if the process crashes mid-write.
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
        except OSError as exc:
            logger.debug("Failed to unlink orchestrator temp file: %s", exc)
        raise


_RESTARTABLE_STATES = frozenset(
    {
        TaskSessionState.RUNNING,
        TaskSessionState.VERIFYING,
        TaskSessionState.VALIDATING,
        TaskSessionState.RETRYING,
        TaskSessionState.HUMAN_REVIEW,
    }
)


def recover_sessions(sessions: dict[int, TaskSession]) -> int:
    """Reset in-flight sessions to DETECTED after a restart.  Returns count."""
    count = 0
    for session in sessions.values():
        if session.state in _RESTARTABLE_STATES:
            session.state = TaskSessionState.DETECTED
            session.checkpoint_phase = ""
            count += 1
    return count
