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

    # Create the worktree with a new branch based on HEAD
    Path(worktree_root).mkdir(parents=True, exist_ok=True)
    result = _run_git(
        ["worktree", "add", "-b", branch_name, worktree_path],
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


def merge_and_cleanup(
    base_dir: str,
    issue_id: int,
    worktree_path: str,
) -> "MergeOutcome":
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
    MergeOutcome
        ``sha`` contains the merge commit SHA (short), or the current HEAD if
        no changes to merge, or empty string if merge failed.
        ``missing_additions`` lists any agent additions lost during the merge.
        ``agent_diff`` is the raw unified diff captured before the merge.
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
        # Return current HEAD — "nothing to merge" is a success, not a failure.
        sha_result = _run_git(["rev-parse", "--short", "HEAD"], cwd=base_dir)
        sha = sha_result.stdout.strip()
        _cleanup_worktree_impl(base_dir, worktree_path, branch_name)
        return MergeOutcome(sha=sha)

    # Capture the agent diff and changed files before removing the worktree.
    agent_diff = get_agent_diff(base_dir, branch_name)
    changed_files = get_changed_files(base_dir, branch_name, target_branch)

    # Remove the worktree first so the branch is no longer "checked out"
    # — git refuses to rebase a branch that is in use by a worktree.
    # Use a longer timeout: NFS mounts can be slow to release .nfs* files.
    _run_git(
        ["worktree", "remove", "--force", worktree_path], cwd=base_dir, timeout=120
    )

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
        missing = verify_merge_integrity(base_dir, agent_diff, changed_files)
        if missing:
            logger.warning(
                "Session #%s: %d file(s) have missing additions after merge",
                issue_id,
                len(missing),
            )
            for m in missing:
                logger.warning("  %s: %s", m.file, m.description)
    else:
        logger.error(
            "Session #%s: merge failed: %s",
            issue_id,
            merge_result.stderr.strip(),
        )
        cleanup_worktree(base_dir, worktree_path, keep_branch=True)
        _unstash(base_dir, stashed, issue_id)
        return MergeOutcome(sha="", agent_diff=agent_diff)

    _unstash(base_dir, stashed, issue_id)
    # Worktree was removed before rebase; just prune stale entries and
    # delete the branch now that the merge is complete.
    _run_git(["worktree", "prune"], cwd=base_dir)
    _run_git(["branch", "-D", branch_name], cwd=base_dir)
    return MergeOutcome(sha=sha, missing_additions=missing, agent_diff=agent_diff)


def merge_in_worktree(
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
            issue_id, wt_result.stderr.strip(),
        )
        return MergeOutcome(
            sha="", agent_diff=agent_diff,
            error=f"merge worktree creation failed: {wt_result.stderr.strip()}",
        )

    try:
        # Merge agent branch into the merge worktree
        merge_result = _run_git(
            ["merge", branch_name, "--no-edit",
             "-m", f"Merge agent/{issue_id} session work"],
            cwd=merge_wt_path,
        )

        if merge_result.returncode != 0:
            logger.warning(
                "Session #%s: merge conflict in worktree: %s",
                issue_id, merge_result.stderr.strip(),
            )
            # Abort the failed merge in the worktree
            _run_git(["merge", "--abort"], cwd=merge_wt_path)
            return MergeOutcome(
                sha="", agent_diff=agent_diff, merge_branch=merge_branch,
                error=f"merge conflict: {merge_result.stderr.strip()}",
            )

        # Merge succeeded — get SHA and verify integrity
        sha_result = _run_git(["rev-parse", "--short", "HEAD"], cwd=merge_wt_path)
        sha = sha_result.stdout.strip()

        missing = verify_merge_integrity(merge_wt_path, agent_diff, changed_files)
        if missing:
            logger.warning(
                "Session #%s: %d file(s) have missing additions after merge",
                issue_id, len(missing),
            )

        logger.info("Session #%s: merged in worktree → %s", issue_id, sha)
        return MergeOutcome(
            sha=sha, missing_additions=missing,
            agent_diff=agent_diff, merge_branch=merge_branch,
        )

    finally:
        # Always clean up the merge worktree
        _run_git(
            ["worktree", "remove", "--force", merge_wt_path],
            cwd=base_dir, timeout=120,
        )
        _run_git(["worktree", "prune"], cwd=base_dir)


def fast_forward_if_safe(
    base_dir: str,
    source_branch: str,
) -> tuple[bool, str]:
    """Attempt to fast-forward the current branch to *source_branch*.

    Returns ``(True, "")`` on success.  Returns ``(False, reason)`` if the
    fast-forward would overwrite dirty files — the working tree is left
    untouched in that case.
    """
    merge_result = _run_git(
        ["merge", "--ff-only", source_branch],
        cwd=base_dir,
    )
    if merge_result.returncode == 0:
        return True, ""

    stderr = merge_result.stderr.strip()
    if "overwritten by merge" in stderr or "local changes" in stderr.lower():
        logger.info(
            "Fast-forward deferred — dirty working tree overlaps with %s",
            source_branch,
        )
        return False, f"dirty working tree overlaps with {source_branch}"

    if "not possible to fast-forward" in stderr.lower():
        logger.warning("Fast-forward not possible for %s: %s", source_branch, stderr)
        return False, f"branches diverged: {stderr}"

    return False, f"ff-only failed: {stderr}"


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
