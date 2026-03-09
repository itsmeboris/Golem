"""Lightweight validation agent for golem results.

Spawns a cheap (opus) read-only Claude invocation that reviews what the task
agent did and produces a structured PASS / PARTIAL / FAIL verdict.

Design follows the AWS Evaluator-Reflect-Refine pattern: a separate, cheaper
model evaluates the output of the task model.

Key exports:
- ``ValidationVerdict`` — dataclass holding verdict, confidence, summary,
  concerns, files_to_fix, test_failures, task_type, and cost_usd.
- ``run_validation`` — main entry point; runs the validation agent and returns
  a ``ValidationVerdict``.
- ``has_uncommitted_changes`` — checks whether a git working tree has changes.
- ``get_git_diff`` — returns a formatted git diff for inclusion in prompts.
"""

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .types import MilestoneDict

from .core.cli_wrapper import (
    CLIConfig,
    CLIType,
    ProgressCallback,
    invoke_cli,
    invoke_cli_monitored,
)
from .core.json_extract import extract_json

from .prompts import format_prompt

logger = logging.getLogger("golem.validation")


@dataclass
class ValidationVerdict:
    """Structured result of the validation agent."""

    verdict: str = "FAIL"  # PASS / PARTIAL / FAIL
    confidence: float = 0.0
    summary: str = ""
    concerns: list[str] = field(default_factory=list)
    files_to_fix: list[str] = field(default_factory=list)
    test_failures: list[str] = field(default_factory=list)
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


def _get_branch_diff(work_dir: str) -> str:
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
    bdiff = branch_diff.stdout
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


def get_git_diff(work_dir: str) -> str:
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
            parts.append(
                f"### Uncommitted changes\n```\n{uncommitted}\n```"
                f"\n\n```diff\n{diff.stdout}\n```"
            )

        # 2. Committed changes relative to base branch (handles worktrees)
        branch_section = _get_branch_diff(work_dir)
        if branch_section:
            parts.append(branch_section)

        if not parts:
            return "(no changes — neither uncommitted nor committed on branch)"
        return "\n\n".join(parts)
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("git diff failed: %s", exc)
        return "(git diff unavailable)"


# ---------------------------------------------------------------------------
# Static antipattern detection
# ---------------------------------------------------------------------------

# Matches lines like: traceback.format_exc(), traceback.print_exc(),
# Traceback (most recent call last), or raise ... from ...
_TRACEBACK_RE = re.compile(
    r"traceback\.(format_exc|print_exc|print_stack)"
    r"|Traceback \(most recent call last\)"
    r"|\.format_exc\(\)",
    re.IGNORECASE,
)

# Matches cross-module private access: obj._private (but not self._ or cls._)
_PRIVATE_ACCESS_RE = re.compile(
    r"(?<!self)(?<!cls)(?<!mock)\.\s*_[a-z]\w*",
)

# Common string-literal status/state comparisons that should use enums
_STRING_CONTROL_RE = re.compile(
    r'(?:==|!=|in)\s*["\']'
    r"(?:ready|running|failed|pending|completed|done|active|inactive|started|stopped)"
    r'["\']',
    re.IGNORECASE,
)

# Raw dict key access via .get("key" or ["key"] — heuristic soft signal
_RAW_DICT_ACCESS_RE = re.compile(
    r'\.get\(\s*["\'][a-z_]+["\']\s*' r"|" r'\[["\'][a-z_]+["\']\]'
)


def _check_line_antipatterns(
    content: str,
    current_file: str | None,
    traceback_hits: list[str],
    private_hits: list[str],
    string_hits: list[str],
    dict_access_hits: list[str],
) -> None:
    """Check a single added line for antipatterns and append to hit lists."""
    loc = current_file or "unknown file"
    if _TRACEBACK_RE.search(content):
        traceback_hits.append(loc)
    if _PRIVATE_ACCESS_RE.search(content):
        # Exclude common false positives: logging, dunder, _() i18n
        if not re.search(r"\._[_A-Z]|logger\._|_\(", content):
            private_hits.append(loc)
    if _STRING_CONTROL_RE.search(content):
        string_hits.append(loc)
    if _RAW_DICT_ACCESS_RE.search(content):
        dict_access_hits.append(loc)


def scan_diff_antipatterns(diff_text: str) -> list[str]:
    """Scan a unified diff for common antipatterns.

    Only examines added lines (``+`` prefix) and skips test files and
    comments.  Returns a list of human-readable concern strings.
    """
    if not diff_text:
        return []

    current_file: str | None = None
    traceback_hits: list[str] = []
    private_hits: list[str] = []
    string_hits: list[str] = []
    dict_access_hits: list[str] = []

    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:]

        # Skip test files and comment lines
        is_test_file = bool(
            current_file
            and ("/test_" in current_file or current_file.startswith("test_"))
        )
        if is_test_file:
            continue
        if content.strip().startswith("#"):
            continue

        _check_line_antipatterns(
            content,
            current_file,
            traceback_hits,
            private_hits,
            string_hits,
            dict_access_hits,
        )

    concerns: list[str] = []
    if traceback_hits:
        files = sorted(set(traceback_hits))
        concerns.append(f"Antipattern: traceback leak in {', '.join(files)}")
    if private_hits:
        files = sorted(set(private_hits))
        concerns.append(
            f"Antipattern: cross-module private access in {', '.join(files)}"
        )
    if string_hits:
        files = sorted(set(string_hits))
        concerns.append(
            f"Antipattern: string-matching control flow in {', '.join(files)}"
        )
    if dict_access_hits:
        files = sorted(set(dict_access_hits))
        concerns.append(
            f"Antipattern: untyped dict access in {', '.join(files)} "
            f"— verify keys match golem/types.py contracts"
        )
    return concerns


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


def _format_event_log(event_log: list[MilestoneDict]) -> str:
    """Format the event log into a human-readable summary."""
    if not event_log:
        return "(no events recorded)"
    lines = []
    for evt in event_log:
        kind = evt.get("kind", "?")
        tool = evt.get("tool_name", "")
        summary = evt.get("summary", "")
        err_tag = " [ERROR]" if evt.get("is_error") else ""
        line = f"- {kind}{err_tag}"
        if tool:
            line += f" ({tool})"
        if summary:
            line += f": {summary}"
        lines.append(line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Verification evidence + types.py helpers
# ---------------------------------------------------------------------------


def _format_verification_evidence(result: Any) -> str:
    """Format a VerificationResult (or None) into human-readable evidence."""
    if result is None:
        return "(no independent verification was run)"
    lines: list[str] = []
    for name, ok, output in [
        ("black", result.black_ok, result.black_output),
        ("pylint", result.pylint_ok, result.pylint_output),
        ("pytest", result.pytest_ok, result.pytest_output),
    ]:
        status = "PASS" if ok else "FAIL"
        lines.append(f"- {name}: {status}")
        if not ok and output:
            lines.append(f"  {output[:500]}")
    if result.pytest_ok:
        lines.append(
            f"  {result.test_count} tests, {len(result.failures)} failures, "
            f"coverage: {result.coverage_pct}%"
        )
    return "\n".join(lines)


def _read_types_py() -> str:
    """Read golem/types.py for inclusion in the validation prompt."""
    types_path = Path(__file__).resolve().parent / "types.py"
    try:
        return types_path.read_text(encoding="utf-8")
    except OSError:
        return "(golem/types.py not found)"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _build_validation_prompt(
    issue_id: int,
    subject: str,
    description: str,
    session_data: dict[str, Any],
    work_dir: str,
    verification_result: Any = None,
) -> str:
    """Build the validation prompt from session data and git state."""
    git_diff = get_git_diff(work_dir)
    event_log_summary = _format_event_log(session_data.get("event_log", []))
    verification_evidence = _format_verification_evidence(verification_result)
    types_py_content = _read_types_py()

    return format_prompt(
        "validate_task.txt",
        issue_id=issue_id,
        subject=subject,
        description=description or "(no description)",
        duration=int(session_data.get("duration_seconds", 0)),
        cost=f"{session_data.get('total_cost_usd', 0):.2f}",
        milestone_count=session_data.get("milestone_count", 0),
        tools=", ".join(session_data.get("tools_called", [])) or "none",
        mcp_tools=", ".join(session_data.get("mcp_tools_called", [])) or "none",
        error_count=len(session_data.get("errors", [])),
        event_log_summary=event_log_summary,
        git_diff=git_diff,
        verification_evidence=verification_evidence,
        types_py_content=types_py_content,
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
        files_to_fix=parsed.get("files_to_fix", []),
        test_failures=parsed.get("test_failures", []),
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
    model: str = "opus",
    budget_usd: float = 0.0,
    timeout_seconds: int = 600,
    callback: ProgressCallback | None = None,
    verification_result: Any = None,
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
    callback
        Optional stream-json event callback for real-time dashboard output.
    verification_result
        Optional VerificationResult from the deterministic verifier.
    """
    prompt = _build_validation_prompt(
        issue_id,
        subject,
        description,
        session_data,
        work_dir,
        verification_result=verification_result,
    )

    cli_config = CLIConfig(
        cli_type=CLIType.CLAUDE,
        model=model,
        max_budget_usd=budget_usd,
        timeout_seconds=timeout_seconds,
        mcp_servers=[],
        cwd=work_dir,
    )

    verdict = _invoke_with_retry(prompt, cli_config, callback)

    # Augment with static antipattern analysis
    diff_text = get_git_diff(work_dir)
    if verdict.task_type == "code_change" or diff_text.startswith("###"):
        antipatterns = scan_diff_antipatterns(diff_text)
        if antipatterns:
            verdict.concerns.extend(antipatterns)
            penalty = min(len(antipatterns) * 0.05, 0.15)
            verdict.confidence = max(0.0, verdict.confidence - penalty)
            logger.info(
                "Static analysis found %d antipattern(s), "
                "confidence adjusted by -%.2f",
                len(antipatterns),
                penalty,
            )

    return verdict


_MAX_VALIDATION_ATTEMPTS = 2


def _invoke_with_retry(
    prompt: str,
    cli_config: CLIConfig,
    callback: ProgressCallback | None,
) -> ValidationVerdict:
    last_exc: Exception | None = None
    for attempt in range(_MAX_VALIDATION_ATTEMPTS):
        try:
            if callback:
                result = invoke_cli_monitored(prompt, cli_config, callback)
            else:
                result = invoke_cli(prompt, cli_config)
            return _parse_validation_output(result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            last_exc = exc
            if attempt < _MAX_VALIDATION_ATTEMPTS - 1:
                logger.warning(
                    "Validation agent failed (attempt %d/%d), retrying: %s",
                    attempt + 1,
                    _MAX_VALIDATION_ATTEMPTS,
                    exc,
                )
            else:
                logger.error(
                    "Validation agent failed after %d attempts: %s",
                    _MAX_VALIDATION_ATTEMPTS,
                    exc,
                )

    return ValidationVerdict(
        verdict="FAIL",
        confidence=0.0,
        summary=(
            f"Validation agent error after "
            f"{_MAX_VALIDATION_ATTEMPTS} attempts: {last_exc}"
        ),
        concerns=["Validation agent itself failed (transient error)"],
        task_type="other",
        cost_usd=0.0,
    )
