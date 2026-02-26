# pylint: disable=too-few-public-methods
"""Tests for golem.committer."""
import subprocess
from unittest.mock import patch

from golem.committer import CommitResult, _run_git, build_commit_message, commit_changes


class TestBuildCommitMessage:
    def test_basic_format(self):
        msg = build_commit_message(
            issue_id=123,
            subject="[AGENT] Fix the parser",
            task_type="bug_fix",
            summary="Fixed a null pointer in the parser module.",
        )
        assert msg.startswith("[BUG]")
        assert "Fix the parser" in msg
        assert "[AGENT]" not in msg
        assert "#123" in msg
        assert "golem" in msg

    def test_strips_agent_tag_case_variants(self):
        for tag in ("[AGENT]", "[agent]", "[Agent]"):
            msg = build_commit_message(
                issue_id=1, subject=f"{tag} task", task_type="other", summary=""
            )
            assert tag not in msg

    def test_truncates_long_first_line(self):
        msg = build_commit_message(
            issue_id=1,
            subject="[AGENT] " + "x" * 100,
            task_type="feature",
            summary="",
        )
        first_line = msg.split("\n")[0]
        assert len(first_line) <= 72

    def test_maps_task_types(self):
        mappings = {
            "code_change": "FIX",
            "bug_fix": "BUG",
            "feature": "FEATURE",
            "refactor": "REFACTOR",
            "documentation": "DOCS",
            "test": "TEST",
            "other": "CHORE",
        }
        for task_type, expected_tag in mappings.items():
            msg = build_commit_message(
                issue_id=1, subject="task", task_type=task_type, summary=""
            )
            assert f"[{expected_tag}]" in msg.split("\n")[0]

    def test_empty_summary_gets_default(self):
        msg = build_commit_message(
            issue_id=1, subject="task", task_type="other", summary=""
        )
        assert "Task completed by agent" in msg

    @patch("golem.committer.load_commit_format")
    def test_unknown_tag_falls_back_to_chore(self, mock_fmt):
        from golem.core.commit_format import CommitFormat

        mock_fmt.return_value = CommitFormat(
            main_tags=("ONLY_THIS",),
            sub_tags_hw=(),
            sub_tags_areas=(),
            sub_tags_chips=(),
        )
        msg = build_commit_message(
            issue_id=1, subject="task", task_type="feature", summary="test"
        )
        assert "[CHORE]" in msg.split("\n")[0]


class TestCommitChanges:
    @patch("golem.committer._run_git")
    def test_no_changes(self, mock_git):
        mock_git.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=""
        )
        result = commit_changes("/work", 1, "task", "fix", "summary")
        assert result.committed is False
        assert "No changes" in result.message

    @patch("golem.committer._run_git")
    def test_successful_commit(self, mock_git):
        def side_effect(args, cwd, timeout=30):
            if args == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=" M file.py\n"
                )
            if args == ["add", "-A"]:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="")
            if args[0] == "commit":
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="")
            if args == ["rev-parse", "--short", "HEAD"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="abc1234\n"
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

        mock_git.side_effect = side_effect
        result = commit_changes("/work", 42, "[AGENT] Fix it", "bug_fix", "Fixed")
        assert result.committed is True
        assert result.sha == "abc1234"

    @patch("golem.committer._run_git")
    def test_add_failure(self, mock_git):
        def side_effect(args, cwd, timeout=30):
            if args == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=" M file.py\n"
                )
            if args == ["add", "-A"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=1, stdout="", stderr="fatal: error"
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

        mock_git.side_effect = side_effect
        result = commit_changes("/work", 1, "task", "fix", "summary")
        assert result.committed is False
        assert "git add failed" in result.error

    @patch("golem.committer._run_git")
    def test_commit_failure_resets(self, mock_git):
        calls = []

        def side_effect(args, cwd, timeout=30):
            calls.append(args)
            if args == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=" M file.py\n"
                )
            if args == ["add", "-A"]:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="")
            if args[0] == "commit":
                return subprocess.CompletedProcess(
                    args=args, returncode=1, stdout="", stderr="hook failed"
                )
            if args == ["reset", "HEAD"]:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

        mock_git.side_effect = side_effect
        result = commit_changes("/work", 1, "task", "fix", "summary")
        assert result.committed is False
        assert "hook failed" in result.error
        assert ["reset", "HEAD"] in calls


class TestRunGit:
    @patch("golem.committer.subprocess.run")
    def test_calls_subprocess(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"], returncode=0, stdout="clean\n"
        )
        result = _run_git(["status"], cwd="/tmp")
        mock_run.assert_called_once_with(
            ["git", "status"],
            cwd="/tmp",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.stdout == "clean\n"


class TestCommitResult:
    def test_defaults(self):
        cr = CommitResult()
        assert cr.committed is False
        assert cr.sha == ""
        assert cr.message == ""
        assert cr.error == ""
