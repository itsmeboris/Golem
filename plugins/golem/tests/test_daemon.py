"""Tests for plugins/golem/scripts/lib/daemon.py."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import daemon


class TestIsGolemInstalled:
    def test_returns_true_when_golem_found(self):
        with patch("daemon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert daemon.is_golem_installed() is True
            mock_run.assert_called_once_with(
                ["golem", "--help"],
                capture_output=True,
                timeout=5,
            )

    def test_returns_false_when_file_not_found(self):
        with patch("daemon.subprocess.run", side_effect=FileNotFoundError):
            assert daemon.is_golem_installed() is False

    def test_returns_false_when_timeout_expires(self):
        with patch(
            "daemon.subprocess.run", side_effect=subprocess.TimeoutExpired("golem", 5)
        ):
            assert daemon.is_golem_installed() is False


class TestIsDaemonRunning:
    def test_returns_true_when_status_returncode_zero(self):
        with patch("daemon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="active\n", stderr=""
            )
            assert daemon.is_daemon_running() is True
            mock_run.assert_called_once_with(
                ["golem", "status", "--hours", "0"],
                capture_output=True,
                timeout=3,
                text=True,
            )

    def test_returns_false_when_status_returncode_nonzero(self):
        with patch("daemon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="not running"
            )
            assert daemon.is_daemon_running() is False

    def test_returns_false_when_file_not_found(self):
        with patch("daemon.subprocess.run", side_effect=FileNotFoundError):
            assert daemon.is_daemon_running() is False

    def test_returns_false_when_timeout_expires(self):
        with patch(
            "daemon.subprocess.run", side_effect=subprocess.TimeoutExpired("golem", 3)
        ):
            assert daemon.is_daemon_running() is False


class TestIsRepoAttached:
    def test_returns_true_when_cwd_in_registry(self, tmp_path):
        target = tmp_path / "myrepo"
        target.mkdir()
        registry = {str(target): {"name": "myrepo"}}
        registry_path = tmp_path / ".golem" / "repos.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(json.dumps(registry))

        with patch("daemon.Path.home", return_value=tmp_path):
            assert daemon.is_repo_attached(str(target)) is True

    def test_returns_false_when_cwd_not_in_registry(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        registry = {str(other): {"name": "other"}}
        registry_path = tmp_path / ".golem" / "repos.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text(json.dumps(registry))

        target = tmp_path / "myrepo"
        target.mkdir()

        with patch("daemon.Path.home", return_value=tmp_path):
            assert daemon.is_repo_attached(str(target)) is False

    def test_returns_false_when_registry_missing(self, tmp_path):
        with patch("daemon.Path.home", return_value=tmp_path):
            assert daemon.is_repo_attached(str(tmp_path)) is False

    def test_returns_false_when_registry_json_invalid(self, tmp_path):
        registry_path = tmp_path / ".golem" / "repos.json"
        registry_path.parent.mkdir(parents=True)
        registry_path.write_text("not-json{{{")

        with patch("daemon.Path.home", return_value=tmp_path):
            assert daemon.is_repo_attached(str(tmp_path)) is False

    def test_uses_cwd_when_no_path_given(self, tmp_path):
        # Attach the current working directory
        with patch("daemon.Path.cwd", return_value=tmp_path):
            registry = {str(tmp_path): {"name": "cwd-repo"}}
            registry_path = tmp_path / ".golem" / "repos.json"
            registry_path.parent.mkdir(parents=True)
            registry_path.write_text(json.dumps(registry))

            with patch("daemon.Path.home", return_value=tmp_path):
                assert daemon.is_repo_attached() is True


class TestStartDaemon:
    def test_returns_started_true_on_success(self):
        with patch("daemon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="daemon started\n",
                stderr="",
            )
            result = daemon.start_daemon()

        assert result["started"] is True
        assert result["detail"] == "daemon started"

    def test_returns_started_false_when_returncode_nonzero(self):
        with patch("daemon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="error starting",
            )
            result = daemon.start_daemon()

        assert result["started"] is False
        assert result["detail"] == "error starting"

    def test_returns_started_false_when_file_not_found(self):
        with patch("daemon.subprocess.run", side_effect=FileNotFoundError):
            result = daemon.start_daemon()

        assert result["started"] is False
        assert result["detail"] == "golem not found in PATH"

    def test_returns_started_true_when_timeout_expires(self):
        # Daemon may background itself — timeout is expected and treated as success
        with patch(
            "daemon.subprocess.run", side_effect=subprocess.TimeoutExpired("golem", 10)
        ):
            result = daemon.start_daemon()

        assert result["started"] is True
        assert "backgrounded" in result["detail"]

    def test_detail_uses_stderr_when_stdout_empty(self):
        with patch("daemon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="",
                stderr="some error",
            )
            result = daemon.start_daemon()

        assert result["detail"] == "some error"


class TestEnsureRunning:
    def test_returns_already_running_when_daemon_up(self):
        with patch("daemon.is_daemon_running", return_value=True):
            result = daemon.ensure_running()

        assert result == {"already_running": True, "started": False}

    def test_starts_daemon_when_not_running(self):
        start_result = {"started": True, "detail": "launched"}
        with patch("daemon.is_daemon_running", return_value=False):
            with patch("daemon.start_daemon", return_value=start_result):
                result = daemon.ensure_running()

        assert result["already_running"] is False
        assert result["started"] is True
        assert result["detail"] == "launched"

    def test_returns_failure_when_start_fails(self):
        start_result = {"started": False, "detail": "golem not found in PATH"}
        with patch("daemon.is_daemon_running", return_value=False):
            with patch("daemon.start_daemon", return_value=start_result):
                result = daemon.ensure_running()

        assert result["already_running"] is False
        assert result["started"] is False


class TestAttachRepo:
    def test_returns_attached_true_on_success(self, tmp_path):
        with patch("daemon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="attached\n", stderr=""
            )
            result = daemon.attach_repo(str(tmp_path))

        assert result["attached"] is True
        assert result["path"] == str(tmp_path)
        assert result["detail"] == "attached"

    def test_returns_attached_false_on_nonzero_returncode(self, tmp_path):
        with patch("daemon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="already attached"
            )
            result = daemon.attach_repo(str(tmp_path))

        assert result["attached"] is False
        assert result["detail"] == "already attached"

    def test_returns_attached_false_when_golem_not_found(self, tmp_path):
        with patch("daemon.subprocess.run", side_effect=FileNotFoundError):
            result = daemon.attach_repo(str(tmp_path))

        assert result["attached"] is False
        assert result["detail"] == "golem not found"

    def test_returns_attached_false_on_timeout(self, tmp_path):
        with patch(
            "daemon.subprocess.run", side_effect=subprocess.TimeoutExpired("golem", 10)
        ):
            result = daemon.attach_repo(str(tmp_path))

        assert result["attached"] is False
        assert result["detail"] == "timeout"

    def test_uses_cwd_when_path_not_given(self):
        with patch("daemon.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            with patch("daemon.Path.cwd", return_value=Path("/some/repo")):
                result = daemon.attach_repo()

        assert result["path"] == "/some/repo"
        called_cmd = mock_run.call_args[0][0]
        assert "--no-detect" in called_cmd
        assert "/some/repo" in called_cmd
