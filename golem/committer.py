"""Deterministic git commit logic for golem completed work.

The orchestrator (not the agent) handles commits to ensure structured,
predictable commit messages following the project's commit format conventions.

Key exports:
- ``CommitResult`` — dataclass holding the outcome of a commit attempt.
- ``build_commit_message`` — constructs a structured commit message from issue
  metadata following ``commit_format.yaml`` conventions.
- ``commit_changes`` — stages all working-tree changes and creates a git commit.
"""

import logging
import re
import subprocess
from dataclasses import dataclass

from .core.commit_format import load_commit_format
from .sandbox import make_sandbox_preexec

logger = logging.getLogger("golem.committer")


@dataclass
class CommitResult:
    """Outcome of a commit attempt."""

    committed: bool = False
    sha: str = ""
    message: str = ""
    error: str = ""


def _run_git(
    args: list[str], cwd: str, timeout: int = 30
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        preexec_fn=make_sandbox_preexec(),
    )


def _clean_subject(raw: str) -> str:
    """Extract a clean, single-line subject from raw prompt/issue text."""
    line = raw.split("\n")[0].strip()
    for marker in ("[AGENT]", "[agent]", "[Agent]"):
        line = line.replace(marker, "")
    line = line.strip()
    line = re.sub(r"^#+\s*", "", line)
    line = re.sub(r"`([^`]*)`", r"\1", line)
    line = line.strip("*_ ")
    return line.strip()


def build_commit_message(
    issue_id: int,
    subject: str,
    task_type: str,
    summary: str,
) -> str:
    """Build a structured commit message following commit_format.yaml.

    Format: ``[MAIN_TAG][SUB_TAG] Brief description``
    """
    fmt = load_commit_format()

    # Map validation task_type to a main tag
    type_to_tag = {
        "code_change": "FIX",
        "bug_fix": "BUG",
        "feature": "FEATURE",
        "refactor": "REFACTOR",
        "investigation": "DOCS",
        "documentation": "DOCS",
        "performance": "PERF",
        "test": "TEST",
        "configuration": "CHORE",
        "other": "CHORE",
    }
    main_tag = type_to_tag.get(task_type, "CHORE")
    # Validate against known main tags (fall back to CHORE)
    if fmt.main_tags and main_tag not in fmt.main_tags:
        main_tag = "CHORE"

    # Try to infer sub-tag from the issue subject using word-boundary
    # matching.  Priority: chips > hardware > areas (most specific first).
    sub_tag = "INFRA"
    subject_upper = subject.upper()
    for tag_list in (fmt.sub_tags_chips, fmt.sub_tags_hw, fmt.sub_tags_areas):
        for tag in tag_list:
            if re.search(rf"\b{re.escape(tag.upper())}\b", subject_upper):
                sub_tag = tag
                break
        else:
            continue
        break

    clean_subject = _clean_subject(subject)

    first_line = f"[{main_tag}][{sub_tag}] {clean_subject}"

    body_parts = [
        f"Redmine issue #{issue_id}",
        "",
        (summary if summary else "Task completed by agent."),
        "",
        "Automated-By: Golem",
    ]

    return first_line + "\n\n" + "\n".join(body_parts)


def commit_changes(
    work_dir: str,
    issue_id: int,
    subject: str,
    task_type: str,
    summary: str,
) -> CommitResult:
    """Stage all changes and create a git commit.

    Only commits if there are actual file changes.  On failure, resets
    the staging area so the working tree is left in a clean state.
    """
    # Check for changes
    status = _run_git(["status", "--porcelain"], cwd=work_dir)
    if not status.stdout.strip():
        return CommitResult(committed=False, message="No changes to commit")

    # Stage all changes
    add = _run_git(["add", "-A"], cwd=work_dir)
    if add.returncode != 0:
        return CommitResult(
            committed=False,
            error=f"git add failed: {add.stderr.strip()}",
        )

    msg = build_commit_message(issue_id, subject, task_type, summary)

    # Skip pre-commit hooks — the supervisor validates separately.
    result = _run_git(["commit", "--no-verify", "-m", msg], cwd=work_dir, timeout=60)
    if result.returncode != 0:
        _run_git(["reset", "HEAD"], cwd=work_dir)
        return CommitResult(
            committed=False,
            error=f"git commit failed: {result.stderr.strip()}",
        )

    sha = _run_git(["rev-parse", "--short", "HEAD"], cwd=work_dir)
    short_sha = sha.stdout.strip()

    logger.info("Committed changes for #%d: %s", issue_id, short_sha)
    return CommitResult(committed=True, sha=short_sha, message=msg)
