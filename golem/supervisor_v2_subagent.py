"""Subagent-based supervisor: single Claude session with Agent tool orchestration.

Replaces the old sequential subprocess supervisor.  Instead of spawning 5-7
separate ``claude -p`` subprocesses (decompose → execute subtask N → summarize),
this supervisor builds a single orchestration prompt that teaches Claude how to
delegate via the built-in Agent tool (Explorer, Implementer, Reviewer, Tester).

Independent subtasks run in parallel via Agent tool calls within the session.
The external validation layer stays as a separate subprocess.  On PARTIAL
verdicts, ``--resume`` enables warm retries instead of cold-starting.
"""

import asyncio
import logging
import time
from typing import Any

from .committer import commit_changes
from .errors import InfrastructureError
from .core.cli_wrapper import CLIConfig, CLIResult, CLIType, invoke_cli_monitored
from .core.config import PROJECT_ROOT, GolemFlowConfig
from .core.flow_base import _write_prompt, _write_trace
from .core.json_extract import extract_json
from .core.log_context import SessionLogAdapter
from .event_tracker import TaskEventTracker
from .interfaces import TaskStatus
from .orchestrator import (
    TaskSession,
    TaskSessionState,
    _now_iso,
)
from .profile import GolemProfile
from .validation import ValidationVerdict, run_validation
from .workdir import resolve_work_dir
from .worktree_manager import cleanup_worktree, create_worktree, merge_and_cleanup

logger = logging.getLogger("golem.supervisor_v2_subagent")


class SubagentSupervisor:
    """Thin supervisor: build prompt → single CLI session → parse → validate."""

    def __init__(
        self,
        session: TaskSession,
        config: Any,
        task_config: GolemFlowConfig,
        *,
        on_milestone: Any | None = None,
        work_dir_lock: asyncio.Lock | None = None,
        save_callback: Any | None = None,
        profile: GolemProfile | None = None,
        event_callback: Any | None = None,
        work_dir_override: str | None = None,
    ):
        self.session = session
        self.config = config
        self.task_config = task_config
        self._on_milestone = on_milestone
        self._work_dir_lock = work_dir_lock or asyncio.Lock()
        self._save_callback = save_callback
        self.profile: GolemProfile = profile  # type: ignore[assignment]
        self._event_callback = event_callback
        self._work_dir_override = work_dir_override
        self._base_work_dir: str = ""
        self._worktree_path: str = ""
        self._slog = SessionLogAdapter(
            logger,
            session_id=session.parent_issue_id,
            subject=session.parent_subject,
        )

    # -- Profile-based helpers -------------------------------------------------

    def _update_task(
        self,
        task_id: int,
        *,
        status: str | None = None,
        progress: int | None = None,
        comment: str | None = None,
    ) -> None:
        if status:
            self.profile.state_backend.update_status(task_id, status)
        if progress is not None:
            self.profile.state_backend.update_progress(task_id, progress)
        if comment:
            self.profile.state_backend.post_comment(task_id, comment)

    def _get_description(self, task_id: int) -> str:
        return self.profile.task_source.get_task_description(task_id)

    def _format_prompt(self, name: str, **kwargs: Any) -> str:
        return self.profile.prompt_provider.format(name, **kwargs)

    def _get_mcp_servers(self, subject: str) -> list[str]:
        return self.profile.tool_provider.servers_for_subject(subject)

    def _chain_event_callback(self, tracker_callback):
        if not self._event_callback:
            return tracker_callback
        ecb = self._event_callback

        def chained(event):
            ecb(event)
            tracker_callback(event)

        return chained

    # -- Main pipeline ---------------------------------------------------------

    async def run(self) -> None:  # pylint: disable=too-many-statements
        """Full subagent orchestration pipeline."""
        issue_id = self.session.parent_issue_id
        self.session.execution_mode = "subagent"

        start = time.time()

        try:
            description = self._get_description(issue_id)

            if self._work_dir_override:
                self._base_work_dir = self._work_dir_override
            else:
                self._base_work_dir = resolve_work_dir(
                    subject=self.session.parent_subject,
                    description=description,
                    work_dirs=self.task_config.work_dirs,
                    default_work_dir=self.task_config.default_work_dir,
                    project_root=str(PROJECT_ROOT),
                )

            # Set up isolated worktree — required when enabled to prevent
            # agents from corrupting the shared working directory.
            work_dir = self._base_work_dir
            if self.task_config.use_worktrees:
                try:
                    self._worktree_path = create_worktree(self._base_work_dir, issue_id)
                    work_dir = self._worktree_path
                    self._slog.info("Using worktree at %s", work_dir)
                except RuntimeError as wt_err:
                    self._slog.error(
                        "Worktree creation failed for task #%s: %s. "
                        "base_dir=%s, branch=agent/%s. "
                        "Refusing to fall back to shared dir to prevent "
                        "repo corruption.",
                        issue_id,
                        wt_err,
                        self._base_work_dir,
                        issue_id,
                    )
                    raise InfrastructureError(
                        f"Worktree creation failed for task #{issue_id}: {wt_err}"
                    ) from wt_err
            # Phase 1: Build orchestration prompt
            prompt = self._build_prompt(issue_id, description, work_dir)

            # Phase 2: Single invoke_cli_monitored() call
            result = await self._invoke_orchestrator(prompt, work_dir, issue_id, start)

            # Phase 3: Parse structured JSON report
            report = self._parse_report(result)
            self.session.result_summary = report.get("summary", "")[:1000]
            self._emit_event(
                f"Orchestrator finished: {report.get('status', 'unknown')}"
            )
            self._update_task(issue_id, status=TaskStatus.FIXED, progress=80)

            # Phase 4: External validation
            self.session.supervisor_phase = "validating"
            self._emit_event("Running external validation...")
            verdict = self._run_overall_validation(issue_id, description, work_dir)
            self._emit_event(
                f"Validation: {verdict.verdict} "
                f"(confidence {verdict.confidence:.0%})"
            )

            # Phase 5: Handle verdict
            if verdict.verdict == "PASS":
                self.session.supervisor_phase = "committing"
                self._emit_event("Committing and merging changes...")
                self._commit_and_complete(issue_id, work_dir, verdict)
            elif (
                verdict.verdict == "PARTIAL"
                and self.session.retry_count < self.task_config.max_retries
            ):
                await self._retry_with_resume(verdict, work_dir, issue_id)
            else:
                self._escalate(verdict)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.session.duration_seconds = time.time() - start
            self.session.state = TaskSessionState.FAILED
            self.session.errors.append(str(exc))
            self._emit_event(f"Supervisor failed: {str(exc)[:150]}", is_error=True)
            self._update_task(
                issue_id,
                comment=f"Golem subagent supervisor failed: {exc}",
            )
            self._slog.error("Supervisor failed: %s", exc)

        finally:
            if self._worktree_path:
                if self.session.state == TaskSessionState.FAILED:
                    cleanup_worktree(
                        self._base_work_dir, self._worktree_path, keep_branch=True
                    )
                elif not self.session.commit_sha:
                    cleanup_worktree(self._base_work_dir, self._worktree_path)
            self._checkpoint()

    # -- Prompt building -------------------------------------------------------

    def _build_prompt(self, issue_id: int, description: str, work_dir: str) -> str:
        return self._format_prompt(
            "orchestrate_task.txt",
            issue_id=issue_id,
            parent_subject=self.session.parent_subject,
            task_description=description,
            work_dir=work_dir,
            inner_retry_max=self.task_config.inner_retry_max,
        )

    # -- CLI invocation --------------------------------------------------------

    async def _invoke_orchestrator(
        self,
        prompt: str,
        work_dir: str,
        issue_id: int,
        start: float,
    ) -> CLIResult:
        """Single CLI call for the full orchestration session."""
        model = self.task_config.orchestrate_model or self.task_config.task_model
        mcp_servers = self._get_mcp_servers(self.session.parent_subject)

        cli_config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=model,
            max_budget_usd=self.task_config.orchestrate_budget_usd,
            timeout_seconds=self.task_config.orchestrate_timeout_seconds,
            mcp_servers=mcp_servers,
            cwd=work_dir,
        )

        tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )
        callback = self._chain_event_callback(tracker.handle_event)

        self.session.supervisor_phase = "orchestrating"
        self._emit_event("Starting single-session orchestration...")

        async with self._work_dir_lock:
            result = await asyncio.get_running_loop().run_in_executor(
                None, invoke_cli_monitored, prompt, cli_config, callback
            )

        elapsed = time.time() - start
        self.session.duration_seconds = elapsed
        self.session.total_cost_usd += result.cost_usd
        self.session.tools_called = list(tracker.state.tools_called)
        self.session.mcp_tools_called = list(tracker.state.mcp_tools_called)
        self.session.errors = list(tracker.state.errors)
        self.session.milestone_count = tracker.state.milestone_count

        # Capture session_id for --resume support
        if result.session_id:
            self.session.cli_session_id = result.session_id

        # Persist traces
        event_id = f"golem-{issue_id}"
        _write_prompt("golem", event_id, prompt)
        if result.trace_events:
            self.session.trace_file = _write_trace(
                "golem", event_id, result.trace_events
            )

        return result

    # -- Report parsing --------------------------------------------------------

    @staticmethod
    def _parse_report(result: CLIResult) -> dict:
        """Extract structured JSON report from CLI output."""
        raw_output = result.output.get("result", "")
        if not raw_output:
            return {"status": "UNKNOWN", "summary": "(no output)"}

        parsed = extract_json(str(raw_output), require_key="status")
        if parsed and isinstance(parsed.get("status"), str):
            return parsed

        # Graceful degradation: return raw output as summary
        return {
            "status": "UNKNOWN",
            "summary": str(raw_output)[:500],
        }

    # -- Validation ------------------------------------------------------------

    def _run_overall_validation(
        self, issue_id: int, description: str, work_dir: str
    ) -> ValidationVerdict:
        self.session.state = TaskSessionState.VALIDATING
        self.session.updated_at = _now_iso()

        verdict = run_validation(
            issue_id=issue_id,
            subject=self.session.parent_subject,
            description=description,
            session_data=self.session.to_dict(),
            work_dir=work_dir,
            model=self.task_config.validation_model,
            budget_usd=self.task_config.validation_budget_usd,
            timeout_seconds=self.task_config.validation_timeout_seconds,
        )
        self.session.validation_verdict = verdict.verdict
        self.session.validation_confidence = verdict.confidence
        self.session.validation_summary = verdict.summary
        self.session.validation_concerns = verdict.concerns
        self.session.validation_cost_usd += verdict.cost_usd
        self.session.total_cost_usd += verdict.cost_usd

        self._slog.info(
            "Validation verdict=%s confidence=%.2f",
            verdict.verdict,
            verdict.confidence,
        )
        return verdict

    # -- Retry with --resume ---------------------------------------------------

    def _build_retry_prompt(
        self,
        use_resume: bool,
        verdict: ValidationVerdict,
        concerns_text: str,
        issue_id: int,
    ) -> tuple[str, str]:
        """Build retry prompt and resume session id."""
        if use_resume:
            self._slog.info(
                "Warm retry with --resume (session %s)",
                self.session.cli_session_id,
            )
            self._emit_event("Warm retry with --resume...")
            prompt = (
                f"The external validator reviewed your work and found issues.\n\n"
                f"**Verdict**: {verdict.verdict}\n"
                f"**Summary**: {verdict.summary}\n\n"
                f"**Concerns**:\n{concerns_text}\n\n"
                f"Address ONLY these concerns. Then re-run tests and produce "
                f"an updated JSON completion report."
            )
            return prompt, self.session.cli_session_id

        self._slog.info("Cold retry (no session_id or resume disabled)")
        self._emit_event("Cold retry (no --resume)...")
        prompt = self._format_prompt(
            "retry_task.txt",
            issue_id=issue_id,
            original_summary=self.session.result_summary or "(no summary)",
            validation_verdict=verdict.verdict,
            validation_summary=verdict.summary,
            concerns=concerns_text,
        )
        return prompt, ""

    async def _retry_with_resume(
        self,
        verdict: ValidationVerdict,
        work_dir: str,
        issue_id: int,
    ) -> None:
        """Retry using --resume for warm context when available."""
        self.session.state = TaskSessionState.RETRYING
        self.session.retry_count += 1
        self.session.updated_at = _now_iso()

        retry_prompt, resume_id = self._build_retry_prompt(
            self.task_config.resume_on_partial and bool(self.session.cli_session_id),
            verdict,
            "\n".join(f"- {c}" for c in verdict.concerns) or "- (none specified)",
            issue_id,
        )
        cli_config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=self.task_config.orchestrate_model or self.task_config.task_model,
            max_budget_usd=self.task_config.retry_budget_usd,
            timeout_seconds=self.task_config.orchestrate_timeout_seconds,
            mcp_servers=self._get_mcp_servers(self.session.parent_subject),
            cwd=work_dir,
            resume_session_id=resume_id,
        )

        tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )
        callback = self._chain_event_callback(tracker.handle_event)

        async with self._work_dir_lock:
            retry_result = await asyncio.get_running_loop().run_in_executor(
                None, invoke_cli_monitored, retry_prompt, cli_config, callback
            )

        self.session.total_cost_usd += retry_result.cost_usd

        # Persist retry trace
        _write_prompt("golem", f"golem-{issue_id}-retry", retry_prompt)
        if retry_result.trace_events:
            self.session.retry_trace_file = _write_trace(
                "golem", f"golem-{issue_id}-retry", retry_result.trace_events
            )

        # Re-validate
        description = self._get_description(issue_id)
        retry_verdict = self._run_overall_validation(issue_id, description, work_dir)
        self._emit_event(
            f"Retry validation: {retry_verdict.verdict} "
            f"(confidence {retry_verdict.confidence:.0%})"
        )

        if retry_verdict.verdict == "PASS":
            self.session.supervisor_phase = "committing"
            self._commit_and_complete(issue_id, work_dir, retry_verdict)
        else:
            self._escalate(retry_verdict)

    # -- Commit & complete -----------------------------------------------------

    def _commit_and_complete(
        self, issue_id: int, work_dir: str, verdict: ValidationVerdict
    ) -> None:
        if self.task_config.auto_commit and verdict.verdict == "PASS":
            task_type = verdict.task_type
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

            if self._worktree_path:
                merge_sha = merge_and_cleanup(
                    self._base_work_dir, issue_id, self._worktree_path
                )
                if merge_sha:
                    self.session.commit_sha = merge_sha
                    self._emit_event(f"Merged worktree branch → {merge_sha}")
                    self._slog.info("Merged to base → %s", merge_sha)
                    self._worktree_path = ""
                else:
                    self._emit_event(
                        "Worktree merge failed — branch preserved",
                        is_error=True,
                    )
                    self.session.state = TaskSessionState.FAILED
                    self.session.errors.append(
                        "worktree merge failed — branch preserved for manual recovery"
                    )
                    self._update_task(
                        issue_id,
                        status=TaskStatus.IN_PROGRESS,
                        comment=(
                            "Agent work passed validation but worktree merge "
                            "failed. Branch "
                            f"agent/{issue_id} preserved for manual recovery."
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

        from .core.run_log import format_duration

        self._emit_event(f"Task completed: ${self.session.total_cost_usd:.2f}{extras}")

        self._update_task(
            issue_id,
            status=TaskStatus.CLOSED,
            progress=100,
            comment=(
                f"Task completed by agent (subagent orchestration)\n"
                f"Cost: ${self.session.total_cost_usd:.2f}, "
                f"Duration: {format_duration(self.session.duration_seconds)}, "
                f"Validation: {self.session.validation_verdict}{extras}"
            ),
        )
        self._slog.info(
            "Completed (subagent, $%.2f, verdict=%s)",
            self.session.total_cost_usd,
            self.session.validation_verdict,
        )

    # -- Escalation ------------------------------------------------------------

    def _escalate(self, verdict: ValidationVerdict) -> None:
        issue_id = self.session.parent_issue_id
        self.session.state = TaskSessionState.FAILED
        self.session.updated_at = _now_iso()

        concerns_text = "\n".join(f"- {c}" for c in verdict.concerns) or "- (none)"

        from .core.run_log import format_duration

        self._update_task(
            issue_id,
            status=TaskStatus.IN_PROGRESS,
            comment=(
                f"**Golem escalation (subagent) — needs human review**\n\n"
                f"Verdict: {verdict.verdict} "
                f"(confidence: {verdict.confidence:.0%})\n"
                f"Summary: {verdict.summary}\n\n"
                f"Concerns:\n{concerns_text}\n\n"
                f"Cost: ${self.session.total_cost_usd:.2f} | "
                f"Duration: {format_duration(self.session.duration_seconds)} | "
                f"Retries: {self.session.retry_count}"
            ),
        )
        self._slog.warning(
            "Escalated (subagent, verdict=%s)",
            verdict.verdict,
        )

    # -- Helpers ---------------------------------------------------------------

    def _emit_event(self, summary: str, *, is_error: bool = False) -> None:
        self.session.event_log.append(
            {
                "kind": "supervisor",
                "tool_name": "",
                "summary": summary,
                "timestamp": time.time(),
                "is_error": is_error,
            }
        )
        if len(self.session.event_log) > 500:
            self.session.event_log = self.session.event_log[-500:]
        self.session.milestone_count += 1
        self._checkpoint()

    def _checkpoint(self) -> None:
        if self._save_callback:
            try:
                self._save_callback()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._slog.warning("Checkpoint failed: %s", exc)
