# pylint: disable=too-few-public-methods,too-many-lines
"""Tests for golem.core.dashboard — dashboard helpers and route handlers."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import golem.core.dashboard as _dashboard_module
from golem.core.dashboard import (
    _FileCache,
    _MAX_TRACE_CACHE,
    _aggregate_stats,
    _check_daemon_status,
    _extract_assistant_events,
    _extract_numeric_id,
    _extract_user_events,
    _format_live_section,
    _parse_trace,
    _parse_trace_terminal,
    _read_and_parse_trace,
    _read_log_tail,
    _read_sessions,
    _resolve_paths,
    _term_ev,
    config_to_snapshot,
    format_status_text,
    format_task_detail_text,
    mount_dashboard,
)
from golem.orchestrator import TaskSession, TaskSessionState
from golem.trace_parser import parse_trace as _parse_trace_structured
from golem.types import MilestoneDict

# ---------------------------------------------------------------------------
# _extract_numeric_id
# ---------------------------------------------------------------------------


class TestExtractNumericId:
    def test_golem_event_id(self):
        assert _extract_numeric_id("golem-123-20260215") == ("golem", "123")

    def test_golem_no_number(self):
        assert _extract_numeric_id("golem-abc") == ("golem", "")

    def test_non_golem(self):
        assert _extract_numeric_id("other-123") == ("", "")


# ---------------------------------------------------------------------------
# _resolve_paths
# ---------------------------------------------------------------------------


class TestResolvePaths:
    def test_non_golem_returns_none(self):
        result = _resolve_paths("other-123")
        assert result == {"trace": None, "prompt": None, "report": None}

    def test_golem_no_files(self, tmp_path):
        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _resolve_paths("golem-42-20260101")
        assert result["trace"] is None
        assert result["prompt"] is None
        assert result["report"] is None

    def test_golem_with_files(self, tmp_path):
        traces = tmp_path / "traces" / "golem"
        reports = tmp_path / "reports" / "golem"
        traces.mkdir(parents=True)
        reports.mkdir(parents=True)

        (traces / "golem-42-20260101.jsonl").write_text("{}", encoding="utf-8")
        (traces / "golem-42-20260101.prompt.txt").write_text("hi", encoding="utf-8")
        (reports / "42.md").write_text("# Report", encoding="utf-8")

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _resolve_paths("golem-42-20260101")

        assert result["trace"] is not None
        assert result["prompt"] is not None
        assert result["report"] is not None

    def test_bare_numeric_id_normalized(self, tmp_path):
        """Bare numeric IDs (from sessions API) are prefixed with golem-."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        (traces / "golem-1773152876161.jsonl").write_text("{}", encoding="utf-8")
        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _resolve_paths("1773152876161")
        assert result["trace"] is not None

    def test_slash_replaced_in_safe_id(self, tmp_path):
        """Event IDs with slashes get sanitized for file lookups."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        (traces / "golem-5_sub1.jsonl").write_text("{}", encoding="utf-8")
        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _resolve_paths("golem-5/sub1")
        assert result["trace"] is not None


# ---------------------------------------------------------------------------
# _parse_trace
# ---------------------------------------------------------------------------


class TestParseTrace:
    def test_parse_system_init(self, tmp_path):
        p = tmp_path / "trace.jsonl"
        p.write_text(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "model": "opus",
                    "tools": ["Read"],
                    "mcp_servers": [],
                    "cwd": "/tmp",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        sections = _parse_trace(p)
        assert len(sections) == 1
        assert sections[0]["type"] == "system_init"
        assert sections[0]["content"]["model"] == "opus"

    def test_parse_assistant_thinking_and_text(self, tmp_path):
        p = tmp_path / "trace.jsonl"
        ev = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "hello"},
                ]
            },
        }
        p.write_text(json.dumps(ev) + "\n", encoding="utf-8")
        sections = _parse_trace(p)
        assert len(sections) == 2
        assert sections[0]["type"] == "thinking"
        assert sections[1]["type"] == "response"

    def test_parse_result(self, tmp_path):
        p = tmp_path / "trace.jsonl"
        ev = {
            "type": "result",
            "duration_ms": 5000,
            "total_cost_usd": 0.12,
            "num_turns": 3,
            "is_error": False,
            "usage": {"input": 100},
        }
        p.write_text(json.dumps(ev) + "\n", encoding="utf-8")
        sections = _parse_trace(p)
        assert len(sections) == 1
        assert sections[0]["type"] == "result"
        assert sections[0]["content"]["duration_ms"] == 5000

    def test_skips_blank_and_invalid_json(self, tmp_path):
        p = tmp_path / "trace.jsonl"
        p.write_text("\n  \n{bad json}\n", encoding="utf-8")
        assert not _parse_trace(p)

    def test_non_dict_content_blocks_ignored(self, tmp_path):
        p = tmp_path / "trace.jsonl"
        ev = {"type": "assistant", "message": {"content": ["just a string"]}}
        p.write_text(json.dumps(ev) + "\n", encoding="utf-8")
        sections = _parse_trace(p)
        assert not sections


# ---------------------------------------------------------------------------
# _term_ev
# ---------------------------------------------------------------------------


class TestTermEv:
    def test_basic(self):
        ev = _term_ev("text", "hello")
        assert ev == {
            "type": "text",
            "text": "hello",
            "tool_name": "",
            "is_error": False,
        }

    def test_with_kwargs(self):
        ev = _term_ev("tool_call", "Read", tool_name="Read", is_error=True)
        assert ev["tool_name"] == "Read"
        assert ev["is_error"] is True


# ---------------------------------------------------------------------------
# _extract_assistant_events / _extract_user_events
# ---------------------------------------------------------------------------


class TestExtractAssistantEvents:
    def test_tool_use(self):
        ev = {
            "message": {
                "content": [{"type": "tool_use", "name": "Read"}],
            }
        }
        events: list = []
        stats = {"tool_calls": 0, "errors": 0}
        _extract_assistant_events(ev, events, stats)
        assert len(events) == 1
        assert events[0]["type"] == "tool_call"
        assert stats["tool_calls"] == 1

    def test_text_and_thinking(self):
        ev = {
            "message": {
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "thinking", "thinking": "hmm"},
                ],
            }
        }
        events: list = []
        stats = {"tool_calls": 0, "errors": 0}
        _extract_assistant_events(ev, events, stats)
        assert len(events) == 2

    def test_skips_non_dict_and_empty_text(self):
        ev = {
            "message": {
                "content": [
                    "just a string",
                    {"type": "text", "text": ""},
                    {"type": "thinking", "thinking": "  "},
                ],
            }
        }
        events: list = []
        stats = {"tool_calls": 0, "errors": 0}
        _extract_assistant_events(ev, events, stats)
        assert not events

    def test_empty_message(self):
        events: list = []
        stats = {"tool_calls": 0, "errors": 0}
        _extract_assistant_events({}, events, stats)
        assert not events


class TestExtractUserEvents:
    def test_tool_result_string_content(self):
        ev = {
            "message": {
                "content": [{"tool_use_id": "1", "content": "ok", "is_error": False}]
            }
        }
        events: list = []
        stats = {"tool_calls": 0, "errors": 0}
        _extract_user_events(ev, events, stats)
        assert len(events) == 1
        assert events[0]["type"] == "tool_result"
        assert events[0]["is_error"] is False

    def test_tool_result_list_content(self):
        ev = {
            "message": {
                "content": [
                    {
                        "tool_use_id": "1",
                        "content": [{"text": "a"}, {"text": "b"}],
                        "is_error": False,
                    }
                ]
            }
        }
        events: list = []
        stats = {"tool_calls": 0, "errors": 0}
        _extract_user_events(ev, events, stats)
        assert "a b" in events[0]["text"]

    def test_error_increments_counter(self):
        ev = {
            "message": {
                "content": [{"tool_use_id": "1", "content": "fail", "is_error": True}]
            }
        }
        events: list = []
        stats = {"tool_calls": 0, "errors": 0}
        _extract_user_events(ev, events, stats)
        assert stats["errors"] == 1
        assert events[0]["is_error"] is True

    def test_skips_non_dict_blocks(self):
        ev = {"message": {"content": ["plain string"]}}
        events: list = []
        stats = {"tool_calls": 0, "errors": 0}
        _extract_user_events(ev, events, stats)
        assert not events

    def test_skips_blocks_without_tool_use_id(self):
        ev = {"message": {"content": [{"type": "text", "text": "hi"}]}}
        events: list = []
        stats = {"tool_calls": 0, "errors": 0}
        _extract_user_events(ev, events, stats)
        assert not events


# ---------------------------------------------------------------------------
# _parse_trace_terminal
# ---------------------------------------------------------------------------


class TestParseTraceTerminal:
    def test_full_trace(self, tmp_path):
        p = tmp_path / "trace.jsonl"
        lines = [
            json.dumps(
                {"type": "system", "subtype": "init", "model": "opus", "cwd": "/"}
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Read"}]},
                }
            ),
            json.dumps(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "tool_use_id": "1",
                                "content": "done",
                                "is_error": False,
                            }
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "total_cost_usd": 0.05,
                    "duration_ms": 3000,
                    "is_error": False,
                }
            ),
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        events, stats = _parse_trace_terminal(p)
        assert stats["total_events"] == 4
        assert stats["tool_calls"] == 1
        assert stats["cost_usd"] == 0.05
        types = [e["type"] for e in events]
        assert "system_init" in types
        assert "tool_call" in types
        assert "result" in types

    def test_skips_blank_and_bad_json(self, tmp_path):
        p = tmp_path / "trace.jsonl"
        p.write_text("\n  \n{bad json}\n", encoding="utf-8")
        events, stats = _parse_trace_terminal(p)
        assert not events
        assert stats["total_events"] == 0


# ---------------------------------------------------------------------------
# _aggregate_stats
# ---------------------------------------------------------------------------


class TestAggregateStats:
    def test_empty(self):
        s = _aggregate_stats([])
        assert s["total_runs"] == 0
        assert s["success_rate"] == 0.0
        assert s["total_cost_usd"] == 0.0

    def test_with_runs(self):
        runs = [
            {
                "success": True,
                "cost_usd": 0.10,
                "duration_s": 10.0,
                "flow": "golem",
                "input_tokens": 100,
                "output_tokens": 50,
            },
            {
                "success": False,
                "cost_usd": 0.05,
                "duration_s": 5.0,
                "flow": "golem",
                "input_tokens": 80,
                "output_tokens": 20,
            },
            {
                "success": True,
                "cost_usd": 0.20,
                "flow": "other",
                "input_tokens": 200,
                "output_tokens": 100,
            },
        ]
        s = _aggregate_stats(runs)
        assert s["total_runs"] == 3
        assert s["success_count"] == 2
        assert s["failure_count"] == 1
        assert s["success_rate"] == 66.7
        assert s["total_cost_usd"] == 0.35
        assert s["total_tokens"] == 550
        assert "golem" in s["by_flow"]
        assert s["by_flow"]["golem"]["total"] == 2
        assert s["by_flow"]["other"]["success_rate"] == 100.0

    def test_no_duration(self):
        runs = [{"success": True, "flow": "x", "input_tokens": 0, "output_tokens": 0}]
        s = _aggregate_stats(runs)
        assert s["avg_duration_s"] == 0.0


# ---------------------------------------------------------------------------
# config_to_snapshot
# ---------------------------------------------------------------------------


class TestConfigToSnapshot:
    def test_none_config(self):
        assert not config_to_snapshot(None)

    def test_valid_config(self):
        golem_cfg = SimpleNamespace(enabled=True, model="opus")
        claude_cfg = SimpleNamespace(
            model="sonnet",
            max_concurrent=4,
            max_budget_usd=10.0,
            timeout_seconds=600,
        )
        cfg = SimpleNamespace(claude=claude_cfg, golem=golem_cfg)
        snap = config_to_snapshot(cfg)
        assert snap["model"] == "sonnet"
        assert snap["max_concurrent"] == 4
        assert snap["flows"]["golem"] is True
        assert snap["flow_models"]["golem"] == "opus"

    def test_broken_config(self):
        """Config that raises internally returns empty dict."""
        assert not config_to_snapshot(object())

    def test_golem_flow_without_model(self):
        golem_cfg = SimpleNamespace(enabled=False, model="")
        claude_cfg = SimpleNamespace(
            model="sonnet",
            max_concurrent=1,
            max_budget_usd=5,
            timeout_seconds=300,
        )
        cfg = SimpleNamespace(claude=claude_cfg, golem=golem_cfg)
        snap = config_to_snapshot(cfg)
        assert snap["flows"]["golem"] is False
        assert "golem" not in snap["flow_models"]

    def test_config_without_golem(self):
        claude_cfg = SimpleNamespace(
            model="opus",
            max_concurrent=2,
            max_budget_usd=5,
            timeout_seconds=300,
        )
        cfg = SimpleNamespace(claude=claude_cfg, golem=None)
        snap = config_to_snapshot(cfg)
        assert snap["flows"] == {}


# ---------------------------------------------------------------------------
# _read_sessions / _read_log_tail
# ---------------------------------------------------------------------------


class TestReadSessions:
    def test_missing_file(self, tmp_path):
        with patch("golem.core.dashboard._SESSIONS_FILE", tmp_path / "nope.json"):
            assert _read_sessions() == {"sessions": {}}

    def test_valid_file(self, tmp_path):
        f = tmp_path / "sessions.json"
        f.write_text('{"sessions": {"1": {"state": "running"}}}', encoding="utf-8")
        with patch("golem.core.dashboard._SESSIONS_FILE", f):
            data = _read_sessions()
        assert "1" in data["sessions"]


class TestReadLogTail:
    def test_missing_log(self, tmp_path):
        with patch("golem.core.dashboard._LOG_DIR", tmp_path):
            result = _read_log_tail()
        assert result == {"lines": [], "file": ""}

    def test_valid_log(self, tmp_path):
        log_file = tmp_path / "daemon_latest.log"
        log_file.write_text("line1\nline2\nline3\n", encoding="utf-8")
        with patch("golem.core.dashboard._LOG_DIR", tmp_path):
            result = _read_log_tail(lines=2)
        assert len(result["lines"]) == 2
        assert result["total_lines"] == 3

    def test_log_fewer_lines_than_requested(self, tmp_path):
        log_file = tmp_path / "daemon_latest.log"
        log_file.write_text("only one\n", encoding="utf-8")
        with patch("golem.core.dashboard._LOG_DIR", tmp_path):
            result = _read_log_tail(lines=100)
        assert len(result["lines"]) == 1

    def test_oserror_on_resolve(self, tmp_path):
        log_file = tmp_path / "daemon_latest.log"
        log_file.write_text("data", encoding="utf-8")
        with patch("golem.core.dashboard._LOG_DIR", tmp_path):
            with patch.object(Path, "resolve", side_effect=OSError("perm")):
                result = _read_log_tail()
        assert result == {"lines": [], "file": ""}


# ---------------------------------------------------------------------------
# _FileCache
# ---------------------------------------------------------------------------


class TestFileCache:
    def test_reads_file(self, tmp_path):
        f = tmp_path / "test.html"
        f.write_text("<html>hello</html>", encoding="utf-8")
        cache = _FileCache(f)
        assert cache.read() == "<html>hello</html>"

    def test_returns_cached_on_same_mtime(self, tmp_path):
        f = tmp_path / "test.html"
        f.write_text("v1", encoding="utf-8")
        cache = _FileCache(f)
        assert cache.read() == "v1"
        # Second read uses cache (same mtime)
        assert cache.read() == "v1"

    def test_reloads_on_mtime_change(self, tmp_path):
        f = tmp_path / "test.html"
        f.write_text("v1", encoding="utf-8")
        cache = _FileCache(f)
        assert cache.read() == "v1"
        f.write_text("v2", encoding="utf-8")
        # Force mtime difference
        import os

        os.utime(f, (f.stat().st_mtime + 1, f.stat().st_mtime + 1))
        assert cache.read() == "v2"

    def test_missing_file_returns_cached(self, tmp_path):
        f = tmp_path / "nonexistent.html"
        cache = _FileCache(f)
        assert cache.read() == ""


# ---------------------------------------------------------------------------
# _check_daemon_status
# ---------------------------------------------------------------------------


class TestCheckDaemonStatus:
    @patch("golem.core.dashboard.read_pid")
    def test_no_pid_file(self, mock_read_pid):
        mock_read_pid.return_value = None
        label, running = _check_daemon_status()
        assert not running
        assert "stopped" in label.lower()

    @patch("golem.core.dashboard.os.kill")
    @patch("golem.core.dashboard.read_pid")
    def test_pid_alive(self, mock_read_pid, mock_kill):
        mock_read_pid.return_value = 12345
        mock_kill.return_value = None
        label, running = _check_daemon_status()
        assert running
        assert "12345" in label

    @patch("golem.core.dashboard.os.kill", side_effect=OSError)
    @patch("golem.core.dashboard.read_pid")
    def test_pid_stale(self, mock_read_pid, mock_kill):
        mock_read_pid.return_value = 99999
        label, running = _check_daemon_status()
        assert not running
        assert "stale" in label.lower()


# ---------------------------------------------------------------------------
# _format_live_section
# ---------------------------------------------------------------------------


class TestFormatLiveSection:
    def test_empty_state_shows_idle(self):
        snap = {
            "uptime_s": 3600,
            "active_count": 0,
            "queue_depth": 0,
            "active_tasks": [],
            "models_active": {},
            "recently_completed": [],
        }
        lines = _format_live_section(snap, sessions={})
        joined = "\n".join(lines)
        assert "No active tasks" in joined
        assert "1h 0m" in joined

    def test_active_with_session_subject(self):
        snap = {
            "uptime_s": 120,
            "active_count": 1,
            "queue_depth": 0,
            "active_tasks": [
                {
                    "event_id": "golem-42-20260309",
                    "flow": "golem",
                    "model": "opus",
                    "phase": "running",
                    "elapsed_s": 65.0,
                }
            ],
            "models_active": {"opus": 1},
            "recently_completed": [],
        }
        sessions = {
            42: SimpleNamespace(
                parent_subject="Fix login bug",
                total_cost_usd=1.23,
            )
        }
        lines = _format_live_section(snap, sessions=sessions)
        joined = "\n".join(lines)
        assert "Fix login bug" in joined
        assert "opus" in joined
        assert "1m 5s" in joined

    def test_recently_completed_shown(self):
        snap = {
            "uptime_s": 60,
            "active_count": 0,
            "queue_depth": 0,
            "active_tasks": [],
            "models_active": {},
            "recently_completed": [
                {
                    "event_id": "golem-10-20260309",
                    "flow": "golem",
                    "success": True,
                    "duration_s": 90.0,
                    "cost_usd": 0.55,
                    "finished_ago_s": 120,
                }
            ],
        }
        lines = _format_live_section(snap, sessions={})
        joined = "\n".join(lines)
        assert "OK" in joined
        assert "$0.55" in joined

    def test_recently_completed_long_subject_truncated(self):
        snap = {
            "uptime_s": 60,
            "active_count": 0,
            "queue_depth": 0,
            "active_tasks": [],
            "models_active": {},
            "recently_completed": [
                {
                    "event_id": "golem-10-20260309",
                    "flow": "golem",
                    "success": True,
                    "duration_s": 90.0,
                    "cost_usd": 0.55,
                    "finished_ago_s": 120,
                }
            ],
        }
        sessions = {
            10: SimpleNamespace(
                parent_subject="A" * 40,
                total_cost_usd=0.0,
            )
        }
        lines = _format_live_section(snap, sessions=sessions)
        joined = "\n".join(lines)
        assert "..." in joined

    def test_queue_depth_shown(self):
        snap = {
            "uptime_s": 60,
            "active_count": 1,
            "queue_depth": 3,
            "active_tasks": [
                {
                    "event_id": "golem-1",
                    "flow": "golem",
                    "model": "sonnet",
                    "phase": "running",
                    "elapsed_s": 5.0,
                }
            ],
            "models_active": {},
            "recently_completed": [],
        }
        lines = _format_live_section(snap, sessions={})
        joined = "\n".join(lines)
        assert "3 waiting" in joined

    def test_long_subject_truncated(self):
        snap = {
            "uptime_s": 0,
            "active_count": 1,
            "queue_depth": 0,
            "active_tasks": [
                {
                    "event_id": "golem-99-20260309",
                    "flow": "golem",
                    "model": "opus",
                    "phase": "running",
                    "elapsed_s": 1.0,
                }
            ],
            "models_active": {},
            "recently_completed": [],
        }
        sessions = {
            99: SimpleNamespace(
                parent_subject="A" * 60,
                total_cost_usd=0.0,
            )
        }
        lines = _format_live_section(snap, sessions=sessions)
        joined = "\n".join(lines)
        assert "..." in joined

    def test_no_session_falls_back_to_event_id(self):
        snap = {
            "uptime_s": 0,
            "active_count": 1,
            "queue_depth": 0,
            "active_tasks": [
                {
                    "event_id": "golem-5-20260309",
                    "flow": "golem",
                    "model": "sonnet",
                    "phase": "validating",
                    "elapsed_s": 10.0,
                }
            ],
            "models_active": {},
            "recently_completed": [],
        }
        lines = _format_live_section(snap, sessions={})
        joined = "\n".join(lines)
        assert "golem-5-20260309" in joined


# ---------------------------------------------------------------------------
# format_status_text
# ---------------------------------------------------------------------------


class TestFormatStatusText:
    @patch("golem.core.dashboard.load_sessions", return_value={})
    @patch("golem.core.dashboard._check_daemon_status", return_value=("stopped", False))
    @patch("golem.core.dashboard.read_live_snapshot")
    @patch("golem.core.dashboard.read_runs")
    def test_basic_output(
        self, mock_read_runs, mock_snap, _mock_daemon, _mock_sessions
    ):
        mock_read_runs.return_value = [
            {
                "success": True,
                "cost_usd": 0.1,
                "duration_s": 10.0,
                "flow": "golem",
                "input_tokens": 100,
                "output_tokens": 50,
                "started_at": "2026-01-01T00:00:00",
                "event_id": "golem-1",
            }
        ]
        mock_snap.return_value = {
            "uptime_s": 0,
            "active_count": 0,
            "queue_depth": 0,
            "active_tasks": [],
            "models_active": {},
            "recently_completed": [],
        }
        text = format_status_text(since_hours=24)
        assert "Golem Status" in text
        assert "HISTORY:" in text
        assert "golem" in text
        assert "Daemon:" in text

    @patch("golem.core.dashboard.load_sessions", return_value={})
    @patch("golem.core.dashboard._check_daemon_status", return_value=("stopped", False))
    @patch("golem.core.dashboard.read_live_snapshot")
    @patch("golem.core.dashboard.read_runs")
    def test_with_flow_filter(
        self, mock_read_runs, mock_snap, _mock_daemon, _mock_sessions
    ):
        mock_read_runs.return_value = []
        mock_snap.return_value = {
            "uptime_s": 0,
            "active_count": 0,
            "queue_depth": 0,
            "active_tasks": [],
            "models_active": {},
            "recently_completed": [],
        }
        text = format_status_text(since_hours=12, flow="golem")
        assert "golem" in text

    @patch("golem.core.dashboard.load_sessions", return_value={})
    @patch("golem.core.dashboard._check_daemon_status", return_value=("stopped", False))
    @patch("golem.core.dashboard.read_live_snapshot")
    @patch("golem.core.dashboard.read_runs")
    def test_non_golem_event_id_renders(
        self, mock_read_runs, mock_snap, _mock_daemon, _mock_sessions
    ):
        mock_read_runs.return_value = [
            {
                "success": False,
                "cost_usd": 0.0,
                "flow": "golem",
                "input_tokens": 0,
                "output_tokens": 0,
                "started_at": "2026-01-01T00:00:00",
                "event_id": "X" * 60,
            }
        ]
        mock_snap.return_value = {
            "uptime_s": 0,
            "active_count": 0,
            "queue_depth": 0,
            "active_tasks": [],
            "models_active": {},
            "recently_completed": [],
        }
        text = format_status_text()
        # Non-golem event IDs won't have a session subject, so the subject
        # column will be empty — but the run line is still present.
        assert "FAIL" in text

    @patch("golem.core.dashboard.load_sessions")
    @patch("golem.core.dashboard._check_daemon_status", return_value=("stopped", False))
    @patch("golem.core.dashboard.read_live_snapshot")
    @patch("golem.core.dashboard.read_runs")
    def test_session_enrichment(
        self, mock_runs, mock_snap, _mock_daemon, mock_sessions
    ):
        mock_runs.return_value = []
        mock_snap.return_value = {
            "uptime_s": 300,
            "active_count": 1,
            "queue_depth": 0,
            "active_tasks": [
                {
                    "event_id": "golem-42-20260309",
                    "flow": "golem",
                    "model": "opus",
                    "phase": "orchestrating",
                    "elapsed_s": 120.0,
                }
            ],
            "models_active": {"opus": 1},
            "recently_completed": [],
        }
        mock_sessions.return_value = {
            42: SimpleNamespace(
                parent_subject="Config wizard",
                total_cost_usd=2.50,
            )
        }
        text = format_status_text()
        assert "Config wizard" in text
        assert "$2.50" in text
        assert "orchestrating" in text

    @patch("golem.core.dashboard.load_sessions", return_value={})
    @patch("golem.core.dashboard._check_daemon_status", return_value=("stopped", False))
    @patch("golem.core.dashboard.read_live_snapshot")
    @patch("golem.core.dashboard.read_runs")
    def test_history_uses_format_duration(
        self, mock_runs, mock_snap, mock_daemon, mock_sessions
    ):
        mock_runs.return_value = [
            {
                "success": True,
                "cost_usd": 0.10,
                "duration_s": 150.0,
                "flow": "golem",
                "input_tokens": 100,
                "output_tokens": 50,
            }
        ]
        mock_snap.return_value = {
            "uptime_s": 0,
            "active_count": 0,
            "queue_depth": 0,
            "active_tasks": [],
            "models_active": {},
            "recently_completed": [],
        }
        text = format_status_text()
        assert "HISTORY:" in text
        assert "2m 30s" in text  # format_duration(150) = "2m 30s"

    @patch("golem.core.dashboard.load_sessions")
    @patch("golem.core.dashboard._check_daemon_status", return_value=("stopped", False))
    @patch("golem.core.dashboard.read_live_snapshot")
    @patch("golem.core.dashboard.read_runs")
    def test_recent_run_long_subject_truncated(
        self, mock_runs, mock_snap, _mock_daemon, mock_sessions
    ):
        mock_runs.return_value = [
            {
                "success": True,
                "cost_usd": 0.10,
                "duration_s": 10.0,
                "flow": "golem",
                "input_tokens": 0,
                "output_tokens": 0,
                "started_at": "2026-01-01T00:00:00",
                "event_id": "golem-77-20260309",
            }
        ]
        mock_snap.return_value = {
            "uptime_s": 0,
            "active_count": 0,
            "queue_depth": 0,
            "active_tasks": [],
            "models_active": {},
            "recently_completed": [],
        }
        mock_sessions.return_value = {
            77: SimpleNamespace(
                parent_subject="B" * 40,
                total_cost_usd=0.0,
            )
        }
        text = format_status_text()
        assert "..." in text
        assert "BBB" in text


# ---------------------------------------------------------------------------
# format_task_detail_text
# ---------------------------------------------------------------------------


def _make_session(**kwargs) -> TaskSession:
    """Helper to build a TaskSession with sensible defaults."""
    defaults = {
        "parent_issue_id": 12345,
        "parent_subject": "Test task subject",
        "state": TaskSessionState.RUNNING,
        "priority": 5,
        "created_at": "2026-03-09T10:00:00",
        "updated_at": "2026-03-09T10:30:00",
        "duration_seconds": 330.0,
        "total_cost_usd": 1.23,
        "budget_usd": 10.0,
        "execution_mode": "subagent",
        "supervisor_phase": "orchestrating",
        "retry_count": 0,
        "worktree_path": "/path/to/worktree",
        "validation_verdict": "",
        "validation_confidence": 0.0,
        "validation_summary": "",
        "validation_concerns": [],
        "errors": [],
        "event_log": [],
        "result_summary": "",
        "commit_sha": "",
        "files_changed": [],
    }
    defaults.update(kwargs)
    return TaskSession(**defaults)


class TestFormatTaskDetailText:
    @patch("golem.core.dashboard.load_sessions", return_value={})
    def test_task_not_found(self, _mock_sessions):
        result = format_task_detail_text(999)
        assert result == "Task #999 not found."

    @patch("golem.core.dashboard.load_sessions")
    def test_basic_detail(self, mock_sessions):
        sess = _make_session()
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "=== Task #12345 ===" in result
        assert "Test task subject" in result
        assert "running" in result
        assert "5m 30s" in result
        assert "$1.23" in result
        assert "$10.00" in result
        assert "subagent" in result
        assert "orchestrating" in result
        assert "EXECUTION:" in result
        assert "ERRORS:" in result
        assert "(none)" in result

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_shows_fix_iteration_and_retry(self, mock_sessions):
        sess = _make_session(fix_iteration=3, retry_count=1)
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "Fix iters:    3" in result
        assert "Full retries: 1" in result

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_with_validation(self, mock_sessions):
        sess = _make_session(
            validation_verdict="PASS",
            validation_confidence=0.95,
            validation_summary="All tests pass",
            validation_concerns=[],
        )
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "VALIDATION:" in result
        assert "PASS" in result
        assert "0.95" in result
        assert "All tests pass" in result
        assert "none" in result  # no concerns

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_with_validation_concerns(self, mock_sessions):
        sess = _make_session(
            validation_verdict="FAIL",
            validation_confidence=0.4,
            validation_summary="Some tests failed",
            validation_concerns=["Missing coverage", "Lint error"],
        )
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "VALIDATION:" in result
        assert "FAIL" in result
        assert "Missing coverage" in result
        assert "Lint error" in result

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_with_errors(self, mock_sessions):
        sess = _make_session(errors=["Connection timeout", "Build failed"])
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "ERRORS:" in result
        assert "Connection timeout" in result
        assert "Build failed" in result

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_with_files(self, mock_sessions):
        sess = _make_session(
            files_changed=["path/to/file1.py", "path/to/file2.py"],
            result_summary="Implemented feature X",
            commit_sha="abc1234",
        )
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "RESULT:" in result
        assert "Implemented feature X" in result
        assert "abc1234" in result
        assert "2 changed" in result
        assert "path/to/file1.py" in result
        assert "path/to/file2.py" in result

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_no_validation(self, mock_sessions):
        sess = _make_session(validation_verdict="")
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "VALIDATION:" not in result

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_with_event_log(self, mock_sessions):
        events = [
            {
                "timestamp": 1741510800.0,  # real Unix float
                "kind": "tool_call",
                "tool_name": "Read",
                "summary": "Phase 1 done",
                "is_error": False,
            },
            {
                "timestamp": 1741511100.0,
                "kind": "error",
                "tool_name": "",
                "summary": "Retry triggered",
                "is_error": True,
            },
        ]
        sess = _make_session(event_log=events)
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "EVENT LOG" in result
        assert "tool_call" in result
        assert "Phase 1 done" in result
        assert "Retry triggered" in result

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_event_log_capped_at_10(self, mock_sessions):
        events = [
            {
                "timestamp": 1741510800.0 + i * 60,
                "kind": "tool_call",
                "tool_name": "Read",
                "summary": f"m{i}",
                "is_error": False,
            }
            for i in range(15)
        ]
        sess = _make_session(event_log=events)
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        # Last 10 entries are shown (m5..m14), not the first 5
        assert "m14" in result
        assert "m5" in result
        assert "m4" not in result

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_result_section_no_commit_or_files(self, mock_sessions):
        sess = _make_session(result_summary="Done", commit_sha="", files_changed=[])
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "RESULT:" in result
        assert "Done" in result

    @patch("golem.core.dashboard.load_sessions")
    def test_detail_result_section_empty(self, mock_sessions):
        sess = _make_session(result_summary="", commit_sha="", files_changed=[])
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        # RESULT section still rendered but empty summary is blank
        assert "RESULT:" in result


# ---------------------------------------------------------------------------
# Event log contract integration
# ---------------------------------------------------------------------------


class TestEventLogContractIntegration:
    """Verify dashboard reads event log entries using the correct contract keys."""

    @patch("golem.core.dashboard.load_sessions")
    def test_format_task_detail_reads_milestone_dict_keys(self, mock_sessions):
        """Build event log entries from MilestoneDict and verify dashboard reads them."""
        event: MilestoneDict = {
            "kind": "tool_call",
            "tool_name": "Read",
            "summary": "reading /tmp/foo.py",
            "timestamp": 1741510800.0,
            "is_error": False,
        }
        sess = _make_session(event_log=[event])
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "tool_call" in result
        assert "reading /tmp/foo.py" in result


# ---------------------------------------------------------------------------
# mount_dashboard
# ---------------------------------------------------------------------------


class TestMountDashboard:
    def test_no_fastapi(self):
        """When FASTAPI_AVAILABLE is False, mount_dashboard is a no-op."""
        app = MagicMock()
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", False):
            mount_dashboard(app)
        app.get.assert_not_called()  # pylint: disable=no-member

    def test_registers_routes(self):
        """When FastAPI is available, routes are registered on the app."""
        app = MagicMock()
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch("golem.core.dashboard.Query"):
                mount_dashboard(app, config_snapshot={"model": "opus"})
        assert app.get.call_count >= 5  # pylint: disable=no-member


class TestMountDashboardRoutes:  # pylint: disable=too-many-public-methods
    """Test actual route handler logic by capturing the registered handlers."""

    @pytest.fixture()
    def handlers(self):
        """Mount dashboard on a mock app and return a dict of route handlers."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(
                    app,
                    config_snapshot={"model": "test"},
                    live_state_file=None,
                )
        return routes

    @pytest.mark.asyncio
    async def test_api_ping(self, handlers):
        resp = await handlers["/api/ping"]()
        data = json.loads(resp.body)
        assert data["status"] == "ok"
        assert isinstance(data["timestamp"], int)

    @pytest.mark.asyncio
    async def test_api_live_with_file(self):
        """When live_state_file is set, it reads from disk."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(app, live_state_file=Path("/fake/state.json"))

        with patch(
            "golem.core.dashboard.read_live_snapshot",
            return_value={"active_count": 0},
        ):
            resp = await routes["/api/live"]()
        body = json.loads(resp.body)
        assert body["active_count"] == 0

    @pytest.mark.asyncio
    async def test_api_live_without_file(self, handlers):
        """When live_state_file is None, uses LiveState singleton."""
        with patch("golem.core.dashboard.LiveState") as mock_ls:
            mock_ls.get.return_value.snapshot.return_value = {"active_count": 1}
            resp = await handlers["/api/live"]()
        body = json.loads(resp.body)
        assert body["active_count"] == 1

    @pytest.mark.asyncio
    async def test_api_sessions(self, handlers):
        with patch(
            "golem.core.dashboard._read_sessions",
            return_value={"sessions": {"1": {}}},
        ):
            resp = await handlers["/api/sessions"]()
        body = json.loads(resp.body)
        assert "1" in body["sessions"]

    @pytest.mark.asyncio
    async def test_api_sessions_error(self, handlers):
        with patch(
            "golem.core.dashboard._read_sessions",
            side_effect=json.JSONDecodeError("bad", "", 0),
        ):
            resp = await handlers["/api/sessions"]()
        body = json.loads(resp.body)
        assert body == {"sessions": {}}

    @pytest.mark.asyncio
    async def test_api_logs(self, handlers):
        with patch(
            "golem.core.dashboard._read_log_tail",
            return_value={"lines": ["hi"], "file": "test.log", "total_lines": 1},
        ):
            resp = await handlers["/api/logs"](lines=200)
        body = json.loads(resp.body)
        assert body["lines"] == ["hi"]

    @pytest.mark.asyncio
    async def test_api_trace_found(self, handlers, tmp_path):
        trace_path = tmp_path / "trace.jsonl"
        trace_path.write_text(
            json.dumps({"type": "result", "duration_ms": 100}) + "\n",
            encoding="utf-8",
        )
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": trace_path, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/trace/{event_id:path}"]("golem-1")
        body = json.loads(resp.body)
        assert body["event_id"] == "golem-1"
        assert len(body["sections"]) == 1

    @pytest.mark.asyncio
    async def test_api_trace_not_found(self, handlers):
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/trace/{event_id:path}"]("golem-999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_api_prompt_found(self, handlers, tmp_path):
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("Do the thing", encoding="utf-8")
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": prompt_path, "report": None},
        ):
            resp = await handlers["/api/prompt/{event_id:path}"]("golem-1")
        body = json.loads(resp.body)
        assert body["prompt"] == "Do the thing"

    @pytest.mark.asyncio
    async def test_api_prompt_not_found(self, handlers):
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/prompt/{event_id:path}"]("golem-1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_api_report_found(self, handlers, tmp_path):
        report_path = tmp_path / "report.md"
        report_path.write_text("# Report\nAll good", encoding="utf-8")
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": report_path},
        ):
            resp = await handlers["/api/report/{event_id:path}"]("golem-1")
        body = json.loads(resp.body)
        assert body["markdown"] == "# Report\nAll good"

    @pytest.mark.asyncio
    async def test_api_report_not_found(self, handlers):
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/report/{event_id:path}"]("golem-1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_api_trace_terminal_found(self, handlers, tmp_path):
        trace_path = tmp_path / "trace.jsonl"
        trace_path.write_text(
            json.dumps({"type": "result", "total_cost_usd": 0.1, "duration_ms": 100})
            + "\n",
            encoding="utf-8",
        )
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": trace_path, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/trace-terminal/{event_id:path}"]("golem-1")
        body = json.loads(resp.body)
        assert "events" in body
        assert "stats" in body

    @pytest.mark.asyncio
    async def test_api_trace_terminal_not_found(self, handlers):
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/trace-terminal/{event_id:path}"]("golem-1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_dashboard_html(self, handlers):
        with patch.object(_FileCache, "read", return_value="<html>dash</html>"):
            resp = await handlers["/dashboard"]()
        assert b"dash" in resp.body

    @pytest.mark.asyncio
    async def test_admin_html(self, handlers):
        with patch.object(_FileCache, "read", return_value="<html>admin</html>"):
            resp = await handlers["/dashboard/admin"]()
        assert b"admin" in resp.body

    @pytest.mark.asyncio
    async def test_shared_css(self, handlers):
        with patch.object(_FileCache, "read", return_value="body { }"):
            resp = await handlers["/dashboard/shared.css"]()
        assert resp.body is not None

    @pytest.mark.asyncio
    async def test_shared_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="console.log(1)"):
            resp = await handlers["/dashboard/shared.js"]()
        assert resp.body is not None

    @pytest.mark.asyncio
    async def test_task_css(self, handlers):
        with patch.object(_FileCache, "read", return_value=".wf-table { }"):
            resp = await handlers["/dashboard/task.css"]()
        assert resp.body is not None

    @pytest.mark.asyncio
    async def test_task_api_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="const S={}"):
            resp = await handlers["/dashboard/task_api.js"]()
        assert resp.media_type == "application/javascript"

    @pytest.mark.asyncio
    async def test_task_timeline_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="function renderDetail(){}"):
            resp = await handlers["/dashboard/task_timeline.js"]()
        assert resp.media_type == "application/javascript"

    @pytest.mark.asyncio
    async def test_task_overview_js(self, handlers):
        with patch.object(
            _FileCache, "read", return_value="function renderOverview(){}"
        ):
            resp = await handlers["/dashboard/task_overview.js"]()
        assert resp.media_type == "application/javascript"

    @pytest.mark.asyncio
    async def test_task_live_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="function startPolling(){}"):
            resp = await handlers["/dashboard/task_live.js"]()
        assert resp.media_type == "application/javascript"

    @pytest.mark.asyncio
    async def test_elk_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="var ELK={}"):
            resp = await handlers["/dashboard/elk.js"]()
        assert resp.media_type == "application/javascript"

    @pytest.mark.asyncio
    async def test_merge_queue_js(self, handlers):
        with patch.object(
            _FileCache, "read", return_value="function renderMergeQueue(){}"
        ):
            resp = await handlers["/dashboard/merge_queue.js"]()
        assert resp.media_type == "application/javascript"

    @pytest.mark.asyncio
    async def test_merge_queue_css(self, handlers):
        with patch.object(_FileCache, "read", return_value=".mq-view{}"):
            resp = await handlers["/dashboard/merge_queue.css"]()
        assert resp.media_type == "text/css"

    @pytest.mark.asyncio
    async def test_config_tab_js(self, handlers):
        with patch.object(
            _FileCache, "read", return_value="function initConfigTab(){}"
        ):
            resp = await handlers["/dashboard/config_tab.js"]()
        assert resp.media_type == "application/javascript"

    @pytest.mark.asyncio
    async def test_config_tab_css(self, handlers):
        with patch.object(_FileCache, "read", return_value=".config-container{}"):
            resp = await handlers["/dashboard/config_tab.css"]()
        assert resp.media_type == "text/css"

    def test_dashboard_html_has_config_tab(self):
        """Config tab nav button and view div must be in the HTML."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert 'data-view="config"' in body, "Missing config nav-tab button"
        assert 'id="view-config"' in body, "Missing view-config div"
        assert "config_tab.js" in body, "Missing config_tab.js script tag"
        assert "config_tab.css" in body, "Missing config_tab.css link tag"

    @pytest.mark.asyncio
    async def test_api_heartbeat_disabled(self, handlers):
        """When heartbeat is None, returns disabled stub."""
        resp = await handlers["/api/heartbeat"]()
        assert resp["enabled"] is False
        assert resp["state"] == "disabled"
        assert resp["daily_spend_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_api_heartbeat_enabled(self):
        """When heartbeat is provided, returns its snapshot."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route

        mock_hb = MagicMock()
        mock_hb.snapshot.return_value = {
            "enabled": True,
            "state": "idle",
            "last_scan_at": "2026-03-15T10:00:00Z",
            "last_scan_tier": 1,
            "daily_spend_usd": 0.03,
            "daily_budget_usd": 1.0,
            "inflight_task_ids": [],
            "candidate_count": 2,
            "dedup_entry_count": 5,
        }
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(app, heartbeat=mock_hb)

        resp = await routes["/api/heartbeat"]()
        assert resp["enabled"] is True
        assert resp["state"] == "idle"
        mock_hb.snapshot.assert_called_once()

    def test_dashboard_html_has_new_layout(self):
        """Verify the HTML file has the Trace Viewer layout structure."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        # Core layout elements
        assert "top-bar" in body, "Missing top-bar"
        assert "overview-layout" in body, "Missing overview-layout"
        assert "ov-task-list" in body, "Missing ov-task-list"
        assert "td-metrics" in body, "Missing td-metrics"
        assert "timeline-container" in body, "Missing timeline-container"
        # New JS module script tags
        assert "task_api.js" in body, "Missing task_api.js script tag"
        assert "task_timeline.js" in body, "Missing task_timeline.js script tag"
        assert "task_overview.js" in body, "Missing task_overview.js script tag"
        assert "task_live.js" in body, "Missing task_live.js script tag"

    def test_dashboard_html_has_theme_toggle(self):
        """Theme toggle button should exist in the top bar."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert "theme-toggle" in body, "Missing theme toggle button"

    def test_dashboard_html_has_nav_tabs(self):
        """Navigation tabs should exist in the top bar."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert "nav-tab" in body, "Missing nav tabs"

    def test_css_has_theme_tokens(self):
        """Shared CSS should define both dark and light theme tokens."""
        css = Path(__file__).resolve().parent.parent / "core" / "dashboard_shared.css"
        body = css.read_text(encoding="utf-8")
        assert "data-theme" in body, "Missing data-theme attribute selector"
        assert "--accent" in body, "Missing --accent CSS variable"

    def test_css_has_overview_layout(self):
        """Task dashboard CSS should have the overview-layout for the trace viewer."""
        css = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.css"
        body = css.read_text(encoding="utf-8")
        assert "overview-layout" in body, "Missing overview-layout CSS"
        assert "timeline-container" in body, "Missing timeline-container CSS"

    def test_shared_js_has_theme_toggle(self):
        """Shared JS should have theme toggle function."""
        js = Path(__file__).resolve().parent.parent / "core" / "dashboard_shared.js"
        body = js.read_text(encoding="utf-8")
        assert (
            "toggleTheme" in body or "setTheme" in body
        ), "Missing theme toggle in shared JS"


# ---------------------------------------------------------------------------
# /api/cost-analytics endpoint
# ---------------------------------------------------------------------------


class TestCostAnalyticsEndpoint:
    """Test the /api/cost-analytics route handler."""

    @pytest.fixture()
    def handlers(self):
        """Mount dashboard on a mock app and return a dict of route handlers."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(
                    app,
                    config_snapshot={"model": "test"},
                    live_state_file=None,
                )
        return routes

    def test_route_is_registered(self, handlers):
        assert "/api/cost-analytics" in handlers

    @pytest.mark.asyncio
    async def test_returns_expected_keys(self, handlers):
        with patch(
            "golem.core.dashboard.read_runs",
            return_value=[
                {
                    "cost_usd": 0.05,
                    "verdict": "PASS",
                    "started_at": "2025-01-01T10:00:00",
                    "actions_taken": [],
                }
            ],
        ):
            with patch(
                "golem.core.dashboard.load_sessions",
                return_value={},
            ):
                resp = await handlers["/api/cost-analytics"]()
        body = json.loads(resp.body)
        for key in (
            "cost_over_time",
            "cost_by_verdict",
            "cost_per_retry",
            "budget_utilization",
            "summary",
        ):
            assert key in body, f"Missing key: {key}"

    @pytest.mark.asyncio
    async def test_returns_json_response(self, handlers):
        with patch(
            "golem.core.dashboard.read_runs",
            return_value=[],
        ):
            with patch(
                "golem.core.dashboard.load_sessions",
                return_value={},
            ):
                resp = await handlers["/api/cost-analytics"]()
        body = json.loads(resp.body)
        assert isinstance(body, dict)


# ---------------------------------------------------------------------------
# _read_and_parse_trace (Task 3.1)
# ---------------------------------------------------------------------------


def _make_minimal_trace_jsonl(path: Path) -> None:
    """Write a minimal valid JSONL trace file to path."""
    events = [
        json.dumps(
            {
                "type": "result",
                "duration_ms": 1000,
                "total_cost_usd": 0.01,
                "num_turns": 1,
                "is_error": False,
                "usage": {},
            }
        )
    ]
    path.write_text("\n".join(events) + "\n", encoding="utf-8")


class TestReadAndParseTrace:
    def setup_method(self):
        """Clear the parsed trace cache before each test."""
        _dashboard_module._parsed_trace_cache.clear()

    def teardown_method(self):
        """Clear the parsed trace cache after each test."""
        _dashboard_module._parsed_trace_cache.clear()

    def test_returns_parsed_trace(self, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        assert "phases" in result
        assert "totals" in result

    def test_returns_none_for_missing_trace(self, tmp_path):
        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-999-20260101")

        assert result is None

    def test_includes_retry_trace_if_exists(self, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        retry_file = traces / "golem-42-20260101-retry.jsonl"
        _make_minimal_trace_jsonl(retry_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        assert "retry" in result
        assert str(retry_file) == result["retry"]["trace_file"]

    def test_includes_fix_iteration_traces(self, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        fix1 = traces / "golem-42-20260101-fix1.jsonl"
        _make_minimal_trace_jsonl(fix1)
        fix2 = traces / "golem-42-20260101-fix2.jsonl"
        _make_minimal_trace_jsonl(fix2)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        assert "fix_iterations" in result
        assert len(result["fix_iterations"]) == 2
        assert result["fix_iterations"][0]["iteration"] == 1
        assert result["fix_iterations"][1]["iteration"] == 2
        assert str(fix1) == result["fix_iterations"][0]["trace_file"]

    def test_no_fix_iterations_key_when_none_exist(self, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        assert "fix_iterations" not in result

    def test_completed_trace_is_cached(self, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result1 = _read_and_parse_trace("golem-42-20260101")

        assert result1 is not None
        assert "golem-42-20260101" in _dashboard_module._parsed_trace_cache

        # Delete the file — cache should still return the result
        trace_file.unlink()
        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result2 = _read_and_parse_trace("golem-42-20260101")

        assert result2 is result1

    def test_malformed_jsonl_skips_bad_lines(self, tmp_path):
        """Malformed JSONL lines are skipped; valid lines still parsed."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        trace_file.write_text("{not valid json at all!!!\n", encoding="utf-8")

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        assert result["phases"] == []

    def test_parse_exception_returns_empty_result(self, tmp_path):
        """If _parse_trace_structured raises, return empty ParsedTrace."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                with patch(
                    "golem.core.dashboard._parse_trace_structured",
                    side_effect=[RuntimeError("boom"), _parse_trace_structured([])],
                ):
                    result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        assert result["phases"] == []

    def test_trace_file_deleted_after_resolve(self, tmp_path):
        """TOCTOU: trace file vanishes between _resolve_paths and open."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        real_open = open  # noqa: SIM115

        def delete_then_open(path, **kwargs):
            if str(path) == str(trace_file):
                trace_file.unlink()
                return real_open(path, **kwargs)  # raises FileNotFoundError
            return real_open(path, **kwargs)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                with patch("builtins.open", side_effect=delete_then_open):
                    result = _read_and_parse_trace("golem-42-20260101")

        assert result is None

    def test_empty_lines_in_jsonl_skipped(self, tmp_path):
        """Empty lines in JSONL trace are silently skipped."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        trace_file.write_text(
            "\n\n"
            '{"type":"result","total_cost_usd":0.1,"duration_ms":1000,'
            '"num_turns":1,"is_error":false,"result":"","modelUsage":{}}\n'
            "\n",
            encoding="utf-8",
        )

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        assert result["phases"] == []

    def test_retry_with_malformed_line(self, tmp_path):
        """Malformed lines in retry JSONL are skipped, valid lines parsed."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        _make_minimal_trace_jsonl(traces / "golem-42-20260101.jsonl")
        retry_file = traces / "golem-42-20260101-retry.jsonl"
        retry_file.write_text(
            "not json!!!\n"
            '{"type":"result","total_cost_usd":0.2,"duration_ms":2000,'
            '"num_turns":2,"is_error":false,"result":"","modelUsage":{}}\n',
            encoding="utf-8",
        )

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        assert result["retry"] is not None

    def test_retry_file_not_found_is_silent(self, tmp_path):
        """When retry file doesn't exist, no retry field is set (no error)."""
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        _make_minimal_trace_jsonl(traces / "golem-42-20260101.jsonl")

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        assert result.get("retry") is None


# ---------------------------------------------------------------------------
# HTTP route tests for /api/trace-parsed/ (Task 3.2)
# ---------------------------------------------------------------------------


class TestApiTraceParsedHTTP:
    """Test the /api/trace-parsed/ route using the handler-capture pattern."""

    @pytest.fixture()
    def handlers(self):
        """Mount dashboard and capture route handlers."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(app, config_snapshot={"model": "test"})
        return routes

    def setup_method(self):
        """Clear the parsed trace cache before each test."""
        _dashboard_module._parsed_trace_cache.clear()

    def teardown_method(self):
        """Clear the parsed trace cache after each test."""
        _dashboard_module._parsed_trace_cache.clear()

    @pytest.mark.asyncio
    async def test_404_for_missing_trace(self, handlers):
        with patch(
            "golem.core.dashboard._read_and_parse_trace",
            return_value=None,
        ):
            resp = await handlers["/api/trace-parsed/{event_id:path}"]("golem-999")
        assert resp.status_code == 404
        body = json.loads(resp.body)
        assert "error" in body

    @pytest.mark.asyncio
    async def test_200_with_valid_trace(self, handlers, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                resp = await handlers["/api/trace-parsed/{event_id:path}"](
                    "golem-42-20260101"
                )

        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert "phases" in body
        assert "totals" in body

    @pytest.mark.asyncio
    async def test_content_type_is_json(self, handlers, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                resp = await handlers["/api/trace-parsed/{event_id:path}"](
                    "golem-42-20260101"
                )

        assert "application/json" in resp.media_type

    @pytest.mark.asyncio
    async def test_since_event_query_param(self, handlers, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        captured_args: list = []

        def fake_read_and_parse(event_id, since_event=0):
            captured_args.append(since_event)
            return {"phases": [], "totals": {}}

        with patch(
            "golem.core.dashboard._read_and_parse_trace",
            side_effect=fake_read_and_parse,
        ):
            resp = await handlers["/api/trace-parsed/{event_id:path}"](
                "golem-42-20260101", since_event=1
            )

        assert resp.status_code == 200
        assert captured_args == [1]

    def test_lru_cache_eviction(self, tmp_path):
        """When cache is full, adding a new entry evicts the oldest."""
        assert _MAX_TRACE_CACHE == 100
        # Fill cache to capacity
        for i in range(_MAX_TRACE_CACHE):
            _dashboard_module._parsed_trace_cache[f"fill-{i}"] = {
                "phases": [],
                "result_meta": {"total_cost_usd": 0},
            }
        assert len(_dashboard_module._parsed_trace_cache) == _MAX_TRACE_CACHE
        assert "fill-0" in _dashboard_module._parsed_trace_cache

        # Create a trace file that will be parsed and cached
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        _make_minimal_trace_jsonl(traces / "golem-42-20260101.jsonl")

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _read_and_parse_trace("golem-42-20260101")

        assert result is not None
        # New entry should be cached, oldest should be evicted
        assert "golem-42-20260101" in _dashboard_module._parsed_trace_cache
        assert "fill-0" not in _dashboard_module._parsed_trace_cache
        assert len(_dashboard_module._parsed_trace_cache) == _MAX_TRACE_CACHE
