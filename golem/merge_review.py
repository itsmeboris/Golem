"""Agent-assisted merge reconciliation and conflict resolution.

When a merge succeeds but silently drops agent additions (detected by
``verify_merge_integrity``), the merge agent re-applies the lost changes.
When a merge fails due to conflicts, the merge agent attempts to produce
a clean resolution.

The merge agent is a lightweight Claude invocation that runs in the repo
working directory with file-write access.  After the agent acts,
the validation agent is re-run to confirm nothing broke.

Key exports:
- ``ReconciliationResult`` — outcome of merge agent resolution.
- ``run_merge_agent`` — resolve conflicts or re-apply lost additions.
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


def run_merge_agent(  # pylint: disable=too-many-locals
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
        parts.append(f"{len(missing)} file(s) have additions lost during merge")
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
