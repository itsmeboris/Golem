# pylint: disable=too-few-public-methods,redefined-outer-name
"""Tests for golem.core.worktree_manager."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from golem.worktree_manager import (
    MissingAddition,
    _cleanup_worktree_impl,
    _current_branch,
    _extract_added_lines,
    _run_git,
    _TRIVIAL_LINE,
    cleanup_worktree,
    create_worktree,
    get_agent_diff,
    verify_merge_integrity,
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

        def mock_run_git(args, **_kwargs):
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


class TestCreateWorktreeStartPoint:
    def test_start_point_branches_from_specific_commit(self, git_repo, tmp_path):
        """When start_point is given, worktree branches from that commit."""
        # Make a second commit
        (git_repo / "second.txt").write_text("second")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "Second commit"], cwd=str(git_repo))

        # Record HEAD and then make a third commit
        result = _run_git(["rev-parse", "HEAD"], cwd=str(git_repo))
        second_sha = result.stdout.strip()

        (git_repo / "third.txt").write_text("third")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "Third commit"], cwd=str(git_repo))

        # Create worktree from the second commit (not HEAD)
        wt_root = str(tmp_path / "worktrees")
        path = create_worktree(
            str(git_repo), 2000, worktree_root=wt_root, start_point=second_sha
        )

        # Worktree should have second.txt but NOT third.txt
        assert (Path(path) / "second.txt").exists()
        assert not (Path(path) / "third.txt").exists()

        # Branch should be at the second commit
        wt_head = _run_git(["rev-parse", "HEAD"], cwd=path)
        assert wt_head.stdout.strip() == second_sha

    def test_none_start_point_uses_head(self, git_repo, tmp_path):
        """When start_point is None (default), worktree branches from HEAD."""
        result = _run_git(["rev-parse", "HEAD"], cwd=str(git_repo))
        head_sha = result.stdout.strip()

        wt_root = str(tmp_path / "worktrees")
        path = create_worktree(
            str(git_repo), 2001, worktree_root=wt_root, start_point=None
        )

        wt_head = _run_git(["rev-parse", "HEAD"], cwd=path)
        assert wt_head.stdout.strip() == head_sha


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


class TestCleanupWorktreeImplFallback:
    def test_remove_fail_prunes_and_warns(self, monkeypatch, tmp_path):
        wt_path = tmp_path / "fake_wt"
        wt_path.mkdir()

        calls = []

        def mock_run_git(args, **_kwargs):
            calls.append(args)
            result = MagicMock()
            result.returncode = 1 if "remove" in args else 0
            result.stderr = "cannot remove"
            result.stdout = ""
            return result

        monkeypatch.setattr("golem.worktree_manager._run_git", mock_run_git)
        _cleanup_worktree_impl("/base", str(wt_path), None)
        assert any("prune" in c for c in calls)


class TestGetChangedFiles:
    def test_returns_changed_files(self, git_repo, tmp_path):
        from golem.worktree_manager import get_changed_files

        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 900, worktree_root=wt_root)

        (Path(wt_path) / "new_file.py").write_text("print('hi')")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Add new file"], cwd=wt_path)

        files = get_changed_files(str(git_repo), "agent/900")
        assert "new_file.py" in files
        cleanup_worktree(str(git_repo), wt_path)

    def test_returns_empty_on_failure(self, monkeypatch, tmp_path):
        from golem.worktree_manager import get_changed_files

        def mock_run_git(args, **_kwargs):
            result = MagicMock()
            if args[0] == "diff":
                result.returncode = 1
            else:
                result.returncode = 0
                result.stdout = "main"
            result.stderr = ""
            return result

        monkeypatch.setattr("golem.worktree_manager._run_git", mock_run_git)
        assert get_changed_files(str(tmp_path / "repo"), "branch") == []

    def test_explicit_target_branch(self, git_repo, tmp_path):
        from golem.worktree_manager import get_changed_files

        branch = _current_branch(str(git_repo))
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 901, worktree_root=wt_root)

        (Path(wt_path) / "target.py").write_text("x")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "target"], cwd=wt_path)

        files = get_changed_files(str(git_repo), "agent/901", target_branch=branch)
        assert "target.py" in files
        cleanup_worktree(str(git_repo), wt_path)

    def test_no_changes_returns_empty(self, git_repo, tmp_path):
        from golem.worktree_manager import get_changed_files

        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 902, worktree_root=wt_root)

        files = get_changed_files(str(git_repo), "agent/902")
        assert files == []
        cleanup_worktree(str(git_repo), wt_path)


class TestMissingAdditionDefaults:
    def test_defaults(self):
        m = MissingAddition(file="a.py")
        assert not m.expected_lines
        assert m.description == ""


class TestTrivialLineRegex:
    def test_blank_lines(self):
        assert _TRIVIAL_LINE.match("")
        assert _TRIVIAL_LINE.match("   ")
        assert _TRIVIAL_LINE.match("\t")

    def test_comments(self):
        assert _TRIVIAL_LINE.match("# a comment")
        assert _TRIVIAL_LINE.match("  # indented")

    def test_trivial_imports(self):
        assert _TRIVIAL_LINE.match("import os")
        assert _TRIVIAL_LINE.match("from sys import argv")
        assert _TRIVIAL_LINE.match("import json")

    def test_pass(self):
        assert _TRIVIAL_LINE.match("    pass")
        assert _TRIVIAL_LINE.match("pass")

    def test_non_trivial(self):
        assert not _TRIVIAL_LINE.match("def hello():")
        assert not _TRIVIAL_LINE.match('x = "value"')
        assert not _TRIVIAL_LINE.match("import custom_module")


class TestExtractAddedLines:
    def test_basic_diff(self):
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            + "--- a/foo.py\n"
            + "+++ b/foo.py\n"
            + "@@ -1 +1,3 @@\n"
            + " existing\n"
            + "+def hello():\n"
            + '+    return "world"\n'
        )
        result = _extract_added_lines(diff)
        assert "foo.py" in result
        assert "def hello():" in result["foo.py"]
        assert '    return "world"' in result["foo.py"]

    def test_filters_trivial_lines(self):
        diff = (
            "+++ b/bar.py\n"
            + "+import os\n"
            + "+\n"
            + "+# just a comment\n"
            + "+def real_code():\n"
            + "+    pass\n"
        )
        result = _extract_added_lines(diff)
        assert "bar.py" in result
        assert result["bar.py"] == ["def real_code():"]

    def test_ignores_lines_before_file_header(self):
        diff = "+orphan line without file header\n"
        result = _extract_added_lines(diff)
        assert not result

    def test_multiple_files(self):
        diff = "+++ b/a.py\n+code_a\n+++ b/b.py\n+code_b\n"
        result = _extract_added_lines(diff)
        assert "a.py" in result
        assert "b.py" in result

    def test_empty_diff(self):
        assert not _extract_added_lines("")


class TestGetAgentDiff:
    def test_returns_diff(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 950, worktree_root=wt_root)

        (Path(wt_path) / "agent_file.py").write_text("agent code")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Agent work"], cwd=wt_path)

        diff = get_agent_diff(str(git_repo), "agent/950")
        assert "agent_file.py" in diff
        assert "agent code" in diff
        cleanup_worktree(str(git_repo), wt_path)

    def test_returns_empty_on_failure(self, monkeypatch, tmp_path):
        def mock_run_git(args, **_kwargs):
            result = MagicMock()
            if args[0] == "diff":
                result.returncode = 1
                result.stdout = ""
            else:
                result.returncode = 0
                result.stdout = "main"
            result.stderr = ""
            return result

        monkeypatch.setattr("golem.worktree_manager._run_git", mock_run_git)
        assert get_agent_diff(str(tmp_path / "repo"), "branch") == ""

    def test_no_changes(self, git_repo, tmp_path):
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 951, worktree_root=wt_root)
        diff = get_agent_diff(str(git_repo), "agent/951")
        assert diff == ""
        cleanup_worktree(str(git_repo), wt_path)


class TestVerifyMergeIntegrity:
    def test_no_diff_returns_empty(self):
        assert not verify_merge_integrity("/base", "", ["a.py"])

    def test_all_present(self, tmp_path):
        (tmp_path / "foo.py").write_text('def hello():\n    return "world"\n')
        diff = '+++ b/foo.py\n+def hello():\n+    return "world"\n'
        result = verify_merge_integrity(str(tmp_path), diff, ["foo.py"])
        assert not result

    def test_missing_lines_detected(self, tmp_path):
        (tmp_path / "foo.py").write_text("only original content\n")
        diff = '+++ b/foo.py\n+def hello():\n+    return "world"\n'
        result = verify_merge_integrity(str(tmp_path), diff, ["foo.py"])
        assert len(result) == 1
        assert result[0].file == "foo.py"
        assert "def hello():" in result[0].expected_lines

    def test_file_not_in_changed_files_skipped(self, tmp_path):
        diff = "+++ b/other.py\n+new code\n"
        result = verify_merge_integrity(str(tmp_path), diff, ["foo.py"])
        assert not result

    def test_file_deleted_after_merge(self, tmp_path):
        diff = "+++ b/gone.py\n+def gone():\n"
        result = verify_merge_integrity(str(tmp_path), diff, ["gone.py"])
        assert len(result) == 1
        assert "does not exist" in result[0].description

    def test_partial_missing(self, tmp_path):
        (tmp_path / "p.py").write_text("def kept():\n    pass\n")
        diff = "+++ b/p.py\n+def kept():\n+def lost():\n"
        result = verify_merge_integrity(str(tmp_path), diff, ["p.py"])
        assert len(result) == 1
        assert "def lost():" in result[0].expected_lines
        assert "def kept():" not in result[0].expected_lines


class TestNewConfigFields:
    def test_defaults(self):
        from golem.core.config import GolemFlowConfig

        config = GolemFlowConfig()
        assert config.use_worktrees is True
        assert config.resume_on_partial is True

    def test_parse_from_yaml(self):
        from golem.core.config import _parse_golem_config

        data = {
            "use_worktrees": False,
            "resume_on_partial": False,
        }
        config = _parse_golem_config(data)
        assert config.use_worktrees is False
        assert config.resume_on_partial is False


class TestMergeReviewConfigFields:
    def test_defaults(self):
        from golem.core.config import GolemFlowConfig

        config = GolemFlowConfig()
        assert config.merge_review_budget_usd == 1.0
        assert config.merge_review_timeout == 600

    def test_parse_from_yaml(self):
        from golem.core.config import _parse_golem_config

        data = {
            "merge_review_budget_usd": 2.5,
            "merge_review_timeout": 300,
        }
        config = _parse_golem_config(data)
        assert config.merge_review_budget_usd == 2.5
        assert config.merge_review_timeout == 300


class TestTaskSessionMergeFields:
    def test_merge_deferred_default(self):
        from golem.orchestrator import TaskSession

        s = TaskSession(parent_issue_id=1)
        assert s.merge_deferred is False
        assert s.merge_branch == ""

    def test_round_trip(self):
        from golem.orchestrator import TaskSession

        s = TaskSession(
            parent_issue_id=1, merge_deferred=True, merge_branch="merge-ready/1"
        )
        d = s.to_dict()
        assert d["merge_deferred"] is True
        assert d["merge_branch"] == "merge-ready/1"
        s2 = TaskSession.from_dict(d)
        assert s2.merge_deferred is True
        assert s2.merge_branch == "merge-ready/1"


class TestMergeInWorktree:
    def test_clean_merge(self, git_repo, tmp_path):
        """Agent branch merges cleanly into master via temp worktree."""
        from golem.worktree_manager import merge_in_worktree

        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 1001, worktree_root=wt_root)

        (Path(wt_path) / "agent_file.py").write_text("agent code\n")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Agent work"], cwd=wt_path)

        # Remove agent worktree (simulating normal flow)
        _run_git(["worktree", "remove", "--force", wt_path], cwd=str(git_repo))

        outcome = merge_in_worktree(str(git_repo), 1001)
        assert outcome.sha
        assert outcome.error == ""
        assert outcome.merge_branch  # temp branch exists
        assert not outcome.missing_additions

    def test_diverged_branches_merge(self, git_repo, tmp_path):
        """Agent and master diverge on different files — merges cleanly."""
        from golem.worktree_manager import merge_in_worktree

        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 1002, worktree_root=wt_root)

        (Path(wt_path) / "agent_file.py").write_text("agent\n")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Agent"], cwd=wt_path)

        # Advance master
        (git_repo / "main_file.py").write_text("main\n")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "Main"], cwd=str(git_repo))

        _run_git(["worktree", "remove", "--force", wt_path], cwd=str(git_repo))

        outcome = merge_in_worktree(str(git_repo), 1002)
        assert outcome.sha
        assert outcome.error == ""

    def test_no_commits_returns_head(self, git_repo, tmp_path):
        """No new commits on agent branch — returns current HEAD."""
        from golem.worktree_manager import merge_in_worktree

        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 1003, worktree_root=wt_root)
        _run_git(["worktree", "remove", "--force", wt_path], cwd=str(git_repo))

        outcome = merge_in_worktree(str(git_repo), 1003)
        assert outcome.sha  # HEAD sha
        assert outcome.error == ""

    def test_dirty_main_not_touched(self, git_repo, tmp_path):
        """User's dirty working tree is untouched during merge."""
        from golem.worktree_manager import merge_in_worktree

        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 1004, worktree_root=wt_root)

        (Path(wt_path) / "shared.py").write_text("agent version\n")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Agent"], cwd=wt_path)
        _run_git(["worktree", "remove", "--force", wt_path], cwd=str(git_repo))

        # Make main dirty with different file
        (git_repo / "user_wip.py").write_text("user work in progress\n")

        outcome = merge_in_worktree(str(git_repo), 1004)
        assert outcome.sha
        # User's dirty file still there, untouched
        assert (git_repo / "user_wip.py").read_text() == "user work in progress\n"

    def test_missing_branch_returns_error(self, git_repo):
        """Non-existent agent branch returns error."""
        from golem.worktree_manager import merge_in_worktree

        outcome = merge_in_worktree(str(git_repo), 9999)
        assert outcome.sha == ""
        assert outcome.error  # should mention branch not found

    def test_conflict_returns_empty_sha(self, git_repo, tmp_path):
        """Conflicting changes on same file return empty sha + error."""
        from golem.worktree_manager import merge_in_worktree

        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 1005, worktree_root=wt_root)

        # Both sides modify README.md
        (Path(wt_path) / "README.md").write_text("agent version\n")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Agent edits README"], cwd=wt_path)

        (git_repo / "README.md").write_text("main version\n")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "Main edits README"], cwd=str(git_repo))

        _run_git(["worktree", "remove", "--force", wt_path], cwd=str(git_repo))

        outcome = merge_in_worktree(str(git_repo), 1005)
        assert outcome.sha == ""
        assert outcome.error  # non-empty error message


class TestMergeInWorktreeEdgeCases:
    def test_stale_merge_worktree_cleaned(self, git_repo, tmp_path):
        """Pre-existing merge worktree directory is cleaned before merge."""
        from golem.worktree_manager import merge_in_worktree

        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 1010, worktree_root=wt_root)

        (Path(wt_path) / "f.py").write_text("code\n")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Work"], cwd=wt_path)
        _run_git(["worktree", "remove", "--force", wt_path], cwd=str(git_repo))

        # Pre-create the merge worktree directory to trigger rmtree
        merge_wt_dir = Path(git_repo) / "data" / "agent" / "merge-worktrees" / "1010"
        merge_wt_dir.mkdir(parents=True)
        (merge_wt_dir / "stale_file").write_text("stale")

        outcome = merge_in_worktree(str(git_repo), 1010)
        assert outcome.sha
        assert outcome.error == ""
        # Stale dir was replaced, merge worked
        assert not merge_wt_dir.exists()  # cleaned up after merge

    def test_worktree_creation_failure(self, git_repo, tmp_path, monkeypatch):
        """Worktree creation failure returns error outcome."""
        from golem.worktree_manager import merge_in_worktree

        # Create agent branch with a commit
        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 1011, worktree_root=wt_root)
        (Path(wt_path) / "f.py").write_text("code\n")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Work"], cwd=wt_path)
        _run_git(["worktree", "remove", "--force", wt_path], cwd=str(git_repo))

        original_run_git = _run_git

        def mock_run_git(args, cwd, timeout=30):
            if "worktree" in args and "add" in args:
                result = MagicMock()
                result.returncode = 128
                result.stderr = "fatal: cannot create worktree"
                result.stdout = ""
                return result
            return original_run_git(args, cwd, timeout=timeout)

        monkeypatch.setattr("golem.worktree_manager._run_git", mock_run_git)
        outcome = merge_in_worktree(str(git_repo), 1011)
        assert outcome.sha == ""
        assert "worktree creation failed" in outcome.error

    def test_missing_additions_warning(self, git_repo, tmp_path, monkeypatch):
        """Merge succeeds but verify_merge_integrity finds missing additions."""
        from golem.worktree_manager import merge_in_worktree

        wt_root = str(tmp_path / "worktrees")
        wt_path = create_worktree(str(git_repo), 1012, worktree_root=wt_root)

        (Path(wt_path) / "agent_file.py").write_text("agent code\n")
        _run_git(["add", "."], cwd=wt_path)
        _run_git(["commit", "-m", "Agent work"], cwd=wt_path)
        _run_git(["worktree", "remove", "--force", wt_path], cwd=str(git_repo))

        # Mock verify_merge_integrity to report missing additions
        fake_missing = [MissingAddition(file="agent_file.py", expected_lines=["lost"])]
        monkeypatch.setattr(
            "golem.worktree_manager.verify_merge_integrity",
            lambda *_args, **_kwargs: fake_missing,
        )

        outcome = merge_in_worktree(str(git_repo), 1012)
        assert outcome.sha  # Merge succeeded
        assert outcome.missing_additions == fake_missing


class TestFastForwardIfSafe:
    def test_clean_ff(self, git_repo):
        """Fast-forward succeeds when working tree is clean."""
        from golem.worktree_manager import fast_forward_if_safe

        # Create a branch one commit ahead
        _run_git(["checkout", "-b", "merge-ready/100"], cwd=str(git_repo))
        (git_repo / "new.py").write_text("new\n")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "ahead"], cwd=str(git_repo))
        _run_git(["checkout", "master"], cwd=str(git_repo))

        ok, reason = fast_forward_if_safe(str(git_repo), "merge-ready/100")
        assert ok is True
        assert reason == ""
        assert (git_repo / "new.py").exists()

    def test_dirty_non_overlapping_ff(self, git_repo):
        """FF succeeds when dirty files don't overlap with merge."""
        from golem.worktree_manager import fast_forward_if_safe

        _run_git(["checkout", "-b", "merge-ready/101"], cwd=str(git_repo))
        (git_repo / "merged.py").write_text("from merge\n")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "merged"], cwd=str(git_repo))
        _run_git(["checkout", "master"], cwd=str(git_repo))

        # Dirty file that doesn't overlap
        (git_repo / "user_wip.txt").write_text("wip")

        ok, _reason = fast_forward_if_safe(str(git_repo), "merge-ready/101")
        assert ok is True
        # User's file still there
        assert (git_repo / "user_wip.txt").read_text() == "wip"

    def test_dirty_overlapping_defers(self, git_repo):
        """FF deferred when dirty files overlap with merge."""
        from golem.worktree_manager import fast_forward_if_safe

        _run_git(["checkout", "-b", "merge-ready/102"], cwd=str(git_repo))
        (git_repo / "README.md").write_text("merged version\n")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "edit readme"], cwd=str(git_repo))
        _run_git(["checkout", "master"], cwd=str(git_repo))

        # Dirty the same file
        (git_repo / "README.md").write_text("user edits\n")

        ok, reason = fast_forward_if_safe(str(git_repo), "merge-ready/102")
        assert ok is False
        assert "overlapping" in reason.lower() or "dirty" in reason.lower()
        # README still has user's version
        assert (git_repo / "README.md").read_text() == "user edits\n"

    def test_diverged_branches_not_ff(self, git_repo):
        """FF fails when branches have diverged (not fast-forwardable)."""
        from golem.worktree_manager import fast_forward_if_safe

        # Create divergent branch
        _run_git(["checkout", "-b", "merge-ready/103"], cwd=str(git_repo))
        (git_repo / "branch_file.py").write_text("branch\n")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "branch commit"], cwd=str(git_repo))
        _run_git(["checkout", "master"], cwd=str(git_repo))

        # Advance master (diverge)
        (git_repo / "master_file.py").write_text("master\n")
        _run_git(["add", "."], cwd=str(git_repo))
        _run_git(["commit", "-m", "master commit"], cwd=str(git_repo))

        ok, reason = fast_forward_if_safe(str(git_repo), "merge-ready/103")
        assert ok is False
        assert "diverged" in reason or "ff-only failed" in reason

    def test_generic_ff_failure(self, monkeypatch, tmp_path):
        """Generic ff-only failure with unexpected error message."""
        from golem.worktree_manager import fast_forward_if_safe

        def mock_run_git(_args, **_kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = "fatal: some unexpected git error"
            return result

        monkeypatch.setattr("golem.worktree_manager._run_git", mock_run_git)
        ok, reason = fast_forward_if_safe(str(tmp_path / "repo"), "branch")
        assert ok is False
        assert "ff-only failed" in reason

    def test_missing_ref_detected(self, git_repo):
        """Non-existent branch ref is reported as 'ref not found'."""
        from golem.worktree_manager import fast_forward_if_safe

        ok, reason = fast_forward_if_safe(str(git_repo), "merge-ready/nonexistent")
        assert ok is False
        assert "ref not found" in reason


class TestCleanupOrphanedWorktrees:
    """Tests for cleanup_orphaned_worktrees."""

    def test_returns_zero_when_no_dirs(self, git_repo):
        """Returns 0 when there are no worktree directories to clean."""
        from golem.worktree_manager import cleanup_orphaned_worktrees

        count = cleanup_orphaned_worktrees(str(git_repo))
        assert count == 0

    def test_removes_unregistered_worktree_dir(self, git_repo):
        """Dirs in data/agent/worktrees/ that are not registered git worktrees
        are removed and counted."""
        from golem.worktree_manager import cleanup_orphaned_worktrees

        orphan_dir = git_repo / "data" / "agent" / "worktrees" / "orphan-123"
        orphan_dir.mkdir(parents=True)
        (orphan_dir / "some_file.py").write_text("orphaned content")

        count = cleanup_orphaned_worktrees(str(git_repo))
        assert count == 1
        assert not orphan_dir.exists()

    def test_keeps_registered_worktree(self, git_repo):
        """Active git worktrees are NOT removed."""
        from golem.worktree_manager import cleanup_orphaned_worktrees, create_worktree

        wt_root = str(git_repo / "data" / "agent" / "worktrees")
        wt_path = create_worktree(str(git_repo), 7001, worktree_root=wt_root)

        count = cleanup_orphaned_worktrees(str(git_repo))
        assert count == 0
        assert Path(wt_path).exists()

        # Cleanup
        _run_git(["worktree", "remove", "--force", wt_path], cwd=str(git_repo))
        _run_git(["branch", "-D", "agent/7001"], cwd=str(git_repo))

    def test_removes_verify_worktrees(self, git_repo):
        """Dirs under data/agent/verify-worktrees/ are always removed."""
        from golem.worktree_manager import cleanup_orphaned_worktrees

        verify_dir = git_repo / "data" / "agent" / "verify-worktrees" / "stale-456"
        verify_dir.mkdir(parents=True)

        count = cleanup_orphaned_worktrees(str(git_repo))
        assert count == 1
        assert not verify_dir.exists()

    def test_removes_bisect_worktrees(self, git_repo):
        """Dirs under data/agent/bisect-worktrees/ are always removed."""
        from golem.worktree_manager import cleanup_orphaned_worktrees

        bisect_dir = git_repo / "data" / "agent" / "bisect-worktrees" / "stale-789"
        bisect_dir.mkdir(parents=True)

        count = cleanup_orphaned_worktrees(str(git_repo))
        assert count == 1
        assert not bisect_dir.exists()

    def test_counts_all_removed_dirs(self, git_repo):
        """Returns the total count across all subdirectories."""
        from golem.worktree_manager import cleanup_orphaned_worktrees

        for subdir in ("worktrees", "verify-worktrees", "bisect-worktrees"):
            d = git_repo / "data" / "agent" / subdir / "orphan-x"
            d.mkdir(parents=True)

        count = cleanup_orphaned_worktrees(str(git_repo))
        assert count == 3

    def test_handles_oserror_gracefully(self, git_repo, monkeypatch):
        """OSError during removal is logged as a warning and does not raise."""
        from golem.worktree_manager import cleanup_orphaned_worktrees

        orphan_dir = git_repo / "data" / "agent" / "worktrees" / "fail-dir"
        orphan_dir.mkdir(parents=True)

        original_rmtree = __import__("shutil").rmtree

        def failing_rmtree(path, **kwargs):
            if "fail-dir" in str(path):
                raise OSError("permission denied")
            original_rmtree(path, **kwargs)

        monkeypatch.setattr("golem.worktree_manager.shutil.rmtree", failing_rmtree)
        # Should not raise
        count = cleanup_orphaned_worktrees(str(git_repo))
        assert count == 0

    def test_handles_oserror_in_verify_worktrees(self, git_repo, monkeypatch):
        """OSError during verify-worktrees removal is logged and not raised."""
        from golem.worktree_manager import cleanup_orphaned_worktrees

        fail_dir = git_repo / "data" / "agent" / "verify-worktrees" / "fail-dir"
        fail_dir.mkdir(parents=True)

        original_rmtree = __import__("shutil").rmtree

        def failing_rmtree(path, **kwargs):
            if "fail-dir" in str(path):
                raise OSError("permission denied")
            original_rmtree(path, **kwargs)

        monkeypatch.setattr("golem.worktree_manager.shutil.rmtree", failing_rmtree)
        count = cleanup_orphaned_worktrees(str(git_repo))
        assert count == 0


class TestWorktreeManagerSandboxPreexec:
    """Verify _run_git in worktree_manager passes preexec_fn to subprocess.run."""

    @patch("golem.worktree_manager.subprocess.run")
    def test_preexec_fn_is_callable(self, mock_run):
        """_run_git must pass a callable preexec_fn to subprocess.run."""
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"], returncode=0, stdout=""
        )
        from golem.worktree_manager import _run_git

        _run_git(["status"], cwd="/tmp")
        kwargs = mock_run.call_args[1]
        assert (
            "preexec_fn" in kwargs
        ), "preexec_fn missing from worktree_manager _run_git"
        assert callable(kwargs["preexec_fn"])
