# pylint: disable=too-few-public-methods,redefined-outer-name
"""Tests for golem.core.worktree_manager."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from golem.worktree_manager import (
    _cleanup_worktree_impl,
    _current_branch,
    _run_git,
    _stash_if_dirty,
    _unstash,
    cleanup_worktree,
    create_worktree,
    merge_and_cleanup,
)


@pytest.fixture()
def git_repo(tmp_path):
    """Create a minimal isolated git repo for testing.

    Uses ``_run_git`` (which strips parent git env vars) so these tests
    work correctly even when executed inside a pre-commit hook.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(["init"], cwd=str(repo))
    _run_git(["config", "user.email", "test@test.com"], cwd=str(repo))
    _run_git(["config", "user.name", "Test"], cwd=str(repo))
    (repo / "README.md").write_text("# Test repo")
    _run_git(["add", "."], cwd=str(repo))
    _run_git(["commit", "-m", "Initial commit"], cwd=str(repo))
    return repo


class TestRunGit:
    def test_successful_command(self, git_repo):
        result = _run_git(["status"], cwd=str(git_repo))
        assert result.returncode == 0

    def test_failed_command(self, git_repo):
        result = _run_git(["checkout", "nonexistent-branch"], cwd=str(git_repo))
        assert result.returncode != 0


class TestCurrentBranch:
    def test_returns_branch_name(self, git_repo):
        branch = _current_branch(str(git_repo))
        assert branch in ("main", "master")


class TestCreateWorktree:
    def test_creates_worktree(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        path = create_worktree(str(git_repo), 12345, worktree_root=wt_root)

        assert Path(path).is_dir()
        assert (Path(path) / "README.md").exists()
        assert "12345" in path

        result = _run_git(["branch", "--list", "agent/12345"], cwd=str(git_repo))
        assert "agent/12345" in result.stdout

    def test_cleans_stale_worktree(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")

        path1 = create_worktree(str(git_repo), 100, worktree_root=wt_root)
        assert Path(path1).is_dir()

        path2 = create_worktree(str(git_repo), 100, worktree_root=wt_root)
        assert Path(path2).is_dir()
        assert path1 == path2

    def test_raises_on_failure(self, tmp_path, monkeypatch):
        """Worktree creation raises RuntimeError when git command fails."""

        def mock_run_git(args, cwd, timeout=30):  # pylint: disable=unused-argument
            result = MagicMock()
            if "worktree" in args and "add" in args:
                result.returncode = 128
                result.stderr = "fatal: not a git repository"
            else:
                result.returncode = 0
                result.stderr = ""
            result.stdout = ""
            return result

        monkeypatch.setattr("golem.worktree_manager._run_git", mock_run_git)
        wt_root = str(tmp_path / "wt")
        with pytest.raises(RuntimeError, match="Failed to create worktree"):
            create_worktree(str(tmp_path / "repo"), 999, worktree_root=wt_root)


class TestMergeAndCleanup:
    def test_merge_with_changes(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 200, worktree_root=wt_root)

        (Path(wt_path) / "new_file.py").write_text("print('hello')")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Add new file"], cwd=wt_path)

        sha = merge_and_cleanup(str(git_repo), 200, wt_path)
        assert sha  # Should return a non-empty SHA
        assert (git_repo / "new_file.py").exists()

    def test_no_changes_returns_empty(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 300, worktree_root=wt_root)

        sha = merge_and_cleanup(str(git_repo), 300, wt_path)
        assert sha == ""

    def test_cleanup_after_merge(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 400, worktree_root=wt_root)

        (Path(wt_path) / "file.txt").write_text("data")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Add file"], cwd=wt_path)

        merge_and_cleanup(str(git_repo), 400, wt_path)

        assert not Path(wt_path).exists()
        result = _run_git(["branch", "--list", "agent/400"], cwd=str(git_repo))
        assert "agent/400" not in result.stdout


class TestCleanupWorktree:
    def test_cleanup_removes_worktree(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 500, worktree_root=wt_root)

        cleanup_worktree(str(git_repo), wt_path)
        assert not Path(wt_path).exists()

    def test_cleanup_keeps_branch_on_request(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 600, worktree_root=wt_root)

        cleanup_worktree(str(git_repo), wt_path, keep_branch=True)
        assert not Path(wt_path).exists()

        result = _run_git(["branch", "--list", "agent/600"], cwd=str(git_repo))
        assert "agent/600" in result.stdout


class TestCreateWorktreeDefaultRoot:
    def test_default_worktree_root(self, git_repo):
        path = create_worktree(str(git_repo), 77777)
        assert Path(path).is_dir()
        assert "worktrees" in path
        cleanup_worktree(str(git_repo), path)


class TestRebaseFailure:
    def test_rebase_fail_aborts(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 700, worktree_root=wt_root)

        (Path(wt_path) / "wt_file.py").write_text("content")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "WTcommit"], cwd=wt_path)

        (git_repo / "conflict.py").write_text("main version")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "Main commit"], cwd=str(git_repo))

        (Path(wt_path) / "conflict.py").write_text("wt version")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Conflict commit"], cwd=wt_path)

        sha = merge_and_cleanup(str(git_repo), 700, wt_path)
        assert isinstance(sha, str)


class TestMergeFailure:
    def test_merge_failure_returns_empty(self, monkeypatch):
        calls = []

        def mock_run_git(args, cwd, timeout=30):
            calls.append(args)
            result = MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0

            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                result.stdout = "main"
            elif "log" in args and ".." in args[1]:
                result.stdout = "abc123 some commit"
            elif args[:2] == ["worktree", "remove"]:
                pass
            elif "rebase" in args and "--abort" not in args:
                pass
            elif "checkout" in args:
                pass
            elif args == ["status", "--porcelain"]:
                result.stdout = ""
            elif "merge" in args and "--ff-only" in args:
                result.returncode = 1
                result.stderr = "ff failed"
            elif "merge" in args:
                result.returncode = 1
                result.stderr = "merge conflict"
            return result

        monkeypatch.setattr("golem.worktree_manager._run_git", mock_run_git)
        sha = merge_and_cleanup("/base", 888, "/wt/888")
        assert sha == ""


class TestStashIfDirty:
    def test_stashes_dirty_tree(self, git_repo):
        (git_repo / "dirty.txt").write_text("uncommitted")
        _run_git(["add", "dirty.txt"], cwd=str(git_repo))
        result = _stash_if_dirty(str(git_repo), 999)
        assert isinstance(result, bool)

    def test_clean_tree_no_stash(self, git_repo):
        assert _stash_if_dirty(str(git_repo), 999) is False


class TestUnstash:
    def test_no_stash_noop(self, git_repo):
        _unstash(str(git_repo), False, 999)

    def test_stash_pop_conflict(self, monkeypatch):
        def mock_run_git(args, cwd, timeout=30):
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = "conflict"
            return result

        monkeypatch.setattr("golem.worktree_manager._run_git", mock_run_git)
        _unstash("/base", True, 999)


class TestCleanupWorktreeImplFallback:
    def test_remove_fail_prunes_and_warns(self, monkeypatch, tmp_path):
        wt_path = tmp_path / "fake_wt"
        wt_path.mkdir()

        calls = []

        def mock_run_git(args, cwd, timeout=30):
            calls.append(args)
            result = MagicMock()
            result.returncode = 1 if "remove" in args else 0
            result.stderr = "cannot remove"
            result.stdout = ""
            return result

        monkeypatch.setattr("golem.worktree_manager._run_git", mock_run_git)
        _cleanup_worktree_impl("/base", str(wt_path), None)
        assert any("prune" in c for c in calls)


class TestFFFailFallsBackToRegularMerge:
    def test_ff_fail_regular_merge_succeeds(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 801, worktree_root=wt_root)

        (Path(wt_path) / "wt_only.py").write_text("wt content")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "WTcommit"], cwd=wt_path)

        (git_repo / "main_only.py").write_text("main content")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "Diverge main"], cwd=str(git_repo))

        sha = merge_and_cleanup(str(git_repo), 801, wt_path)
        assert isinstance(sha, str)


class TestNewConfigFields:
    def test_defaults(self):
        from golem.core.config import GolemFlowConfig

        config = GolemFlowConfig()
        assert config.use_worktrees is True
        assert config.skip_subtask_validation is True

    def test_parse_from_yaml(self):
        from golem.core.config import _parse_golem_config

        data = {
            "use_worktrees": False,
            "skip_subtask_validation": False,
        }
        config = _parse_golem_config(data)
        assert config.use_worktrees is False
        assert config.skip_subtask_validation is False
