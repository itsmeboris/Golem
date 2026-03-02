# pylint: disable=too-few-public-methods
"""Tests for golem.cli — CLI entry point, argument parsing, helpers."""

import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from golem.cli import (
    _make_event_handler,
    _now_iso,
    _print_cli_summary,
    _print_run_header,
    _save_cli_session,
    cmd_run,
    cmd_poll,
    cmd_stop,
    main,
    poll_for_agent_issues,
    print_results,
)
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

    def test_supervisor_mode(self, capsys):
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.COMPLETED,
            execution_mode="supervisor",
            subtask_results=[{"status": "completed"}],
        )
        _print_cli_summary(session)
        out = capsys.readouterr().out
        assert "Subtasks: 1" in out

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
    def test_merges_session(self, mock_load, mock_save):
        session = TaskSession(parent_issue_id=42)
        _save_cli_session(session)
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][0]
        assert 42 in saved


class TestCmdRun:
    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli.load_config")
    def test_with_issue_id(self, mock_config, mock_run):
        args = SimpleNamespace(
            parent_id=123,
            config=None,
            prompt="",
            file="",
            dry=False,
            subject="",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0
        mock_run.assert_called_once()

    @patch("golem.cli.load_config")
    def test_no_id_no_prompt(self, mock_config, capsys):
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
    def test_dry_run(self, mock_config, mock_run):
        args = SimpleNamespace(
            parent_id=42,
            config=None,
            prompt="",
            file="",
            dry=True,
            subject="",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0


class TestCmdPoll:
    @patch("golem.cli.poll_for_agent_issues", return_value=[])
    @patch("golem.cli.load_config")
    def test_no_issues(self, mock_config, mock_poll):
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
    def test_run_mode(self, mock_config, mock_poll, mock_run):
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
    @patch("golem.cli.read_pid", return_value=None)
    def test_no_pid_file(self, _, capsys):
        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 1

    @patch("golem.cli.remove_pid")
    @patch("os.kill", side_effect=OSError("not running"))
    @patch("golem.cli.read_pid", return_value=12345)
    def test_stale_pid(self, _, __, mock_remove, capsys):
        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 0
        mock_remove.assert_called()


class TestMainArgparse:
    def test_no_command(self, capsys):
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
    def test_poll_command(self, mock_poll):
        with patch("sys.argv", ["golem", "poll"]):
            result = main()
        assert result == 0

    def test_verbose_flag(self):
        import logging

        with patch("sys.argv", ["golem", "-v", "poll"]):
            with patch("golem.cli.cmd_poll", return_value=0):
                main()
        assert logging.getLogger().level == logging.DEBUG
        logging.getLogger().setLevel(logging.INFO)
