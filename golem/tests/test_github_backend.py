"""Tests for the GitHub Issues backend (golem.backends.github)."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from golem.backends.github import (
    GitHubStateBackend,
    GitHubTaskSource,
    _gh,
)


class TestGhHelper:
    """Tests for the _gh() helper function."""

    @patch("golem.backends.github.subprocess.run")
    def test_basic_call(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = _gh("issue", "list")
        mock_run.assert_called_once_with(
            ["gh", "issue", "list"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0

    @patch("golem.backends.github.subprocess.run")
    def test_check_flag(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        _gh("issue", "list", check=True)
        mock_run.assert_called_once_with(
            ["gh", "issue", "list"],
            capture_output=True,
            text=True,
            check=True,
        )


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
        mock_run.assert_called_once_with(
            ["gh", "issue", "view", "42", "--json", "title", "--repo", "owner/repo"],
            capture_output=True,
            text=True,
            check=False,
        )

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
    def test_get_task_comments_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="err")
        source = GitHubTaskSource()
        comments = source.get_task_comments(42)
        assert comments == []

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
        def side_effect(cmd, **kwargs):
            if "--add-label" in cmd:
                return MagicMock(returncode=1, stdout="", stderr="label err")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "fixed") is False

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_add_label_os_error(self, mock_run):
        def side_effect(cmd, **kwargs):
            if "--add-label" in cmd:
                raise OSError("gh not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "fixed") is False

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_remove_label_os_error(self, mock_run):
        """OSError during remove-label is silently caught."""

        def side_effect(cmd, **kwargs):
            if "--remove-label" in cmd:
                raise OSError("fail")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "in_progress") is True

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
        )
        assert close_call in mock_run.call_args_list

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_closed_close_fails(self, mock_run):
        """If issue close OSError, label is still attempted."""

        def side_effect(cmd, **kwargs):
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

        def side_effect(cmd, **kwargs):
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
        )
        assert reopen_call in mock_run.call_args_list

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_in_progress_reopen_fails(self, mock_run):
        """If issue reopen OSError, label is still attempted."""

        def side_effect(cmd, **kwargs):
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

        def side_effect(cmd, **kwargs):
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
        import logging

        with caplog.at_level(logging.WARNING, logger="golem.backends.profiles"):
            profile = build_profile("github", config)
        assert profile.task_source._repo == "owner/repo1"
        assert "only first repo" in caplog.text
        assert "poll-only" in caplog.text

    @patch("golem.backends.github.subprocess.run")
    def test_poll_untagged_tasks_filters_labeled(self, mock_run):
        """poll_untagged_tasks excludes issues that have the exclude_tag label."""
        import json

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
        import json

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

        def side_effect(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:  # close
                return MagicMock(returncode=0, stdout="", stderr="")
            if call_count[0] == 2:  # verify
                raise OSError("network error")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        backend = GitHubStateBackend()
        assert backend.update_status(42, "closed") is True
