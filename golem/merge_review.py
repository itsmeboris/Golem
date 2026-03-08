"""Agent-assisted merge reconciliation and conflict resolution.

When a merge succeeds but silently drops agent additions (detected by
``verify_merge_integrity``), the reconciliation agent re-applies the lost
changes.  When a merge fails due to conflicts, the conflict-resolution
agent attempts to produce a clean resolution.

Both agents are lightweight Claude invocations that run in the repo
working directory with file-write access.  After either agent acts,
the validation agent is re-run to confirm nothing broke.

Key exports:
- ``ReconciliationResult`` — outcome of reconciliation or conflict resolution.
- ``run_merge_reconciliation`` — re-apply lost additions after a silent overwrite.
- ``run_conflict_resolution`` — resolve git merge conflicts.
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .core.cli_wrapper import CLIConfig, CLIType, invoke_cli
from .core.json_extract import extract_json
from .prompts import format_prompt
from .worktree_manager import MissingAddition

logger = logging.getLogger("golem.merge_review")


@dataclass
class ReconciliationResult:
    resolved: bool = False
    commit_sha: str = ""
    explanation: str = ""


def _read_file_content(base_dir: str, filepath: str) -> str:
    full = Path(base_dir) / filepath
    if not full.exists():
        return f"(file {filepath} does not exist)"
    return full.read_text(encoding="utf-8", errors="replace")


def _format_missing_summary(missing: list[MissingAddition]) -> str:
    parts: list[str] = []
    for m in missing:
        lines_preview = "\n".join(m.expected_lines)
        parts.append(f"### {m.file}\n{m.description}\n```\n{lines_preview}\n```")
    return "\n\n".join(parts)


def _format_current_files(base_dir: str, missing: list[MissingAddition]) -> str:
    parts: list[str] = []
    for m in missing:
        content = _read_file_content(base_dir, m.file)
        parts.append(f"### {m.file}\n```\n{content}\n```")
    return "\n\n".join(parts)


def _get_short_sha(base_dir: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=base_dir,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result.stdout.strip()


def run_merge_reconciliation(
    base_dir: str,
    agent_diff: str,
    missing: list[MissingAddition],
    *,
    budget_usd: float = 1.0,
    timeout_seconds: int = 120,
) -> ReconciliationResult:
    """Spawn a reconciliation agent to re-apply lost additions.

    Returns a ``ReconciliationResult`` indicating whether the agent
    successfully created a fixup commit.
    """
    if not missing:
        return ReconciliationResult(resolved=True, explanation="nothing missing")

    prompt = format_prompt(
        "reconcile_merge.txt",
        agent_diff=agent_diff,
        missing_summary=_format_missing_summary(missing),
        current_files=_format_current_files(base_dir, missing),
    )

    cli_config = CLIConfig(
        cli_type=CLIType.CLAUDE,
        model="sonnet",
        max_budget_usd=budget_usd,
        timeout_seconds=timeout_seconds,
        mcp_servers=[],
        cwd=base_dir,
    )

    try:
        result = invoke_cli(prompt, cli_config)
        raw = result.output.get("result", "")
        if isinstance(raw, dict):
            parsed = raw
        else:
            parsed = extract_json(str(raw), require_key="resolved") or {}

        resolved = bool(parsed.get("resolved", False))
        explanation = parsed.get("explanation", "")

        if resolved:
            sha = _get_short_sha(base_dir)
            logger.info("Reconciliation succeeded: %s (commit %s)", explanation, sha)
            return ReconciliationResult(
                resolved=True, commit_sha=sha, explanation=explanation
            )

        logger.warning("Reconciliation agent declined: %s", explanation)
        return ReconciliationResult(resolved=False, explanation=explanation)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Reconciliation agent failed: %s", exc)
        return ReconciliationResult(resolved=False, explanation=f"agent error: {exc}")


def _read_conflict_content(base_dir: str, files: list[str]) -> str:
    parts: list[str] = []
    for filepath in files:
        content = _read_file_content(base_dir, filepath)
        parts.append(f"### {filepath}\n```\n{content}\n```")
    return "\n\n".join(parts)


def run_merge_agent(
    work_dir: str,
    issue_id: int,
    agent_diff: str,
    *,
    conflict_files: list[str] | None = None,
    missing: list[MissingAddition] | None = None,
    budget_usd: float = 1.0,
    timeout_seconds: int = 600,
    model: str = "sonnet",
) -> ReconciliationResult:
    """Spawn a single merge agent to resolve conflicts or re-apply lost additions."""
    conflict_files = conflict_files or []
    missing = missing or []

    if not conflict_files and not missing:
        return ReconciliationResult(resolved=True, explanation="nothing to resolve")

    # Build situation description
    parts: list[str] = []
    if conflict_files:
        parts.append(
            f"Merge conflict in {len(conflict_files)} file(s): "
            + ", ".join(conflict_files)
        )
    if missing:
        parts.append(
            f"{len(missing)} file(s) have additions lost during merge"
        )
    situation = ". ".join(parts)

    # Build current files section
    all_files = list(dict.fromkeys(conflict_files + [m.file for m in missing]))
    file_parts: list[str] = []
    for filepath in all_files:
        content = _read_file_content(work_dir, filepath)
        file_parts.append(f"### {filepath}\n```\n{content}\n```")
    current_files = "\n\n".join(file_parts)

    if missing:
        current_files += "\n\n## Missing additions\n\n"
        current_files += _format_missing_summary(missing)

    prompt = format_prompt(
        "merge_agent.txt",
        situation=situation,
        agent_diff=agent_diff,
        current_files=current_files,
        issue_id=str(issue_id),
    )

    cli_config = CLIConfig(
        cli_type=CLIType.CLAUDE,
        model=model,
        max_budget_usd=budget_usd,
        timeout_seconds=timeout_seconds,
        mcp_servers=[],
        cwd=work_dir,
    )

    try:
        result = invoke_cli(prompt, cli_config)
        raw = result.output.get("result", "")
        if isinstance(raw, dict):
            parsed = raw
        else:
            parsed = extract_json(str(raw), require_key="resolved") or {}

        resolved = bool(parsed.get("resolved", False))
        explanation = parsed.get("explanation", "")

        if resolved:
            sha = _get_short_sha(work_dir)
            logger.info("Merge agent resolved: %s (commit %s)", explanation, sha)
            return ReconciliationResult(
                resolved=True, commit_sha=sha, explanation=explanation
            )

        logger.warning("Merge agent declined: %s", explanation)
        return ReconciliationResult(resolved=False, explanation=explanation)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Merge agent failed: %s", exc)
        return ReconciliationResult(resolved=False, explanation=f"agent error: {exc}")


def run_conflict_resolution(
    base_dir: str,
    conflict_files: list[str],
    *,
    budget_usd: float = 1.0,
    timeout_seconds: int = 120,
) -> ReconciliationResult:
    """Spawn a conflict-resolution agent to resolve merge conflicts.

    Returns a ``ReconciliationResult`` indicating whether the agent
    successfully resolved all conflicts.
    """
    if not conflict_files:
        return ReconciliationResult(resolved=True, explanation="no conflicts")

    prompt = format_prompt(
        "resolve_conflict.txt",
        conflict_files=", ".join(conflict_files),
        conflict_content=_read_conflict_content(base_dir, conflict_files),
    )

    cli_config = CLIConfig(
        cli_type=CLIType.CLAUDE,
        model="sonnet",
        max_budget_usd=budget_usd,
        timeout_seconds=timeout_seconds,
        mcp_servers=[],
        cwd=base_dir,
    )

    try:
        result = invoke_cli(prompt, cli_config)
        raw = result.output.get("result", "")
        if isinstance(raw, dict):
            parsed = raw
        else:
            parsed = extract_json(str(raw), require_key="resolved") or {}

        resolved = bool(parsed.get("resolved", False))
        explanation = parsed.get("explanation", "")

        if resolved:
            logger.info("Conflict resolution succeeded: %s", explanation)
            return ReconciliationResult(resolved=True, explanation=explanation)

        logger.warning("Conflict resolution agent declined: %s", explanation)
        return ReconciliationResult(resolved=False, explanation=explanation)

    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Conflict resolution agent failed: %s", exc)
        return ReconciliationResult(resolved=False, explanation=f"agent error: {exc}")
