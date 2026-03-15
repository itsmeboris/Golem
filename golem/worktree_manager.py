"""Git worktree management for session isolation.

Each golem session gets its own git worktree so multiple agents can work
concurrently without file conflicts.  Worktrees share the same git object store,
so disk overhead is minimal (only the checked-out files are duplicated).

Lifecycle:
    1. ``create_worktree(base_dir, issue_id)`` → creates a worktree + branch
    2. Agent works in the worktree directory
    3. Committer commits changes in the worktree (on the agent branch)
    4. ``merge_in_worktree(base_dir, issue_id)`` → merges the branch in a
       temporary worktree; ``fast_forward_if_safe()`` lands the result.

Key exports:
    create_worktree      — set up an isolated worktree for a session
    merge_in_worktree    — merge the agent branch in a disposable worktree
    fast_forward_if_safe — land the merge result onto the current branch
    cleanup_worktree     — remove a worktree without merging (for failures)
"""

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("golem.worktree_manager")

# Worktrees live under data/agent/worktrees/<issue_id>/
_WORKTREE_SUBDIR = "worktrees"

# Git env vars that can leak from parent processes (e.g. pre-commit hooks)
# and must be stripped so child git commands target the correct repo.
_GIT_ENV_STRIP = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
}


def _clean_env() -> dict[str, str]:
    """Return os.environ with parent-git env vars removed."""
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_STRIP}


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
        env=_clean_env(),
    )


def _current_branch(base_dir: str) -> str:
    """Return the current branch name of the base repo."""
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=base_dir)
    return result.stdout.strip() or "main"


def create_worktree(
    base_dir: str,
    issue_id: int,
    worktree_root: str | None = None,
    start_point: str | None = None,
) -> str:
    """Create a git worktree for an agent session.

    Parameters
    ----------
    base_dir
        Path to the main git repository.
    issue_id
        Redmine issue ID (used for branch and directory naming).
    worktree_root
        Parent directory for worktrees.  Defaults to ``<base_dir>/data/agent/worktrees``.

    Returns
    -------
    str
        Absolute path to the created worktree directory.

    Raises
    ------
    RuntimeError
        If worktree creation fails.
    """
    if worktree_root is None:
        worktree_root = str(Path(base_dir) / "data" / "agent" / _WORKTREE_SUBDIR)

    worktree_path = str(Path(worktree_root) / str(issue_id))
    branch_name = f"agent/{issue_id}"

    # Clean up stale worktree if it exists (from a previous crashed session)
    existing = Path(worktree_path)
    if existing.exists():
        logger.warning(
            "Stale worktree for #%s found at %s — removing", issue_id, worktree_path
        )
        _cleanup_worktree_impl(base_dir, worktree_path, branch_name)

    # Delete stale branch if it exists
    del_result = _run_git(["branch", "-D", branch_name], cwd=base_dir)
    if del_result.returncode == 0:
        logger.info("Deleted stale branch %s before worktree creation", branch_name)

    # Prune stale worktree references that may block branch creation
    _run_git(["worktree", "prune"], cwd=base_dir)

    # Create the worktree with a new branch based on start_point (or HEAD)
    Path(worktree_root).mkdir(parents=True, exist_ok=True)
    cmd = ["worktree", "add", "-b", branch_name, worktree_path]
    if start_point is not None:
        cmd.append(start_point)
    result = _run_git(
        cmd,
        cwd=base_dir,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create worktree for #{issue_id}: "
            f"{result.stderr.strip()}. "
            f"base_dir={base_dir}, worktree_path={worktree_path}, "
            f"branch={branch_name}"
        )

    logger.info(
        "Created worktree for #%s at %s (branch %s)",
        issue_id,
        worktree_path,
        branch_name,
    )
    return worktree_path


def merge_in_worktree(  # pylint: disable=too-many-locals
    base_dir: str,
    issue_id: int,
    target_branch: str | None = None,
) -> "MergeOutcome":
    """Merge agent branch into target using a temporary merge worktree.

    All git operations happen in a disposable worktree — the user's
    working tree is never touched.

    Returns
    -------
    MergeOutcome
        ``sha`` is the merge commit SHA (empty if merge failed).
        ``merge_branch`` is the temp branch name with the merge result.
        ``error`` is non-empty on failure.

    Note: the caller is responsible for deleting ``agent/{issue_id}`` and
    ``merge_branch`` after the result has been consumed (e.g. after
    fast-forwarding into the main branch).
    """
    branch_name = f"agent/{issue_id}"
    if target_branch is None:
        target_branch = _current_branch(base_dir)

    # Verify the agent branch actually exists before proceeding
    verify = _run_git(["rev-parse", "--verify", branch_name], cwd=base_dir)
    if verify.returncode != 0:
        return MergeOutcome(sha="", error=f"branch {branch_name} not found")

    # Check if agent branch has commits beyond target
    result = _run_git(
        ["log", f"{target_branch}..{branch_name}", "--oneline"],
        cwd=base_dir,
    )
    if not result.stdout.strip():
        logger.info("Session #%s: no new commits to merge", issue_id)
        sha_result = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_dir)
        sha = sha_result.stdout.strip()
        _run_git(["branch", "-D", branch_name], cwd=base_dir)
        return MergeOutcome(sha=sha)

    # Capture agent diff and changed files before merge
    agent_diff = get_agent_diff(base_dir, branch_name, target_branch)
    changed_files = get_changed_files(base_dir, branch_name, target_branch)

    # Create a temporary merge worktree from the target branch HEAD
    merge_branch = f"merge-ready/{issue_id}"
    # Delete stale merge branch if exists
    _run_git(["branch", "-D", merge_branch], cwd=base_dir)
    _run_git(["worktree", "prune"], cwd=base_dir)

    merge_wt_root = str(Path(base_dir) / "data" / "agent" / "merge-worktrees")
    merge_wt_path = str(Path(merge_wt_root) / str(issue_id))

    # Clean up stale merge worktree
    if Path(merge_wt_path).exists():
        shutil.rmtree(merge_wt_path, ignore_errors=True)

    Path(merge_wt_root).mkdir(parents=True, exist_ok=True)
    wt_result = _run_git(
        ["worktree", "add", "-b", merge_branch, merge_wt_path, target_branch],
        cwd=base_dir,
    )
    if wt_result.returncode != 0:
        logger.error(
            "Session #%s: failed to create merge worktree: %s",
            issue_id,
            wt_result.stderr.strip(),
        )
        return MergeOutcome(
            sha="",
            agent_diff=agent_diff,
            error=f"merge worktree creation failed: {wt_result.stderr.strip()}",
        )

    try:
        # Merge agent branch into the merge worktree
        merge_result = _run_git(
            [
                "merge",
                branch_name,
                "--no-edit",
                "-m",
                f"Merge agent/{issue_id} session work",
            ],
            cwd=merge_wt_path,
        )

        if merge_result.returncode != 0:
            logger.warning(
                "Session #%s: merge conflict in worktree: %s",
                issue_id,
                merge_result.stderr.strip(),
            )
            # Abort the failed merge in the worktree
            _run_git(["merge", "--abort"], cwd=merge_wt_path)
            return MergeOutcome(
                sha="",
                agent_diff=agent_diff,
                merge_branch=merge_branch,
                error=f"merge conflict: {merge_result.stderr.strip()}",
            )

        # Merge succeeded — get SHA and verify integrity
        sha_result = _run_git(["rev-parse", "--short", "HEAD"], cwd=merge_wt_path)
        sha = sha_result.stdout.strip()

        missing = verify_merge_integrity(merge_wt_path, agent_diff, changed_files)
        if missing:
            logger.warning(
                "Session #%s: %d file(s) have missing additions after merge",
                issue_id,
                len(missing),
            )

        logger.info("Session #%s: merged in worktree → %s", issue_id, sha)
        return MergeOutcome(
            sha=sha,
            missing_additions=missing,
            agent_diff=agent_diff,
            merge_branch=merge_branch,
        )

    finally:
        # Always clean up the merge worktree
        _run_git(
            ["worktree", "remove", "--force", merge_wt_path],
            cwd=base_dir,
            timeout=120,
        )
        _run_git(["worktree", "prune"], cwd=base_dir)


def fast_forward_if_safe(
    base_dir: str,
    source_branch: str,
    stash_if_dirty: bool = False,
) -> tuple[bool, str]:
    """Attempt to fast-forward the current branch to *source_branch*.

    Returns ``(True, "")`` on success.  Returns ``(False, reason)`` if the
    fast-forward would overwrite dirty files — the working tree is left
    untouched in that case.

    When *stash_if_dirty* is ``True`` and the merge would clobber dirty files,
    the working tree is stashed, the fast-forward retried, and the stash
    popped.  If the pop conflicts, a warning is logged but the merge is
    still reported as successful (the stash entry is preserved).
    """
    merge_result = _run_git(
        ["merge", "--ff-only", source_branch],
        cwd=base_dir,
    )
    if merge_result.returncode == 0:
        return True, ""

    output = (merge_result.stdout + merge_result.stderr).strip()
    if "overwritten by merge" in output or "local changes" in output.lower():
        if stash_if_dirty:
            return _stash_and_ff(base_dir, source_branch)
        logger.info(
            "Fast-forward deferred — dirty working tree overlaps with %s",
            source_branch,
        )
        return False, f"dirty working tree overlaps with {source_branch}"

    if "not possible to fast-forward" in output.lower():
        logger.warning("Fast-forward not possible for %s: %s", source_branch, output)
        return False, f"branches diverged: {output}"

    return False, f"ff-only failed: {output}"


def _stash_and_ff(base_dir: str, source_branch: str) -> tuple[bool, str]:
    """Stash dirty changes, fast-forward, then pop the stash."""
    stash_result = _run_git(["stash", "--include-untracked"], cwd=base_dir)
    if stash_result.returncode != 0:
        logger.warning("git stash failed: %s", stash_result.stderr.strip())
        return False, f"dirty working tree overlaps with {source_branch}"

    retry = _run_git(["merge", "--ff-only", source_branch], cwd=base_dir)
    pop = _run_git(["stash", "pop"], cwd=base_dir)
    if pop.returncode != 0:
        logger.warning(
            "Stash pop had conflicts after ff merge of %s — resolve manually or check 'git stash list'",
            source_branch,
        )

    if retry.returncode == 0:
        logger.info(
            "Fast-forward succeeded after stashing dirty working tree for %s",
            source_branch,
        )
        return True, ""

    return False, f"dirty working tree overlaps with {source_branch}"


def get_changed_files(
    base_dir: str,
    branch_name: str,
    target_branch: str | None = None,
) -> list[str]:
    """Return files changed on *branch_name* relative to *target_branch*.

    Uses ``git diff --name-only`` to list paths touched by the branch.
    Returns an empty list if the diff command fails.
    """
    if target_branch is None:
        target_branch = _current_branch(base_dir)
    result = _run_git(
        ["diff", "--name-only", f"{target_branch}...{branch_name}"],
        cwd=base_dir,
    )
    if result.returncode != 0:
        return []
    return [f for f in result.stdout.strip().splitlines() if f]


@dataclass
class MissingAddition:
    file: str
    expected_lines: list[str] = field(default_factory=list)
    description: str = ""


@dataclass
class MergeOutcome:
    """Return type of merge operations with integrity information."""

    sha: str
    missing_additions: list[MissingAddition] = field(default_factory=list)
    agent_diff: str = ""
    error: str = ""
    merge_branch: str = ""  # branch name with the merge result


_TRIVIAL_LINE = re.compile(
    r"^\s*$"
    r"|^\s*#"
    r"|^\s*import\s+(os|sys|re|json|logging|typing|pathlib|collections)\b"
    r"|^\s*from\s+(os|sys|re|json|logging|typing|pathlib|collections)\b"
    r"|^\s*pass\s*$"
)


def get_agent_diff(
    base_dir: str, branch_name: str, target_branch: str | None = None
) -> str:
    """Capture ``git diff target..branch`` before merge.

    Parameters
    ----------
    target_branch
        Explicit base branch for the diff.  Falls back to the current
        branch of *base_dir* when ``None``.

    Returns the raw unified diff string (empty on failure).
    """
    if target_branch is None:
        target_branch = _current_branch(base_dir)
    result = _run_git(["diff", f"{target_branch}..{branch_name}"], cwd=base_dir)
    if result.returncode != 0:
        return ""
    return result.stdout


def _extract_added_lines(diff_text: str) -> dict[str, list[str]]:
    """Parse a unified diff and return ``{filepath: [added lines]}``."""
    file_adds: dict[str, list[str]] = {}
    current_file: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            if current_file is None:
                continue
            content = line[1:]
            if _TRIVIAL_LINE.match(content):
                continue
            file_adds.setdefault(current_file, []).append(content)
    return file_adds


def verify_merge_integrity(
    base_dir: str,
    agent_diff: str,
    changed_files: list[str],
) -> list[MissingAddition]:
    """Check that key additions from the agent diff survive the merge.

    Compares added lines from *agent_diff* against the post-merge content
    of each file on disk.  Returns a list of ``MissingAddition`` for any
    file where non-trivial added lines are missing.
    """
    if not agent_diff:
        return []

    added_by_file = _extract_added_lines(agent_diff)
    missing: list[MissingAddition] = []

    for filepath, expected_lines in added_by_file.items():
        if filepath not in changed_files:
            continue
        full_path = Path(base_dir) / filepath
        if not full_path.exists():
            missing.append(
                MissingAddition(
                    file=filepath,
                    expected_lines=expected_lines,
                    description=f"File {filepath} does not exist after merge",
                )
            )
            continue

        file_content = full_path.read_text(encoding="utf-8", errors="replace")
        lost = [ln for ln in expected_lines if ln not in file_content]
        if lost:
            missing.append(
                MissingAddition(
                    file=filepath,
                    expected_lines=lost,
                    description=(
                        f"{len(lost)}/{len(expected_lines)} added lines"
                        f" missing from {filepath}"
                    ),
                )
            )

    return missing


def cleanup_worktree(
    base_dir: str,
    worktree_path: str,
    *,
    keep_branch: bool = False,
) -> None:
    """Remove a worktree (and optionally its branch) without merging.

    Use this for failed sessions or when merge was already handled.
    """
    # Infer branch name from worktree path
    issue_id = Path(worktree_path).name
    branch_name = f"agent/{issue_id}" if not keep_branch else None
    _cleanup_worktree_impl(base_dir, worktree_path, branch_name)


def _cleanup_worktree_impl(
    base_dir: str,
    worktree_path: str,
    branch_name: str | None,
) -> None:
    """Internal: remove worktree directory and optionally delete branch."""
    # Remove the worktree — use a longer timeout for NFS mounts.
    result = _run_git(
        ["worktree", "remove", worktree_path, "--force"], cwd=base_dir, timeout=120
    )
    if result.returncode != 0:
        # Fallback: prune and try again
        _run_git(["worktree", "prune"], cwd=base_dir)
        # If directory still exists, force-remove it so a fresh worktree
        # can be created (the .git file inside may be corrupted/missing).
        wt = Path(worktree_path)
        if wt.exists():
            logger.warning(
                "Could not remove worktree at %s via git: %s — removing directory",
                worktree_path,
                result.stderr.strip(),
            )
            shutil.rmtree(str(wt), ignore_errors=True)

    # Delete the branch
    if branch_name:
        _run_git(["branch", "-D", branch_name], cwd=base_dir)
        logger.debug("Deleted branch %s", branch_name)
