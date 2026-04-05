# pylint: disable=too-few-public-methods
"""Tests for golem.cli — CLI entry point, argument parsing, helpers."""

import argparse
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from golem.cli import (
    _cmd_run_prompt,
    _make_event_handler,
    _now_iso,
    _print_cli_summary,
    _print_run_header,
    _save_cli_session,
    _submit_to_daemon,
    cmd_attach,
    cmd_detach,
    cmd_run,
    cmd_poll,
    cmd_stop,
    main,
    poll_for_agent_issues,
    print_results,
)
from golem.core.config import DaemonConfig
from golem.orchestrator import TaskSession, TaskSessionState


class TestCliNowIso:
    def test_returns_iso(self):
        ts = _now_iso()
        assert "T" in ts
        assert "+" in ts or "Z" in ts


class TestPrintResults:
    def test_empty(self, capsys):
        print_results([])
        assert capsys.readouterr().out == ""

    def test_prints_table(self, capsys):
        print_results([(1, True), (2, False)])
        out = capsys.readouterr().out
        assert "#1: OK" in out
        assert "#2: FAIL" in out


class TestPrintCliSummary:
    def test_basic(self, capsys):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.COMPLETED,
            total_cost_usd=0.50,
            duration_seconds=120,
            milestone_count=5,
            tools_called=["Read", "Write"],
        )
        _print_cli_summary(session)
        out = capsys.readouterr().out
        assert "completed" in out
        assert "$0.50" in out

    def test_with_validation(self, capsys):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.COMPLETED,
            validation_verdict="PASS",
            validation_confidence=0.95,
            validation_summary="All good",
        )
        _print_cli_summary(session)
        out = capsys.readouterr().out
        assert "PASS" in out
        assert "All good" in out

    def test_with_errors(self, capsys):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.FAILED,
            errors=["error 1", "error 2"],
        )
        _print_cli_summary(session)
        out = capsys.readouterr().out
        assert "error 1" in out

    def test_with_commit(self, capsys):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.COMPLETED,
            commit_sha="abc123",
        )
        _print_cli_summary(session)
        out = capsys.readouterr().out
        assert "abc123" in out

    def test_subagent_mode(self, capsys):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.COMPLETED,
            execution_mode="subagent",
            supervisor_phase="committing",
        )
        _print_cli_summary(session)
        out = capsys.readouterr().out
        assert "subagent" in out
        assert "committing" in out

    def test_with_concerns(self, capsys):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.COMPLETED,
            validation_verdict="PARTIAL",
            validation_confidence=0.6,
            validation_concerns=["concern A", "concern B"],
        )
        _print_cli_summary(session)
        out = capsys.readouterr().out
        assert "concern A" in out


class TestPrintRunHeader:
    def test_basic(self, capsys):
        profile = MagicMock()
        profile.name = "prompt"
        profile.task_source.get_child_tasks.return_value = []
        profile.tool_provider.servers_for_subject.return_value = ["jenkins"]
        tc = MagicMock()
        tc.task_model = "sonnet"
        tc.budget_per_task_usd = 5.0
        tc.task_timeout_seconds = 1800
        tc.default_work_dir = "/work"

        _print_run_header(42, "Test subject", profile, tc, "")
        out = capsys.readouterr().out
        assert "#42" in out
        assert "prompt" in out
        assert "sonnet" in out

    def test_with_children(self, capsys):
        profile = MagicMock()
        profile.name = "redmine"
        profile.task_source.get_child_tasks.return_value = [
            {"id": 100, "subject": "Sub1", "status": {"name": "New"}},
        ]
        profile.tool_provider.servers_for_subject.return_value = []
        tc = MagicMock()
        tc.task_model = "opus"
        tc.budget_per_task_usd = 10.0
        tc.task_timeout_seconds = 3600
        tc.default_work_dir = ""

        _print_run_header(99, "Parent", profile, tc, "/custom")
        out = capsys.readouterr().out
        assert "#100" in out
        assert "/custom" in out

    def test_with_no_budget(self, capsys):
        profile = MagicMock()
        profile.name = "test"
        profile.task_source.get_child_tasks.return_value = []
        profile.tool_provider.servers_for_subject.return_value = []
        tc = MagicMock()
        tc.task_model = "sonnet"
        tc.budget_per_task_usd = 0
        tc.task_timeout_seconds = 60
        tc.default_work_dir = ""

        _print_run_header(1, "Task", profile, tc, "")
        out = capsys.readouterr().out
        assert "unlimited" in out

    def test_fallback_from_daemon_config(self, capsys):
        profile = MagicMock()
        profile.name = "test"
        profile.task_source.get_child_tasks.return_value = []
        profile.tool_provider.servers_for_subject.return_value = []
        daemon_cfg = DaemonConfig(
            fallback_budget_usd=25.0,
            fallback_task_timeout_seconds=900,
        )

        _print_run_header(1, "Task", profile, None, "", daemon_cfg=daemon_cfg)
        out = capsys.readouterr().out
        assert "$25.0" in out
        assert "900s" in out


class TestMakeEventHandler:
    def test_basic_handler(self):
        tracker = MagicMock()
        tracker.handle_event.return_value = None
        printer = MagicMock()

        handler = _make_event_handler(tracker, printer)
        handler({"type": "assistant"})

        printer.handle.assert_called_once()
        tracker.handle_event.assert_called_once()

    def test_handler_updates_session_on_milestone(self):
        tracker = MagicMock()
        mock_milestone = MagicMock()
        tracker.handle_event.return_value = mock_milestone
        tracker.state.milestone_count = 3
        tracker.state.tools_called = ["Read"]
        tracker.state.mcp_tools_called = ["redmine"]
        tracker.state.last_activity = "reading"
        tracker.state.event_log = []
        printer = MagicMock()

        session = TaskSession(parent_issue_id=1)
        handler = _make_event_handler(
            tracker, printer, session=session, start_time=time.time() - 10
        )

        with patch("golem.cli._save_cli_session"):
            handler({"type": "assistant"})

        assert session.milestone_count == 3
        assert session.tools_called == ["Read"]


class TestSaveCliSession:
    @patch("golem.cli.save_sessions")
    @patch("golem.cli.load_sessions", return_value={})
    def test_merges_session(self, _mock_load, mock_save):
        session = TaskSession(parent_issue_id=42)
        _save_cli_session(session)
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert 42 in saved


class TestCmdRun:
    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli.load_config")
    def test_with_issue_id(self, _mock_config, mock_run):
        args = SimpleNamespace(
            parent_id=123,
            config=None,
            prompt="",
            file="",
            dry=False,
            subject="",
            cwd="",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0
        mock_run.assert_called_once()

    @patch("golem.cli.load_config")
    def test_no_id_no_prompt(self, _mock_config):
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="",
            file="",
        )
        result = cmd_run(args)
        assert result == 1

    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli.load_config")
    def test_dry_run(self, _mock_config, _mock_run):
        args = SimpleNamespace(
            parent_id=42,
            config=None,
            prompt="",
            file="",
            dry=True,
            subject="",
            cwd="",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0

    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli.load_config")
    def test_cwd_passed_to_run_issue(self, mock_config, mock_run):
        args = SimpleNamespace(
            parent_id=99,
            config=None,
            prompt="",
            file="",
            dry=False,
            subject="",
            cwd="/my/workdir",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0
        mock_run.assert_called_once_with(
            99,
            mock_config.return_value,
            dry=False,
            subject_override="",
            cwd_override="/my/workdir",
            mcp_override=None,
        )

    @patch("golem.cli._submit_to_daemon", return_value={"task_id": 1})
    @patch("golem.cli._ensure_daemon")
    @patch("golem.cli.load_config")
    def test_cwd_passed_to_daemon_via_prompt(
        self, mock_config, _mock_ensure, mock_submit
    ):
        mock_config.return_value.dashboard.port = 8080
        mock_config.return_value.daemon = DaemonConfig()
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="do stuff",
            file="",
            dry=False,
            subject="",
            cwd="/custom/dir",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0
        mock_submit.assert_called_once()
        _, kwargs = mock_submit.call_args
        assert kwargs["work_dir"] == "/custom/dir"

    @patch("golem.cli._submit_to_daemon", return_value={"task_id": 2})
    @patch("golem.cli._ensure_daemon")
    @patch("golem.cli.load_config")
    def test_cwd_passed_to_daemon_via_file(
        self, mock_config, _mock_ensure, mock_submit, tmp_path
    ):
        mock_config.return_value.dashboard.port = 8080
        mock_config.return_value.daemon = DaemonConfig()
        prompt_file = tmp_path / "task.md"
        prompt_file.write_text("do file stuff")
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="",
            file=str(prompt_file),
            dry=False,
            subject="",
            cwd="/from/file",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0
        mock_submit.assert_called_once()
        _, kwargs = mock_submit.call_args
        assert kwargs["work_dir"] == "/from/file"


class TestSubmitToDaemonHeaders:
    @patch("golem.cli.urllib.request.urlopen")
    @patch("golem.cli.urllib.request.Request")
    def test_sends_bearer_header_when_api_key_set(self, mock_req_cls, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'{"ok": true, "task_id": 1}'
        mock_urlopen.return_value = mock_resp

        _submit_to_daemon(prompt="hello", port=8081, api_key="secret-key")

        _, kwargs = mock_req_cls.call_args
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret-key"

    @patch("golem.cli.urllib.request.urlopen")
    @patch("golem.cli.urllib.request.Request")
    def test_no_bearer_header_when_no_api_key(self, mock_req_cls, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.read.return_value = b'{"ok": true, "task_id": 1}'
        mock_urlopen.return_value = mock_resp

        _submit_to_daemon(prompt="hello", port=8081, api_key="")

        _, kwargs = mock_req_cls.call_args
        headers = kwargs["headers"]
        assert "Authorization" not in headers


class TestSubmitToDaemonApiKey:
    @patch("golem.cli._submit_to_daemon", return_value={"task_id": 7})
    @patch("golem.cli._ensure_daemon")
    @patch("golem.cli.load_config")
    def test_api_key_passed_from_config(self, mock_config, _mock_ensure, mock_submit):
        mock_config.return_value.dashboard.port = 8081
        mock_config.return_value.dashboard.api_key = "my-secret"
        mock_config.return_value.daemon = DaemonConfig()
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="do stuff",
            file="",
            dry=False,
            subject="",
            cwd="",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0
        _, kwargs = mock_submit.call_args
        assert kwargs["api_key"] == "my-secret"

    @patch("golem.cli._submit_to_daemon", return_value={"task_id": 8})
    @patch("golem.cli._ensure_daemon")
    @patch("golem.cli.load_config")
    def test_empty_api_key_not_sent(self, mock_config, _mock_ensure, mock_submit):
        mock_config.return_value.dashboard.port = 8081
        mock_config.return_value.dashboard.api_key = ""
        mock_config.return_value.daemon = DaemonConfig()
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="do stuff",
            file="",
            dry=False,
            subject="",
            cwd="",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0
        _, kwargs = mock_submit.call_args
        assert kwargs["api_key"] == ""


class TestCmdPoll:
    @patch("golem.cli.poll_for_agent_issues", return_value=[])
    @patch("golem.cli.load_config")
    def test_no_issues(self, _mock_config, _mock_poll):
        args = SimpleNamespace(config=None, dry=False, run=False)
        result = cmd_poll(args)
        assert result == 0

    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli.poll_for_agent_issues")
    @patch("golem.cli.load_config")
    def test_dry_mode(self, mock_config, mock_poll, mock_run):
        mock_poll.return_value = [{"id": 1, "subject": "Task"}]
        args = SimpleNamespace(config=None, dry=True, run=False)
        cmd_poll(args)
        mock_run.assert_called_once_with(1, mock_config.return_value, dry=True)

    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli.poll_for_agent_issues")
    @patch("golem.cli.load_config")
    def test_run_mode(self, _mock_config, mock_poll, _mock_run):
        mock_poll.return_value = [{"id": 1, "subject": "Task"}]
        args = SimpleNamespace(config=None, dry=False, run=True)
        result = cmd_poll(args)
        assert result == 0


class TestPollForAgentIssues:
    def test_polls_projects(self, capsys):
        config = MagicMock()
        tc = MagicMock()
        tc.projects = ["proj"]
        tc.detection_tag = "[AGENT]"
        config.get_flow_config.return_value = tc

        profile = MagicMock()
        profile.name = "test"
        profile.task_source.poll_tasks.return_value = [
            {"id": 1, "subject": "[AGENT] Task"},
        ]
        profile.task_source.get_child_tasks.return_value = []

        with patch("golem.cli._get_profile", return_value=profile):
            issues = poll_for_agent_issues(config)

        assert len(issues) == 1
        out = capsys.readouterr().out
        assert "#1" in out


class TestCmdStop:
    @patch("golem.cli._pid_from_health", return_value=None)
    @patch("golem.cli.load_config")
    @patch("golem.cli.read_pid", return_value=None)
    def test_no_pid_file(self, _read, _cfg, _health):
        _cfg.return_value = MagicMock(dashboard=MagicMock(port=8081))
        args = SimpleNamespace(dashboard=False, pid_file=None, force=False, config=None)
        result = cmd_stop(args)
        assert result == 1

    @patch("golem.cli.remove_pid")
    @patch("os.kill", side_effect=OSError("not running"))
    @patch("golem.cli.read_pid", return_value=12345)
    def test_stale_pid(self, _, __, mock_remove):
        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 0
        mock_remove.assert_called()


class TestMainArgparse:
    def test_no_command(self):
        with patch("sys.argv", ["golem"]):
            result = main()
        assert result == 1

    @patch("golem.cli.cmd_run", return_value=0)
    def test_run_command(self, mock_run):
        with patch("sys.argv", ["golem", "run", "123"]):
            result = main()
        assert result == 0
        mock_run.assert_called_once()

    @patch("golem.cli.cmd_poll", return_value=0)
    def test_poll_command(self, _mock_poll):
        with patch("sys.argv", ["golem", "poll"]):
            result = main()
        assert result == 0

    @patch("golem.cli.cmd_run", return_value=0)
    def test_run_cwd_long_flag(self, mock_run):
        with patch("sys.argv", ["golem", "run", "42", "--cwd", "/tmp/work"]):
            result = main()
        assert result == 0
        parsed_args = mock_run.call_args[0][0]
        assert parsed_args.cwd == "/tmp/work"

    @patch("golem.cli.cmd_run", return_value=0)
    def test_run_cwd_short_flag(self, mock_run):
        with patch("sys.argv", ["golem", "run", "42", "-C", "/tmp/work"]):
            result = main()
        assert result == 0
        parsed_args = mock_run.call_args[0][0]
        assert parsed_args.cwd == "/tmp/work"

    @patch("golem.cli.cmd_run", return_value=0)
    def test_run_cwd_default_empty(self, mock_run):
        with patch("sys.argv", ["golem", "run", "42"]):
            result = main()
        assert result == 0
        parsed_args = mock_run.call_args[0][0]
        assert parsed_args.cwd == ""

    def test_verbose_flag(self):
        import logging

        with patch("sys.argv", ["golem", "-v", "poll"]):
            with patch("golem.cli.cmd_poll", return_value=0):
                main()
        assert logging.getLogger().level == logging.DEBUG


class TestCwdDefault:
    """golem run --prompt defaults work_dir to caller's cwd."""

    @patch("golem.cli._submit_to_daemon")
    @patch("golem.cli._ensure_daemon")
    @patch("golem.cli.load_config")
    def test_prompt_defaults_to_cwd(
        self, mock_config, _mock_ensure, mock_submit, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        mock_config.return_value.dashboard.port = 8080
        mock_config.return_value.daemon = DaemonConfig()
        mock_submit.return_value = {"task_id": 1}
        args = SimpleNamespace(
            prompt="test prompt",
            cwd="",
            subject="",
            config=None,
            file="",
        )
        _cmd_run_prompt(args, mock_config.return_value, "test prompt")
        _, kwargs = mock_submit.call_args
        assert kwargs.get("work_dir") == str(tmp_path)

    @patch("golem.cli._submit_to_daemon")
    @patch("golem.cli._ensure_daemon")
    @patch("golem.cli.load_config")
    def test_explicit_cwd_overrides(self, mock_config, _mock_ensure, mock_submit):
        mock_config.return_value.dashboard.port = 8080
        mock_config.return_value.daemon = DaemonConfig()
        mock_submit.return_value = {"task_id": 1}
        args = SimpleNamespace(
            prompt="test",
            cwd="/explicit/path",
            subject="",
            config=None,
            file="",
        )
        _cmd_run_prompt(args, mock_config.return_value, "test")
        _, kwargs = mock_submit.call_args
        assert kwargs.get("work_dir") == "/explicit/path"


class TestAttachDetach:
    """Tests for golem attach / golem detach subcommands."""

    def test_attach_cwd(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOLEM_REGISTRY_PATH", str(tmp_path / "repos.json"))
        monkeypatch.chdir(tmp_path)
        args = argparse.Namespace(path=None, no_heartbeat=False)
        result = cmd_attach(args)
        assert result == 0

        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        repos = reg.list_repos()
        assert len(repos) == 1
        assert repos[0]["path"] == str(tmp_path)
        assert repos[0]["heartbeat"] is True

    def test_attach_explicit_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOLEM_REGISTRY_PATH", str(tmp_path / "repos.json"))
        target = tmp_path / "myrepo"
        target.mkdir()
        args = argparse.Namespace(path=str(target), no_heartbeat=False)
        result = cmd_attach(args)
        assert result == 0

        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        repos = reg.list_repos()
        assert repos[0]["path"] == str(target)

    def test_attach_no_heartbeat(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOLEM_REGISTRY_PATH", str(tmp_path / "repos.json"))
        monkeypatch.chdir(tmp_path)
        args = argparse.Namespace(path=None, no_heartbeat=True)
        cmd_attach(args)

        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        assert reg.list_repos()[0]["heartbeat"] is False

    def test_attach_nonexistent_dir_fails(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOLEM_REGISTRY_PATH", str(tmp_path / "repos.json"))
        args = argparse.Namespace(path="/nonexistent/path/12345", no_heartbeat=False)
        result = cmd_attach(args)
        assert result == 1

    def test_detach_cwd(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOLEM_REGISTRY_PATH", str(tmp_path / "repos.json"))
        monkeypatch.chdir(tmp_path)
        from golem.repo_registry import RepoRegistry

        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg.attach(str(tmp_path))

        args = argparse.Namespace(path=None)
        result = cmd_detach(args)
        assert result == 0

        reg2 = RepoRegistry(registry_path=tmp_path / "repos.json")
        assert reg2.list_repos() == []

    def test_detach_not_attached_warns(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("GOLEM_REGISTRY_PATH", str(tmp_path / "repos.json"))
        args = argparse.Namespace(path="/not/attached")
        result = cmd_detach(args)
        assert result == 1
        assert "not attached" in capsys.readouterr().err.lower()


class TestEnsureGolemHome:
    """Tests for _ensure_golem_home auto-init."""

    def test_creates_config_when_missing(self, tmp_path, monkeypatch):
        golem_home = tmp_path / "golem_home"
        monkeypatch.setattr("golem.cli.GOLEM_HOME", golem_home)
        from golem.cli import _ensure_golem_home

        _ensure_golem_home()
        assert golem_home.exists()
        assert (golem_home / "config.yaml").exists()
        content = (golem_home / "config.yaml").read_text()
        assert "profile: local" in content

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        golem_home = tmp_path / "golem_home"
        golem_home.mkdir()
        (golem_home / "config.yaml").write_text("custom: true")
        monkeypatch.setattr("golem.cli.GOLEM_HOME", golem_home)
        from golem.cli import _ensure_golem_home

        _ensure_golem_home()
        assert (golem_home / "config.yaml").read_text() == "custom: true"


class TestCmdAttachDetection:
    def test_attach_prints_detected_stack(self, tmp_path, capsys):
        from unittest.mock import patch

        from golem.verify_config import VerifyCommand, VerifyConfig

        mock_cfg = VerifyConfig(
            version=1,
            commands=[
                VerifyCommand(role="test", cmd=["npm", "test"], source="auto-detected")
            ],
            detected_at="2026-04-05T00:00:00Z",
            stack=["javascript"],
        )

        class Args:
            path = str(tmp_path)
            no_heartbeat = False
            no_detect = False

        with (
            patch("golem.cli.RepoRegistry") as MockReg,
            patch("golem.verify_config.load_verify_config", return_value=mock_cfg),
        ):
            MockReg.return_value.attach.return_value = None
            result = cmd_attach(Args())

        assert result == 0
        captured = capsys.readouterr()
        assert "javascript" in captured.out
        assert "[test] npm test" in captured.out

    def test_attach_prints_no_commands_when_empty(self, tmp_path, capsys):
        from unittest.mock import patch

        from golem.verify_config import VerifyConfig

        mock_cfg = VerifyConfig(
            version=1, commands=[], detected_at="2026-04-05T00:00:00Z", stack=[]
        )

        class Args:
            path = str(tmp_path)
            no_heartbeat = False
            no_detect = False

        with (
            patch("golem.cli.RepoRegistry") as MockReg,
            patch("golem.verify_config.load_verify_config", return_value=mock_cfg),
        ):
            MockReg.return_value.attach.return_value = None
            result = cmd_attach(Args())

        assert result == 0
        assert "No verification commands detected" in capsys.readouterr().out

    def test_attach_no_detect_skips_output(self, tmp_path, capsys):
        from unittest.mock import patch

        class Args:
            path = str(tmp_path)
            no_heartbeat = False
            no_detect = True

        with patch("golem.cli.RepoRegistry") as MockReg:
            MockReg.return_value.attach.return_value = None
            result = cmd_attach(Args())

        assert result == 0
        out = capsys.readouterr().out
        assert "Detected stack" not in out
        assert "No verification commands" not in out
