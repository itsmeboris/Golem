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
import subprocess
import time
from typing import Any

from .checkpoint import delete_checkpoint, save_checkpoint
from .committer import commit_changes
from .errors import InfrastructureError
from .core.cli_wrapper import CLIConfig, CLIResult, CLIType, invoke_cli_monitored
from .core.config import PROJECT_ROOT, GolemFlowConfig
from .core.flow_base import _StreamingTraceWriter, _write_prompt, _write_trace
from .core.json_extract import extract_json
from .core.log_context import SessionLogAdapter
from .log_context import phase_var, set_task_context
from .event_tracker import TaskEventTracker
from .interfaces import TaskStatus
from .orchestrator import (
    TaskSession,
    TaskSessionState,
    _now_iso,
)
from .profile import GolemProfile
from .sandbox import make_sandbox_preexec
from .ensemble import EnsembleResult, pick_best_result
from .validation import ValidationVerdict, run_validation
from .workdir import resolve_work_dir
from .utils import format_duration
from .verifier import run_verification
from .worktree_manager import (
    cleanup_worktree,
    create_worktree,
)
from .pitfall_extractor import extract_pitfalls
from .pitfall_writer import update_agents_md

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
        verified_ref: str | None = None,
        on_verified_ref: Any | None = None,
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
        self._verified_ref = verified_ref
        self._on_verified_ref = on_verified_ref
        self._base_work_dir: str = ""
        self._worktree_path: str = ""
        self._trace_writer: _StreamingTraceWriter | None = None
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

    def _build_verify_section(self, work_dir: str) -> str:
        """Build the VERIFY phase command block based on repo config.

        Used to fill the {verify_commands_section} placeholder in
        orchestrate_task.txt.
        """
        from .verify_config import (
            load_verify_config,
        )  # pylint: disable=import-outside-toplevel
        from .verifier import (
            _has_golem_source,
        )  # pylint: disable=import-outside-toplevel

        cfg = load_verify_config(work_dir)
        if cfg and cfg.commands:
            lines = []
            for i, cmd in enumerate(cfg.commands, 1):
                lines.append(f"{i}. {' '.join(cmd.cmd)}  # {cmd.role}")
            cmd_block = "\n".join(lines)
            return (
                "Read .golem/verify.yaml to confirm the command list,"
                " then run:\n\n" + cmd_block
            )
        if _has_golem_source(work_dir):
            return (
                "Run these commands in sequence and report results:\n"
                "1. black --check golem/\n"
                "2. pylint --errors-only golem/\n"
                "3. pytest --cov=golem --cov-fail-under=100"
            )
        return (
            "No verification commands are configured for this repo.\n"
            "Run 'golem attach --force-detect' to auto-detect the stack,\n"
            "or create .golem/verify.yaml manually.\n"
            "Report BLOCKED — verification cannot proceed without commands."
        )

    def _get_mcp_servers(self, subject: str) -> list[str]:
        servers = self.profile.tool_provider.servers_for_subject(subject)
        max_servers = self.task_config.max_mcp_servers
        self._slog.info(
            "MCP servers for '%s': %d servers %s", subject, len(servers), servers
        )
        if max_servers > 0 and len(servers) > max_servers:
            self._slog.warning(
                "MCP server count %d exceeds limit %d, truncating to first %d",
                len(servers),
                max_servers,
                max_servers,
            )
            servers = servers[:max_servers]
        return servers

    def _handle_mcp_tool_validation(self, event: dict) -> None:
        """Validate MCP tool definitions received in a CLI session init event.

        When the CLI session emits a ``{"type":"system","subtype":"init"}`` event,
        it includes a ``tools`` list of MCP tool definitions advertised by the
        configured servers.  This method calls ``validate_tools`` on the active
        tool provider so that invalid tool definitions are logged before the
        agent uses them.

        No-op for non-init events or when the tool provider is absent.
        """
        if event.get("type") != "system" or event.get("subtype") != "init":
            return
        tools = event.get("tools")
        if not tools:
            return
        # The init event's "tools" list contains tool names (strings) for
        # built-in tools and dict objects for MCP tools.  Only validate dicts.
        mcp_tools = [t for t in tools if isinstance(t, dict)]
        if not mcp_tools:
            return
        provider = getattr(self.profile, "tool_provider", None)
        if provider is None:
            return
        _valid, warnings = provider.validate_tools(mcp_tools)
        if warnings:
            self._slog.warning(
                "MCP tool validation: %d tool(s) rejected from init event",
                len(warnings),
            )

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
        """Full subagent orchestration pipeline.

        When ``self.session.checkpoint_phase`` is set (from a previous crash),
        the pipeline skips phases that have already completed:
        - ``post_execute`` → skip CLI execution, jump to validation
        - ``post_validate`` + PASS verdict → skip to commit
        - ``post_validate`` + PARTIAL verdict → skip to retry
        """
        issue_id = self.session.parent_issue_id
        self.session.execution_mode = "subagent"
        resume_phase = self.session.checkpoint_phase
        self.session.checkpoint_phase = ""  # consumed

        set_task_context(str(issue_id))
        start = time.time()

        # Create trace writer early so pre-flight events are visible in dashboard
        event_id = f"golem-{issue_id}"
        self._trace_writer = _StreamingTraceWriter("golem", event_id)
        self.session.trace_file = self._trace_writer.relative_path
        self._checkpoint()

        try:
            self._emit_event("Task picked up, starting setup...")
            # Assign issue to Golem on pickup (GitHub-specific)
            backend = self.profile.state_backend
            if hasattr(backend, "assign_issue"):
                try:
                    backend.assign_issue(issue_id)
                except Exception:  # pylint: disable=broad-exception-caught
                    self._slog.debug("assign_issue failed (non-fatal)", exc_info=True)
            description = self._get_description(issue_id)
            work_dir = await self._setup_work_dir(issue_id, description)

            skip_execute = resume_phase in ("post_execute", "post_validate")
            skip_validate = resume_phase == "post_validate"

            if skip_execute:
                self._slog.info(
                    "Resuming from checkpoint phase=%s, skipping CLI execution",
                    resume_phase,
                )
                self._emit_event(f"Resumed from checkpoint (phase={resume_phase})")
            else:
                await self._execute_phases(issue_id, description, work_dir, start)

            verdict = await self._resolve_verdict(
                skip_validate, issue_id, description, work_dir
            )

            # Phase 5: Handle verdict
            #   PASS  → commit
            #   PARTIAL → fix loop (up to validator_fix_depth)
            #           → if still PARTIAL: full retry (up to max_retries)
            #           → escalate
            #   FAIL  → escalate
            if verdict.verdict == "PASS":
                self.session.supervisor_phase = "committing"
                phase_var.set("committing")
                self._emit_event("Finalizing task...")
                await self._commit_and_complete(issue_id, work_dir, verdict)
            elif verdict.verdict == "PARTIAL":
                verdict = await self._fix_loop(verdict, work_dir, issue_id, description)
                if verdict.verdict == "PASS":
                    self.session.supervisor_phase = "committing"
                    phase_var.set("committing")
                    self._emit_event("Finalizing task...")
                    await self._commit_and_complete(issue_id, work_dir, verdict)
                elif (
                    verdict.verdict == "PARTIAL"
                    and self.session.retry_count < self.task_config.max_retries
                ):
                    await self._retry_with_resume(verdict, work_dir, issue_id)
                else:
                    self._escalate(verdict)
            else:
                self._escalate(verdict)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.session.duration_seconds = time.time() - start
            self.session.state = TaskSessionState.FAILED
            self.session.errors.append(str(exc))
            self._emit_event(f"Supervisor failed: {exc}", is_error=True)
            self._update_task(
                issue_id,
                comment=f"Golem subagent supervisor failed: {exc}",
            )
            self._slog.error("Supervisor failed: %s", exc)

        finally:
            if self._trace_writer:
                self._trace_writer.close()
            self._delete_checkpoint(issue_id)
            if self._worktree_path:
                if self.session.state == TaskSessionState.FAILED:
                    cleanup_worktree(
                        self._base_work_dir, self._worktree_path, keep_branch=True
                    )
                elif not self.session.commit_sha:
                    cleanup_worktree(self._base_work_dir, self._worktree_path)
            self._checkpoint()

    async def _setup_work_dir(self, issue_id: int, description: str) -> str:
        """Resolve base work dir and optionally create worktree."""
        if self._work_dir_override:
            self._base_work_dir = self._work_dir_override
        elif self.session.base_work_dir:
            self._base_work_dir = self.session.base_work_dir
        else:
            self._base_work_dir = resolve_work_dir(
                subject=self.session.parent_subject,
                description=description,
                work_dirs=self.task_config.work_dirs,
                default_work_dir=self.task_config.default_work_dir,
                project_root=str(PROJECT_ROOT),
            )

        self.session.base_work_dir = self._base_work_dir
        work_dir = self._base_work_dir
        if self.task_config.use_worktrees:
            self._emit_event("Creating isolated worktree...")
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

        # Pre-flight verification: ensure base branch is healthy before spending budget
        if getattr(self.task_config, "preflight_verify", True):
            self._emit_event(
                "Running pre-flight verification (black, pylint, pytest)..."
            )
            self._slog.info("Running pre-flight verification on base branch...")
            loop = asyncio.get_running_loop()
            _vt = getattr(self.task_config, "verification_timeout_seconds", 120)
            vr = await loop.run_in_executor(
                None, lambda: run_verification(work_dir, timeout=_vt)
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

                # Fallback: recreate worktree from last-known-good ref
                if self._verified_ref and self.task_config.use_worktrees:
                    self._slog.warning(
                        "HEAD failed pre-flight, falling back to verified ref %s",
                        self._verified_ref,
                    )
                    self._emit_event(
                        "HEAD broken — falling back to last verified commit"
                    )
                    cleanup_worktree(self._base_work_dir, self._worktree_path)
                    self._worktree_path = create_worktree(
                        self._base_work_dir,
                        issue_id,
                        start_point=self._verified_ref,
                    )
                    work_dir = self._worktree_path
                else:
                    raise InfrastructureError(
                        f"Base branch verification failed — aborting to save budget. {detail}"
                    )
            else:
                self._emit_event(f"Pre-flight passed ({vr.duration_s:.0f}s)")
                self._slog.info("Pre-flight verification passed (%.1fs)", vr.duration_s)
                # Record HEAD SHA as verified for future tasks
                if self._on_verified_ref:
                    result = await asyncio.to_thread(
                        subprocess.run,
                        ["git", "rev-parse", "HEAD"],
                        capture_output=True,
                        text=True,
                        check=False,
                        cwd=work_dir,
                    )
                    if result.returncode == 0:
                        self._on_verified_ref(result.stdout.strip())

        return work_dir

    async def _execute_phases(
        self, issue_id: int, description: str, work_dir: str, start: float
    ) -> None:
        """Phases 0-3: clarity gate, build prompt, invoke CLI, parse report."""
        # Phase 0: clarity gate (opt-in)
        if self.task_config.clarity_check:
            from functools import partial  # pylint: disable=import-outside-toplevel

            from .clarity import (
                check_clarity,
            )  # pylint: disable=import-outside-toplevel

            loop = asyncio.get_running_loop()
            cr = await loop.run_in_executor(
                None,
                partial(
                    check_clarity,
                    self.session.parent_subject,
                    description,
                    sandbox_enabled=self.task_config.sandbox_enabled,
                    sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
                    sandbox_memory_gb=self.task_config.sandbox_memory_gb,
                ),
            )
            self.session.total_cost_usd += cr.cost_usd
            if not cr.is_clear(self.task_config.clarity_threshold):
                self._slog.warning(
                    "Task clarity too low (%d/5): %s", cr.score, cr.reason
                )
                self._emit_event(
                    f"Task too vague (clarity {cr.score}/5: {cr.reason}). "
                    f"Escalating for human clarification."
                )
                self._update_task(
                    issue_id,
                    comment=(
                        f"**Golem: task description too vague for autonomous execution**\n\n"
                        f"Clarity score: {cr.score}/5 "
                        f"(threshold: {self.task_config.clarity_threshold})\n"
                        f"Reason: {cr.reason}\n\n"
                        f"Please add more detail to the task description and re-assign."
                    ),
                )
                self.session.state = TaskSessionState.FAILED
                self.session.errors.append(f"Clarity too low: {cr.score}/5")
                from .errors import TaskExecutionError

                raise TaskExecutionError(f"Task clarity below threshold: {cr.score}/5")

        prompt = self._build_prompt(issue_id, description, work_dir)
        result = await self._invoke_orchestrator(prompt, work_dir, issue_id, start)
        report = self._parse_report(result)
        self.session.result_summary = report.get("summary", "")
        self._emit_event(f"Orchestrator finished: {report.get('status', 'unknown')}")
        self._update_task(issue_id, status=TaskStatus.FIXED, progress=80)
        self._save_checkpoint(issue_id, "post_execute")

    async def _resolve_verdict(
        self,
        skip_validate: bool,
        issue_id: int,
        description: str,
        work_dir: str,
    ) -> ValidationVerdict:
        """Run validation or reconstruct verdict from checkpoint."""
        if skip_validate:
            verdict = ValidationVerdict(
                verdict=self.session.validation_verdict or "PASS",
                confidence=self.session.validation_confidence,
                summary=self.session.validation_summary,
                concerns=self.session.validation_concerns,
            )
            self._slog.info(
                "Skipping validation (checkpoint), using stored verdict=%s",
                verdict.verdict,
            )
            return verdict

        self.session.supervisor_phase = "validating"
        phase_var.set("validating")
        self._emit_event("Running external validation...")
        verdict = await self._run_overall_validation(issue_id, description, work_dir)
        self._emit_event(
            f"Validation: {verdict.verdict} " f"(confidence {verdict.confidence:.0%})"
        )
        self._save_checkpoint(issue_id, "post_validate")
        return verdict

    # -- Checkpoint helpers ----------------------------------------------------

    def _save_checkpoint(self, issue_id: int, phase: str) -> None:
        try:
            save_checkpoint(issue_id, self.session, phase=phase)
        except Exception:  # pylint: disable=broad-exception-caught
            self._slog.debug("save_checkpoint failed", exc_info=True)

    def _delete_checkpoint(self, issue_id: int) -> None:
        try:
            delete_checkpoint(issue_id)
        except Exception:  # pylint: disable=broad-exception-caught
            self._slog.debug("delete_checkpoint failed", exc_info=True)

    # -- Prompt building -------------------------------------------------------

    def _build_prompt(self, issue_id: int, description: str, work_dir: str) -> str:
        simplify_section = ""
        if self.task_config.enable_simplify_pass:
            simplify_section = (
                "### Phase 3.5: Simplify\n"
                "\n"
                "Write: ``## Phase: SIMPLIFY``\n"
                "\n"
                "After all Builders complete but before Review, dispatch a cleanup\n"
                "Builder to simplify the code produced during BUILD.\n"
                "\n"
                'Dispatch one Builder (subagent_type: "builder") with this prompt:\n'
                "\n"
                "````\n"
                f"Working directory: {work_dir}\n"
                "\n"
                "## Skills\n"
                "Invoke the 'simplify' skill before making any changes.\n"
                "\n"
                "## Your task\n"
                "Review and simplify ONLY the files changed by previous Builders.\n"
                "Focus on:\n"
                "- Removing over-defensive error handling (redundant try/except,\n"
                "  unnecessary None checks on values that cannot be None)\n"
                "- Removing redundant type checks and assertions\n"
                "- Removing commented-out code and dead imports\n"
                "- Simplifying verbose or over-engineered patterns\n"
                "\n"
                "Preserve ALL business logic, test coverage, and public interfaces.\n"
                "Do NOT add new features, change architecture, or refactor beyond\n"
                "what is needed to remove sloppiness.\n"
                "\n"
                "Files to review:\n"
                "[List files changed by Builders here]\n"
                "\n"
                "## Self-verification\n"
                "Before reporting back, run ONLY these - nothing more:\n"
                "1. pytest path/to/your/test_file.py -x  (targeted, NOT full suite)\n"
                "2. black --check path/to/changed/files\n"
                "3. pylint --errors-only path/to/changed/files\n"
                "Do NOT run pytest --cov, pytest without a path, or the full test suite.\n"
                "````\n"
                "\n"
                "**Scoping rule:** Only include files that Builders reported as changed.\n"
                "If no files were changed, skip this phase with a one-line note.\n"
            )
        enhanced_review_section = ""
        if self.task_config.enhanced_review:
            from .parallel_review import (  # pylint: disable=import-outside-toplevel
                ReviewerRole,
                enhanced_reviewers,
                roles_from_config,
            )

            if self.task_config.review_roles:
                roles = roles_from_config(self.task_config.review_roles)
            else:
                roles = enhanced_reviewers()

            # Filter out SPEC and QUALITY since those are the existing 2-stage review
            extra_roles = [
                r for r in roles if r not in (ReviewerRole.SPEC, ReviewerRole.QUALITY)
            ]

            if extra_roles:
                role_list = "\n".join(
                    "- **%s**: %s" % (r.value, r.description) for r in extra_roles
                )
                enhanced_review_section = (
                    "### Phase 4.5: Enhanced Parallel Review\n"
                    "\n"
                    "After the standard 2-stage review passes, dispatch additional\n"
                    "specialized reviewers **in parallel** (use a single message with\n"
                    "multiple Agent tool calls). Each reviewer is a separate\n"
                    '``subagent_type: "reviewer"`` dispatch.\n'
                    "\n"
                    "**Additional reviewers to dispatch:**\n"
                    "%s\n"
                    "\n"
                    "Each reviewer prompt template is in ``golem/prompts/`` — read it\n"
                    "with the Read tool and fill in the ``{work_dir}`` placeholder.\n"
                    "Include the same context you gave the Stage 1/2 reviewers.\n"
                    "\n"
                    "**Aggregation rules:**\n"
                    "- Only report findings with confidence >= 80\n"
                    "- If ANY reviewer reports NEEDS_FIXES with findings >= 80\n"
                    "  confidence → dispatch a Builder to fix → re-review that\n"
                    "  specific reviewer only\n"
                    "- All enhanced reviewers must APPROVE before proceeding to\n"
                    "  Phase 5 (Verify)\n"
                    "\n"
                ) % role_list
        role_contexts = ""
        if self.task_config.context_injection:
            from .context_injection import (
                build_role_context_section,
            )  # pylint: disable=import-outside-toplevel

            role_contexts = build_role_context_section()
        structured_planning_section = (
            "Read ``golem/prompts/orchestrate_planner_template.txt`` with the Read\n"
            'tool, then dispatch a Planner (subagent_type: "reviewer") following\n'
            "that template exactly.\n\n"
            "The Planner will produce:\n"
            "1. A File Map: every file that changes and its responsibility\n"
            "2. Step-by-step implementation tasks with exact code (TDD order)\n"
            "3. A test strategy: what is tested, which edge cases are covered\n\n"
            "After the Planner returns:\n"
            "- **Trivial** tasks: accept the plan directly — skip plan review.\n"
            "- **Standard** and **Complex** tasks: dispatch a Plan Reviewer\n"
            '  (subagent_type: "reviewer") to validate completeness, no\n'
            "  placeholders, type consistency, and buildability.\n\n"
            "Write the final plan as a ``## Implementation Plan`` section in your\n"
            "PLAN phase message. Builders receive this verbatim — zero placeholders.\n"
        )
        return self._format_prompt(
            "orchestrate_task.txt",
            issue_id=issue_id,
            parent_subject=self.session.parent_subject,
            task_description=description,
            work_dir=work_dir,
            inner_retry_max=self.task_config.inner_retry_max,
            validator_fix_depth=self.task_config.validator_fix_depth,
            simplify_section=simplify_section,
            enhanced_review_section=enhanced_review_section,
            role_contexts=role_contexts,
            verify_commands_section=self._build_verify_section(work_dir),
            structured_planning_section=structured_planning_section,
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
            model=self.task_config.orchestrate_model or self.task_config.task_model,
            max_budget_usd=self.task_config.orchestrate_budget_usd,
            timeout_seconds=self.task_config.orchestrate_timeout_seconds,
            mcp_servers=mcp_servers,
            cwd=work_dir,
            system_prompt=system_prompt,
            sandbox_enabled=self.task_config.sandbox_enabled,
            sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
            sandbox_memory_gb=self.task_config.sandbox_memory_gb,
        )

        tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )

        # Write prompt immediately so the dashboard can show it during the run
        _write_prompt("golem", f"golem-{issue_id}", prompt)

        def _streaming_callback(event: dict) -> None:
            if self._trace_writer:
                self._trace_writer.append(event)
            self._handle_mcp_tool_validation(event)
            tracker.handle_event(event)

        callback = self._chain_event_callback(_streaming_callback)

        self.session.supervisor_phase = "orchestrating"
        phase_var.set("orchestrating")
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
            "summary": str(raw_output),
        }

    # -- Validation ------------------------------------------------------------

    async def _run_overall_validation(
        self, issue_id: int, description: str, work_dir: str
    ) -> ValidationVerdict:
        # Budget guard: skip validation if budget already exceeded
        max_cost = self.task_config.max_fix_cost_usd
        if max_cost > 0 and self.session.total_cost_usd >= max_cost:
            self._slog.warning(
                "Validation skipped: total cost $%.2f already exceeds limit $%.2f",
                self.session.total_cost_usd,
                max_cost,
            )
            return ValidationVerdict(
                verdict="SKIP",
                confidence=0.0,
                summary="validation skipped — budget exceeded ($%.2f of $%.2f)"
                % (self.session.total_cost_usd, max_cost),
            )

        self.session.state = TaskSessionState.VALIDATING
        self.session.updated_at = _now_iso()

        from functools import partial

        loop = asyncio.get_running_loop()
        verdict = await loop.run_in_executor(
            None,
            partial(
                run_validation,
                issue_id=issue_id,
                subject=self.session.parent_subject,
                description=description,
                session_data=self.session.to_dict(),
                work_dir=work_dir,
                model=self.task_config.validation_model,
                budget_usd=self.task_config.validation_budget_usd,
                timeout_seconds=self.task_config.validation_timeout_seconds,
                ast_analysis=self.task_config.ast_analysis,
                sandbox_enabled=self.task_config.sandbox_enabled,
                sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
                sandbox_memory_gb=self.task_config.sandbox_memory_gb,
            ),
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

    # -- Fix loop (inner fix cycle) --------------------------------------------

    async def _fix_loop(
        self,
        verdict: ValidationVerdict,
        work_dir: str,
        issue_id: int,
        description: str,
    ) -> ValidationVerdict:
        """Inner fix loop: retry with validator concerns up to validator_fix_depth times."""
        depth = self.task_config.validator_fix_depth
        for i in range(depth):
            max_cost = self.task_config.max_fix_cost_usd
            if max_cost > 0 and self.session.total_cost_usd >= max_cost:
                self._slog.info(
                    "Fix loop cost guard: $%.2f >= $%.2f limit, stopping",
                    self.session.total_cost_usd,
                    max_cost,
                )
                self._emit_event(
                    f"Fix loop stopped: cost ${self.session.total_cost_usd:.2f} "
                    f"exceeds limit ${max_cost:.2f}"
                )
                return verdict

            self.session.fix_iteration = i + 1
            self.session.state = TaskSessionState.RETRYING
            self.session.updated_at = _now_iso()

            self._emit_event(
                f"Fix iteration {i + 1}/{depth}: addressing validator concerns..."
            )

            concerns_text = (
                "\n".join(f"- {c}" for c in verdict.concerns) or "- (none specified)"
            )
            use_resume = self.task_config.resume_on_partial and bool(
                self.session.cli_session_id
            )
            retry_prompt, resume_id = self._build_retry_prompt(
                use_resume,
                verdict,
                concerns_text,
                issue_id,
                fix_iteration=i + 1,
                fix_depth=depth,
            )

            cli_config = CLIConfig(
                cli_type=CLIType.CLAUDE,
                model=self.task_config.orchestrate_model or self.task_config.task_model,
                max_budget_usd=self.task_config.retry_budget_usd,
                timeout_seconds=self.task_config.orchestrate_timeout_seconds,
                mcp_servers=self._get_mcp_servers(self.session.parent_subject),
                cwd=work_dir,
                resume_session_id=resume_id,
                sandbox_enabled=self.task_config.sandbox_enabled,
                sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
                sandbox_memory_gb=self.task_config.sandbox_memory_gb,
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

            # Persist fix iteration trace
            _write_prompt("golem", f"golem-{issue_id}-fix{i + 1}", retry_prompt)
            if retry_result.trace_events:
                trace_path = _write_trace(
                    "golem", f"golem-{issue_id}-fix{i + 1}", retry_result.trace_events
                )
                self.session.fix_trace_files.append(trace_path)
            self._save_checkpoint(issue_id, "post_execute")

            # Capture updated session_id for next iteration
            if retry_result.session_id:
                self.session.cli_session_id = retry_result.session_id

            # Re-validate
            verdict = await self._run_overall_validation(
                issue_id, description, work_dir
            )
            self._emit_event(
                f"Fix iteration {i + 1}/{depth} validation: {verdict.verdict} "
                f"(confidence {verdict.confidence:.0%})"
            )
            self._save_checkpoint(issue_id, "post_validate")

            if verdict.verdict != "PARTIAL":
                return verdict

        return verdict

    # -- Retry with --resume ---------------------------------------------------

    def _build_retry_prompt(
        self,
        use_resume: bool,
        verdict: ValidationVerdict,
        concerns_text: str,
        issue_id: int,
        *,
        fix_iteration: int = 0,
        fix_depth: int = 0,
    ) -> tuple[str, str]:
        """Build retry prompt and resume session id.

        When *fix_iteration* > 0 the prompt includes iteration context so
        the agent knows how many attempts remain and can prioritise accordingly.
        """
        files_text = (
            "\n".join(f"- {f}" for f in verdict.files_to_fix) or "- (none specified)"
        )
        tests_text = "\n".join(f"- {t}" for t in verdict.test_failures) or "- (none)"

        if use_resume:
            self._slog.info(
                "Warm retry with --resume (session %s)",
                self.session.cli_session_id,
            )
            self._emit_event("Warm retry with --resume...")
            iteration_header = ""
            if fix_iteration:
                remaining = fix_depth - fix_iteration
                iteration_header = (
                    f"**Fix iteration**: {fix_iteration}/{fix_depth} "
                    f"({remaining} attempt(s) remaining before escalation)\n\n"
                )
            prompt = (
                f"The external validator reviewed your work and found issues.\n\n"
                f"{iteration_header}"
                f"**Verdict**: {verdict.verdict}\n"
                f"**Summary**: {verdict.summary}\n\n"
                f"**Concerns**:\n{concerns_text}\n\n"
                f"**Files to fix**:\n{files_text}\n\n"
                f"**Test failures**:\n{tests_text}\n\n"
                f"Address ONLY these concerns — do not redo work that already "
                f"passed review. Investigate each concern to understand the root "
                f"cause before applying fixes. After fixing, re-run "
                f"``black --check .``, ``pylint --errors-only golem/``, and "
                f"``pytest`` to confirm your changes. Then produce an updated "
                f"JSON completion report."
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
            files_to_fix=files_text,
            test_failures=tests_text,
            verification_feedback=self._verification_feedback(),
            fix_iteration=fix_iteration,
            fix_depth=fix_depth,
        )
        return prompt, ""

    def _verification_feedback(self) -> str:
        """Format verification result for prompt injection."""
        vr = self.session.verification_result
        if not vr:
            return "(no verification failures)"
        if vr.get("passed", True):
            return "(verification passed)"
        parts = []

        # Generic verification path — use command_results when present
        if vr.get("command_results"):
            for cr in vr["command_results"]:
                if not cr["passed"]:
                    parts.append(
                        "%s (%s): FAILED\n%s"
                        % (cr["role"], cr["cmd"], cr["output"][:3000])
                    )
            if vr.get("error"):
                parts.append(vr["error"])
            return "\n".join(parts) if parts else "(verification passed)"

        # Legacy Python verification path
        if vr.get("black_output"):
            parts.append("Black:\n%s" % vr["black_output"])
        if vr.get("pylint_output"):
            parts.append("Pylint:\n%s" % vr["pylint_output"])
        if vr.get("pytest_output"):
            parts.append("Pytest:\n%s" % vr["pytest_output"][:3000])
        return "\n".join(parts) if parts else "(verification passed)"

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
            sandbox_enabled=self.task_config.sandbox_enabled,
            sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
            sandbox_memory_gb=self.task_config.sandbox_memory_gb,
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
        self._save_checkpoint(issue_id, "post_execute")

        # Re-validate
        description = self._get_description(issue_id)
        retry_verdict = await self._run_overall_validation(
            issue_id, description, work_dir
        )
        self._emit_event(
            f"Retry validation: {retry_verdict.verdict} "
            f"(confidence {retry_verdict.confidence:.0%})"
        )
        self._save_checkpoint(issue_id, "post_validate")

        if retry_verdict.verdict == "PASS":
            self.session.supervisor_phase = "committing"
            phase_var.set("committing")
            await self._commit_and_complete(issue_id, work_dir, retry_verdict)
        elif (
            self.task_config.ensemble_on_second_retry
            and self.session.retry_count < self.task_config.max_retries + 1
        ):
            await self._run_ensemble_retry(retry_verdict, work_dir, issue_id)
        else:
            self._escalate(retry_verdict)

    # -- Ensemble retry --------------------------------------------------------

    _ENSEMBLE_HINTS = [
        "Try a different approach than before.",
        "Focus on the test failures and fix them directly.",
        "Simplify the implementation — remove complexity.",
    ]

    async def _run_ensemble_retry(
        self,
        verdict: ValidationVerdict,
        work_dir: str,
        issue_id: int,
    ) -> None:
        """Spawn N parallel candidates with different strategies; commit the best PASS."""
        n = self.task_config.ensemble_candidates

        # Budget guard: check if we can afford N candidates before spawning
        max_cost = self.task_config.max_fix_cost_usd
        if max_cost > 0:
            remaining = max_cost - self.session.total_cost_usd
            estimated_cost = n * self.task_config.retry_budget_usd
            if estimated_cost > remaining:
                self._slog.warning(
                    "Ensemble retry skipped: estimated cost $%.2f exceeds remaining budget $%.2f",
                    estimated_cost,
                    remaining,
                )
                self._escalate(
                    ValidationVerdict(
                        verdict="FAIL",
                        confidence=0.0,
                        summary="ensemble retry skipped — would exceed budget ($%.2f remaining, $%.2f estimated)"
                        % (remaining, estimated_cost),
                    )
                )
                return

        self._emit_event("Ensemble retry: spawning %d parallel candidates" % n)
        self._slog.info("Ensemble retry: spawning %d candidates for #%s", n, issue_id)

        base_dir = self._base_work_dir or work_dir
        description = self._get_description(issue_id)

        # Build (worktree_path, candidate_id) pairs
        candidate_ids = [issue_id * 1000 + i for i in range(n)]
        worktree_paths: list[str] = []

        async def _run_one(idx: int, cand_work_dir: str) -> tuple[CLIResult, str]:
            hint = self._ENSEMBLE_HINTS[idx % len(self._ENSEMBLE_HINTS)]
            concerns_text = (
                "\n".join("- %s" % c for c in verdict.concerns) or "- (none specified)"
            )
            files_text = (
                "\n".join("- %s" % f for f in verdict.files_to_fix)
                or "- (none specified)"
            )
            tests_text = (
                "\n".join("- %s" % t for t in verdict.test_failures) or "- (none)"
            )
            prompt = (
                "Ensemble candidate %d/%d. %s\n\n"
                "The previous attempt received verdict: %s\n"
                "Summary: %s\n\n"
                "Concerns:\n%s\n\n"
                "Files to fix:\n%s\n\n"
                "Test failures:\n%s\n\n"
                "Address these issues and produce an updated JSON completion report."
                % (
                    idx + 1,
                    n,
                    hint,
                    verdict.verdict,
                    verdict.summary,
                    concerns_text,
                    files_text,
                    tests_text,
                )
            )
            cli_config = CLIConfig(
                cli_type=CLIType.CLAUDE,
                model=self.task_config.orchestrate_model or self.task_config.task_model,
                max_budget_usd=self.task_config.retry_budget_usd,
                timeout_seconds=self.task_config.orchestrate_timeout_seconds,
                mcp_servers=self._get_mcp_servers(self.session.parent_subject),
                cwd=cand_work_dir,
                sandbox_enabled=self.task_config.sandbox_enabled,
                sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
                sandbox_memory_gb=self.task_config.sandbox_memory_gb,
            )
            _write_prompt("golem", "golem-%s-ensemble%d" % (issue_id, idx), prompt)
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None, invoke_cli_monitored, prompt, cli_config, None
            )
            self.session.total_cost_usd += result.cost_usd
            return result, cand_work_dir

        try:
            for cid in candidate_ids:
                wt_path = create_worktree(base_dir, cid)
                worktree_paths.append(wt_path)

            candidate_outputs = await asyncio.gather(
                *[_run_one(i, wt) for i, wt in enumerate(worktree_paths)]
            )

            # Validate each candidate
            from functools import partial

            loop = asyncio.get_running_loop()
            ensemble_results: list[EnsembleResult] = []
            for idx, (cli_result, cand_work_dir) in enumerate(candidate_outputs):
                val_verdict = await loop.run_in_executor(
                    None,
                    partial(
                        run_validation,
                        issue_id=candidate_ids[idx],
                        subject=self.session.parent_subject,
                        description=description,
                        session_data=self.session.to_dict(),
                        work_dir=cand_work_dir,
                        model=self.task_config.validation_model,
                        budget_usd=self.task_config.validation_budget_usd,
                        timeout_seconds=self.task_config.validation_timeout_seconds,
                        ast_analysis=self.task_config.ast_analysis,
                        sandbox_enabled=self.task_config.sandbox_enabled,
                        sandbox_cpu_seconds=self.task_config.sandbox_cpu_seconds,
                        sandbox_memory_gb=self.task_config.sandbox_memory_gb,
                    ),
                )
                self.session.total_cost_usd += val_verdict.cost_usd
                self.session.validation_cost_usd += val_verdict.cost_usd
                ensemble_results.append(
                    EnsembleResult(
                        verdict=val_verdict.verdict,
                        confidence=val_verdict.confidence,
                        cost_usd=cli_result.cost_usd + val_verdict.cost_usd,
                        work_dir=cand_work_dir,
                        summary=val_verdict.summary,
                    )
                )
                self._slog.info(
                    "Ensemble candidate %d: %s (confidence %.2f)",
                    idx,
                    val_verdict.verdict,
                    val_verdict.confidence,
                )

            best = pick_best_result(ensemble_results)
            if best is None or best.verdict != "PASS":
                self._emit_event(
                    "Ensemble retry: no PASS found (best=%s), escalating"
                    % (best.verdict if best else "none")
                )
                self._slog.warning(
                    "Ensemble retry: best verdict=%s — escalating",
                    best.verdict if best else "none",
                )
                # Build a ValidationVerdict from the best ensemble result for escalation
                best_verdict = ValidationVerdict(
                    verdict=best.verdict if best else "FAIL",
                    confidence=best.confidence if best else 0.0,
                    summary=best.summary if best else "(no results)",
                )
                self._escalate(best_verdict)
                return

            # Copy winning work to main work_dir
            self._emit_event(
                "Ensemble retry: PASS — using candidate from %s" % best.work_dir
            )
            rsync_result = await asyncio.to_thread(
                subprocess.run,
                [
                    "rsync",
                    "-a",
                    "--exclude",
                    ".git",
                    best.work_dir + "/",
                    work_dir + "/",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if rsync_result.returncode != 0:
                self._slog.error(
                    "Ensemble rsync failed (rc=%d): %s",
                    rsync_result.returncode,
                    rsync_result.stderr.strip(),
                )
                self._escalate(
                    ValidationVerdict(
                        verdict="FAIL",
                        confidence=0.0,
                        summary="rsync failed copying ensemble winner",
                    )
                )
                return
            # Build a ValidationVerdict from the winning EnsembleResult
            winning_verdict = ValidationVerdict(
                verdict=best.verdict,
                confidence=best.confidence,
                summary=best.summary,
            )
            self.session.supervisor_phase = "committing"
            phase_var.set("committing")
            await self._commit_and_complete(issue_id, work_dir, winning_verdict)

        finally:
            for cand_work_dir in worktree_paths:
                cleanup_worktree(base_dir, cand_work_dir)

    # -- Commit & complete -----------------------------------------------------

    async def _commit_and_complete(
        self, issue_id: int, work_dir: str, verdict: ValidationVerdict
    ) -> None:
        pr_url = ""
        if self.task_config.auto_commit and verdict.verdict == "PASS":
            task_type = verdict.task_type
            from functools import partial

            loop = asyncio.get_running_loop()
            cr = await loop.run_in_executor(
                None,
                partial(
                    commit_changes,
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
                # Create PR if backend supports it (GitHub-specific)
                if hasattr(self.profile.state_backend, "create_pull_request"):
                    try:
                        branch = f"agent/{issue_id}"
                        base_branch = self._detect_base_branch(work_dir)
                        pr_url = self.profile.state_backend.create_pull_request(
                            head=branch,
                            base=base_branch,
                            title=f"#{issue_id}: {self.session.parent_subject}",
                            body=(
                                f"Resolves #{issue_id}\n\n"
                                f"**Verdict**: {self.session.validation_verdict}\n"
                                f"**Cost**: ${self.session.total_cost_usd:.2f}\n"
                                f"**Duration**: {format_duration(self.session.duration_seconds)}"
                            ),
                        )
                        if pr_url:
                            self._emit_event(f"Created PR: {pr_url}")
                    except Exception:  # pylint: disable=broad-exception-caught
                        self._slog.debug(
                            "create_pull_request failed (non-fatal)", exc_info=True
                        )
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
            else:
                self._slog.info("No file changes to commit")
                self._emit_event("No file changes — skipping commit and merge")

            if self._worktree_path and cr.committed:
                # Hand off to the flow's merge queue instead of merging
                # inline.  The merge queue handles retries for transient
                # infra failures (NFS timeouts, stale handles) and runs
                # merge-integrity checks + reconciliation.
                self.session.merge_ready = True
                self.session.worktree_path = self._worktree_path
                self.session.base_work_dir = self._base_work_dir
                self._emit_event("Queued for merge via merge queue")
                self._slog.info(
                    "Set merge_ready for #%s (worktree=%s)",
                    issue_id,
                    self._worktree_path,
                )

        self.session.state = TaskSessionState.COMPLETED
        self.session.updated_at = _now_iso()

        extras = ""
        if self.session.commit_sha:
            extras += f", commit {self.session.commit_sha}"
        if self.session.fix_iteration:
            extras += f", {self.session.fix_iteration} fix iteration(s)"
        if self.session.retry_count:
            extras += f", {self.session.retry_count} full retry"

        # -- Post-task learning: extract pitfalls into AGENTS.md -----------
        await self._run_post_task_learning()

        self._emit_event(f"Task completed: ${self.session.total_cost_usd:.2f}{extras}")

        pr_note = f"\nPR: {pr_url}" if pr_url else ""
        self._update_task(
            issue_id,
            status=TaskStatus.CLOSED,
            progress=100,
            comment=(
                f"Task completed by agent (subagent orchestration)\n"
                f"Cost: ${self.session.total_cost_usd:.2f}, "
                f"Duration: {format_duration(self.session.duration_seconds)}, "
                f"Validation: {self.session.validation_verdict}{extras}{pr_note}"
            ),
        )
        self._slog.info(
            "Completed (subagent, $%.2f, verdict=%s)",
            self.session.total_cost_usd,
            self.session.validation_verdict,
        )

    async def _run_post_task_learning(self) -> None:
        """Extract pitfalls from recent sessions and update AGENTS.md.

        Runs as an awaited step before the final 'Task completed' event so
        that dashboard events appear in the correct order.  Failures are
        logged and emitted but never block the pipeline.
        """
        self._emit_event("Running post-task learning...")
        try:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(None, self._extract_pitfalls)
            if count:
                self._emit_event(
                    "Post-task learning: %d pitfall(s) written to AGENTS.md" % count
                )
            else:
                self._emit_event("Post-task learning: no new pitfalls found")
        except Exception:  # noqa: BLE001
            self._slog.warning("Pitfall extraction failed (non-fatal)", exc_info=True)
            self._emit_event("Post-task learning failed (non-fatal)", is_error=True)

    def _extract_pitfalls(self) -> int:
        """Extract pitfalls from recent sessions and update AGENTS.md.

        Returns the number of pitfalls written, or 0 if none.
        """
        from .orchestrator import load_sessions  # lazy import to avoid circular

        sessions = load_sessions()
        completed = [
            s.to_dict()
            for s in sessions.values()
            if s.state == TaskSessionState.COMPLETED
        ]
        # Include current session
        current = self.session.to_dict()
        if current not in completed:
            completed.append(current)
        # Limit to last 20
        completed = completed[-20:]

        pitfalls = extract_pitfalls(completed)
        if pitfalls:
            update_agents_md(pitfalls)
        return len(pitfalls)

    # -- Escalation ------------------------------------------------------------

    def _escalate(self, verdict: ValidationVerdict) -> None:
        issue_id = self.session.parent_issue_id
        self.session.state = TaskSessionState.FAILED
        self.session.updated_at = _now_iso()

        concerns_text = "\n".join(f"- {c}" for c in verdict.concerns) or "- (none)"

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
                f"Fix iterations: {self.session.fix_iteration} | "
                f"Full retries: {self.session.retry_count}"
            ),
        )
        self._slog.warning(
            "Escalated (subagent, verdict=%s)",
            verdict.verdict,
        )

    # -- Helpers ---------------------------------------------------------------

    @staticmethod
    def _detect_base_branch(work_dir: str) -> str:
        """Detect the default branch from git remote."""
        try:
            result = subprocess.run(
                ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
                capture_output=True,
                text=True,
                check=False,
                cwd=work_dir,
                timeout=30,
                preexec_fn=make_sandbox_preexec(),
            )
        except subprocess.TimeoutExpired:
            logger.warning("_detect_base_branch timed out; defaulting to master")
            return "master"
        if result.returncode == 0:
            ref = result.stdout.strip()
            return ref.replace("refs/remotes/origin/", "")
        return "master"

    def _emit_event(self, summary: str, *, is_error: bool = False) -> None:
        event = {
            "kind": "supervisor",
            "tool_name": "",
            "summary": summary,
            "timestamp": time.time(),
            "is_error": is_error,
        }
        self.session.event_log.append(event)
        self.session.milestone_count += 1
        if self._trace_writer:
            self._trace_writer.append(event)
        self._checkpoint()

    def _checkpoint(self) -> None:
        if self._save_callback:
            try:
                self._save_callback()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._slog.warning("Checkpoint failed: %s", exc)
