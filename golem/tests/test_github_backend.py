"""Tests for the GitHub Issues backend (golem.backends.github)."""

import json
import logging
from unittest.mock import ANY, MagicMock, call, patch

import pytest

from golem.backends.github import (
    GitHubStateBackend,
    GitHubTaskSource,
    _detect_circular_deps,
    _gh,
    _is_issue_closed,
    parse_dependencies,
)


class TestGhHelper:
    """Tests for the _gh() helper function."""

    @patch("golem.backends.github.subprocess.run")
    def test_basic_call(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = _gh("issue", "list")
        kwargs = mock_run.call_args[1]
        assert mock_run.call_args[0][0] == ["gh", "issue", "list"]
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 60
        assert result.returncode == 0

    @patch("golem.backends.github.subprocess.run")
    def test_check_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        _gh("issue", "list", check=True)
        kwargs = mock_run.call_args[1]
        assert mock_run.call_args[0][0] == ["gh", "issue", "list"]
        assert kwargs["check"] is True

    @patch("golem.backends.github.subprocess.run")
    def test_custom_timeout(self, mock_run):
        """Custom timeout is forwarded to subprocess.run."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _gh("issue", "list", timeout=120)
        kwargs = mock_run.call_args[1]
        assert mock_run.call_args[0][0] == ["gh", "issue", "list"]
        assert kwargs["timeout"] == 120

    @patch("golem.backends.github.subprocess.run")
    def test_preexec_fn_is_callable(self, mock_run):
        """subprocess.run in _gh must include a callable preexec_fn."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        _gh("issue", "list")
        kwargs = mock_run.call_args[1]
        assert "preexec_fn" in kwargs, "preexec_fn missing from _gh subprocess.run"
        assert callable(kwargs["preexec_fn"])

    @patch("golem.backends.github.subprocess.run")
    def test_timeout_expired_returns_failed_process(self, mock_run, caplog):
        """TimeoutExpired returns a CompletedProcess with rc=-1 and logs a warning."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(["gh", "issue", "list"], 60)
        with caplog.at_level(logging.WARNING, logger="golem.backends.github"):
            result = _gh("issue", "list")
        assert result.returncode == -1
        assert result.stdout == ""
        assert result.stderr == "timeout"
        assert "timed out" in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_timeout_expired_includes_args_in_warning(self, mock_run, caplog):
        """Timeout warning message includes the gh subcommand args."""
        import subprocess

        mock_run.side_effect = subprocess.TimeoutExpired(["gh", "pr", "create"], 60)
        with caplog.at_level(logging.WARNING, logger="golem.backends.github"):
            _gh("pr", "create")
        assert "pr create" in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_nonzero_returncode_logs_debug(self, mock_run, caplog):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="auth error")
        with caplog.at_level(logging.DEBUG, logger="golem.backends.github"):
            result = _gh("issue", "list")
        assert result.returncode == 1
        assert "gh issue list failed (rc=1): auth error" in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_zero_returncode_no_debug_log(self, mock_run, caplog):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with caplog.at_level(logging.DEBUG, logger="golem.backends.github"):
            _gh("issue", "list")
        assert "failed (rc=" not in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_nonzero_returncode_check_true_no_debug_log(self, mock_run, caplog):
        """When check=True, _gh() does NOT emit the debug log (caller gets exception)."""
        # Simulate the subprocess raising on non-zero since check=True
        mock_run.side_effect = Exception("non-zero returncode")
        with caplog.at_level(logging.DEBUG, logger="golem.backends.github"):
            with pytest.raises(Exception, match="non-zero returncode"):
                _gh("issue", "list", check=True)
        assert "failed (rc=" not in caplog.text


class TestGitHubTaskSource:
    """Tests for GitHubTaskSource."""

    def test_default_repo(self):
        source = GitHubTaskSource()
        assert source._repo == ""

    def test_repo_arg_stored(self):
        source = GitHubTaskSource(repo="owner/repo")
        assert source._repo == "owner/repo"

    @patch("golem.backends.github.subprocess.run")
    def test_poll_tasks_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='[{"number": 42, "title": "Fix bug"}]',
            stderr="",
        )
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert tasks == [{"id": 42, "subject": "Fix bug"}]

    @patch("golem.backends.github.subprocess.run")
    def test_poll_tasks_multiple_repos(self, mock_run):
        mock_run.side_effect = [
            MagicMock(
                returncode=0,
                stdout='[{"number": 1, "title": "A"}]',
                stderr="",
            ),
            MagicMock(
                returncode=0,
                stdout='[{"number": 2, "title": "B"}]',
                stderr="",
            ),
        ]
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["r1", "r2"], "tag")
        assert len(tasks) == 2
        assert tasks[0] == {"id": 1, "subject": "A"}
        assert tasks[1] == {"id": 2, "subject": "B"}

    @patch("golem.backends.github.subprocess.run")
    def test_poll_tasks_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert not tasks

    @patch("golem.backends.github.subprocess.run")
    def test_poll_tasks_empty_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  ", stderr="")
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert not tasks

    @patch("golem.backends.github.subprocess.run")
    def test_poll_tasks_json_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert not tasks

    @patch("golem.backends.github.subprocess.run")
    def test_poll_tasks_os_error(self, mock_run):
        mock_run.side_effect = OSError("no gh")
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert not tasks

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_subject_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"title": "My Issue"}', stderr=""
        )
        source = GitHubTaskSource()
        assert source.get_task_subject(42) == "My Issue"

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_subject_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        source = GitHubTaskSource()
        assert source.get_task_subject(42) == ""

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_subject_os_error(self, mock_run):
        mock_run.side_effect = OSError("fail")
        source = GitHubTaskSource()
        assert source.get_task_subject(42) == ""

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_subject_with_repo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"title": "My Issue"}', stderr=""
        )
        source = GitHubTaskSource(repo="owner/repo")
        assert source.get_task_subject(42) == "My Issue"
        call_kwargs = mock_run.call_args
        assert call_kwargs[0][0] == [
            "gh",
            "issue",
            "view",
            "42",
            "--json",
            "title",
            "--repo",
            "owner/repo",
        ]
        assert call_kwargs[1]["timeout"] == 60
        assert callable(call_kwargs[1].get("preexec_fn"))

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_description_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"body": "Description text"}', stderr=""
        )
        source = GitHubTaskSource()
        assert source.get_task_description(42) == "Description text"

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_description_null_body(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"body": null}', stderr=""
        )
        source = GitHubTaskSource()
        assert source.get_task_description(42) == ""

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_description_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        source = GitHubTaskSource()
        assert source.get_task_description(42) == ""

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_description_os_error(self, mock_run):
        mock_run.side_effect = OSError("fail")
        source = GitHubTaskSource()
        assert source.get_task_description(42) == ""

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_description_with_repo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"body": "text"}', stderr=""
        )
        source = GitHubTaskSource(repo="owner/repo")
        assert source.get_task_description(42) == "text"
        mock_run.assert_called_once_with(
            ["gh", "issue", "view", "42", "--json", "body", "--repo", "owner/repo"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )

    def test_get_child_tasks_returns_empty(self):
        source = GitHubTaskSource()
        assert not source.get_child_tasks(42)

    def test_create_child_task_returns_none(self):
        source = GitHubTaskSource()
        assert source.create_child_task(42, "sub", "desc") is None

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_comments_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"comments": [{"author": {"login": "alice"}, "body": "hello", "createdAt": "2024-01-02T00:00:00Z"}]}',
            stderr="",
        )
        source = GitHubTaskSource()
        comments = source.get_task_comments(42)
        assert comments == [
            {"author": "alice", "body": "hello", "created_at": "2024-01-02T00:00:00Z"}
        ]

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_comments_empty(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"comments": []}',
            stderr="",
        )
        source = GitHubTaskSource()
        comments = source.get_task_comments(42)
        assert comments == []

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_comments_since_filter(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=(
                '{"comments": ['
                '{"author": {"login": "alice"}, "body": "old", "createdAt": "2024-01-01T00:00:00Z"},'
                '{"author": {"login": "bob"}, "body": "new", "createdAt": "2024-01-03T00:00:00Z"}'
                "]}"
            ),
            stderr="",
        )
        source = GitHubTaskSource()
        comments = source.get_task_comments(42, since="2024-01-02T00:00:00Z")
        assert len(comments) == 1
        assert comments[0]["author"] == "bob"

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_comments_failure(self, mock_run, caplog):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        source = GitHubTaskSource()
        with caplog.at_level(logging.WARNING, logger="golem.backends.github"):
            comments = source.get_task_comments(42)
        assert comments == []
        assert "gh issue view comments failed for #42" in caplog.text
        assert "err" in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_comments_os_error(self, mock_run):
        mock_run.side_effect = OSError("fail")
        source = GitHubTaskSource()
        comments = source.get_task_comments(42)
        assert comments == []

    @patch("golem.backends.github.subprocess.run")
    def test_get_task_comments_with_repo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"comments": []}',
            stderr="",
        )
        source = GitHubTaskSource(repo="owner/repo")
        source.get_task_comments(42)
        mock_run.assert_called_once_with(
            [
                "gh",
                "issue",
                "view",
                "42",
                "--json",
                "comments",
                "--repo",
                "owner/repo",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )


class TestGitHubStateBackend:
    """Tests for GitHubStateBackend."""

    def test_default_repo(self):
        backend = GitHubStateBackend()
        assert backend._repo == ""

    def test_repo_arg_stored(self):
        backend = GitHubStateBackend(repo="owner/repo")
        assert backend._repo == "owner/repo"

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend()
        assert backend.update_status(42, "in_progress") is True
        # Should have called reopen + remove-label for other statuses + add-label for target
        add_call = call(
            ["gh", "issue", "edit", "42", "--add-label", "in-progress"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )
        assert add_call in mock_run.call_args_list

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_unknown(self, mock_run):
        backend = GitHubStateBackend()
        assert backend.update_status(42, "nonexistent") is False
        mock_run.assert_not_called()

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_add_label_fails(self, mock_run):
        # remove-label calls succeed, add-label fails
        def side_effect(cmd, **_kwargs):
            if "--add-label" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="label err")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "fixed") is False

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_add_label_os_error(self, mock_run):
        def side_effect(cmd, **_kwargs):
            if "--add-label" in cmd:
                raise OSError("gh not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "fixed") is False

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_remove_label_os_error(self, mock_run):
        """OSError during remove-label is silently caught."""

        def side_effect(cmd, **_kwargs):
            if "--remove-label" in cmd:
                raise OSError("fail")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "in_progress") is True

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_remove_label_nonzero_returncode(self, mock_run):
        """Non-zero returncode from remove-label is non-fatal; add-label still executes."""

        def side_effect(cmd, **_kwargs):
            if "--remove-label" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="label not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "in_progress") is True
        add_label_calls = [
            c for c in mock_run.call_args_list if "--add-label" in c.args[0]
        ]
        assert len(add_label_calls) == 1

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_closed_closes_issue(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend()
        assert backend.update_status(42, "closed") is True
        close_call = call(
            ["gh", "issue", "close", "42"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )
        assert close_call in mock_run.call_args_list

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_closed_close_fails(self, mock_run):
        """If issue close OSError, label is still attempted."""

        def side_effect(cmd, **_kwargs):
            if "close" in cmd:
                raise OSError("fail")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        # Should still succeed (close failure is best-effort)
        assert backend.update_status(42, "closed") is True

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_closed_close_nonzero_returncode(self, mock_run):
        """Non-zero returncode from gh issue close is logged but non-fatal."""

        def side_effect(cmd, **_kwargs):
            if "close" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="already closed")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "closed") is True

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_in_progress_reopens(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend()
        assert backend.update_status(42, "in_progress") is True
        reopen_call = call(
            ["gh", "issue", "reopen", "42"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )
        assert reopen_call in mock_run.call_args_list

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_in_progress_reopen_fails(self, mock_run):
        """If issue reopen OSError, label is still attempted."""

        def side_effect(cmd, **_kwargs):
            if "reopen" in cmd:
                raise OSError("fail")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        # Should still succeed (reopen failure is best-effort)
        assert backend.update_status(42, "in_progress") is True

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_in_progress_reopen_nonzero_returncode(self, mock_run):
        """Non-zero returncode from gh issue reopen is logged but non-fatal."""

        def side_effect(cmd, **_kwargs):
            if "reopen" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="already open")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "in_progress") is True

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_with_repo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend(repo="owner/repo")
        assert backend.update_status(42, "closed") is True
        close_call = call(
            ["gh", "issue", "close", "42", "--repo", "owner/repo"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )
        assert close_call in mock_run.call_args_list
        add_call = call(
            [
                "gh",
                "issue",
                "edit",
                "42",
                "--add-label",
                "closed",
                "--repo",
                "owner/repo",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )
        assert add_call in mock_run.call_args_list

    @patch("golem.backends.github.subprocess.run")
    def test_post_comment_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend()
        assert backend.post_comment(42, "hello") is True
        mock_run.assert_called_once_with(
            ["gh", "issue", "comment", "42", "--body", "hello"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )

    @patch("golem.backends.github.subprocess.run")
    def test_post_comment_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        backend = GitHubStateBackend()
        assert backend.post_comment(42, "hello") is False

    @patch("golem.backends.github.subprocess.run")
    def test_post_comment_os_error(self, mock_run):
        mock_run.side_effect = OSError("fail")
        backend = GitHubStateBackend()
        assert backend.post_comment(42, "hello") is False

    @patch("golem.backends.github.subprocess.run")
    def test_post_comment_with_repo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend(repo="owner/repo")
        assert backend.post_comment(42, "hello") is True
        mock_run.assert_called_once_with(
            [
                "gh",
                "issue",
                "comment",
                "42",
                "--body",
                "hello",
                "--repo",
                "owner/repo",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )

    @patch("golem.backends.github.subprocess.run")
    def test_update_progress(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend()
        assert backend.update_progress(42, 50) is True
        mock_run.assert_called_once_with(
            ["gh", "issue", "comment", "42", "--body", "Progress: 50%"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )

    @patch("golem.backends.github.subprocess.run")
    def test_assign_issue_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend()
        assert backend.assign_issue(42) is True
        mock_run.assert_called_once_with(
            ["gh", "issue", "edit", "42", "--add-assignee", "@me"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )

    @patch("golem.backends.github.subprocess.run")
    def test_assign_issue_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        backend = GitHubStateBackend()
        assert backend.assign_issue(42) is False

    @patch("golem.backends.github.subprocess.run")
    def test_assign_issue_os_error(self, mock_run):
        mock_run.side_effect = OSError("fail")
        backend = GitHubStateBackend()
        assert backend.assign_issue(42) is False

    @patch("golem.backends.github.subprocess.run")
    def test_assign_issue_with_repo(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend(repo="owner/repo")
        assert backend.assign_issue(42, "alice") is True
        mock_run.assert_called_once_with(
            [
                "gh",
                "issue",
                "edit",
                "42",
                "--add-assignee",
                "alice",
                "--repo",
                "owner/repo",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )

    @patch("golem.backends.github.subprocess.run")
    def test_create_pull_request_success(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="https://github.com/owner/repo/pull/1\n",
            stderr="",
        )
        backend = GitHubStateBackend()
        url = backend.create_pull_request("feature-branch", "main", "My PR", "PR body")
        assert url == "https://github.com/owner/repo/pull/1"

    @patch("golem.backends.github.subprocess.run")
    def test_create_pull_request_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        backend = GitHubStateBackend()
        url = backend.create_pull_request("feature", "main", "PR", "body")
        assert url == ""

    @patch("golem.backends.github.subprocess.run")
    def test_create_pull_request_os_error(self, mock_run):
        mock_run.side_effect = OSError("fail")
        backend = GitHubStateBackend()
        url = backend.create_pull_request("feature", "main", "PR", "body")
        assert url == ""

    @patch("golem.backends.github.subprocess.run")
    def test_create_pull_request_with_repo(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="https://github.com/owner/repo/pull/2\n",
            stderr="",
        )
        backend = GitHubStateBackend(repo="owner/repo")
        url = backend.create_pull_request("feature", "main", "PR", "body")
        assert url == "https://github.com/owner/repo/pull/2"
        mock_run.assert_called_once_with(
            [
                "gh",
                "pr",
                "create",
                "--head",
                "feature",
                "--base",
                "main",
                "--title",
                "PR",
                "--body",
                "body",
                "--repo",
                "owner/repo",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )


class TestBuildGitHubProfile:
    """Tests for the github profile registration."""

    def test_github_in_available_profiles(self):
        import golem.backends.profiles  # noqa: F401  # pylint: disable=unused-import
        from golem.profile import available_profiles

        assert "github" in available_profiles()

    @patch("golem.backends.profiles._build_notifier")
    def test_build_github_profile(self, mock_notifier):
        from golem.backends.local import LogNotifier
        from golem.profile import build_profile

        mock_notifier.return_value = LogNotifier()
        config = MagicMock()
        config.get_flow_config.return_value = None
        profile = build_profile("github", config)
        assert profile.name == "github"
        assert isinstance(profile.task_source, GitHubTaskSource)
        assert isinstance(profile.state_backend, GitHubStateBackend)

    @patch("golem.backends.profiles._build_notifier")
    def test_build_github_profile_with_projects(self, mock_notifier):
        from golem.backends.local import LogNotifier
        from golem.profile import build_profile

        mock_notifier.return_value = LogNotifier()
        config = MagicMock()
        task_config = MagicMock()
        task_config.projects = ["owner/repo"]
        task_config.prompts_dir = ""
        task_config.mcp_enabled = False
        config.get_flow_config.return_value = task_config
        profile = build_profile("github", config)
        assert profile.task_source._repo == "owner/repo"
        assert profile.state_backend._repo == "owner/repo"

    @patch("golem.backends.profiles._build_notifier")
    def test_build_github_profile_no_projects(self, mock_notifier):
        from golem.backends.local import LogNotifier
        from golem.profile import build_profile

        mock_notifier.return_value = LogNotifier()
        config = MagicMock()
        task_config = MagicMock()
        task_config.projects = []
        task_config.prompts_dir = ""
        task_config.mcp_enabled = False
        config.get_flow_config.return_value = task_config
        profile = build_profile("github", config)
        assert profile.task_source._repo == ""
        assert profile.state_backend._repo == ""

    @patch("golem.backends.profiles._build_notifier")
    def test_build_github_profile_multi_repo_warns(self, mock_notifier, caplog):
        from golem.backends.local import LogNotifier
        from golem.profile import build_profile

        mock_notifier.return_value = LogNotifier()
        config = MagicMock()
        task_config = MagicMock()
        task_config.projects = ["owner/repo1", "owner/repo2"]
        task_config.prompts_dir = ""
        task_config.mcp_enabled = False
        config.get_flow_config.return_value = task_config
        with caplog.at_level(logging.WARNING, logger="golem.backends.profiles"):
            profile = build_profile("github", config)
        assert profile.task_source._repo == "owner/repo1"
        assert "only first repo" in caplog.text
        assert "poll-only" in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_poll_untagged_tasks_filters_labeled(self, mock_run):
        """poll_untagged_tasks excludes issues that have the exclude_tag label."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "number": 1,
                        "title": "Bug A",
                        "body": "desc",
                        "labels": [{"name": "bug"}],
                    },
                    {
                        "number": 2,
                        "title": "Agent task",
                        "body": "desc",
                        "labels": [{"name": "golem"}, {"name": "bug"}],
                    },
                    {
                        "number": 3,
                        "title": "Bug C",
                        "body": "desc",
                        "labels": [],
                    },
                ]
            ),
            stderr="",
        )
        source = GitHubTaskSource(repo="owner/repo")
        result = source.poll_untagged_tasks(["owner/repo"], "golem")
        ids = [r["id"] for r in result]
        assert 1 in ids
        assert 3 in ids
        assert 2 not in ids  # has "golem" label, should be excluded

    @patch("golem.backends.github.subprocess.run")
    def test_poll_untagged_tasks_nonzero_returncode(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        source = GitHubTaskSource()
        result = source.poll_untagged_tasks(["owner/repo"], "golem")
        assert result == []

    @patch("golem.backends.github.subprocess.run")
    def test_poll_untagged_tasks_os_error(self, mock_run):
        mock_run.side_effect = OSError("gh not found")
        source = GitHubTaskSource()
        result = source.poll_untagged_tasks(["owner/repo"], "golem")
        assert result == []

    @patch("golem.backends.github.subprocess.run")
    def test_poll_untagged_tasks_json_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        source = GitHubTaskSource()
        result = source.poll_untagged_tasks(["owner/repo"], "golem")
        assert result == []

    @patch("golem.backends.github.subprocess.run")
    def test_poll_untagged_tasks_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  ", stderr="")
        source = GitHubTaskSource()
        result = source.poll_untagged_tasks(["owner/repo"], "golem")
        assert result == []

    @patch("golem.backends.github.subprocess.run")
    def test_poll_untagged_tasks_limits_results(self, mock_run):
        """Results are capped at the limit parameter."""
        issues = [
            {"number": i, "title": f"Issue {i}", "body": "", "labels": []}
            for i in range(30)
        ]
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(issues),
            stderr="",
        )
        source = GitHubTaskSource()
        result = source.poll_untagged_tasks(["owner/repo"], "golem", limit=5)
        assert len(result) == 5


class TestUpdateStatusClosedVerification:
    """Tests for post-close verification in GitHubStateBackend.update_status."""

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_closed_verifies_state(self, mock_run):
        """After closing, gh issue view is called to verify state."""
        mock_run.side_effect = [
            # close call
            MagicMock(returncode=0, stdout="", stderr=""),
            # verify call (gh issue view)
            MagicMock(returncode=0, stdout='{"state": "CLOSED"}', stderr=""),
            # remove-label calls (2x for other statuses)
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            # add-label call
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        backend = GitHubStateBackend()
        assert backend.update_status(42, "closed") is True
        # Verify the view call was made
        view_call = call(
            ["gh", "issue", "view", "42", "--json", "state"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
            preexec_fn=ANY,
        )
        assert view_call in mock_run.call_args_list

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_closed_verify_still_open_warns(self, mock_run, caplog):
        """If issue is still OPEN after close, log warning but continue."""
        mock_run.side_effect = [
            # close call
            MagicMock(returncode=0, stdout="", stderr=""),
            # verify call — still open
            MagicMock(returncode=0, stdout='{"state": "OPEN"}', stderr=""),
            # remove-label + add-label
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        backend = GitHubStateBackend()
        import logging

        with caplog.at_level(logging.WARNING, logger="golem.backends.github"):
            assert backend.update_status(42, "closed") is True
        assert "expected CLOSED but got OPEN" in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_closed_verify_fails_gracefully(self, mock_run):
        """If verification gh call fails, proceed without crashing."""
        mock_run.side_effect = [
            # close call
            MagicMock(returncode=0, stdout="", stderr=""),
            # verify call fails
            MagicMock(returncode=1, stdout="", stderr="api error"),
            # remove-label + add-label
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ]
        backend = GitHubStateBackend()
        assert backend.update_status(42, "closed") is True

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_closed_verify_os_error(self, mock_run):
        """OSError during verification is handled gracefully."""
        call_count = [0]

        def side_effect(_cmd, **_kwargs):
            call_count[0] += 1
            if call_count[0] == 1:  # close
                return MagicMock(returncode=0, stdout="", stderr="")
            if call_count[0] == 2:  # verify
                raise OSError("network error")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "closed") is True


class TestParseDependencies:
    """Tests for parse_dependencies()."""

    @pytest.mark.parametrize(
        "body,expected",
        [
            ("Depends on #5", {5}),
            ("Blocked by #10", {10}),
            ("After #3", {3}),
            ("depends on #5 and Blocked by #10", {5, 10}),
            ("", set()),
            ("No dependencies here", set()),
            ("DEPENDS ON #7", {7}),
            ("Depends on #5\nAfter #3", {5, 3}),
        ],
    )
    def test_parse(self, body, expected):
        assert parse_dependencies(body) == expected

    def test_none_body_returns_empty(self):
        assert parse_dependencies(None) == set()

    def test_duplicate_dep_deduplicated(self):
        assert parse_dependencies("Depends on #5, also Depends on #5") == {5}


class TestDetectCircularDeps:
    """Tests for _detect_circular_deps()."""

    @pytest.mark.parametrize(
        "deps,expected",
        [
            ({}, set()),
            ({1: {2}, 2: {3}}, set()),
            ({1: {2}, 2: {1}}, {1, 2}),
            ({1: {1}}, {1}),
            ({1: {2}, 2: {3}, 3: {2}}, {2, 3}),
        ],
    )
    def test_detect(self, deps, expected):
        assert _detect_circular_deps(deps) == expected


class TestIsIssueClosed:
    """Tests for _is_issue_closed()."""

    @patch("golem.backends.github.subprocess.run")
    def test_closed_returns_true(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"state": "CLOSED"}', stderr=""
        )
        assert _is_issue_closed(42, "owner/repo") is True

    @patch("golem.backends.github.subprocess.run")
    def test_open_returns_false(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"state": "OPEN"}', stderr=""
        )
        assert _is_issue_closed(42, "owner/repo") is False

    @patch("golem.backends.github.subprocess.run")
    def test_api_error_returns_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="api error")
        assert _is_issue_closed(42, "owner/repo") is False

    @patch("golem.backends.github.subprocess.run")
    def test_os_error_returns_false(self, mock_run):
        mock_run.side_effect = OSError("network fail")
        assert _is_issue_closed(42, "owner/repo") is False

    @patch("golem.backends.github.subprocess.run")
    def test_json_error_returns_false(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        assert _is_issue_closed(42, "owner/repo") is False

    @patch("golem.backends.github.subprocess.run")
    def test_state_case_insensitive(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout='{"state": "closed"}', stderr=""
        )
        assert _is_issue_closed(42, "owner/repo") is True


class TestPollTasksDependencies:
    """Tests for dependency filtering in GitHubTaskSource.poll_tasks()."""

    @patch("golem.backends.github.subprocess.run")
    def test_no_deps_passes_through(self, mock_run):
        import json

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{"number": 1, "title": "A", "body": "No dependencies"}]),
            stderr="",
        )
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert tasks == [{"id": 1, "subject": "A"}]

    @patch("golem.backends.github.subprocess.run")
    def test_closed_dep_passes_through(self, mock_run):
        import json

        list_result = MagicMock(
            returncode=0,
            stdout=json.dumps([{"number": 5, "title": "B", "body": "Depends on #10"}]),
            stderr="",
        )
        view_result = MagicMock(returncode=0, stdout='{"state": "CLOSED"}', stderr="")
        mock_run.side_effect = [list_result, view_result]
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert tasks == [{"id": 5, "subject": "B"}]

    @patch("golem.backends.github.subprocess.run")
    def test_open_dep_filtered_out(self, mock_run, caplog):
        import json
        import logging

        list_result = MagicMock(
            returncode=0,
            stdout=json.dumps([{"number": 5, "title": "B", "body": "Depends on #10"}]),
            stderr="",
        )
        view_result = MagicMock(returncode=0, stdout='{"state": "OPEN"}', stderr="")
        mock_run.side_effect = [list_result, view_result]
        source = GitHubTaskSource()
        with caplog.at_level(logging.INFO, logger="golem.backends.github"):
            tasks = source.poll_tasks(["owner/repo"], "agent")
        assert tasks == []
        assert "Skipping #5: blocked by open dependency #10" in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_circular_dep_filtered_out(self, mock_run, caplog):
        import json
        import logging

        list_result = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"number": 1, "title": "X", "body": "Depends on #2"},
                    {"number": 2, "title": "Y", "body": "Depends on #1"},
                ]
            ),
            stderr="",
        )
        mock_run.return_value = list_result
        source = GitHubTaskSource()
        with caplog.at_level(logging.WARNING, logger="golem.backends.github"):
            tasks = source.poll_tasks(["owner/repo"], "agent")
        assert tasks == []
        assert "Circular dependency detected for #1, skipping" in caplog.text
        assert "Circular dependency detected for #2, skipping" in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_mixed_blocked_and_unblocked(self, mock_run, caplog):
        import json
        import logging

        list_result = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"number": 1, "title": "Free", "body": "no deps"},
                    {"number": 2, "title": "Blocked", "body": "Depends on #3"},
                ]
            ),
            stderr="",
        )
        view_result = MagicMock(returncode=0, stdout='{"state": "OPEN"}', stderr="")
        mock_run.side_effect = [list_result, view_result]
        source = GitHubTaskSource()
        with caplog.at_level(logging.INFO, logger="golem.backends.github"):
            tasks = source.poll_tasks(["owner/repo"], "agent")
        assert [t["id"] for t in tasks] == [1]
        assert "Skipping #2: blocked by open dependency #3" in caplog.text
