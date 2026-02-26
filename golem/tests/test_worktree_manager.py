"""Tests for golem.worktree_manager."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=redefined-outer-name

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from golem.worktree_manager import (
    _current_branch,
    _run_git,
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
