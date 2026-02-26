"""Lightweight validation agent for task-agent results.

Spawns a cheap (opus) read-only Claude invocation that reviews what the task
agent did and produces a structured PASS / PARTIAL / FAIL verdict.

Design follows the AWS Evaluator-Reflect-Refine pattern: a separate, cheaper
model evaluates the output of the task model.

Key exports:
- ``ValidationVerdict`` — dataclass holding verdict, confidence, summary,
  concerns, task_type, and cost_usd.
- ``run_validation`` — main entry point; runs the validation agent and returns
  a ``ValidationVerdict``.
- ``has_uncommitted_changes`` — checks whether a git working tree has changes.
- ``get_git_diff`` — returns a formatted git diff for inclusion in prompts.
"""

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any

from .core.cli_wrapper import CLIConfig, CLIType, invoke_cli
from .core.json_extract import extract_json

from .prompts import format_prompt

logger = logging.getLogger("Tools.AgentAutomation.Flows.TaskAgent.Validation")


@dataclass
class ValidationVerdict:
    """Structured result of the validation agent."""

    verdict: str = "FAIL"  # PASS / PARTIAL / FAIL
    confidence: float = 0.0
    summary: str = ""
    concerns: list[str] = field(default_factory=list)
    task_type: str = "other"
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------


def has_uncommitted_changes(work_dir: str) -> bool:
    """Return True if there are uncommitted changes in *work_dir*."""
    if not work_dir:
        return False
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return bool(result.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


def _find_merge_base(work_dir: str) -> str:
    """Find the merge-base between HEAD and the main/master branch."""
    for base in ("main", "master"):
        result = subprocess.run(
            ["git", "merge-base", base, "HEAD"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return ""


def _get_branch_diff(work_dir: str, max_bytes: int) -> str:
    """Return formatted diff of committed changes relative to the base branch."""
    merge_base = _find_merge_base(work_dir)
    if not merge_base:
        return ""
    branch_stat = subprocess.run(
        ["git", "diff", "--stat", f"{merge_base}..HEAD"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    branch_changes = branch_stat.stdout.strip()
    if not branch_changes:
        return ""
    branch_diff = subprocess.run(
        ["git", "diff", f"{merge_base}..HEAD"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    bdiff = branch_diff.stdout[: max(max_bytes, 2000)]
    if len(branch_diff.stdout) > len(bdiff):
        bdiff += "\n... (diff truncated)"
    log = subprocess.run(
        ["git", "log", "--oneline", f"{merge_base}..HEAD"],
        cwd=work_dir,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    log_text = log.stdout.strip()
    return (
        f"### Committed changes (branch vs base)\n"
        f"Commits:\n```\n{log_text}\n```\n"
        f"```\n{branch_changes}\n```"
        f"\n\n```diff\n{bdiff}\n```"
    )


def get_git_diff(work_dir: str, max_bytes: int = 30_000) -> str:
    """Return git changes for validation.

    Checks both uncommitted changes (``git diff HEAD``) and committed changes
    relative to the base branch (``git diff <merge-base>..HEAD``).  This handles
    worktree branches where agents commit their work before validation.
    """
    if not work_dir:
        return "(no working directory configured)"
    parts: list[str] = []
    try:
        # 1. Uncommitted changes
        stat = subprocess.run(
            ["git", "diff", "--stat", "HEAD"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        uncommitted = stat.stdout.strip()
        if uncommitted:
            diff = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            diff_text = diff.stdout[:max_bytes]
            if len(diff.stdout) > max_bytes:
                diff_text += "\n... (diff truncated)"
            parts.append(
                f"### Uncommitted changes\n```\n{uncommitted}\n```"
                f"\n\n```diff\n{diff_text}\n```"
            )

        # 2. Committed changes relative to base branch (handles worktrees)
        branch_section = _get_branch_diff(
            work_dir, max_bytes - sum(len(p) for p in parts)
        )
        if branch_section:
            parts.append(branch_section)

        if not parts:
            return "(no changes — neither uncommitted nor committed on branch)"
        return "\n\n".join(parts)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("git diff failed: %s", exc)
        return "(git diff unavailable)"


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


def _format_event_log(event_log: list[dict], max_entries: int = 30) -> str:
    """Format the event log into a human-readable summary."""
    if not event_log:
        return "(no events recorded)"
    lines = []
    for evt in event_log[-max_entries:]:
        kind = evt.get("kind", "?")
        tool = evt.get("tool_name", "")
        summary = evt.get("summary", "")[:120]
        err_tag = " [ERROR]" if evt.get("is_error") else ""
        line = f"- {kind}{err_tag}"
        if tool:
            line += f" ({tool})"
        if summary:
            line += f": {summary}"
        lines.append(line)
    if len(event_log) > max_entries:
        lines.insert(0, f"(showing last {max_entries} of {len(event_log)} events)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _build_validation_prompt(
    issue_id: int,
    subject: str,
    description: str,
    session_data: dict[str, Any],
    work_dir: str,
) -> str:
    """Build the validation prompt from session data and git state."""
    git_diff = get_git_diff(work_dir)
    event_log_summary = _format_event_log(session_data.get("event_log", []))

    return format_prompt(
        "validate_task.txt",
        issue_id=issue_id,
        subject=subject,
        description=(description or "(no description)")[:3000],
        duration=int(session_data.get("duration_seconds", 0)),
        cost=f"{session_data.get('total_cost_usd', 0):.2f}",
        milestone_count=session_data.get("milestone_count", 0),
        tools=", ".join(session_data.get("tools_called", [])) or "none",
        mcp_tools=", ".join(session_data.get("mcp_tools_called", [])) or "none",
        error_count=len(session_data.get("errors", [])),
        event_log_summary=event_log_summary,
        git_diff=git_diff,
    )


def _parse_validation_output(result: Any) -> ValidationVerdict:
    """Parse a CLIResult into a ValidationVerdict."""
    raw = result.output.get("result", "")
    if isinstance(raw, dict):
        parsed = raw
    else:
        parsed = extract_json(str(raw), require_key="verdict") or {}

    return ValidationVerdict(
        verdict=parsed.get("verdict", "FAIL").upper(),
        confidence=float(parsed.get("confidence", 0.0)),
        summary=parsed.get("summary", "Validation could not parse result"),
        concerns=parsed.get("concerns", []),
        task_type=parsed.get("task_type", "other"),
        cost_usd=result.cost_usd,
    )


def run_validation(
    issue_id: int,
    subject: str,
    description: str,
    session_data: dict[str, Any],
    work_dir: str,
    *,
    model: str = "opu",
    budget_usd: float = 0.50,
    timeout_seconds: int = 120,
) -> ValidationVerdict:
    """Run the validation agent and return a structured verdict.

    Parameters
    ----------
    issue_id
        Redmine issue ID.
    subject
        Issue subject line.
    description
        Issue description text (from Redmine).
    session_data
        Dict with keys: event_log, tools_called, mcp_tools_called,
        errors, milestone_count, duration_seconds, total_cost_usd.
    work_dir
        Working directory where the agent ran (for git diff).
    model
        Model to use for validation (default: opus).
    budget_usd
        Max spend for the validation call.
    timeout_seconds
        Subprocess timeout.
    """
    prompt = _build_validation_prompt(
        issue_id, subject, description, session_data, work_dir
    )

    cli_config = CLIConfig(
        cli_type=CLIType.CLAUDE,
        model=model,
        max_budget_usd=budget_usd,
        timeout_seconds=timeout_seconds,
        mcp_servers=[],  # No MCP — validation is read-only analysis
    )

    max_attempts = 2
    last_exc: Exception | None = None
    for attempt in range(max_attempts):
        try:
            result = invoke_cli(prompt, cli_config)
            return _parse_validation_output(result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last_exc = exc
            if attempt < max_attempts - 1:
                logger.warning(
                    "Validation agent failed (attempt %d/%d), retrying: %s",
                    attempt + 1,
                    max_attempts,
                    exc,
                )
            else:
                logger.error(
                    "Validation agent failed after %d attempts: %s", max_attempts, exc
                )

    return ValidationVerdict(
        verdict="FAIL",
        confidence=0.0,
        summary=f"Validation agent error after {max_attempts} attempts: {last_exc}",
        concerns=["Validation agent itself failed (transient error)"],
        task_type="other",
        cost_usd=0.0,
    )
