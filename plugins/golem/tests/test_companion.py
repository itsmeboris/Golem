"""Tests for plugins/golem/scripts/golem-companion.py.

We import the module as a module after manipulating sys.path, since the file
has a hyphen in its name (not a valid Python identifier for direct import).
We use importlib to load it.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Load golem-companion.py as a module via importlib
_companion_path = Path(__file__).parent.parent / "scripts" / "golem-companion.py"
_lib_path = _companion_path.parent / "lib"

# Ensure lib is on sys.path before loading the companion module
if str(_lib_path) not in sys.path:
    sys.path.insert(0, str(_lib_path))

_spec = importlib.util.spec_from_file_location("golem_companion", str(_companion_path))
_companion = importlib.util.module_from_spec(_spec)
sys.modules["golem_companion"] = _companion
_spec.loader.exec_module(_companion)


class TestExtractTaskId:
    @pytest.mark.parametrize(
        "output,expected",
        [
            ("Submitted task #123\n", 123),
            ("task_id: 456\n", 456),
            ("task id: 789\n", 789),
            ("task: 100\n", 100),
            ("101\n", 101),
            ("no match here at all\n", None),
            ("", None),
            ("task #0\n", 0),
            # Multiple lines — first match wins
            ("starting...\nSubmitted task #55\ndone\n", 55),
        ],
        ids=[
            "submitted_task_hash",
            "task_id_colon",
            "task_id_space",
            "task_colon",
            "bare_number",
            "no_match",
            "empty_string",
            "task_zero",
            "multiline",
        ],
    )
    def test_extract_task_id(self, output, expected):
        result = _companion._extract_task_id(output)
        assert result == expected


class TestResolveTracePath:
    def test_resolves_relative_path_within_data_root(self, tmp_path):
        data_root = tmp_path / "data"
        data_root.mkdir()
        trace_file = data_root / "traces" / "session-abc.jsonl"
        trace_file.parent.mkdir(parents=True)
        trace_file.write_text("{}")

        result = _companion._resolve_trace_path("traces/session-abc.jsonl", data_root)
        assert result == trace_file.resolve()

    def test_resolves_absolute_path_within_data_root(self, tmp_path):
        data_root = tmp_path / "data"
        data_root.mkdir()
        trace_file = data_root / "traces" / "session-xyz.jsonl"
        trace_file.parent.mkdir(parents=True)
        trace_file.write_text("{}")

        result = _companion._resolve_trace_path(str(trace_file), data_root)
        assert result == trace_file.resolve()

    def test_returns_none_for_path_traversal_attempt(self, tmp_path):
        data_root = tmp_path / "data"
        data_root.mkdir()

        result = _companion._resolve_trace_path("../../../etc/passwd", data_root)
        assert result is None

    def test_returns_none_for_absolute_path_outside_data_root(self, tmp_path):
        data_root = tmp_path / "data"
        data_root.mkdir()

        result = _companion._resolve_trace_path("/etc/passwd", data_root)
        assert result is None

    def test_returns_none_when_file_does_not_exist(self, tmp_path):
        data_root = tmp_path / "data"
        data_root.mkdir()

        result = _companion._resolve_trace_path("traces/no_such_file.jsonl", data_root)
        assert result is None


class TestOutput:
    def test_outputs_json_when_use_json_true(self, capsys):
        data = {"key": "value", "count": 42}
        _companion._output(data, use_json=True)
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed["key"] == "value"
        assert parsed["count"] == 42

    def test_outputs_human_readable_when_use_json_false(self, capsys):
        data = {"ready": True, "error": "none"}
        _companion._output(data, use_json=False)
        captured = capsys.readouterr()
        assert "ready: True" in captured.out
        assert "error: none" in captured.out

    def test_json_output_is_indented(self, capsys):
        _companion._output({"a": 1}, use_json=True)
        captured = capsys.readouterr()
        # Indented JSON has newlines
        assert "\n" in captured.out


class TestCmdSetup:
    def test_returns_1_when_golem_not_installed(self):
        args = MagicMock()
        args.finalize = False
        args.verify = False
        args.cwd = None
        args.json = False

        with patch("golem_companion.is_golem_installed", return_value=False):
            rc = _companion.cmd_setup(args)

        assert rc == 1

    def test_returns_0_when_all_checks_pass(self, tmp_path, capsys):
        args = MagicMock()
        args.finalize = False
        args.verify = False
        args.cwd = str(tmp_path)
        args.json = True

        with patch("golem_companion.is_golem_installed", return_value=True):
            with patch(
                "golem_companion.ensure_running",
                return_value={"already_running": True, "started": False},
            ):
                with patch(
                    "golem_companion.attach_repo",
                    return_value={
                        "attached": True,
                        "path": str(tmp_path),
                        "detail": "ok",
                    },
                ):
                    with patch(
                        "golem_companion.collect_repo_signals",
                        return_value={
                            "repo_path": str(tmp_path),
                            "repo_name": "tmp",
                            "detected_files": {},
                            "missing_files": {},
                        },
                    ):
                        rc = _companion.cmd_setup(args)

        assert rc == 0
        output = json.loads(capsys.readouterr().out)
        assert output["ready"] is True

    def test_finalize_returns_1_on_failure(self, tmp_path):
        args = MagicMock()
        args.finalize = True
        args.cwd = str(tmp_path)
        args.json = False

        with patch(
            "golem_companion.finalize_setup",
            return_value={"ok": False, "error": "golem.md not found"},
        ):
            rc = _companion.cmd_setup(args)

        assert rc == 1

    def test_finalize_returns_0_on_success(self, tmp_path, capsys):
        args = MagicMock()
        args.finalize = True
        args.cwd = str(tmp_path)
        args.json = True

        with patch(
            "golem_companion.finalize_setup",
            return_value={
                "ok": True,
                "command_count": 2,
                "verify_yaml_path": "/tmp/x/.golem/verify.yaml",
            },
        ):
            rc = _companion.cmd_setup(args)

        assert rc == 0


class TestCmdRun:
    def test_returns_1_when_no_prompt(self):
        args = MagicMock()
        args.task = []
        args.json = False

        rc = _companion.cmd_run(args)
        assert rc == 1

    def test_submits_task_and_records_delegation(self, tmp_path, capsys):
        args = MagicMock()
        args.task = ["Fix", "the", "bug"]
        args.cwd = str(tmp_path)
        args.background = True
        args.wait = False
        args.json = True

        mock_result = MagicMock(returncode=0, stdout="Submitted task #42\n", stderr="")
        with patch("golem_companion.subprocess.run", return_value=mock_result):
            with patch("golem_companion.record_delegation") as mock_record:
                rc = _companion.cmd_run(args)

        assert rc == 0
        mock_record.assert_called_once_with(42, "Fix the bug", "background")
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True
        assert output["task_id"] == 42

    def test_returns_1_when_task_id_not_extracted(self, tmp_path, capsys):
        args = MagicMock()
        args.task = ["do", "something"]
        args.cwd = str(tmp_path)
        args.background = True
        args.wait = False
        args.json = True

        mock_result = MagicMock(returncode=0, stdout="no task id here\n", stderr="")
        with patch("golem_companion.subprocess.run", return_value=mock_result):
            rc = _companion.cmd_run(args)

        assert rc == 1

    def test_returns_1_when_golem_not_found(self, tmp_path):
        args = MagicMock()
        args.task = ["do", "something"]
        args.cwd = str(tmp_path)
        args.json = False

        with patch("golem_companion.subprocess.run", side_effect=FileNotFoundError):
            rc = _companion.cmd_run(args)

        assert rc == 1

    def test_returns_1_when_timeout(self, tmp_path):
        args = MagicMock()
        args.task = ["do", "something"]
        args.cwd = str(tmp_path)
        args.json = False

        with patch(
            "golem_companion.subprocess.run",
            side_effect=subprocess.TimeoutExpired("golem", 30),
        ):
            rc = _companion.cmd_run(args)

        assert rc == 1


class TestCmdStatus:
    def test_returns_golem_status_and_session_info(self, tmp_path, capsys):
        args = MagicMock()
        args.task_id = None
        args.hours = 24
        args.watch = None
        args.json = True

        mock_result = MagicMock(returncode=0, stdout="1 task running\n", stderr="")
        with patch("golem_companion.subprocess.run", return_value=mock_result):
            with patch("golem_companion.get_session_jobs", return_value=[]):
                with patch(
                    "golem_companion.get_session_stats",
                    return_value={"delegated": 0, "completed": 0, "failed": 0},
                ):
                    rc = _companion.cmd_status(args)

        assert rc == 0
        output = json.loads(capsys.readouterr().out)
        assert output["golem_status"] == "1 task running"
        assert output["session_jobs"] == []

    def test_returns_1_on_subprocess_error(self):
        args = MagicMock()
        args.task_id = None
        args.hours = 24
        args.watch = None
        args.json = False

        with patch(
            "golem_companion.subprocess.run",
            side_effect=FileNotFoundError("golem not found"),
        ):
            rc = _companion.cmd_status(args)

        assert rc == 1


class TestCmdCancel:
    def test_cancels_task_and_updates_status(self, tmp_path, capsys):
        args = MagicMock()
        args.task_id = 77
        args.json = True

        mock_result = MagicMock(returncode=0, stdout="cancelled\n", stderr="")
        with patch("golem_companion.subprocess.run", return_value=mock_result):
            with patch("golem_companion.update_job_status") as mock_update:
                rc = _companion.cmd_cancel(args)

        assert rc == 0
        mock_update.assert_called_once_with(77, "cancelled")
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True

    def test_does_not_update_status_on_failure(self, tmp_path):
        args = MagicMock()
        args.task_id = 88
        args.json = False

        mock_result = MagicMock(returncode=1, stdout="", stderr="task not found")
        with patch("golem_companion.subprocess.run", return_value=mock_result):
            with patch("golem_companion.update_job_status") as mock_update:
                _companion.cmd_cancel(args)

        mock_update.assert_not_called()

    def test_returns_1_on_subprocess_error(self):
        args = MagicMock()
        args.task_id = 99
        args.json = False

        with patch("golem_companion.subprocess.run", side_effect=FileNotFoundError):
            rc = _companion.cmd_cancel(args)

        assert rc == 1


class TestCmdSessionStart:
    def test_silent_when_golem_not_installed(self, capsys):
        args = MagicMock()
        with patch("golem_companion.is_golem_installed", return_value=False):
            rc = _companion.cmd_session_start(args)

        assert rc == 0
        assert capsys.readouterr().out == ""

    def test_prints_daemon_active_message_when_running_and_attached(self, capsys):
        args = MagicMock()
        with patch("golem_companion.is_golem_installed", return_value=True):
            with patch("golem_companion.is_daemon_running", return_value=True):
                with patch("golem_companion.is_repo_attached", return_value=True):
                    rc = _companion.cmd_session_start(args)

        assert rc == 0
        output = capsys.readouterr().out
        assert "[Golem]" in output
        assert "Daemon active" in output
        assert "Repo attached" in output

    def test_prints_not_attached_when_daemon_running_but_not_attached(self, capsys):
        args = MagicMock()
        with patch("golem_companion.is_golem_installed", return_value=True):
            with patch("golem_companion.is_daemon_running", return_value=True):
                with patch("golem_companion.is_repo_attached", return_value=False):
                    rc = _companion.cmd_session_start(args)

        assert rc == 0
        output = capsys.readouterr().out
        assert "not attached" in output

    def test_prints_daemon_not_running_message(self, capsys):
        args = MagicMock()
        with patch("golem_companion.is_golem_installed", return_value=True):
            with patch("golem_companion.is_daemon_running", return_value=False):
                rc = _companion.cmd_session_start(args)

        assert rc == 0
        output = capsys.readouterr().out
        assert "Daemon not running" in output


class TestCmdSessionEnd:
    def test_flushes_stats_and_returns_0(self):
        args = MagicMock()
        with patch("golem_companion.flush_stats_to_global") as mock_flush:
            rc = _companion.cmd_session_end(args)

        assert rc == 0
        mock_flush.assert_called_once()

    def test_returns_0_even_when_flush_raises(self):
        args = MagicMock()
        with patch(
            "golem_companion.flush_stats_to_global",
            side_effect=RuntimeError("disk error"),
        ):
            rc = _companion.cmd_session_end(args)

        assert rc == 0


class TestMain:
    def test_returns_1_with_no_subcommand(self, capsys):
        with patch("sys.argv", ["golem-companion.py"]):
            rc = _companion.main()
        assert rc == 1

    def test_dispatches_setup_subcommand(self):
        with patch("sys.argv", ["golem-companion.py", "setup"]):
            with patch("golem_companion.cmd_setup", return_value=0) as mock_cmd:
                rc = _companion.main()
        assert rc == 0
        mock_cmd.assert_called_once()

    def test_dispatches_run_subcommand(self):
        with patch("sys.argv", ["golem-companion.py", "run", "fix", "the", "bug"]):
            with patch("golem_companion.cmd_run", return_value=0) as mock_cmd:
                rc = _companion.main()
        assert rc == 0
        mock_cmd.assert_called_once()

    def test_dispatches_status_subcommand(self):
        with patch("sys.argv", ["golem-companion.py", "status"]):
            with patch("golem_companion.cmd_status", return_value=0) as mock_cmd:
                rc = _companion.main()
        assert rc == 0
        mock_cmd.assert_called_once()

    def test_dispatches_session_start_subcommand(self):
        with patch("sys.argv", ["golem-companion.py", "session-start"]):
            with patch("golem_companion.cmd_session_start", return_value=0) as mock_cmd:
                rc = _companion.main()
        assert rc == 0
        mock_cmd.assert_called_once()

    def test_dispatches_session_end_subcommand(self):
        with patch("sys.argv", ["golem-companion.py", "session-end"]):
            with patch("golem_companion.cmd_session_end", return_value=0) as mock_cmd:
                rc = _companion.main()
        assert rc == 0
        mock_cmd.assert_called_once()

    def test_dispatches_cancel_subcommand(self):
        with patch("sys.argv", ["golem-companion.py", "cancel", "42"]):
            with patch("golem_companion.cmd_cancel", return_value=0) as mock_cmd:
                rc = _companion.main()
        assert rc == 0
        mock_cmd.assert_called_once()

    def test_dispatches_config_subcommand(self):
        with patch("sys.argv", ["golem-companion.py", "config", "get", "api_key"]):
            with patch("golem_companion.cmd_config", return_value=0) as mock_cmd:
                rc = _companion.main()
        assert rc == 0
        mock_cmd.assert_called_once()

    def test_dispatches_query_subcommand(self):
        with patch("sys.argv", ["golem-companion.py", "query", "123"]):
            with patch("golem_companion.cmd_query", return_value=0) as mock_cmd:
                rc = _companion.main()
        assert rc == 0
        mock_cmd.assert_called_once()
