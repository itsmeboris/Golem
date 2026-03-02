"""Supervisor/worker execution engine for golem sessions.

Decomposes a parent Redmine issue into subtasks, executes each sequentially
via a worker agent, validates per-subtask, and summarizes on completion.

Pipeline:
    Phase 0: Fetch parent + child issues from Redmine
    Phase 1: Decompose (only if NO children exist)
    Phase 2: Execute subtasks sequentially (one worker at a time)
    Phase 3: Summarize → closing comment on parent
    Phase 4: Overall validation + commit + complete parent
    Fallback: no children AND decompose fails → monolithic run_task.txt
"""

import asyncio
import logging
import time
from dataclasses import asdict
from typing import Any

from .core.cli_wrapper import CLIConfig, CLIType, invoke_cli_monitored
from .core.config import PROJECT_ROOT, GolemFlowConfig
from .core.json_extract import extract_json
from .core.flow_base import _write_prompt, _write_trace

from .committer import commit_changes
from .event_tracker import TaskEventTracker
from .interfaces import TaskStatus
from .orchestrator import (
    SubtaskResult,
    TaskSession,
    TaskSessionState,
    _now_iso,
)
from .profile import GolemProfile
from .validation import ValidationVerdict, run_validation
from .workdir import resolve_work_dir
from .worktree_manager import cleanup_worktree, create_worktree, merge_and_cleanup

logger = logging.getLogger("golem.supervisor")


class TaskSupervisor:
    """Supervisor/worker engine: decompose → execute subtasks → summarize → commit."""

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
        self.profile = profile
        self._event_callback = event_callback
        self._work_dir_override = work_dir_override
        self._base_work_dir: str = ""
        self._worktree_path: str = ""

    # -- Profile-based helpers -------------------------------------------------

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

    def _get_base_mcp_servers(self) -> list[str]:
        """Return base MCP servers via profile."""
        return self.profile.tool_provider.base_servers()

    def _chain_event_callback(self, tracker_callback):
        """Wrap *tracker_callback* with the optional CLI event_callback."""
        if not self._event_callback:
            return tracker_callback
        ecb = self._event_callback

        def chained(event):
            ecb(event)
            tracker_callback(event)

        return chained

    def _get_child_tasks(self, parent_id: int) -> list[dict[str, Any]]:
        """Fetch child tasks via profile."""
        return self.profile.task_source.get_child_tasks(parent_id)

    def _create_child(
        self, parent_id: int, subject: str, description: str
    ) -> int | str | None:
        """Create a child task via profile."""
        return self.profile.task_source.create_child_task(
            parent_id, subject, description
        )

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    async def run(self) -> None:
        """Full supervisor pipeline."""
        issue_id = self.session.parent_issue_id
        self.session.execution_mode = "supervisor"
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

        # Set up isolated worktree if enabled
        work_dir = self._base_work_dir
        if self.task_config.use_worktrees:
            try:
                self._worktree_path = create_worktree(self._base_work_dir, issue_id)
                work_dir = self._worktree_path
                logger.info("Session %s: using worktree at %s", issue_id, work_dir)
            except RuntimeError as wt_err:
                logger.warning(
                    "Session %s: worktree creation failed (%s), using shared dir",
                    issue_id,
                    wt_err,
                )

        start = time.time()

        try:
            # Phase 0: Fetch children
            self._emit_supervisor_event("Fetching child issues...")
            children = self._get_child_tasks(issue_id)
            children.sort(key=lambda c: c["id"])  # Execute in creation order
            logger.info(
                "Session %s: found %d existing child issues",
                issue_id,
                len(children),
            )

            # Phase 1: Decompose if no children
            if not children:
                self.session.supervisor_phase = "decomposing"
                self._emit_supervisor_event(
                    "No existing subtasks — decomposing task..."
                )
                children = await self._decompose(issue_id, work_dir)
                if not children:
                    logger.info(
                        "Session %s: decompose returned no children, falling back to monolithic",
                        issue_id,
                    )
                    await self._run_monolithic(issue_id, work_dir, start)
                    return

            # Store the full subtask plan so the dashboard can show all
            # subtasks (pending / active / completed) immediately.
            self.session.subtask_plan = [
                {"id": c["id"], "subject": c.get("subject", f"Subtask #{c['id']}")}
                for c in children
            ]
            child_ids = ", ".join(f"#{c['id']}" for c in children)
            self._emit_supervisor_event(f"Plan: {len(children)} subtasks ({child_ids})")
            self.session.supervisor_phase = "executing"
            self._checkpoint()

            # Phase 2: Execute subtasks sequentially
            results: list[SubtaskResult] = []
            total = len(children)
            for idx, child in enumerate(children):
                child_id = child["id"]
                child_subject = child.get("subject", f"Subtask #{child_id}")
                self._emit_supervisor_event(
                    f"Executing subtask {idx + 1}/{total}: "
                    f"#{child_id} — {child_subject}"
                )
                # Tag events with the active subtask so the dashboard can
                # render per-subtask live feeds.
                self.session.active_subtask_id = child_id
                result = await self._execute_subtask(
                    child_id, child_subject, issue_id, work_dir, results
                )
                self.session.active_subtask_id = 0
                results.append(result)
                self.session.subtask_results.append(asdict(result))
                self._emit_supervisor_event(
                    f"Subtask #{child_id} finished: "
                    f"{result.status} ({result.verdict or 'N/A'})"
                )
                # Update parent progress (reserve last 10% for summarize+validate).
                pct = int((idx + 1) / total * 90)
                self._update_task(issue_id, progress=pct)
                self._checkpoint()

            # Phase 3: Summarize
            self.session.supervisor_phase = "summarizing"
            self._emit_supervisor_event("Generating summary of all subtask results...")
            await self._summarize(issue_id, results, work_dir)
            self._emit_supervisor_event("Summary posted to Redmine")

            # Phase 4: Overall validation + commit
            self.session.supervisor_phase = "validating"
            self._emit_supervisor_event("Running overall validation...")
            self.session.duration_seconds = time.time() - start
            verdict = self._run_overall_validation(issue_id, description, work_dir)
            self._emit_supervisor_event(
                f"Validation: {verdict.verdict} "
                f"(confidence {verdict.confidence:.0%})"
            )

            if verdict.verdict == "PASS":
                self.session.supervisor_phase = "committing"
                self._emit_supervisor_event("Committing and merging changes...")
                self._commit_and_complete(issue_id, work_dir, verdict)
            elif (
                verdict.verdict == "PARTIAL"
                and self.session.retry_count < self.task_config.max_retries
            ):
                await self._retry_overall(verdict, work_dir, issue_id)
            else:
                self._escalate(verdict)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.session.duration_seconds = time.time() - start
            self.session.state = TaskSessionState.FAILED
            self.session.errors.append(str(exc))
            self._emit_supervisor_event(
                f"Supervisor failed: {str(exc)[:150]}", is_error=True
            )
            self._update_task(
                issue_id,
                comment=f"Golem supervisor failed: {exc}",
            )
            logger.error("Session %s: supervisor failed: %s", issue_id, exc)

        finally:
            # Clean up worktree (merge handled in _commit_and_complete)
            if self._worktree_path:
                if self.session.state == TaskSessionState.FAILED:
                    cleanup_worktree(
                        self._base_work_dir, self._worktree_path, keep_branch=True
                    )
                elif not self.session.commit_sha:
                    # No commit was made — just clean up
                    cleanup_worktree(self._base_work_dir, self._worktree_path)
            self._checkpoint()

    # -- Phase 1: Decompose ---------------------------------------------------

    async def _decompose(self, issue_id: int, work_dir: str) -> list[dict[str, Any]]:
        """Spawn a decompose agent, parse JSON, create child tasks."""
        logger.info("Session %s: decomposing into subtasks", issue_id)

        description = self._get_description(issue_id)
        prompt = self._format_prompt(
            "decompose_task.txt",
            parent_id=issue_id,
            parent_subject=self.session.parent_subject,
            task_description=description,
        )

        model = self.task_config.decompose_model or self.task_config.task_model
        cli_config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=model,
            max_budget_usd=self.task_config.decompose_budget_usd,
            timeout_seconds=300,
            mcp_servers=self._get_base_mcp_servers(),
            cwd=work_dir,
        )

        result = await asyncio.get_running_loop().run_in_executor(
            None, invoke_cli_monitored, prompt, cli_config, None
        )
        self.session.total_cost_usd += result.cost_usd
        self._persist_trace(f"golem-{issue_id}-decompose", prompt, result)

        # Parse subtasks from output
        subtask_defs = self._parse_decompose_output(result, issue_id)
        if not subtask_defs:
            return []

        # Create child tasks
        return self._create_children(issue_id, subtask_defs)

    def _parse_decompose_output(self, result, issue_id: int) -> list[dict]:
        """Extract subtask definitions from the decompose agent output."""
        raw_output = result.output.get("result", "")
        if not raw_output:
            return []
        parsed = extract_json(str(raw_output), require_key="subtasks")
        if not parsed or not isinstance(parsed.get("subtasks"), list):
            logger.warning("Session %s: decompose output has no subtasks", issue_id)
            return []
        return parsed["subtasks"]

    def _create_children(
        self, issue_id: int, subtask_defs: list[dict]
    ) -> list[dict[str, Any]]:
        """Create child tasks from subtask definitions."""
        children: list[dict[str, Any]] = []
        for st in subtask_defs:
            subject = st.get("subject", "Unnamed subtask")
            desc = st.get("description", "")
            child_id = self._create_child(issue_id, subject, desc)
            if child_id:
                children.append({"id": child_id, "subject": subject})
                logger.info(
                    "Session %s: created subtask #%s: %s",
                    issue_id,
                    child_id,
                    subject,
                )
        return children

    # -- Phase 2: Execute subtasks -------------------------------------------

    async def _execute_subtask(
        self,
        child_id: int,
        child_subject: str,
        parent_id: int,
        work_dir: str,
        prior_results: list[SubtaskResult],
    ) -> SubtaskResult:
        """Spawn a worker agent for one subtask, optionally validate + retry."""
        logger.info(
            "Session %s: executing subtask #%s: %s",
            parent_id,
            child_id,
            child_subject,
        )
        self._update_task(child_id, status=TaskStatus.IN_PROGRESS)

        child_description = self._get_description(child_id)
        prompt = self._format_prompt(
            "execute_subtask.txt",
            parent_id=parent_id,
            parent_subject=self.session.parent_subject,
            subtask_id=child_id,
            subtask_subject=child_subject,
            sibling_status=self._build_sibling_status(prior_results),
            task_description=child_description,
        )
        mcp_servers = self._get_mcp_servers(child_subject)

        start = time.time()
        try:
            _, elapsed, cost = await self._invoke_subtask(
                prompt,
                self._subtask_cli_config(mcp_servers, work_dir),
                child_id,
                parent_id,
                start,
            )
            # Skip per-subtask validation when configured (validate at end only).
            # Go straight to Closed — the Fixed(16) intermediate is not a valid
            # transition in many tracker workflows and adds no value here.
            if self.task_config.skip_subtask_validation:
                self._update_task(
                    child_id,
                    status=TaskStatus.CLOSED,
                    progress=100,
                    comment=f"Subtask executed by agent (${cost:.2f}, {elapsed:.0f}s) "
                    f"— validation deferred to overall",
                )
                return SubtaskResult(
                    issue_id=child_id,
                    subject=child_subject,
                    status="completed",
                    verdict="DEFERRED",
                    cost_usd=cost,
                    duration_seconds=elapsed,
                    summary="Validation deferred to overall check",
                )

            # Per-subtask validation path
            verdict, cost, retry_count = await self._validate_and_retry_subtask(
                child_id,
                child_subject,
                work_dir,
                mcp_servers,
                parent_id,
                cost,
            )
            self._update_child_status(child_id, verdict, cost, elapsed)

            return SubtaskResult(
                issue_id=child_id,
                subject=child_subject,
                status="completed" if verdict.verdict == "PASS" else "failed",
                verdict=verdict.verdict,
                cost_usd=cost,
                duration_seconds=elapsed,
                summary=verdict.summary,
                retry_count=retry_count,
            )

        except Exception as exc:  # pylint: disable=broad-exception-caught
            elapsed = time.time() - start
            logger.error("Session %s: subtask #%s failed: %s", parent_id, child_id, exc)
            self._update_task(child_id, comment=f"Subtask failed: {exc}")
            return SubtaskResult(
                issue_id=child_id,
                subject=child_subject,
                status="failed",
                duration_seconds=elapsed,
                summary=str(exc)[:200],
            )

    def _subtask_cli_config(self, mcp_servers: list[str], work_dir: str) -> CLIConfig:
        """Build CLIConfig for a subtask worker."""
        model = self.task_config.subtask_model or self.task_config.task_model
        return CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=model,
            max_budget_usd=self.task_config.subtask_budget_usd,
            timeout_seconds=self.task_config.subtask_timeout_seconds,
            mcp_servers=mcp_servers,
            cwd=work_dir,
        )

    async def _invoke_subtask(
        self,
        prompt,
        cli_config,
        child_id,
        parent_id,
        start,
    ):
        """Run the subtask and persist traces. Returns (result, elapsed, cost)."""
        tracker = TaskEventTracker(
            session_id=child_id,
            on_milestone=self._on_milestone,
        )
        callback = self._chain_event_callback(tracker.handle_event)
        async with self._work_dir_lock:
            result = await asyncio.get_running_loop().run_in_executor(
                None,
                invoke_cli_monitored,
                prompt,
                cli_config,
                callback,
            )
        elapsed = time.time() - start
        cost = result.cost_usd
        self.session.total_cost_usd += cost
        self._persist_trace(
            f"golem-{parent_id}-sub{child_id}",
            prompt,
            result,
        )
        return result, elapsed, cost

    async def _validate_and_retry_subtask(
        self,
        child_id,
        child_subject,
        work_dir,
        mcp_servers,
        parent_id,
        cost,
    ):
        """Validate subtask, retry on PARTIAL. Returns (verdict, cost, retry_count)."""
        verdict = await self._validate_subtask(child_id, child_subject, work_dir)
        self.session.total_cost_usd += verdict.cost_usd

        retry_count = 0
        if verdict.verdict == "PARTIAL" and self.task_config.max_subtask_retries > 0:
            retry_count = 1
            retry_cost = await self._retry_subtask(
                child_id,
                verdict,
                work_dir,
                mcp_servers,
                parent_id,
            )
            cost += retry_cost
            self.session.total_cost_usd += retry_cost
            verdict = await self._validate_subtask(child_id, child_subject, work_dir)
            self.session.total_cost_usd += verdict.cost_usd

        return verdict, cost, retry_count

    def _update_child_status(
        self,
        child_id: int,
        verdict: ValidationVerdict,
        cost: float,
        elapsed: float,
    ) -> None:
        """Update child task status based on verdict."""
        if verdict.verdict == "PASS":
            self._update_task(
                child_id,
                status=TaskStatus.CLOSED,
                progress=100,
                comment=f"Subtask completed by agent (${cost:.2f}, {elapsed:.0f}s)",
            )
        else:
            self._update_task(
                child_id,
                comment=(
                    f"Subtask validation: {verdict.verdict}\n"
                    f"Summary: {verdict.summary}"
                ),
            )

    def _build_sibling_status(self, prior_results: list[SubtaskResult]) -> str:
        """Format completed subtask summaries for the {sibling_status} placeholder."""
        if not prior_results:
            return "No prior subtasks have been executed yet."

        lines = []
        for r in prior_results:
            lines.append(
                f"- #{r.issue_id} ({r.subject}): {r.status} "
                f"— {r.summary[:100] if r.summary else '(no summary)'}"
            )
        return "\n".join(lines)

    async def _validate_subtask(
        self, child_id: int, subject: str, work_dir: str
    ) -> ValidationVerdict:
        """Run per-subtask validation using the shared validation pipeline."""
        from functools import partial

        description = self._get_description(child_id)
        session_data = self.session.to_dict()

        return await asyncio.get_running_loop().run_in_executor(
            None,
            partial(
                run_validation,
                issue_id=child_id,
                subject=subject,
                description=description,
                session_data=session_data,
                work_dir=work_dir,
                model=self.task_config.validation_model,
                budget_usd=self.task_config.validation_budget_usd,
                timeout_seconds=self.task_config.validation_timeout_seconds,
            ),
        )

    async def _retry_subtask(
        self,
        child_id: int,
        verdict: ValidationVerdict,
        work_dir: str,
        mcp_servers: list[str],
        parent_id: int,
    ) -> float:
        """Retry a subtask using retry_task.txt.  Returns additional cost."""
        concerns_text = (
            "\n".join(f"- {c}" for c in verdict.concerns) or "- (none specified)"
        )

        retry_prompt = self._format_prompt(
            "retry_task.txt",
            issue_id=child_id,
            original_summary=verdict.summary or "(no summary)",
            validation_verdict=verdict.verdict,
            validation_summary=verdict.summary,
            concerns=concerns_text,
        )

        model = self.task_config.subtask_model or self.task_config.task_model
        cli_config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=model,
            max_budget_usd=self.task_config.retry_budget_usd,
            timeout_seconds=self.task_config.subtask_timeout_seconds,
            mcp_servers=mcp_servers,
            cwd=work_dir,
        )

        async with self._work_dir_lock:
            result = await asyncio.get_running_loop().run_in_executor(
                None, invoke_cli_monitored, retry_prompt, cli_config, None
            )

        self._persist_trace(
            f"golem-{parent_id}-sub{child_id}-retry",
            retry_prompt,
            result,
        )

        return result.cost_usd

    # -- Phase 3: Summarize ---------------------------------------------------

    async def _summarize(
        self,
        issue_id: int,
        results: list[SubtaskResult],
        work_dir: str,
    ) -> None:
        """Spawn a summarize agent to post a closing comment on the parent."""
        logger.info("Session %s: generating summary", issue_id)

        subtask_lines = []
        for r in results:
            subtask_lines.append(
                f"- #{r.issue_id} ({r.subject}): **{r.status}** "
                f"[{r.verdict or 'N/A'}] ${r.cost_usd:.2f}, "
                f"{r.duration_seconds:.0f}s — {r.summary[:100] if r.summary else '(no summary)'}"
            )
        subtask_summary = "\n".join(subtask_lines)

        prompt = self._format_prompt(
            "summarize_task.txt",
            parent_id=issue_id,
            parent_subject=self.session.parent_subject,
            subtask_summary=subtask_summary,
            total_cost=f"${self.session.total_cost_usd:.2f}",
        )

        cli_config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=self.task_config.summarize_model,
            max_budget_usd=self.task_config.summarize_budget_usd,
            timeout_seconds=120,
            mcp_servers=self._get_base_mcp_servers(),
            cwd=work_dir,
        )

        result = await asyncio.get_running_loop().run_in_executor(
            None, invoke_cli_monitored, prompt, cli_config, None
        )
        self.session.total_cost_usd += result.cost_usd

    # -- Phase 4: Overall validation + commit ---------------------------------

    def _run_overall_validation(
        self, issue_id: int, description: str, work_dir: str
    ) -> ValidationVerdict:
        """Run overall validation on the parent issue."""
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

        logger.info(
            "Session %s: overall validation verdict=%s confidence=%.2f",
            issue_id,
            verdict.verdict,
            verdict.confidence,
        )
        return verdict

    def _commit_and_complete(
        self, issue_id: int, work_dir: str, verdict: ValidationVerdict
    ) -> None:
        """Commit changes, merge worktree if applicable, and mark COMPLETED."""
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
                logger.info("Session %s: committed %s", issue_id, cr.sha)
            elif cr.error:
                logger.warning("Session %s: commit failed: %s", issue_id, cr.error)
                # Commit failed — escalate to human instead of silently closing.
                # The worktree branch is preserved by the finally block (FAILED state).
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

            # Merge worktree branch back to the base repo.
            # This must run even when commit_changes found nothing to commit
            # (cr.committed=False, no error) because worker agents may have
            # committed directly inside the worktree during execution.
            if self._worktree_path:
                merge_sha = merge_and_cleanup(
                    self._base_work_dir, issue_id, self._worktree_path
                )
                if merge_sha:
                    self.session.commit_sha = merge_sha
                    self._emit_supervisor_event(f"Merged worktree branch → {merge_sha}")
                    logger.info("Session %s: merged to base → %s", issue_id, merge_sha)
                    # Mark worktree as handled so finally block skips cleanup
                    self._worktree_path = ""
                else:
                    # Merge failed — escalate rather than marking completed
                    # without the worker's commits.
                    self._emit_supervisor_event(
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
                            "failed (dirty working tree?). Branch "
                            f"agent/{issue_id} preserved for manual recovery."
                        ),
                    )
                    return

        self.session.state = TaskSessionState.COMPLETED
        self.session.updated_at = _now_iso()

        completed_count = sum(
            1 for r in self.session.subtask_results if r.get("status") == "completed"
        )
        total_count = len(self.session.subtask_results)

        extras = ""
        if self.session.commit_sha:
            extras += f", commit {self.session.commit_sha}"

        self._emit_supervisor_event(
            f"Task completed: {completed_count}/{total_count} subtasks, "
            f"${self.session.total_cost_usd:.2f}{extras}"
        )

        self._update_task(
            issue_id,
            status=TaskStatus.CLOSED,
            progress=100,
            comment=(
                f"Task completed by agent (supervisor mode)\n"
                f"Subtasks: {completed_count}/{total_count} completed\n"
                f"Cost: ${self.session.total_cost_usd:.2f}, "
                f"Duration: {self.session.duration_seconds:.0f}s, "
                f"Validation: {self.session.validation_verdict}{extras}"
            ),
        )
        logger.info(
            "Session %s: completed (supervisor, %d/%d subtasks, $%.2f)",
            issue_id,
            completed_count,
            total_count,
            self.session.total_cost_usd,
        )

    async def _retry_overall(
        self, verdict: ValidationVerdict, work_dir: str, issue_id: int
    ) -> None:
        """Retry the overall task and re-validate."""
        self.session.state = TaskSessionState.RETRYING
        self.session.retry_count += 1
        self.session.updated_at = _now_iso()

        concerns_text = (
            "\n".join(f"- {c}" for c in verdict.concerns) or "- (none specified)"
        )

        retry_prompt = self._format_prompt(
            "retry_task.txt",
            issue_id=issue_id,
            original_summary=self.session.validation_summary or "(no summary)",
            validation_verdict=verdict.verdict,
            validation_summary=verdict.summary,
            concerns=concerns_text,
        )

        mcp_servers = self._get_mcp_servers(self.session.parent_subject)
        model = self.task_config.subtask_model or self.task_config.task_model
        cli_config = CLIConfig(
            cli_type=CLIType.CLAUDE,
            model=model,
            max_budget_usd=self.task_config.retry_budget_usd,
            timeout_seconds=self.task_config.task_timeout_seconds,
            mcp_servers=mcp_servers,
            cwd=work_dir,
        )

        async with self._work_dir_lock:
            result = await asyncio.get_running_loop().run_in_executor(
                None, invoke_cli_monitored, retry_prompt, cli_config, None
            )
        self.session.total_cost_usd += result.cost_usd

        # Re-validate
        description = self._get_description(issue_id)
        retry_verdict = self._run_overall_validation(issue_id, description, work_dir)

        if retry_verdict.verdict == "PASS":
            self._commit_and_complete(issue_id, work_dir, retry_verdict)
        else:
            self._escalate(retry_verdict)

    def _escalate(self, verdict: ValidationVerdict) -> None:
        """Mark session FAILED and post escalation to Redmine."""
        issue_id = self.session.parent_issue_id
        self.session.state = TaskSessionState.FAILED
        self.session.updated_at = _now_iso()

        concerns_text = "\n".join(f"- {c}" for c in verdict.concerns) or "- (none)"

        self._update_task(
            issue_id,
            status=TaskStatus.IN_PROGRESS,
            comment=(
                f"**Golem escalation (supervisor) — needs human review**\n\n"
                f"Verdict: {verdict.verdict} "
                f"(confidence: {verdict.confidence:.0%})\n"
                f"Summary: {verdict.summary}\n\n"
                f"Concerns:\n{concerns_text}\n\n"
                f"Cost: ${self.session.total_cost_usd:.2f} | "
                f"Duration: {self.session.duration_seconds:.0f}s | "
                f"Retries: {self.session.retry_count}"
            ),
        )
        logger.warning(
            "Session %s: escalated (supervisor, verdict=%s)",
            issue_id,
            verdict.verdict,
        )

    # -- Fallback: monolithic ------------------------------------------------

    async def _run_monolithic(  # pylint: disable=too-many-locals
        self, issue_id: int, work_dir: str, start: float
    ) -> None:
        """Fallback to single-agent run_task.txt when decompose fails."""
        self.session.execution_mode = "monolithic"
        logger.info("Session %s: running in monolithic fallback mode", issue_id)

        description = self._get_description(issue_id)
        prompt = self._format_prompt(
            "run_task.txt", issue_id=issue_id, task_description=description
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

        tracker = TaskEventTracker(
            session_id=issue_id,
            on_milestone=self._on_milestone,
        )
        callback = self._chain_event_callback(tracker.handle_event)

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
        self.session.result_summary = str(result.output.get("result", ""))[:1000]
        self.session.event_log = [
            {
                "kind": m.kind,
                "tool_name": m.tool_name,
                "summary": m.summary,
                "timestamp": m.timestamp,
                "is_error": m.is_error,
            }
            for m in tracker.state.event_log
        ]

        # Persist traces
        event_id = f"golem-{issue_id}"
        _write_prompt("golem", event_id, prompt)
        if result.trace_events:
            self.session.trace_file = _write_trace(
                "golem", event_id, result.trace_events
            )

        # Validate
        description = self._get_description(issue_id)
        verdict = self._run_overall_validation(issue_id, description, work_dir)

        if verdict.verdict == "PASS":
            self._commit_and_complete(issue_id, work_dir, verdict)
        elif (
            verdict.verdict == "PARTIAL"
            and self.session.retry_count < self.task_config.max_retries
        ):
            await self._retry_overall(verdict, work_dir, issue_id)
        else:
            self._escalate(verdict)

    # -- Helpers ---------------------------------------------------------------

    def _emit_supervisor_event(self, summary: str, *, is_error: bool = False) -> None:
        """Append a supervisor-level event (no subtask_id) to event_log."""
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
        """Persist session state to disk via the save callback."""
        if self._save_callback:
            try:
                self._save_callback()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Session %s: checkpoint failed: %s",
                    self.session.parent_issue_id,
                    exc,
                )

    @staticmethod
    def _persist_trace(event_id: str, prompt: str, result) -> None:
        """Write prompt and trace files to disk."""
        _write_prompt("golem", event_id, prompt)
        if result and result.trace_events:
            _write_trace("golem", event_id, result.trace_events)
