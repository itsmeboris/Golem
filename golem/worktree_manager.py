"""Git worktree management for session isolation.

Each golem session gets its own git worktree so multiple agents can work
concurrently without file conflicts.  Worktrees share the same git object store,
so disk overhead is minimal (only the checked-out files are duplicated).

Lifecycle:
    1. ``create_worktree(base_dir, issue_id)`` → creates a worktree + branch
    2. Agent works in the worktree directory
    3. Committer commits changes in the worktree (on the agent branch)
    4. ``merge_and_cleanup(base_dir, issue_id, worktree_path)`` → merges the
       branch back to the original HEAD and removes the worktree

Key exports:
    create_worktree  — set up an isolated worktree for a session
    merge_and_cleanup — merge the worktree branch back and clean up
    cleanup_worktree — remove a worktree without merging (for failures)
"""

import logging
import os
import subprocess
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
    _run_git(["branch", "-D", branch_name], cwd=base_dir)

    # Create the worktree with a new branch based on HEAD
    Path(worktree_root).mkdir(parents=True, exist_ok=True)
    result = _run_git(
        ["worktree", "add", "-b", branch_name, worktree_path],
        cwd=base_dir,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Failed to create worktree for #{issue_id}: {result.stderr.strip()}"
        )

    logger.info(
        "Created worktree for #%s at %s (branch %s)",
        issue_id,
        worktree_path,
        branch_name,
    )
    return worktree_path


def merge_and_cleanup(
    base_dir: str,
    issue_id: int,
    worktree_path: str,
) -> str:
    """Merge the worktree branch back to the base branch and clean up.

    Parameters
    ----------
    base_dir
        Path to the main git repository.
    issue_id
        Redmine issue ID.
    worktree_path
        Path to the worktree directory.

    Returns
    -------
    str
        The merge commit SHA (short) or empty string if merge failed/no changes.
    """
    branch_name = f"agent/{issue_id}"
    target_branch = _current_branch(base_dir)

    # Check if the agent branch has any commits beyond the base
    result = _run_git(
        ["log", f"{target_branch}..{branch_name}", "--oneline"],
        cwd=base_dir,
    )
    if not result.stdout.strip():
        logger.info("Session #%s: no new commits to merge", issue_id)
        _cleanup_worktree_impl(base_dir, worktree_path, branch_name)
        return ""

    # Remove the worktree first so the branch is no longer "checked out"
    # — git refuses to rebase a branch that is in use by a worktree.
    _run_git(["worktree", "remove", "--force", worktree_path], cwd=base_dir)

    # Rebase the agent branch onto the current target so that concurrent
    # tasks whose branches forked from an older HEAD can be fast-forwarded.
    # This handles the common case where two tasks modify non-overlapping
    # files.  On genuine conflicts the rebase fails and we preserve the
    # branch for manual recovery.
    rebase_result = _run_git(["rebase", target_branch, branch_name], cwd=base_dir)
    if rebase_result.returncode != 0:
        logger.warning(
            "Session #%s: rebase onto %s failed — %s",
            issue_id,
            target_branch,
            rebase_result.stderr.strip(),
        )
        _run_git(["rebase", "--abort"], cwd=base_dir)

    # `git rebase target branch` leaves us on `branch` — switch back to
    # the target so that the merge operates on the correct base.
    _run_git(["checkout", target_branch], cwd=base_dir)

    # Stash local changes in the base repo if needed.
    stashed = _stash_if_dirty(base_dir, issue_id)

    # Try fast-forward merge first (will succeed after a clean rebase)
    merge_result = _run_git(
        ["merge", "--ff-only", branch_name],
        cwd=base_dir,
    )
    if merge_result.returncode != 0:
        # Fast-forward failed — try a regular merge
        logger.info(
            "Session #%s: fast-forward failed, attempting regular merge",
            issue_id,
        )
        merge_result = _run_git(
            [
                "merge",
                branch_name,
                "--no-edit",
                "-m",
                f"Merge agent/{issue_id} session work",
            ],
            cwd=base_dir,
        )

    sha = ""
    if merge_result.returncode == 0:
        sha_result = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_dir)
        sha = sha_result.stdout.strip()
        logger.info(
            "Session #%s: merged branch %s → %s",
            issue_id,
            branch_name,
            sha,
        )
    else:
        logger.error(
            "Session #%s: merge failed: %s",
            issue_id,
            merge_result.stderr.strip(),
        )
        cleanup_worktree(base_dir, worktree_path, keep_branch=True)
        _unstash(base_dir, stashed, issue_id)
        return ""

    _unstash(base_dir, stashed, issue_id)
    # Worktree was removed before rebase; just prune stale entries and
    # delete the branch now that the merge is complete.
    _run_git(["worktree", "prune"], cwd=base_dir)
    _run_git(["branch", "-D", branch_name], cwd=base_dir)
    return sha


def _stash_if_dirty(base_dir: str, issue_id: int) -> bool:
    """Stash uncommitted changes in *base_dir*.  Returns True if stashed."""
    status = _run_git(["status", "--porcelain"], cwd=base_dir)
    if not (status.stdout or "").strip():
        return False
    logger.info("Session #%s: stashing dirty working tree before merge", issue_id)
    sr = _run_git(
        ["stash", "push", "-m", f"auto-stash for agent/{issue_id} merge"],
        cwd=base_dir,
    )
    return sr.returncode == 0 and "No local changes" not in (sr.stdout or "")


def _unstash(base_dir: str, stashed: bool, issue_id: int) -> None:
    """Pop the stash if we stashed earlier."""
    if not stashed:
        return
    pop = _run_git(["stash", "pop"], cwd=base_dir)
    if pop.returncode != 0:
        logger.warning(
            "Session #%s: stash pop had conflicts — resolve manually",
            issue_id,
        )


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
    # Remove the worktree
    result = _run_git(["worktree", "remove", worktree_path, "--force"], cwd=base_dir)
    if result.returncode != 0:
        # Fallback: prune and try again
        _run_git(["worktree", "prune"], cwd=base_dir)
        # If directory still exists, just log a warning
        if Path(worktree_path).exists():
            logger.warning(
                "Could not remove worktree at %s: %s",
                worktree_path,
                result.stderr.strip(),
            )

    # Delete the branch
    if branch_name:
        _run_git(["branch", "-D", branch_name], cwd=base_dir)
        logger.debug("Deleted branch %s", branch_name)
