"""Tests for golem.git_utils."""

import pytest
from unittest.mock import MagicMock, patch

from golem.git_utils import detect_github_remote, is_git_repo


class TestIsGitRepo:
    """Test is_git_repo detection."""

    def test_git_repo_returns_true(self):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert is_git_repo("/some/repo") is True

    def test_non_git_returns_false(self, tmp_path):
        assert is_git_repo(str(tmp_path)) is False

    def test_subprocess_uses_rev_parse(self):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = is_git_repo("/some/path")
            assert result is True
            cmd = mock_run.call_args[0][0]
            assert "rev-parse" in cmd
            assert mock_run.call_args[1].get("cwd") == "/some/path"

    def test_subprocess_failure_returns_false(self):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128)
            assert is_git_repo("/not/a/repo") is False

    def test_file_not_found_returns_false(self):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            assert is_git_repo("/some/path") is False

    def test_timeout_returns_false(self):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.side_effect = __import__("subprocess").TimeoutExpired(
                cmd="git", timeout=5
            )
            assert is_git_repo("/some/path") is False


class TestDetectGithubRemote:
    """Test GitHub remote auto-detection."""

    @pytest.mark.parametrize(
        "url, expected",
        [
            ("git@github.com:owner/repo.git", "owner/repo"),
            ("git@github.com:owner/repo", "owner/repo"),
            ("https://github.com/owner/repo.git", "owner/repo"),
            ("https://github.com/owner/repo", "owner/repo"),
            ("ssh://git@github.com/owner/repo.git", "owner/repo"),
            ("ssh://git@github.com/owner/repo", "owner/repo"),
        ],
        ids=[
            "ssh-with-git-suffix",
            "ssh-no-suffix",
            "https-with-git-suffix",
            "https-no-suffix",
            "ssh-scheme-with-suffix",
            "ssh-scheme-no-suffix",
        ],
    )
    def test_github_urls(self, url, expected):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=url + "\n")
            result = detect_github_remote("/some/path")
            assert result == expected

    def test_non_github_url_returns_none(self):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="git@gitlab.com:owner/repo.git\n"
            )
            assert detect_github_remote("/some/path") is None

    def test_no_remote_returns_none(self):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=128, stdout="")
            assert detect_github_remote("/some/path") is None

    def test_not_git_repo_returns_none(self):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.side_effect = FileNotFoundError("git not found")
            assert detect_github_remote("/some/path") is None

    def test_timeout_returns_none(self):
        with patch("golem.git_utils.subprocess.run") as mock_run:
            mock_run.side_effect = __import__("subprocess").TimeoutExpired(
                cmd="git", timeout=5
            )
            assert detect_github_remote("/some/path") is None


class TestGitUtilsSandboxPreexec:
    """Verify subprocess.run calls in git_utils include preexec_fn."""

    @pytest.mark.parametrize(
        "func,kwargs",
        [
            ("is_git_repo", {"path": "/some/path"}),
            ("detect_github_remote", {"repo_path": "/some/path"}),
        ],
        ids=["is_git_repo", "detect_github_remote"],
    )
    @patch("golem.git_utils.subprocess.run")
    def test_preexec_fn_is_callable(self, mock_run, func, kwargs):
        """All subprocess.run calls in git_utils must include a callable preexec_fn."""
        import golem.git_utils as git_utils

        mock_run.return_value = MagicMock(returncode=0, stdout="owner/repo\n")
        getattr(git_utils, func)(**kwargs)
        for call in mock_run.call_args_list:
            call_kwargs = call[1]
            assert (
                "preexec_fn" in call_kwargs
            ), "preexec_fn missing from %s subprocess.run: %s" % (func, call)
            assert callable(call_kwargs["preexec_fn"])
