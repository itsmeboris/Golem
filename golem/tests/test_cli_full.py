# pylint: disable=too-few-public-methods,not-callable,too-many-lines
"""Tests for golem.cli — full coverage."""

import asyncio
import contextlib
import json
import os
import signal
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.cli import (
    _cmd_run_file,
    _cmd_run_prompt,
    _daemon_health,
    _ensure_daemon,
    _get_profile,
    _make_event_handler,
    _manage_golem_tick,
    _pid_from_health,
    _submit_to_daemon,
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
        _mock_header,
        mock_save,
        _mock_printer,
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

        # Verify orchestrator received the session, config, and profile
        orch_kwargs = mock_orch_cls.call_args.kwargs
        assert orch_kwargs["config"] is config
        assert orch_kwargs["profile"] is profile
        assert orch_kwargs["task_config"] is tc
        session_arg = orch_kwargs["session"]
        assert session_arg.parent_issue_id == 1

        # Verify _save_cli_session was called with the same session object
        first_save_call = mock_save.call_args_list[0]
        assert first_save_call.args[0] is session_arg

    @patch("golem.cli._print_run_header")
    @patch("golem.cli._get_profile")
    def test_dry_run(self, mock_profile, _mock_header, capsys):
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
        _mock_header,
        mock_save,
        _mock_printer,
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

        # Verify orchestrator was called with config and the profile
        assert mock_orch_cls.called
        orch_kwargs = mock_orch_cls.call_args.kwargs
        assert orch_kwargs["config"] is config
        session_arg = orch_kwargs["session"]
        assert session_arg.parent_issue_id == 1

        # Verify save was called with the session object
        assert mock_save.called
        first_save_call = mock_save.call_args_list[0]
        assert first_save_call.args[0] is session_arg

    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    def test_mcp_disable_override(
        self,
        _mock_header,
        mock_save,
        _mock_printer,
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

        # Verify orchestrator was called with config and the session
        assert mock_orch_cls.called
        orch_kwargs = mock_orch_cls.call_args.kwargs
        assert orch_kwargs["config"] is config
        session_arg = orch_kwargs["session"]
        assert session_arg.parent_issue_id == 1

        # Verify save was called with the session object
        assert mock_save.called
        first_save_call = mock_save.call_args_list[0]
        assert first_save_call.args[0] is session_arg

    @patch("golem.cli.asyncio")
    @patch("golem.cli.TaskOrchestrator")
    @patch("golem.cli._StreamPrinter")
    @patch("golem.cli._save_cli_session")
    @patch("golem.cli._print_run_header")
    @patch("golem.cli._get_profile")
    def test_failed_session(
        self,
        mock_profile,
        _mock_header,
        mock_save,
        _mock_printer,
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

        # Verify orchestrator was called with the expected arguments
        assert mock_orch_cls.called
        orch_kwargs = mock_orch_cls.call_args.kwargs
        assert orch_kwargs["config"] is config
        assert orch_kwargs["profile"] is profile
        assert orch_kwargs["task_config"] is None
        session_arg = orch_kwargs["session"]
        assert session_arg.parent_issue_id == 1

        # Verify _save_cli_session was called with the session object
        assert mock_save.called
        first_save_call = mock_save.call_args_list[0]
        assert first_save_call.args[0] is session_arg


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
        _mock_header,
        _mock_save,
        _mock_printer,
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
        _mock_header,
        _mock_save,
        _mock_printer,
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
        _mock_header,
        mock_save,
        _mock_printer,
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

    def test_forwards_reload_event(self):
        config = MagicMock()
        tc = MagicMock()
        tc.enabled = True
        config.get_flow_config.return_value = tc

        mock_flow = MagicMock()
        mock_flow.start_tick_loop.return_value = MagicMock()
        reload_event = asyncio.Event()

        tasks = []
        with patch("golem.flow.GolemFlow", return_value=mock_flow) as mock_cls:
            _manage_golem_tick(config, tasks, reload_event=reload_event)

        mock_cls.assert_called_once_with(config, reload_event=reload_event)


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
        args = SimpleNamespace(port=8080, config="config.yaml")
        config = MagicMock()
        config.dashboard.port = 8080
        config.daemon.drain_timeout_seconds = 300

        mock_flow = MagicMock()
        mock_flow._self_update = None

        tick_task = asyncio.ensure_future(asyncio.sleep(100))

        def fake_manage(_cfg, tasks, reload_event=None):
            tasks.append(tick_task)
            return mock_flow

        async def fake_dash(*_a, **_kw):
            return asyncio.ensure_future(asyncio.sleep(100)), MagicMock()

        with (
            patch("golem.cli.LiveState", create=True) as mock_ls,
            patch("golem.cli._manage_golem_tick", side_effect=fake_manage),
            patch("golem.cli._start_dashboard_server", side_effect=fake_dash),
            patch("golem.cli.config_to_snapshot", return_value={}, create=True),
            patch("golem.cli.wire_control_api") as _mock_wire,
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

    async def test_sighup_handler_registered(self):
        """SIGHUP handler is registered to set reload_event."""
        args = SimpleNamespace(port=8080, config="config.yaml")
        config = MagicMock()
        config.dashboard.port = 8080
        config.daemon.drain_timeout_seconds = 300

        mock_flow = MagicMock()
        mock_flow._self_update = None

        def fake_manage(_cfg, tasks, reload_event=None):
            tasks.append(asyncio.ensure_future(asyncio.sleep(100)))
            return mock_flow

        async def fake_dash(*_a, **_kw):
            return asyncio.ensure_future(asyncio.sleep(100)), MagicMock()

        signal_handlers = {}

        def capture_signal(sig, handler):
            signal_handlers[sig] = handler

        with (
            patch("golem.cli.LiveState", create=True) as mock_ls,
            patch("golem.cli._manage_golem_tick", side_effect=fake_manage),
            patch("golem.cli._start_dashboard_server", side_effect=fake_dash),
            patch("golem.cli.config_to_snapshot", return_value={}, create=True),
            patch("golem.cli.wire_control_api"),
        ):
            mock_live = MagicMock()
            mock_ls.get.return_value = mock_live

            loop = asyncio.get_event_loop()
            orig_add = loop.add_signal_handler
            loop.add_signal_handler = capture_signal
            try:
                task = asyncio.create_task(run_daemon(args, config))
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            finally:
                loop.add_signal_handler = orig_add

        assert signal.SIGHUP in signal_handlers

    async def test_wire_control_api_receives_reload_params(self):
        """wire_control_api is called with config_path, reload_event, self_update_manager."""
        args = SimpleNamespace(port=8080, config="myconfig.yaml")
        config = MagicMock()
        config.dashboard.port = 8080
        config.dashboard.api_key = "testkey"
        config.daemon.drain_timeout_seconds = 300

        mock_flow = MagicMock()
        mock_self_update = MagicMock()
        mock_flow._self_update = mock_self_update

        def fake_manage(_cfg, tasks, reload_event=None):
            tasks.append(asyncio.ensure_future(asyncio.sleep(100)))
            return mock_flow

        async def fake_dash(*_a, **_kw):
            return asyncio.ensure_future(asyncio.sleep(100)), MagicMock()

        with (
            patch("golem.cli.LiveState", create=True) as mock_ls,
            patch("golem.cli._manage_golem_tick", side_effect=fake_manage),
            patch("golem.cli._start_dashboard_server", side_effect=fake_dash),
            patch("golem.cli.config_to_snapshot", return_value={}, create=True),
            patch("golem.cli.wire_control_api") as mock_wire,
        ):
            mock_live = MagicMock()
            mock_ls.get.return_value = mock_live

            task = asyncio.create_task(run_daemon(args, config))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_wire.assert_called_once()
        call_kwargs = mock_wire.call_args[1]
        assert call_kwargs["config_path"] == "myconfig.yaml"
        assert call_kwargs["reload_event"] is not None
        assert isinstance(call_kwargs["reload_event"], asyncio.Event)
        assert call_kwargs["self_update_manager"] is mock_self_update

    async def test_reload_task_spawned_and_cancelled(self):
        """_handle_reload task is spawned and cancelled on shutdown."""
        args = SimpleNamespace(port=8080, config="config.yaml")
        config = MagicMock()
        config.dashboard.port = 8080
        config.daemon.drain_timeout_seconds = 300

        mock_flow = MagicMock()
        mock_flow._self_update = None

        def fake_manage(_cfg, tasks, reload_event=None):
            tasks.append(asyncio.ensure_future(asyncio.sleep(100)))
            return mock_flow

        async def fake_dash(*_a, **_kw):
            return asyncio.ensure_future(asyncio.sleep(100)), MagicMock()

        created_tasks = []
        original_create_task = asyncio.create_task

        def spy_create_task(coro, **kwargs):
            t = original_create_task(coro, **kwargs)
            created_tasks.append(t)
            return t

        with (
            patch("golem.cli.LiveState", create=True) as mock_ls,
            patch("golem.cli._manage_golem_tick", side_effect=fake_manage),
            patch("golem.cli._start_dashboard_server", side_effect=fake_dash),
            patch("golem.cli.config_to_snapshot", return_value={}, create=True),
            patch("golem.cli.wire_control_api"),
            patch("golem.cli.asyncio.create_task", side_effect=spy_create_task),
        ):
            mock_live = MagicMock()
            mock_ls.get.return_value = mock_live

            task = original_create_task(run_daemon(args, config))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # At least one created_task should be the _handle_reload task
        assert len(created_tasks) >= 1


class TestCmdRunPrompt:
    @patch(
        "golem.cli._submit_to_daemon",
        return_value={"task_id": 123, "status": "submitted"},
    )
    @patch("golem.cli._ensure_daemon")
    @patch("golem.cli.load_config")
    def test_prompt_submits_to_daemon(self, mock_config, mock_ensure, mock_submit):
        cfg = mock_config.return_value
        cfg.dashboard.port = 8082
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="fix the bug",
            file="",
            dry=False,
            subject="",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0
        mock_ensure.assert_called_once()
        mock_submit.assert_called_once()

    @patch("golem.cli._submit_to_daemon", return_value=None)
    @patch("golem.cli._ensure_daemon")
    @patch("golem.cli.load_config")
    def test_prompt_submit_failure(self, mock_config, _mock_ensure, _mock_submit):
        cfg = mock_config.return_value
        cfg.dashboard.port = 8082
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="fail task",
            file="",
            dry=False,
            subject="",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 1

    @patch(
        "golem.cli._submit_to_daemon",
        return_value={"task_id": 1, "status": "submitted"},
    )
    @patch("golem.cli._ensure_daemon")
    @patch("golem.cli.load_config")
    def test_prompt_with_subject(self, mock_config, _mock_ensure, mock_submit):
        cfg = mock_config.return_value
        cfg.dashboard.port = 8082
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="do work",
            file="",
            dry=False,
            subject="Custom subject",
            mcp=None,
        )
        cmd_run(args)
        call_kwargs = mock_submit.call_args[1]
        assert call_kwargs.get("subject") == "Custom subject"


class TestCmdRunPromptDirect:
    @patch(
        "golem.cli._submit_to_daemon",
        return_value={"task_id": 42, "status": "submitted"},
    )
    @patch("golem.cli._ensure_daemon")
    def test_direct_call(self, _mock_ensure, mock_submit):
        args = SimpleNamespace(subject="", config=None)
        config = MagicMock()
        config.dashboard.port = 8082
        result = _cmd_run_prompt(args, config, "hello world")
        assert result == 0
        mock_submit.assert_called_once()

    @patch("golem.cli._submit_to_daemon", return_value=None)
    @patch("golem.cli._ensure_daemon")
    def test_failure(self, _mock_ensure, _mock_submit):
        args = SimpleNamespace(subject="", config=None)
        config = MagicMock()
        config.dashboard.port = 8082
        result = _cmd_run_prompt(args, config, "fail")
        assert result == 1


class TestDaemonHealth:
    def test_healthy(self):
        with patch("golem.cli.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            assert _daemon_health(8082) is True

    def test_unreachable(self):
        import urllib.error

        with patch(
            "golem.cli.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert _daemon_health(8082) is False


class TestPidFromHealth:
    def test_returns_pid(self):
        with patch("golem.cli.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"ok": True, "pid": 4242}).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            assert _pid_from_health(8082) == 4242

    def test_returns_none_on_connection_error(self):
        import urllib.error

        with patch(
            "golem.cli.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            assert _pid_from_health(8082) is None

    def test_returns_none_on_missing_key(self):
        with patch("golem.cli.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps({"ok": True}).encode()
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            assert _pid_from_health(8082) is None

    def test_returns_none_on_invalid_json(self):
        with patch("golem.cli.urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b"not json"
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp

            assert _pid_from_health(8082) is None


class TestEnsureDaemon:
    @patch("golem.cli._daemon_health", return_value=True)
    def test_already_running(self, mock_health):
        args = SimpleNamespace(config=None)
        config = MagicMock()
        _ensure_daemon(args, config, 8082)
        mock_health.assert_called_once_with(8082, timeout=3)

    @patch("golem.cli._daemon_health", side_effect=[False, False, True])
    @patch("golem.cli.read_pid", return_value=None)
    @patch("golem.cli.time.sleep")
    def test_starts_daemon(self, _mock_sleep, _mock_pid, _mock_health, tmp_path):
        with (
            patch("golem.cli.DATA_DIR", tmp_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock()
            args = SimpleNamespace(config=None)
            config = MagicMock()
            _ensure_daemon(args, config, 8082)

        mock_popen.assert_called_once()


class TestSubmitToDaemon:
    def test_success(self):
        resp_data = {"ok": True, "task_id": 123, "status": "submitted"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("golem.cli.urllib.request.urlopen", return_value=mock_resp):
            result = _submit_to_daemon("do stuff", port=8082)

        assert result is not None
        assert result["task_id"] == 123

    def test_http_error(self):
        import urllib.error

        exc = urllib.error.HTTPError(
            "http://x", 500, "err", {}, MagicMock(read=lambda: b"error")
        )
        with patch("golem.cli.urllib.request.urlopen", side_effect=exc):
            result = _submit_to_daemon("fail", port=8082)

        assert result is None

    def test_connection_error(self):
        import urllib.error

        with patch(
            "golem.cli.urllib.request.urlopen",
            side_effect=urllib.error.URLError("refused"),
        ):
            result = _submit_to_daemon("fail", port=8082)

        assert result is None

    def test_with_file_path(self):
        resp_data = {"ok": True, "task_id": 1, "status": "submitted"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch(
            "golem.cli.urllib.request.urlopen", return_value=mock_resp
        ) as mock_open:
            result = _submit_to_daemon("", port=8082, file_path="/tmp/plan.md")

        assert result is not None
        req = mock_open.call_args[0][0]
        payload = json.loads(req.data.decode())
        assert payload["file"] == "/tmp/plan.md"


class TestCmdRunFile:
    @patch(
        "golem.cli._submit_to_daemon",
        return_value={"task_id": 1, "status": "submitted"},
    )
    @patch("golem.cli._ensure_daemon")
    def test_reads_file_and_submits(self, _mock_ensure, mock_submit, tmp_path):
        prompt_file = tmp_path / "plan.md"
        prompt_file.write_text("Do this important thing")

        args = SimpleNamespace(subject="", config=None)
        config = MagicMock()
        config.dashboard.port = 8082
        result = _cmd_run_file(args, config, str(prompt_file))
        assert result == 0
        mock_submit.assert_called_once()

    def test_missing_file(self, capsys):
        args = SimpleNamespace(subject="", config=None)
        config = MagicMock()
        config.dashboard.port = 8082
        result = _cmd_run_file(args, config, "/nonexistent/file.md")
        assert result == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_empty_file(self, tmp_path, capsys):
        empty_file = tmp_path / "empty.md"
        empty_file.write_text("")

        args = SimpleNamespace(subject="", config=None)
        config = MagicMock()
        config.dashboard.port = 8082
        result = _cmd_run_file(args, config, str(empty_file))
        assert result == 1
        err = capsys.readouterr().err
        assert "empty" in err


class TestCmdRunFileViaCmdRun:
    @patch("golem.cli._cmd_run_file", return_value=0)
    @patch("golem.cli.load_config")
    def test_file_flag_routes_to_file_handler(self, _mock_config, mock_file):
        args = SimpleNamespace(
            parent_id=None,
            config=None,
            prompt="",
            file="plan.md",
            dry=False,
            subject="",
            mcp=None,
        )
        result = cmd_run(args)
        assert result == 0
        mock_file.assert_called_once()


class TestEnsureDaemonEdgeCases:
    @patch("golem.cli._daemon_health", side_effect=[False, False] + [False] * 30)
    @patch("golem.cli.read_pid", return_value=9999)
    @patch("os.kill")
    @patch("golem.cli.time.sleep")
    def test_pid_exists_but_health_fails(
        self, _mock_sleep, _mock_kill, _mock_pid, _mock_health, tmp_path, capsys
    ):
        with (
            patch("golem.cli.DATA_DIR", tmp_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock()
            args = SimpleNamespace(config=None)
            config = MagicMock()
            _ensure_daemon(args, config, 8082)

        err = capsys.readouterr().err
        assert "may not be ready" in err

    @patch("golem.cli._daemon_health", side_effect=[False, False] + [False] * 30)
    @patch("golem.cli.read_pid", return_value=9999)
    @patch("os.kill", side_effect=OSError("not running"))
    @patch("golem.cli.remove_pid")
    @patch("golem.cli.time.sleep")
    def test_stale_pid_removed_before_start(
        self,
        _mock_sleep,
        mock_remove,
        _mock_kill,
        _mock_pid,
        _mock_health,
        tmp_path,
    ):
        with (
            patch("golem.cli.DATA_DIR", tmp_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock()
            args = SimpleNamespace(config=None)
            config = MagicMock()
            _ensure_daemon(args, config, 8082)

        mock_remove.assert_called()

    @patch("golem.cli._daemon_health", side_effect=[False, False, True])
    @patch("golem.cli.read_pid", return_value=None)
    @patch("golem.cli.time.sleep")
    def test_with_config_path(self, _mock_sleep, _mock_pid, _mock_health, tmp_path):
        with (
            patch("golem.cli.DATA_DIR", tmp_path),
            patch("subprocess.Popen") as mock_popen,
        ):
            mock_popen.return_value = MagicMock()
            args = SimpleNamespace(config="/my/config.yaml")
            config = MagicMock()
            _ensure_daemon(args, config, 8082)

        cmd = mock_popen.call_args[0][0]
        assert "-c" in cmd
        assert "/my/config.yaml" in cmd


class TestSubmitToDaemonEdgeCases:
    def test_with_subject(self):
        resp_data = {"ok": True, "task_id": 1, "status": "submitted"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch(
            "golem.cli.urllib.request.urlopen", return_value=mock_resp
        ) as mock_open:
            _submit_to_daemon("do stuff", port=8082, subject="Custom Subject")

        req = mock_open.call_args[0][0]
        payload = json.loads(req.data.decode())
        assert payload["subject"] == "Custom Subject"

    def test_with_work_dir(self):
        resp_data = {"ok": True, "task_id": 1, "status": "submitted"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch(
            "golem.cli.urllib.request.urlopen", return_value=mock_resp
        ) as mock_open:
            _submit_to_daemon("do stuff", port=8082, work_dir="/path/to/project")

        req = mock_open.call_args[0][0]
        payload = json.loads(req.data.decode())
        assert payload["work_dir"] == "/path/to/project"


class TestCmdPollRunBranch:
    @patch("golem.cli.print_results")
    @patch("golem.cli.run_issue", return_value=True)
    @patch("golem.cli.poll_for_agent_issues")
    @patch("golem.cli.load_config")
    def test_run_executes_all(self, _mock_config, mock_poll, mock_run, _mock_print):
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
    def test_partial_failure(self, _mock_config, mock_poll, _mock_run, _mock_print):
        mock_poll.return_value = [
            {"id": 1, "subject": "T1"},
            {"id": 2, "subject": "T2"},
        ]
        args = SimpleNamespace(config=None, dry=False, run=True)
        result = cmd_poll(args)
        assert result == 1

    @patch("golem.cli.poll_for_agent_issues")
    @patch("golem.cli.load_config")
    def test_no_run_no_dry(self, _mock_config, mock_poll):
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
        _mock_config,
        _mock_read_pid,
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
        self, _mock_config, _mock_read_pid, mock_remove, _mock_kill
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
    def test_already_running(self, _mock_config, _mock_read_pid, _mock_kill, capsys):
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

    @patch("golem.cli.setup_daemon_tee")
    @patch("golem.cli.asyncio")
    @patch("golem.cli.write_pid")
    @patch("golem.cli.remove_pid")
    @patch("golem.cli.load_config")
    def test_own_pid_skips_already_running(
        self, _mock_config, _mock_remove, mock_write, mock_asyncio, mock_tee, tmp_path
    ):
        """After os.execv the PID file contains our own PID — should NOT abort."""
        own_pid = os.getpid()
        with patch("golem.cli.read_pid", return_value=own_pid):
            mock_asyncio.run.return_value = 0
            mock_tee.return_value = (tmp_path / "log", lambda: None)
            args = SimpleNamespace(
                config=None,
                log_dir=str(tmp_path),
                pid_file=None,
                foreground=True,
                port=None,
            )
            result = cmd_daemon(args)
        assert result == 0
        mock_write.assert_called_once()

    @patch("golem.cli.update_latest_symlink")
    @patch("golem.cli.daemonize")
    @patch("golem.cli.asyncio")
    @patch("golem.cli.write_pid")
    @patch("golem.cli.remove_pid")
    @patch("golem.cli.read_pid", return_value=None)
    @patch("golem.cli.load_config")
    def test_background_mode(
        self,
        _mock_config,
        _mock_read_pid,
        mock_remove,
        mock_write,
        mock_asyncio,
        mock_daemonize,
        _mock_symlink,
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
    def test_exits_immediately(self, _mock_kill, _mock_sleep):
        assert _wait_for_exit(123, 5) is True

    @patch("time.sleep")
    @patch("os.kill")
    def test_never_exits(self, _mock_kill, mock_sleep):
        assert _wait_for_exit(123, 3) is False
        assert mock_sleep.call_count == 3

    @patch("time.sleep")
    @patch("os.kill", side_effect=[None, OSError])
    def test_exits_after_one_tick(self, _mock_kill, mock_sleep):
        assert _wait_for_exit(123, 5) is True
        assert mock_sleep.call_count == 2


class TestCmdStopSignalPaths:
    @patch("golem.cli._wait_for_exit", return_value=True)
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_graceful_stop(
        self, _mock_read, mock_kill, _mock_remove, _mock_wait, capsys
    ):
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
    def test_force_stop(self, _mock_read, mock_kill, _mock_remove, _mock_wait, capsys):
        mock_kill.side_effect = lambda pid, sig: None
        args = SimpleNamespace(dashboard=False, pid_file=None, force=True)
        result = cmd_stop(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "SIGKILL" in out

    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_kill_fails(self, _mock_read, mock_kill, _mock_remove, capsys):
        def side(_pid, sig):
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
        self, _mock_read, mock_kill, _mock_remove, _mock_wait, capsys
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
    def test_did_not_exit(
        self, _mock_read, mock_kill, _mock_remove, _mock_wait, capsys
    ):
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
        self, _mock_read, mock_kill, _mock_remove, _mock_wait
    ):
        call_count = [0]

        def side(_pid, sig):
            call_count[0] += 1
            if sig == 0:
                return
            if sig == signal.SIGKILL:
                raise OSError("no such process")

        mock_kill.side_effect = side

        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 1

    @patch("golem.cli._wait_for_exit", return_value=False)
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_sigkill_fails_oserror_logs_debug(
        self, _mock_read, mock_kill, _mock_remove, _mock_wait, caplog
    ):
        """SIGKILL failure is logged at debug level."""
        import logging

        def side(_pid, sig):
            if sig == 0:
                return
            if sig == signal.SIGKILL:
                raise OSError("no such process")

        mock_kill.side_effect = side

        args = SimpleNamespace(dashboard=False, pid_file=None, force=False)
        with caplog.at_level(logging.DEBUG, logger="golem.cli"):
            result = cmd_stop(args)

        assert result == 1
        assert any(
            "SIGKILL failed" in r.message and r.levelno == logging.DEBUG
            for r in caplog.records
        )

    @patch("golem.cli._wait_for_exit", return_value=True)
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli.read_pid", return_value=5555)
    def test_dashboard_stop(
        self, _mock_read, mock_kill, _mock_remove, _mock_wait, capsys
    ):
        mock_kill.side_effect = lambda pid, sig: None
        args = SimpleNamespace(dashboard=True, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "Dashboard" in out

    @patch("golem.cli._wait_for_exit", return_value=True)
    @patch("golem.cli.remove_pid")
    @patch("os.kill")
    @patch("golem.cli._pid_from_health", return_value=7777)
    @patch("golem.cli.load_config")
    @patch("golem.cli.read_pid", return_value=None)
    def test_stop_falls_back_to_health_endpoint(
        self,
        _mock_read,
        mock_cfg,
        _mock_health,
        mock_kill,
        _mock_remove,
        _mock_wait,
        capsys,
    ):
        mock_cfg.return_value = MagicMock(dashboard=MagicMock(port=8081))
        mock_kill.side_effect = lambda pid, sig: None
        args = SimpleNamespace(dashboard=False, pid_file=None, force=False, config=None)
        result = cmd_stop(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "recovered PID 7777" in out

    @patch("golem.cli._pid_from_health", return_value=None)
    @patch("golem.cli.load_config")
    @patch("golem.cli.read_pid", return_value=None)
    def test_stop_no_pid_file_no_health(
        self, _mock_read, mock_cfg, _mock_health, capsys
    ):
        mock_cfg.return_value = MagicMock(dashboard=MagicMock(port=8081))
        args = SimpleNamespace(dashboard=False, pid_file=None, force=False, config=None)
        result = cmd_stop(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "PID file" in err

    @patch("golem.cli.read_pid", return_value=None)
    def test_dashboard_stop_no_health_fallback(self, _mock_read):
        """Dashboard stop does not attempt health endpoint fallback."""
        args = SimpleNamespace(dashboard=True, pid_file=None, force=False)
        result = cmd_stop(args)
        assert result == 1


class TestCmdStatus:
    @patch("golem.core.dashboard.format_status_text", return_value="Status OK")
    def test_basic(self, _mock_format, capsys):
        args = SimpleNamespace(hours=24, config=None)
        result = cmd_status(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "Status OK" in out

    @patch("golem.core.dashboard.format_status_text", return_value="48h status")
    def test_custom_hours(self, mock_format):
        args = SimpleNamespace(hours=48, config=None)
        cmd_status(args)
        mock_format.assert_called_once_with(since_hours=48, flow="golem")

    @patch(
        "golem.core.dashboard.format_task_detail_text", return_value="Task #5 detail"
    )
    def test_task_detail(self, mock_detail, capsys):
        args = SimpleNamespace(hours=24, task=5, watch=None, config=None)
        result = cmd_status(args)
        assert result == 0
        mock_detail.assert_called_once_with(5)
        assert "Task #5 detail" in capsys.readouterr().out

    @patch("golem.core.dashboard.format_status_text", return_value="watch output")
    def test_watch_mode(self, _mock_format, capsys):
        """Watch mode prints with ANSI clear, then exits on KeyboardInterrupt."""
        with patch("time.sleep", side_effect=KeyboardInterrupt):
            args = SimpleNamespace(hours=24, task=None, watch=2.0, config=None)
            result = cmd_status(args)
        assert result == 0
        out = capsys.readouterr().out
        assert "watch output" in out
        assert "\033[2J\033[H" in out

    @patch("golem.core.dashboard.format_status_text", return_value="clamp test")
    def test_watch_clamps_interval(self, _mock_format):
        """Watch interval is clamped to minimum 0.5s."""
        with patch("time.sleep", side_effect=KeyboardInterrupt) as mock_sleep:
            args = SimpleNamespace(hours=24, task=None, watch=0.1, config=None)
            cmd_status(args)
        mock_sleep.assert_called_once_with(0.5)


class TestCmdDashboard:
    @patch("golem.cli.FASTAPI_AVAILABLE", False)
    def test_no_fastapi(self, capsys):
        args = SimpleNamespace(config=None, port=None)
        result = cmd_dashboard(args)
        assert result == 1
        err = capsys.readouterr().err
        assert "FastAPI" in err

    @patch("golem.cli.FASTAPI_AVAILABLE", True)
    def test_runs_dashboard(self):
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
    def test_config_load_failure(self):
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
    def test_control_router_none(self):
        cfg = MagicMock()
        cfg.dashboard.port = 9090

        mock_app = MagicMock()
        mock_uvi = MagicMock()

        with (
            patch("golem.cli.load_config", return_value=cfg),
            patch("uvicorn.run", mock_uvi),
            patch("fastapi.FastAPI", return_value=mock_app),
            patch("golem.core.dashboard.mount_dashboard"),
            patch("golem.core.control_api.control_router", None),
            patch("golem.core.control_api.health_router", None),
            patch("golem.core.control_api.wire_control_api"),
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
    def test_stop_command(self, _mock_stop):
        with patch("sys.argv", ["golem", "stop"]):
            result = main()
        assert result == 0

    @patch("golem.cli.cmd_status", return_value=0)
    def test_status_command(self, _mock_status):
        with patch("sys.argv", ["golem", "status"]):
            result = main()
        assert result == 0

    @patch("golem.cli.cmd_dashboard", return_value=0)
    def test_dashboard_command(self, _mock_dash):
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
    def test_run_with_file(self, mock_run):
        with patch("sys.argv", ["golem", "run", "-f", "plan.md"]):
            result = main()
        assert result == 0
        call_args = mock_run.call_args[0][0]
        assert call_args.file == "plan.md"

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

    @patch("golem.cli.cmd_status", return_value=0)
    def test_status_watch(self, mock_status):
        with patch("sys.argv", ["golem", "status", "--watch"]):
            result = main()
        assert result == 0
        # Verify watch arg was parsed (const=2.0)
        call_args = mock_status.call_args[0][0]
        assert call_args.watch == 2.0

    @patch("golem.cli.cmd_status", return_value=0)
    def test_status_watch_custom(self, mock_status):
        with patch("sys.argv", ["golem", "status", "--watch", "5"]):
            result = main()
        assert result == 0
        call_args = mock_status.call_args[0][0]
        assert call_args.watch == 5.0

    @patch("golem.cli.cmd_status", return_value=0)
    def test_status_task(self, mock_status):
        with patch("sys.argv", ["golem", "status", "--task", "42"]):
            result = main()
        assert result == 0
        call_args = mock_status.call_args[0][0]
        assert call_args.task == 42


class TestControlApiWiring:
    def test_wire_control_api_sets_flow(self):
        from golem.core.control_api import wire_control_api

        mock_flow = MagicMock()
        wire_control_api(golem_flow=mock_flow)

        from golem.core import control_api

        assert control_api._golem_flow is mock_flow
        wire_control_api(golem_flow=None)

    def test_health_endpoint(self):
        from golem.core.control_api import health_check

        result = asyncio.run(health_check())
        assert result["ok"] is True
        assert "pid" in result
        assert "uptime_seconds" in result

    def test_health_endpoint_includes_metrics_when_wired(self):
        from golem.core.control_api import health_check, wire_control_api

        mock_flow = MagicMock()
        mock_flow.health.snapshot.return_value = {"status": "healthy"}
        wire_control_api(golem_flow=mock_flow)
        result = asyncio.run(health_check())
        assert result["health"] == {"status": "healthy"}
        wire_control_api(golem_flow=None)

    def test_submit_without_flow_raises(self):
        from golem.core.control_api import submit_task, wire_control_api
        from fastapi import HTTPException

        wire_control_api(golem_flow=None)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"prompt": "test"})

        with patch("golem.core.control_api._golem_flow", None):
            with pytest.raises(HTTPException) as exc_info:
                asyncio.run(submit_task(mock_request))
            assert exc_info.value.status_code == 503

    def test_submit_with_prompt(self):
        from golem.core.control_api import submit_task, wire_control_api

        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 42, "status": "submitted"}
        wire_control_api(golem_flow=mock_flow)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"prompt": "do stuff"})

        result = asyncio.run(submit_task(mock_request))
        assert result["ok"] is True
        assert result["task_id"] == 42
        mock_flow.submit_task.assert_called_once_with(
            prompt="do stuff", subject="", work_dir=""
        )

        wire_control_api(golem_flow=None)

    def test_submit_with_file(self, tmp_path):
        from golem.core.control_api import submit_task, wire_control_api

        prompt_file = tmp_path / "plan.md"
        prompt_file.write_text("detailed plan here")

        mock_flow = MagicMock()
        mock_flow.submit_task.return_value = {"task_id": 99, "status": "submitted"}
        wire_control_api(golem_flow=mock_flow)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"file": str(prompt_file)})

        result = asyncio.run(submit_task(mock_request))
        assert result["ok"] is True
        call_kwargs = mock_flow.submit_task.call_args[1]
        assert "detailed plan here" in call_kwargs["prompt"]

        wire_control_api(golem_flow=None)

    def test_submit_missing_prompt_and_file(self):
        from golem.core.control_api import submit_task, wire_control_api
        from fastapi import HTTPException

        mock_flow = MagicMock()
        wire_control_api(golem_flow=mock_flow)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={})

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(submit_task(mock_request))
        assert exc_info.value.status_code == 400

        wire_control_api(golem_flow=None)

    def test_submit_nonexistent_file(self):
        from golem.core.control_api import submit_task, wire_control_api
        from fastapi import HTTPException

        mock_flow = MagicMock()
        wire_control_api(golem_flow=mock_flow)

        mock_request = MagicMock()
        mock_request.json = AsyncMock(return_value={"file": "/nonexistent/path.md"})

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(submit_task(mock_request))
        assert exc_info.value.status_code == 400

        wire_control_api(golem_flow=None)


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
        _mock_header,
        _mock_save,
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

            task, server = await _start_dashboard_server(
                8080, config_snapshot={"k": "v"}
            )

        assert task is not None
        assert server is mock_server
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_cancellation_signals_graceful_exit(self):
        """Cancelling the dashboard task sets server.should_exit."""
        mock_app = MagicMock()
        mock_server = MagicMock()
        mock_server.should_exit = False

        async def _hang():
            await asyncio.Event().wait()

        mock_server.serve = _hang

        with (
            patch("uvicorn.Config", return_value=MagicMock()),
            patch("uvicorn.Server", return_value=mock_server),
            patch("fastapi.FastAPI", return_value=mock_app),
            patch("golem.core.dashboard.mount_dashboard"),
            patch("golem.core.control_api.control_router", MagicMock()),
            patch("socket.getfqdn", return_value="test.local"),
        ):
            from golem.cli import _start_dashboard_server

            task, server = await _start_dashboard_server(8080)

        assert server is mock_server
        await asyncio.sleep(0)  # let the task start
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        assert mock_server.should_exit is True

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
            patch("golem.core.control_api.health_router", None),
            patch("socket.getfqdn", return_value="test.local"),
        ):
            from golem.cli import _start_dashboard_server

            task, _server = await _start_dashboard_server(
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
