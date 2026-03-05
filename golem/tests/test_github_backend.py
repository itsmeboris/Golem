"""Tests for the GitHub Issues backend (golem.backends.github)."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

from golem.backends.github import (
    GitHubStateBackend,
    GitHubTaskSource,
    _STATUS_LABELS,
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
        assert tasks == []

    @patch("golem.backends.github.subprocess.run")
    def test_poll_tasks_empty_stdout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="  ", stderr="")
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert tasks == []

    @patch("golem.backends.github.subprocess.run")
    def test_poll_tasks_json_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert tasks == []

    @patch("golem.backends.github.subprocess.run")
    def test_poll_tasks_os_error(self, mock_run):
        mock_run.side_effect = OSError("no gh")
        source = GitHubTaskSource()
        tasks = source.poll_tasks(["owner/repo"], "agent")
        assert tasks == []

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

    def test_get_child_tasks_returns_empty(self):
        source = GitHubTaskSource()
        assert source.get_child_tasks(42) == []

    def test_create_child_task_returns_none(self):
        source = GitHubTaskSource()
        assert source.create_child_task(42, "sub", "desc") is None


class TestGitHubStateBackend:
    """Tests for GitHubStateBackend."""

    @patch("golem.backends.github.subprocess.run")
    def test_update_status_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        backend = GitHubStateBackend()
        assert backend.update_status(42, "in_progress") is True
        # Should have called remove-label for other statuses + add-label for target
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


class TestBuildGitHubProfile:
    """Tests for the github profile registration."""

    def test_github_in_available_profiles(self):
        import golem.backends.profiles  # noqa: F401
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
