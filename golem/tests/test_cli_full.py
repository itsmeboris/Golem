# pylint: disable=too-few-public-methods,not-callable,too-many-lines
"""Tests for golem.cli — full coverage."""
import asyncio
import signal
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from golem.cli import (
    _build_prompt_profile,
    _cmd_run_prompt,
    _get_profile,
    _make_event_handler,
    _manage_golem_tick,
    _run_prompt_bg,
    _wait_for_exit,
    cmd_daemon,
    cmd_dashboard,
    cmd_poll,
    cmd_run,
    cmd_status,
    cmd_stop,
    main,
    poll_for_agent_issues,
    run_daemon,
    run_issue,
)
from golem.orchestrator import TaskSession


class TestGetProfile:
    @patch("golem.cli.build_profile")
    def test_uses_config_profile(self, mock_build):
        config = MagicMock()
        tc = MagicMock()
        tc.profile = "prompt"
        config.get_flow_config.return_value = tc
        mock_build.return_value = MagicMock()

        result = _get_profile(config)

        mock_build.assert_called_once_with("prompt", config)
        assert result is mock_build.return_value

    @patch("golem.cli.build_profile")
    def test_defaults_to_redmine(self, mock_build):
        config = MagicMock()
        config.get_flow_config.return_value = None
        mock_build.return_value = MagicMock()

        _get_profile(config)

        mock_build.assert_called_once_with("redmine", config)


class TestRunIssue:
    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    @patch("golem.cli._get_profile")
    def test_full_run_completed(
        self,
        mock_profile,
        mock_header,
        mock_save,
        mock_printer,
        mock_orch_cls,
        mock_asyncio,
    ):
        profile = MagicMock()
        profile.name = "prompt"
        profile.task_source.get_task_subject.return_value = "subj"
        mock_profile.return_value = profile

        config = MagicMock()
        tc = MagicMock()
        tc.budget_per_task_usd = 5.0
        tc.use_worktrees = False
        config.get_flow_config.return_value = tc

        orch = mock_orch_cls.return_value

        async def fake_run():
            pass

        orch.run_once = fake_run

        def capture_run(coro):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(coro)
            finally:
                loop.close()

        mock_asyncio.run = capture_run

        run_issue(1, config)

        assert mock_orch_cls.called
        assert mock_save.called

    @patch("golem.cli._print_run_header")
    @patch("golem.cli._get_profile")
    def test_dry_run(self, mock_profile, mock_header, capsys):
        profile = MagicMock()
        profile.task_source.get_task_subject.return_value = "task"
        mock_profile.return_value = profile

        config = MagicMock()
        config.get_flow_config.return_value = None

        result = run_issue(1, config, dry=True)

        assert result is True
        out = capsys.readouterr().out
        assert "DRY RUN" in out

    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    def test_mcp_enable_override(
        self,
        mock_header,
        mock_save,
        mock_printer,
        mock_orch_cls,
        mock_asyncio,
    ):
        profile = MagicMock()
        profile.name = "redmine"
        profile.task_source.get_task_subject.return_value = "sub"

        config = MagicMock()
        tc = MagicMock()
        tc.budget_per_task_usd = 1.0
        config.get_flow_config.return_value = tc

        mock_asyncio.run = MagicMock()

        with patch("golem.cli.KeywordToolProvider", create=True) as ktp_cls:
            ktp_cls.return_value = MagicMock()
            with patch("golem.cli._get_profile", return_value=profile):
                run_issue(1, config, mcp_override=True)

    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    def test_mcp_disable_override(
        self,
        mock_header,
        mock_save,
        mock_printer,
        mock_orch_cls,
        mock_asyncio,
    ):
        profile = MagicMock()
        profile.name = "redmine"
        profile.task_source.get_task_subject.return_value = "sub"

        config = MagicMock()
        tc = MagicMock()
        tc.budget_per_task_usd = 1.0
        config.get_flow_config.return_value = tc

        mock_asyncio.run = MagicMock()

        with patch("golem.cli._get_profile", return_value=profile):
            run_issue(1, config, mcp_override=False)

        from golem.backends.local import NullToolProvider

        assert isinstance(profile.tool_provider, NullToolProvider)

    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    @patch("golem.cli._get_profile")
    def test_failed_session(
        self,
        mock_profile,
        mock_header,
        mock_save,
        mock_printer,
        mock_orch_cls,
        mock_asyncio,
    ):
        profile = MagicMock()
        profile.name = "redmine"
        profile.task_source.get_task_subject.return_value = "sub"
        mock_profile.return_value = profile

        config = MagicMock()
        config.get_flow_config.return_value = None

        orch = mock_orch_cls.return_value

        async def fail_run():
            pass

        orch.run_once = fail_run

        def capture_run(coro):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(coro)
            finally:
                loop.close()

        mock_asyncio.run = capture_run

        ok = run_issue(1, config)
        assert ok is False


class TestRunIssueOnProgress:
    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    @patch("golem.cli._get_profile")
    def test_on_progress_callback(  # pylint: disable=too-many-locals
        self,
        mock_profile,
        mock_header,
        mock_save,
        mock_printer,
        mock_orch_cls,
        mock_asyncio,
        capsys,
    ):
        profile = MagicMock()
        profile.name = "prompt"
        profile.task_source.get_task_subject.return_value = "subj"
        mock_profile.return_value = profile

        config = MagicMock()
        tc = MagicMock()
        tc.budget_per_task_usd = 5.0
        config.get_flow_config.return_value = tc

        captured_on_progress = None

        def capture_orch(**kwargs):
            nonlocal captured_on_progress
            captured_on_progress = kwargs.get("on_progress")
            orch = MagicMock()

            async def noop():
                pass

            orch.run_once = noop
            return orch

        mock_orch_cls.side_effect = capture_orch

        def do_run(coro):
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(coro)
            finally:
                loop.close()

        mock_asyncio.run = do_run

        run_issue(1, config)

        assert captured_on_progress is not None
        milestone = MagicMock()
        milestone.is_error = False
        milestone.kind = "tool"
        milestone.summary = "did stuff"
        session = TaskSession(parent_issue_id=1)
        captured_on_progress(session, milestone)
        err_out = capsys.readouterr().err
        assert "MILESTONE:TOOL" in err_out

    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    @patch("golem.cli._get_profile")
    def test_on_progress_error_milestone(  # pylint: disable=too-many-locals
        self,
        mock_profile,
        mock_header,
        mock_save,
        mock_printer,
        mock_orch_cls,
        mock_asyncio,
        capsys,
    ):
        profile = MagicMock()
        profile.name = "prompt"
        profile.task_source.get_task_subject.return_value = "subj"
        mock_profile.return_value = profile

        config = MagicMock()
        tc = MagicMock()
        tc.budget_per_task_usd = 5.0
        config.get_flow_config.return_value = tc

        captured_on_progress = None

        def capture_orch(**kwargs):
            nonlocal captured_on_progress
            captured_on_progress = kwargs.get("on_progress")
            orch = MagicMock()

            async def noop():
                pass

            orch.run_once = noop
            return orch

        mock_orch_cls.side_effect = capture_orch

        mock_asyncio.run = lambda coro: asyncio.new_event_loop().run_until_complete(
            coro
        )

        run_issue(1, config)

        milestone = MagicMock()
        milestone.is_error = True
        milestone.kind = "error"
        milestone.summary = "bad thing"
        session = TaskSession(parent_issue_id=1)
        captured_on_progress(session, milestone)
        err_out = capsys.readouterr().err
        assert "MILESTONE:ERROR" in err_out

    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    @patch("golem.cli._get_profile")
    def test_on_progress_save_failure(
        self,
        mock_profile,
        mock_header,
        mock_save,
        mock_printer,
        mock_orch_cls,
        mock_asyncio,
    ):
        profile = MagicMock()
        profile.name = "prompt"
        profile.task_source.get_task_subject.return_value = "subj"
        mock_profile.return_value = profile

        config = MagicMock()
        tc = MagicMock()
        tc.budget_per_task_usd = 5.0
        config.get_flow_config.return_value = tc

        captured_on_progress = None

        def capture_orch(**kwargs):
            nonlocal captured_on_progress
            captured_on_progress = kwargs.get("on_progress")
            orch = MagicMock()

            async def noop():
                pass

            orch.run_once = noop
            return orch

        mock_orch_cls.side_effect = capture_orch
        mock_asyncio.run = lambda coro: asyncio.new_event_loop().run_until_complete(
            coro
        )

        run_issue(1, config)

        mock_save.side_effect = RuntimeError("disk full")
        milestone = MagicMock()
        milestone.is_error = False
        milestone.kind = "tool"
        milestone.summary = "ok"
        session = TaskSession(parent_issue_id=1)
        captured_on_progress(session, milestone)


class TestPollForAgentIssuesEmpty:
    def test_no_issues_returns_empty(self, capsys):
        config = MagicMock()
        tc = MagicMock()
        tc.projects = ["p"]
        tc.detection_tag = "[AGENT]"
        config.get_flow_config.return_value = tc

        profile = MagicMock()
        profile.name = "test"
        profile.task_source.poll_tasks.return_value = []

        with patch("golem.cli._get_profile", return_value=profile):
            result = poll_for_agent_issues(config)

        assert not result
        out = capsys.readouterr().out
        assert "No [AGENT] issues found" in out


class TestManageGolemTick:
    def test_enabled(self):
        config = MagicMock()
        tc = MagicMock()
        tc.enabled = True
        config.get_flow_config.return_value = tc

        mock_flow = MagicMock()
        mock_flow.start_tick_loop.return_value = MagicMock()

        tasks = []
        with patch("golem.flow.GolemFlow", return_value=mock_flow):
            result = _manage_golem_tick(config, tasks)

        assert result is mock_flow
        assert len(tasks) == 1

    def test_disabled(self):
        config = MagicMock()
        tc = MagicMock()
        tc.enabled = False
        config.get_flow_config.return_value = tc

        tasks = []
        result = _manage_golem_tick(config, tasks)

        assert result is None
        assert not tasks

    def test_no_config(self):
        config = MagicMock()
        config.get_flow_config.return_value = None

        tasks = []
        result = _manage_golem_tick(config, tasks)

        assert result is None

    def test_no_tick_loop_method(self):
        config = MagicMock()
        tc = MagicMock()
        tc.enabled = True
        config.get_flow_config.return_value = tc

        mock_flow = MagicMock(spec=[])
        tasks = []
        with patch("golem.flow.GolemFlow", return_value=mock_flow):
            result = _manage_golem_tick(config, tasks)

        assert result is mock_flow
        assert not tasks


class TestRunDaemon:
    async def test_returns_1_when_flow_none(self, capsys):
        args = SimpleNamespace(port=8080)
        config = MagicMock()

        with (
            patch("golem.cli.LiveState", create=True) as mock_ls,
            patch("golem.cli._manage_golem_tick", return_value=None),
        ):
            mock_live = MagicMock()
            mock_ls.get.return_value = mock_live

            result = await run_daemon(args, config)

        assert result == 1
        err = capsys.readouterr().err
        assert "not enabled" in err

    async def test_runs_until_shutdown(self):
        args = SimpleNamespace(port=8080)
        config = MagicMock()
        config.dashboard.port = 8080

        mock_flow = MagicMock()

        tick_task = asyncio.ensure_future(asyncio.sleep(100))

        def fake_manage(cfg, tasks):
            tasks.append(tick_task)
            return mock_flow

        async def fake_dash(*a, **kw):
            return asyncio.ensure_future(asyncio.sleep(100))

        with (
            patch("golem.cli.LiveState", create=True) as mock_ls,
            patch("golem.cli._manage_golem_tick", side_effect=fake_manage),
            patch("golem.cli._start_dashboard_server", side_effect=fake_dash),
            patch("golem.cli.config_to_snapshot", return_value={}, create=True),
        ):
            mock_live = MagicMock()
            mock_ls.get.return_value = mock_live

            async def run_with_shutdown():
                task = asyncio.create_task(run_daemon(args, config))
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    return await task
                except asyncio.CancelledError:
                    return 0

            result = await run_with_shutdown()
            assert result == 0


class TestCmdRunPrompt:
    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli._build_prompt_profile")
    @patch("golem.cli.load_config")
    def test_prompt_mode(self, mock_config, mock_build, mock_run):
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="fix the bug",
            dry=False,
            subject="",
            mcp=None,
            bg=False,
        )
        result = cmd_run(args)
        assert result == 0
        mock_build.assert_called_once()
        mock_run.assert_called_once()

    @patch("golem.cli.run_issue", return_value=False)
    @patch("golem.cli._build_prompt_profile")
    @patch("golem.cli.load_config")
    def test_prompt_mode_failure(self, mock_config, mock_build, mock_run):
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="fail task",
            dry=False,
            subject="",
            mcp=None,
            bg=False,
        )
        result = cmd_run(args)
        assert result == 1

    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli._build_prompt_profile")
    @patch("golem.cli.load_config")
    def test_prompt_dry_run(self, mock_config, mock_build, mock_run):
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="dry task",
            dry=True,
            subject="",
            mcp=None,
            bg=False,
        )
        result = cmd_run(args)
        assert result == 0

    @patch("golem.cli._run_prompt_bg", return_value=0)
    @patch("golem.cli.load_config")
    def test_prompt_bg_mode(self, mock_config, mock_bg):
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="bg task",
            dry=False,
            subject="",
            mcp=None,
            bg=True,
        )
        result = cmd_run(args)
        assert result == 0
        mock_bg.assert_called_once()

    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli._build_prompt_profile")
    @patch("golem.cli.load_config")
    def test_prompt_with_mcp_enabled(self, mock_config, mock_build, mock_run):
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="mcp task",
            dry=False,
            subject="",
            mcp=True,
            bg=False,
        )
        cmd_run(args)
        mock_build.assert_called_once()
        _, kwargs = mock_build.call_args
        assert kwargs.get("mcp_enabled") or mock_build.call_args[0][-1]

    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli._build_prompt_profile")
    @patch("golem.cli.load_config")
    def test_prompt_with_mcp_disabled(self, mock_config, mock_build, mock_run):
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="no mcp task",
            dry=False,
            subject="",
            mcp=False,
            bg=False,
        )
        cmd_run(args)
        mock_build.assert_called_once()


class TestCmdRunPromptDirect:
    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli._build_prompt_profile")
    def test_direct_call(self, mock_build, mock_run):
        args = SimpleNamespace(
            bg=False,
            mcp=None,
            dry=False,
            config=None,
        )
        config = MagicMock()
        result = _cmd_run_prompt(args, config, "hello world")
        assert result == 0
        mock_run.assert_called_once()

    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli._build_prompt_profile")
    def test_dry_run(self, mock_build, mock_run):
        args = SimpleNamespace(bg=False, mcp=None, dry=True, config=None)
        config = MagicMock()
        result = _cmd_run_prompt(args, config, "test")
        assert result == 0

    @patch("golem.cli.run_issue", return_value=False)
    @patch("golem.cli._build_prompt_profile")
    def test_failure(self, mock_build, mock_run):
        args = SimpleNamespace(bg=False, mcp=None, dry=False, config=None)
        config = MagicMock()
        result = _cmd_run_prompt(args, config, "fail")
        assert result == 1


class TestBuildPromptProfile:
    def test_creates_profile_with_mcp(self, tmp_path):
        with patch("golem.cli.DATA_DIR", tmp_path):
            profile = _build_prompt_profile(12345, "do something", mcp_enabled=True)

        assert profile.name == "prompt"
        from golem.backends.mcp_tools import KeywordToolProvider

        assert isinstance(profile.tool_provider, KeywordToolProvider)

    def test_creates_profile_without_mcp(self, tmp_path):
        with patch("golem.cli.DATA_DIR", tmp_path):
            profile = _build_prompt_profile(12345, "do something", mcp_enabled=False)

        assert profile.name == "prompt"
        from golem.backends.local import NullToolProvider

        assert isinstance(profile.tool_provider, NullToolProvider)

    def test_writes_task_file(self, tmp_path):
        with patch("golem.cli.DATA_DIR", tmp_path):
            _build_prompt_profile(99999, "my prompt text")

        task_file = tmp_path / "prompt_tasks" / "99999.json"
        assert task_file.exists()
        import json

        data = json.loads(task_file.read_text())
        assert data["id"] == "99999"
        assert "my prompt text" in data["description"]


class TestRunPromptBg:
    def test_spawns_background(self, tmp_path, capsys):
        with patch("golem.cli.DATA_DIR", tmp_path), patch(
            "subprocess.Popen"
        ) as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 42
            mock_popen.return_value = mock_proc

            args = SimpleNamespace(config=None, worktree=False, mcp=None)
            result = _run_prompt_bg(args, "test prompt", 1234)

        assert result == 0
        out = capsys.readouterr().out
        assert "42" in out
        assert "1234" in out

    def test_with_config_and_worktree(self, tmp_path, capsys):
        with patch("golem.cli.DATA_DIR", tmp_path), patch(
            "subprocess.Popen"
        ) as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 10
            mock_popen.return_value = mock_proc

            args = SimpleNamespace(config="/my/config.yaml", worktree=True, mcp=None)
            result = _run_prompt_bg(args, "prompt", 5678)

        assert result == 0
        cmd = mock_popen.call_args[0][0]
        assert "-c" in cmd
        assert "/my/config.yaml" in cmd
        assert "--worktree" in cmd

    def test_with_mcp_true(self, tmp_path, capsys):
        with patch("golem.cli.DATA_DIR", tmp_path), patch(
            "subprocess.Popen"
        ) as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 11
            mock_popen.return_value = mock_proc

            args = SimpleNamespace(config=None, worktree=False, mcp=True)
            _run_prompt_bg(args, "prompt", 111)

        cmd = mock_popen.call_args[0][0]
        assert "--mcp" in cmd

    def test_with_mcp_false(self, tmp_path, capsys):
        with patch("golem.cli.DATA_DIR", tmp_path), patch(
            "subprocess.Popen"
        ) as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12
            mock_popen.return_value = mock_proc

            args = SimpleNamespace(config=None, worktree=False, mcp=False)
            _run_prompt_bg(args, "prompt", 222)

        cmd = mock_popen.call_args[0][0]
        assert "--no-mcp" in cmd


class TestCmdPollRunBranch:
    @patch("golem.cli.print_results")
    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli.poll_for_agent_issues")
    @patch("golem.cli.load_config")
    def test_run_executes_all(self, mock_config, mock_poll, mock_run, mock_print):
        mock_poll.return_value = [
            {"id": 1, "subject": "T1"},
            {"id": 2, "subject": "T2"},
        ]
        args = SimpleNamespace(config=None, dry=False, run=True)
        result = cmd_poll(args)
        assert result == 0
        assert mock_run.call_count == 2

    @patch("golem.cli.print_results")
    @patch("golem.cli.run_issue", side_effect=[True, False])
    @patch("golem.cli.poll_for_agent_issues")
    @patch("golem.cli.load_config")
    def test_partial_failure(self, mock_config, mock_poll, mock_run, mock_print):
        mock_poll.return_value = [
            {"id": 1, "subject": "T1"},
            {"id": 2, "subject": "T2"},
        ]
        args = SimpleNamespace(config=None, dry=False, run=True)
        result = cmd_poll(args)
        assert result == 1

    @patch("golem.cli.poll_for_agent_issues")
    @patch("golem.cli.load_config")
    def test_no_run_no_dry(self, mock_config, mock_poll):
        mock_poll.return_value = [{"id": 1, "subject": "T1"}]
        args = SimpleNamespace(config=None, dry=False, run=False)
        result = cmd_poll(args)
        assert result == 0


class TestCmdDaemon:
    @patch("golem.cli.asyncio")
    @patch("golem.cli.setup_daemon_tee")
    @patch("golem.cli.write_pid")
    @patch("golem.cli.remove_pid")
    @patch("golem.cli.read_pid", return_value=None)
    @patch("golem.cli.load_config")
    def test_foreground_mode(
        self,
        mock_config,
        mock_read_pid,
        mock_remove,
        mock_write,
        mock_tee,
        mock_asyncio,
    ):
        mock_tee.return_value = ("/var/log/test.log", MagicMock())
        mock_asyncio.run.return_value = 0

        args = SimpleNamespace(
            config=None,
            log_dir=None,
            pid_file=None,
            foreground=True,
            port=None,
        )
        result = cmd_daemon(args)
        assert result == 0
        mock_write.assert_called_once()
        mock_remove.assert_called_once()

    @patch("os.kill", side_effect=OSError)
    @patch("golem.cli.remove_pid")
    @patch("golem.cli.read_pid", return_value=9999)
    @patch("golem.cli.load_config")
    def test_stale_pid_removed(
        self, mock_config, mock_read_pid, mock_remove, mock_kill
    ):
        args = SimpleNamespace(
            config=None,
            log_dir=None,
            pid_file=None,
            foreground=True,
            port=None,
        )
        with (
            patch("golem.cli.setup_daemon_tee", return_value=("/log", MagicMock())),
            patch("golem.cli.write_pid"),
            patch("golem.cli.asyncio") as mock_asyncio,
        ):
            mock_asyncio.run.return_value = 0
            result = cmd_daemon(args)
        assert result == 0
        mock_remove.assert_called()

    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=9999)
    @patch("golem.cli.load_config")
    def test_already_running(self, mock_config, mock_read_pid, mock_kill, capsys):
        args = SimpleNamespace(
            config=None,
            log_dir=None,
            pid_file=None,
            foreground=True,
            port=None,
        )
        result = cmd_daemon(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "already running" in err

    @patch("golem.cli.update_latest_symlink")
    @patch("golem.cli.daemonize")
    @patch("golem.cli.asyncio")
    @patch("golem.cli.write_pid")
    @patch("golem.cli.remove_pid")
    @patch("golem.cli.read_pid", return_value=None)
    @patch("golem.cli.load_config")
    def test_background_mode(
        self,
        mock_config,
        mock_read_pid,
        mock_remove,
        mock_write,
        mock_asyncio,
        mock_daemonize,
        mock_symlink,
        tmp_path,
    ):
        mock_asyncio.run.return_value = 0

        args = SimpleNamespace(
            config=None,
            log_dir=str(tmp_path),
            pid_file=None,
            foreground=False,
            port=None,
        )
        result = cmd_daemon(args)
        assert result == 0
        mock_daemonize.assert_called_once()
        mock_write.assert_called_once()
        mock_remove.assert_called_once()


class TestWaitForExit:
    @patch("time.sleep")
    @patch("os.kill", side_effect=OSError)
    def test_exits_immediately(self, mock_kill, mock_sleep):
        assert _wait_for_exit(123, 5) is True

    @patch("time.sleep")
    @patch("os.kill")
    def test_never_exits(self, mock_kill, mock_sleep):
        assert _wait_for_exit(123, 3) is False
        assert mock_sleep.call_count == 3

    @patch("time.sleep")
    @patch("os.kill", side_effect=[None, OSError])
    def test_exits_after_one_tick(self, mock_kill, mock_sleep):
        assert _wait_for_exit(123, 5) is True
        assert mock_sleep.call_count == 2


class TestCmdStopSignalPaths:
    @patch("golem.cli._wait_for_exit", return_value=True)
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_graceful_stop(self, mock_read, mock_kill, mock_remove, mock_wait, capsys):
        mock_kill_calls = []

        def track_kill(pid, sig):
            mock_kill_calls.append((pid, sig))

        mock_kill.side_effect = track_kill

        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "stopped" in out.lower()

    @patch("golem.cli._wait_for_exit", return_value=True)
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_force_stop(self, mock_read, mock_kill, mock_remove, mock_wait, capsys):
        mock_kill.side_effect = lambda pid, sig: None
        args = SimpleNamespace(dashboard=False, pid_file=None, force=True)
        result = cmd_stop(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "SIGKILL" in out

    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_kill_fails(self, mock_read, mock_kill, mock_remove, capsys):
        def side(pid, sig):
            if sig == 0:
                return
            raise OSError("permission denied")

        mock_kill.side_effect = side

        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "Failed" in err

    @patch("golem.cli._wait_for_exit", side_effect=[False, True])
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_escalate_to_sigkill(
        self, mock_read, mock_kill, mock_remove, mock_wait, capsys
    ):
        mock_kill.side_effect = lambda pid, sig: None
        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "SIGKILL" in out
        assert "killed" in out.lower()

    @patch("golem.cli._wait_for_exit", return_value=False)
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_did_not_exit(self, mock_read, mock_kill, mock_remove, mock_wait, capsys):
        mock_kill.side_effect = lambda pid, sig: None
        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "did not exit" in err

    @patch("golem.cli._wait_for_exit", return_value=False)
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_sigkill_fails_oserror(
        self, mock_read, mock_kill, mock_remove, mock_wait, capsys
    ):
        call_count = [0]

        def side(pid, sig):
            call_count[0] += 1
            if sig == 0:
                return
            if sig == signal.SIGKILL:
                raise OSError("no such process")

        mock_kill.side_effect = side

        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 1

    @patch("golem.cli._wait_for_exit", return_value=True)
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_dashboard_stop(self, mock_read, mock_kill, mock_remove, mock_wait, capsys):
        mock_kill.side_effect = lambda pid, sig: None
        args = SimpleNamespace(dashboard=True, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "Dashboard" in out


class TestCmdStatus:
    @patch("golem.core.dashboard.format_status_text", return_value="Status OK")
    def test_basic(self, mock_format, capsys):
        args = SimpleNamespace(hours=24, config=None)
        result = cmd_status(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "Status OK" in out

    @patch("golem.core.dashboard.format_status_text", return_value="48h status")
    def test_custom_hours(self, mock_format, capsys):
        args = SimpleNamespace(hours=48, config=None)
        cmd_status(args)
        mock_format.assert_called_once_with(since_hours=48, flow="golem")


class TestCmdDashboard:
    @patch("golem.cli.FASTAPI_AVAILABLE", False)
    def test_no_fastapi(self, capsys):
        args = SimpleNamespace(config=None, port=None)
        result = cmd_dashboard(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "FastAPI" in err

    @patch("golem.cli.FASTAPI_AVAILABLE", True)
    def test_runs_dashboard(self, capsys):
        cfg = MagicMock()
        cfg.dashboard.port = 9090

        mock_app = MagicMock()
        mock_uvi = MagicMock()

        with (
            patch("golem.cli.load_config", return_value=cfg),
            patch("uvicorn.run", mock_uvi),
            patch("uvicorn.Config", MagicMock()),
            patch("fastapi.FastAPI", return_value=mock_app),
            patch("golem.core.dashboard.config_to_snapshot", return_value={}),
            patch("golem.core.dashboard.mount_dashboard"),
            patch("golem.core.control_api.control_router", MagicMock()),
            patch("socket.getfqdn", return_value="testhost"),
        ):
            result = cmd_dashboard(args=SimpleNamespace(config=None, port=None))

        assert result == 0
        mock_uvi.assert_called_once()

    @patch("golem.cli.FASTAPI_AVAILABLE", True)
    def test_config_load_failure(self, capsys):
        mock_app = MagicMock()
        mock_uvi = MagicMock()

        with (
            patch("golem.cli.load_config", side_effect=RuntimeError("bad")),
            patch("uvicorn.run", mock_uvi),
            patch("fastapi.FastAPI", return_value=mock_app),
            patch("golem.core.dashboard.config_to_snapshot", return_value={}),
            patch("golem.core.dashboard.mount_dashboard"),
            patch("golem.core.control_api.control_router", MagicMock()),
            patch("golem.core.live_state.DEFAULT_LIVE_STATE_FILE", "/tmp/ls.json"),
            patch("socket.getfqdn", return_value="testhost"),
        ):
            result = cmd_dashboard(args=SimpleNamespace(config=None, port=8888))

        assert result == 0
        assert mock_uvi.call_args[1]["port"] == 8888

    @patch("golem.cli.FASTAPI_AVAILABLE", True)
    def test_control_router_none(self, capsys):
        cfg = MagicMock()
        cfg.dashboard.port = 9090

        mock_app = MagicMock()
        mock_uvi = MagicMock()

        with (
            patch("golem.cli.load_config", return_value=cfg),
            patch("uvicorn.run", mock_uvi),
            patch("fastapi.FastAPI", return_value=mock_app),
            patch("golem.core.dashboard.config_to_snapshot", return_value={}),
            patch("golem.core.dashboard.mount_dashboard"),
            patch("golem.core.control_api.control_router", None),
            patch("socket.getfqdn", return_value="testhost"),
        ):
            result = cmd_dashboard(args=SimpleNamespace(config=None, port=None))

        assert result == 0
        mock_app.include_router.assert_not_called()


class TestMainArgparseExtended:
    @patch("golem.cli.cmd_daemon", return_value=0)
    def test_daemon_command(self, mock_daemon):
        with patch("sys.argv", ["golem", "daemon", "--foreground"]):
            result = main()
        assert result == 0
        mock_daemon.assert_called_once()

    @patch("golem.cli.cmd_stop", return_value=0)
    def test_stop_command(self, mock_stop):
        with patch("sys.argv", ["golem", "stop"]):
            result = main()
        assert result == 0

    @patch("golem.cli.cmd_status", return_value=0)
    def test_status_command(self, mock_status):
        with patch("sys.argv", ["golem", "status"]):
            result = main()
        assert result == 0

    @patch("golem.cli.cmd_dashboard", return_value=0)
    def test_dashboard_command(self, mock_dash):
        with patch("sys.argv", ["golem", "dashboard"]):
            result = main()
        assert result == 0

    @patch("golem.cli.cmd_run", return_value=0)
    def test_run_with_prompt(self, mock_run):
        with patch("sys.argv", ["golem", "run", "-p", "hello"]):
            result = main()
        assert result == 0
        call_args = mock_run.call_args[0][0]
        assert call_args.prompt == "hello"

    @patch("golem.cli.cmd_run", return_value=0)
    def test_run_with_mcp(self, mock_run):
        with patch("sys.argv", ["golem", "run", "-p", "task", "--mcp"]):
            result = main()
        assert result == 0
        call_args = mock_run.call_args[0][0]
        assert call_args.mcp is True

    @patch("golem.cli.cmd_run", return_value=0)
    def test_run_with_no_mcp(self, mock_run):
        with patch("sys.argv", ["golem", "run", "-p", "task", "--no-mcp"]):
            result = main()
        assert result == 0
        call_args = mock_run.call_args[0][0]
        assert call_args.mcp is False

    @patch("golem.cli.cmd_stop", return_value=0)
    def test_stop_with_force(self, mock_stop):
        with patch("sys.argv", ["golem", "stop", "--force"]):
            main()
        call_args = mock_stop.call_args[0][0]
        assert call_args.force is True

    @patch("golem.cli.cmd_stop", return_value=0)
    def test_stop_dashboard(self, mock_stop):
        with patch("sys.argv", ["golem", "stop", "--dashboard"]):
            main()
        call_args = mock_stop.call_args[0][0]
        assert call_args.dashboard is True


class TestMakeEventHandlerSaveFailure:
    def test_save_exception_suppressed(self):
        tracker = MagicMock()
        mock_milestone = MagicMock()
        tracker.handle_event.return_value = mock_milestone
        tracker.state.milestone_count = 1
        tracker.state.tools_called = []
        tracker.state.mcp_tools_called = []
        tracker.state.last_activity = ""
        tracker.state.event_log = []
        printer = MagicMock()

        session = TaskSession(parent_issue_id=1)
        handler = _make_event_handler(
            tracker, printer, session=session, start_time=time.time()
        )

        with patch("golem.cli._save_cli_session", side_effect=OSError("disk fail")):
            handler({"type": "assistant"})

        assert session.milestone_count == 1


class TestRunIssueOnEvent:
    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    @patch("golem.cli._get_profile")
    def test_on_event_forwards_to_printer(
        self,
        mock_profile,
        mock_header,
        mock_save,
        mock_printer_cls,
        mock_orch_cls,
        mock_asyncio,
    ):
        profile = MagicMock()
        profile.name = "prompt"
        profile.task_source.get_task_subject.return_value = "subj"
        mock_profile.return_value = profile

        config = MagicMock()
        tc = MagicMock()
        tc.budget_per_task_usd = 5.0
        config.get_flow_config.return_value = tc

        printer_instance = MagicMock()
        mock_printer_cls.return_value = printer_instance

        captured_event_cb = None

        def capture_orch(**kwargs):
            nonlocal captured_event_cb
            captured_event_cb = kwargs.get("event_callback")
            orch = MagicMock()

            async def noop():
                pass

            orch.run_once = noop
            return orch

        mock_orch_cls.side_effect = capture_orch
        mock_asyncio.run = lambda coro: asyncio.new_event_loop().run_until_complete(
            coro
        )

        run_issue(1, config)

        assert captured_event_cb is not None
        captured_event_cb({"type": "test"})
        printer_instance.handle.assert_called_once_with({"type": "test"})


class TestStartDashboardServerAsync:
    async def test_creates_server_task(self):
        mock_app = MagicMock()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock()

        mock_uvi_config = MagicMock()

        with (
            patch("uvicorn.Config", return_value=mock_uvi_config),
            patch("uvicorn.Server", return_value=mock_server),
            patch("fastapi.FastAPI", return_value=mock_app),
            patch("golem.core.dashboard.mount_dashboard"),
            patch("golem.core.control_api.control_router", MagicMock()),
            patch("socket.getfqdn", return_value="test.local"),
        ):
            from golem.cli import _start_dashboard_server

            task = await _start_dashboard_server(8080, config_snapshot={"k": "v"})

        assert task is not None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_with_live_state_file(self):
        mock_app = MagicMock()
        mock_server = MagicMock()
        mock_server.serve = AsyncMock()

        with (
            patch("uvicorn.Config", return_value=MagicMock()),
            patch("uvicorn.Server", return_value=mock_server),
            patch("fastapi.FastAPI", return_value=mock_app),
            patch("golem.core.dashboard.mount_dashboard") as mock_mount,
            patch("golem.core.control_api.control_router", None),
            patch("socket.getfqdn", return_value="test.local"),
        ):
            from golem.cli import _start_dashboard_server

            task = await _start_dashboard_server(
                9090, config_snapshot=None, live_state_file=Path("/tmp/ls.json")
            )

        mock_mount.assert_called_once()
        call_kwargs = mock_mount.call_args[1]
        assert call_kwargs["live_state_file"] == Path("/tmp/ls.json")
        mock_app.include_router.assert_not_called()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
