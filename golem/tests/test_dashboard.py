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
    _is_within,
    _parse_trace,
    _parse_trace_terminal,
    _read_and_parse_trace,
    _read_log_tail,
    _read_sessions,
    _resolve_paths,
    _safe_to_thread,
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

    def test_path_traversal_returns_all_none(self, tmp_path):
        """event_id with path-traversal sequences returns all None (not found)."""
        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _resolve_paths("../../etc/passwd")
        assert result == {"trace": None, "prompt": None, "report": None}

    def test_golem_with_valid_files_still_resolves(self, tmp_path):
        """SEC-002: valid golem-1 event_id still resolves files after adding _is_within check."""
        traces = tmp_path / "traces" / "golem"
        reports = tmp_path / "reports" / "golem"
        traces.mkdir(parents=True)
        reports.mkdir(parents=True)
        (traces / "golem-1.jsonl").write_text("{}", encoding="utf-8")
        (traces / "golem-1.prompt.txt").write_text("prompt", encoding="utf-8")
        (reports / "1.md").write_text("# Report", encoding="utf-8")
        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                result = _resolve_paths("golem-1")
        assert result["trace"] is not None
        assert result["prompt"] is not None
        assert result["report"] is not None


# ---------------------------------------------------------------------------
# _is_within
# ---------------------------------------------------------------------------


class TestIsWithin:
    def test_rejects_path_outside_base(self, tmp_path):
        """Path outside the base directory is rejected."""
        base = tmp_path / "base"
        base.mkdir()
        outside = tmp_path / "other" / "file.txt"
        assert not _is_within(outside, base)

    def test_accepts_path_inside_base(self, tmp_path):
        """Path inside the base directory is accepted."""
        base = tmp_path / "base"
        base.mkdir()
        inside = base / "subdir" / "file.txt"
        assert _is_within(inside, base)

    def test_rejects_dotdot_traversal(self, tmp_path):
        """Path using .. to escape base directory is rejected."""
        base = tmp_path / "base"
        base.mkdir()
        traversal = base / ".." / "secret.txt"
        assert not _is_within(traversal, base)

    def test_accepts_base_itself(self, tmp_path):
        """Base directory itself is within base."""
        base = tmp_path / "base"
        base.mkdir()
        assert _is_within(base, base)


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
    def test_pid_stale(self, mock_read_pid, _mock_kill):
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
        self, mock_runs, mock_snap, _mock_daemon, _mock_sessions
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
                mount_dashboard(app, _config_snapshot={"model": "opus"})
        assert app.get.call_count >= 5  # pylint: disable=no-member


class TestMountDashboardRoutes:  # pylint: disable=too-many-public-methods
    """Test actual route handler logic by capturing the registered handlers."""

    @pytest.fixture(autouse=True)
    def _bypass_api_key(self):
        """Patch _require_api_key to a no-op for all direct handler invocations.

        These tests call route handlers as plain Python functions (bypassing the
        FastAPI request pipeline), so authentication is irrelevant here.
        """
        with patch("golem.core.dashboard._require_api_key"):
            yield

    @pytest.fixture()
    def handlers(self):
        """Mount dashboard on a mock app and return a dict of route handlers."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **_kwargs):
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
                    _config_snapshot={"model": "test"},
                    live_state_file=None,
                )
        return routes

    async def test_api_ping(self, handlers):
        resp = await handlers["/api/ping"]()
        data = json.loads(resp.body)
        assert data["status"] == "ok"
        assert isinstance(data["timestamp"], int)

    async def test_api_live_with_file(self):
        """When live_state_file is set, it reads from disk."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **_kwargs):
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
            with patch("golem.core.dashboard._require_api_key"):
                resp = await routes["/api/live"](MagicMock())
        body = json.loads(resp.body)
        assert body["active_count"] == 0

    async def test_api_live_without_file(self, handlers):
        """When live_state_file is None, uses LiveState singleton."""
        with patch("golem.core.dashboard.LiveState") as mock_ls:
            mock_ls.get.return_value.snapshot.return_value = {"active_count": 1}
            resp = await handlers["/api/live"](MagicMock())
        body = json.loads(resp.body)
        assert body["active_count"] == 1

    async def test_api_sessions(self, handlers):
        with patch(
            "golem.core.dashboard._read_sessions",
            return_value={"sessions": {"1": {}}},
        ):
            resp = await handlers["/api/sessions"](MagicMock())
        body = json.loads(resp.body)
        assert "1" in body["sessions"]

    async def test_api_sessions_error(self, handlers):
        with patch(
            "golem.core.dashboard._read_sessions",
            side_effect=json.JSONDecodeError("bad", "", 0),
        ):
            resp = await handlers["/api/sessions"](MagicMock())
        body = json.loads(resp.body)
        assert body == {"sessions": {}}

    async def test_api_sessions_during_shutdown(self, handlers):
        """api_sessions returns empty data when executor is shut down."""
        old = _dashboard_module._shutting_down
        _dashboard_module._shutting_down = True
        try:
            with patch(
                "golem.core.dashboard._read_sessions",
                side_effect=RuntimeError("Executor shutdown"),
            ):
                resp = await handlers["/api/sessions"](MagicMock())
            body = json.loads(resp.body)
            assert body == {"sessions": {}}
        finally:
            _dashboard_module._shutting_down = old

    async def test_api_logs(self, handlers):
        with patch(
            "golem.core.dashboard._read_log_tail",
            return_value={"lines": ["hi"], "file": "test.log", "total_lines": 1},
        ):
            resp = await handlers["/api/logs"](MagicMock(), lines=200)
        body = json.loads(resp.body)
        assert body["lines"] == ["hi"]

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
            resp = await handlers["/api/trace/{event_id:path}"](MagicMock(), "golem-1")
        body = json.loads(resp.body)
        assert body["event_id"] == "golem-1"
        assert len(body["sections"]) == 1

    async def test_api_trace_not_found(self, handlers):
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/trace/{event_id:path}"](
                MagicMock(), "golem-999"
            )
        assert resp.status_code == 404

    async def test_api_prompt_found(self, handlers, tmp_path):
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("Do the thing", encoding="utf-8")
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": prompt_path, "report": None},
        ):
            resp = await handlers["/api/prompt/{event_id:path}"](MagicMock(), "golem-1")
        body = json.loads(resp.body)
        assert body["prompt"] == "Do the thing"

    async def test_api_prompt_not_found(self, handlers):
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/prompt/{event_id:path}"](MagicMock(), "golem-1")
        assert resp.status_code == 404

    async def test_api_report_found(self, handlers, tmp_path):
        report_path = tmp_path / "report.md"
        report_path.write_text("# Report\nAll good", encoding="utf-8")
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": report_path},
        ):
            resp = await handlers["/api/report/{event_id:path}"](MagicMock(), "golem-1")
        body = json.loads(resp.body)
        assert body["markdown"] == "# Report\nAll good"

    async def test_api_report_not_found(self, handlers):
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/report/{event_id:path}"](MagicMock(), "golem-1")
        assert resp.status_code == 404

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
            resp = await handlers["/api/trace-terminal/{event_id:path}"](
                MagicMock(), "golem-1"
            )
        body = json.loads(resp.body)
        assert "events" in body
        assert "stats" in body

    async def test_api_trace_terminal_not_found(self, handlers):
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": None},
        ):
            resp = await handlers["/api/trace-terminal/{event_id:path}"](
                MagicMock(), "golem-1"
            )
        assert resp.status_code == 404

    async def test_dashboard_html(self, handlers):
        with patch.object(_FileCache, "read", return_value="<html>dash</html>"):
            resp = await handlers["/dashboard"]()
        assert b"dash" in resp.body

    async def test_admin_html(self, handlers):
        with patch.object(_FileCache, "read", return_value="<html>admin</html>"):
            resp = await handlers["/dashboard/admin"]()
        assert b"admin" in resp.body

    async def test_shared_css(self, handlers):
        with patch.object(_FileCache, "read", return_value="body { }"):
            resp = await handlers["/dashboard/shared.css"]()
        assert resp.media_type == "text/css"
        assert b"body { }" in resp.body

    async def test_shared_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="console.log(1)"):
            resp = await handlers["/dashboard/shared.js"]()
        assert resp.media_type == "application/javascript"
        assert b"console.log(1)" in resp.body

    async def test_task_css(self, handlers):
        with patch.object(_FileCache, "read", return_value=".wf-table { }"):
            resp = await handlers["/dashboard/task.css"]()
        assert resp.media_type == "text/css"
        assert b".wf-table { }" in resp.body

    async def test_task_api_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="const S={}"):
            resp = await handlers["/dashboard/task_api.js"]()
        assert resp.media_type == "application/javascript"

    async def test_task_timeline_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="function renderDetail(){}"):
            resp = await handlers["/dashboard/task_timeline.js"]()
        assert resp.media_type == "application/javascript"

    async def test_task_overview_js(self, handlers):
        with patch.object(
            _FileCache, "read", return_value="function renderOverview(){}"
        ):
            resp = await handlers["/dashboard/task_overview.js"]()
        assert resp.media_type == "application/javascript"

    async def test_task_live_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="function startPolling(){}"):
            resp = await handlers["/dashboard/task_live.js"]()
        assert resp.media_type == "application/javascript"

    async def test_heartbeat_widget_js(self, handlers):
        with patch.object(
            _FileCache, "read", return_value="function renderHeartbeatChip(){}"
        ):
            resp = await handlers["/dashboard/heartbeat_widget.js"]()
        assert resp.media_type == "application/javascript"

    async def test_elk_js(self, handlers):
        with patch.object(_FileCache, "read", return_value="var ELK={}"):
            resp = await handlers["/dashboard/elk.js"]()
        assert resp.media_type == "application/javascript"

    async def test_merge_queue_js(self, handlers):
        with patch.object(
            _FileCache, "read", return_value="function renderMergeQueue(){}"
        ):
            resp = await handlers["/dashboard/merge_queue.js"]()
        assert resp.media_type == "application/javascript"

    async def test_merge_queue_css(self, handlers):
        with patch.object(_FileCache, "read", return_value=".mq-view{}"):
            resp = await handlers["/dashboard/merge_queue.css"]()
        assert resp.media_type == "text/css"

    async def test_config_tab_js(self, handlers):
        with patch.object(
            _FileCache, "read", return_value="function initConfigTab(){}"
        ):
            resp = await handlers["/dashboard/config_tab.js"]()
        assert resp.media_type == "application/javascript"

    async def test_config_tab_css(self, handlers):
        with patch.object(_FileCache, "read", return_value=".config-container{}"):
            resp = await handlers["/dashboard/config_tab.css"]()
        assert resp.media_type == "text/css"

    async def test_prompt_analytics_js(self, handlers):
        with patch.object(
            _FileCache, "read", return_value="async function renderPromptAnalytics(){}"
        ):
            resp = await handlers["/dashboard/prompt_analytics.js"]()
        assert resp.media_type == "application/javascript"
        assert b"renderPromptAnalytics" in resp.body

    def test_dashboard_html_has_config_tab(self):
        """Config tab nav button and view div must be in the HTML."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert 'data-view="config"' in body, "Missing config nav-tab button"
        assert 'id="view-config"' in body, "Missing view-config div"
        assert "config_tab.js" in body, "Missing config_tab.js script tag"
        assert "config_tab.css" in body, "Missing config_tab.css link tag"

    async def test_api_heartbeat_disabled(self, handlers):
        """When heartbeat is None, returns disabled stub."""
        resp = await handlers["/api/heartbeat"](MagicMock())
        assert resp["enabled"] is False
        assert resp["state"] == "disabled"
        assert resp["daily_spend_usd"] == 0.0
        assert resp["next_tick_seconds"] == 0

    async def test_api_heartbeat_enabled(self):
        """When heartbeat is provided, returns its snapshot."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **_kwargs):
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

        with patch("golem.core.dashboard._require_api_key"):
            resp = await routes["/api/heartbeat"](MagicMock())
        assert resp["enabled"] is True
        assert resp["state"] == "idle"
        mock_hb.snapshot.assert_called_once()

    async def test_api_heartbeat_trigger_disabled(self):
        """POST /api/heartbeat/trigger returns error when heartbeat is None."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **_kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route
        app.post = capture_route
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(app, heartbeat=None)

        with patch("golem.core.dashboard._require_api_key"):
            resp = await routes["/api/heartbeat/trigger"](MagicMock())
        assert resp["ok"] is False

    async def test_api_heartbeat_trigger_enabled(self):
        """POST /api/heartbeat/trigger calls trigger() on heartbeat."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **_kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route
        app.post = capture_route

        mock_hb = MagicMock()
        mock_hb.trigger.return_value = True
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(app, heartbeat=mock_hb)

        with patch("golem.core.dashboard._require_api_key"):
            resp = await routes["/api/heartbeat/trigger"](MagicMock())
        assert resp["ok"] is True
        mock_hb.trigger.assert_called_once()

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
        assert "heartbeat_widget.js" in body, "Missing heartbeat_widget.js script tag"
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

    # --- UX-001: Accessibility improvements ---

    def test_html_has_main_landmark(self):
        """Main content area has role=main for screen reader navigation."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert 'role="main"' in body, "Missing role=main landmark"

    def test_html_nav_has_aria_label(self):
        """Top navigation has aria-label for screen readers."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert 'aria-label="Main navigation"' in body, "Missing aria-label on nav"

    def test_html_modal_has_dialog_role(self):
        """Resubmit modal has role=dialog and aria-modal=true."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert 'role="dialog"' in body, "Missing role=dialog on modal"
        assert 'aria-modal="true"' in body, "Missing aria-modal=true on modal"

    def test_html_modal_has_aria_labelledby(self):
        """Resubmit modal title is linked via aria-labelledby."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert "aria-labelledby=" in body, "Missing aria-labelledby on modal"
        assert 'id="resubmit-modal-title"' in body, "Missing modal title id"

    def test_html_theme_button_has_aria_label(self):
        """Theme toggle button has aria-label describing its action."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert (
            'aria-label="Toggle light/dark theme"' in body
        ), "Missing aria-label on theme toggle button"

    def test_css_has_focus_visible_rule(self):
        """Shared CSS defines a :focus-visible outline for keyboard navigation."""
        css = Path(__file__).resolve().parent.parent / "core" / "dashboard_shared.css"
        body = css.read_text(encoding="utf-8")
        assert ":focus-visible" in body, "Missing :focus-visible focus indicator"

    def test_css_dark_text_muted_meets_wcag_aa(self):
        """Dark theme --text-muted has sufficient contrast for WCAG AA (≥4.5:1).

        Contrast is computed against --bg-base (#0c0c0e).
        Previous value #605e56 gave ~3.1:1; updated to #7c7a72 (~4.9:1).
        """
        css = Path(__file__).resolve().parent.parent / "core" / "dashboard_shared.css"
        body = css.read_text(encoding="utf-8")
        # The updated muted colour must not be the old, too-dark value
        assert (
            "--text-muted: #605e56" not in body
        ), "--text-muted reverted to low-contrast value"
        assert (
            "--text-muted: #7c7a72" in body
        ), "--text-muted not updated to WCAG AA value"

    # --- shutdown-guard tests for _safe_to_thread returning None ---

    async def test_api_live_shutdown(self):
        """api_live returns empty when _safe_to_thread returns None."""
        app = MagicMock()
        routes: dict = {}

        def capture(path, **_kw):
            def dec(fn):
                routes[path] = fn
                return fn

            return dec

        app.get = capture
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(app, live_state_file=Path("/fake"))

        with patch("golem.core.dashboard._safe_to_thread", return_value=None):
            with patch("golem.core.dashboard._require_api_key"):
                resp = await routes["/api/live"](MagicMock())
        assert json.loads(resp.body) == {}

    async def test_api_logs_shutdown(self, handlers):
        with patch("golem.core.dashboard._safe_to_thread", return_value=None):
            resp = await handlers["/api/logs"](MagicMock(), lines=200)
        body = json.loads(resp.body)
        assert body == {"lines": [], "file": "", "total_lines": 0}

    async def test_api_analytics_shutdown(self, handlers):
        with patch("golem.core.dashboard._safe_to_thread", return_value=None):
            resp = await handlers["/api/analytics"](MagicMock())
        assert json.loads(resp.body) == {}

    async def test_api_analytics_by_prompt_shutdown(self, handlers):
        with patch("golem.core.dashboard._safe_to_thread", return_value=None):
            resp = await handlers["/api/analytics/by-prompt"](MagicMock())
        assert json.loads(resp.body) == {}

    async def test_api_trace_shutdown(self, handlers, tmp_path):
        trace_path = tmp_path / "trace.jsonl"
        trace_path.write_text("{}\n", encoding="utf-8")
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": trace_path, "prompt": None, "report": None},
        ):
            with patch("golem.core.dashboard._safe_to_thread", return_value=None):
                resp = await handlers["/api/trace/{event_id:path}"](
                    MagicMock(), "golem-1"
                )
        assert json.loads(resp.body) == {}

    async def test_api_prompt_shutdown(self, handlers, tmp_path):
        prompt_path = tmp_path / "prompt.txt"
        prompt_path.write_text("x", encoding="utf-8")
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": prompt_path, "report": None},
        ):
            with patch("golem.core.dashboard._safe_to_thread", return_value=None):
                resp = await handlers["/api/prompt/{event_id:path}"](
                    MagicMock(), "golem-1"
                )
        assert json.loads(resp.body) == {}

    async def test_api_report_shutdown(self, handlers, tmp_path):
        report_path = tmp_path / "report.md"
        report_path.write_text("x", encoding="utf-8")
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": None, "prompt": None, "report": report_path},
        ):
            with patch("golem.core.dashboard._safe_to_thread", return_value=None):
                resp = await handlers["/api/report/{event_id:path}"](
                    MagicMock(), "golem-1"
                )
        assert json.loads(resp.body) == {}

    async def test_api_trace_terminal_shutdown(self, handlers, tmp_path):
        trace_path = tmp_path / "trace.jsonl"
        trace_path.write_text("{}\n", encoding="utf-8")
        with patch(
            "golem.core.dashboard._resolve_paths",
            return_value={"trace": trace_path, "prompt": None, "report": None},
        ):
            with patch("golem.core.dashboard._safe_to_thread", return_value=None):
                resp = await handlers["/api/trace-terminal/{event_id:path}"](
                    MagicMock(), "golem-1"
                )
        assert json.loads(resp.body) == {}


# ---------------------------------------------------------------------------
# /api/cost-analytics endpoint
# ---------------------------------------------------------------------------


class TestCostAnalyticsEndpoint:
    """Test the /api/cost-analytics route handler."""

    @pytest.fixture(autouse=True)
    def _no_api_key_auth(self):
        """Bypass API key authentication for direct handler call tests."""
        with patch("golem.core.dashboard._require_api_key"):
            yield

    @pytest.fixture()
    def handlers(self):
        """Mount dashboard on a mock app and return a dict of route handlers."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **_kwargs):
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
                    _config_snapshot={"model": "test"},
                    live_state_file=None,
                )
        return routes

    def test_route_is_registered(self, handlers):
        assert "/api/cost-analytics" in handlers

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
                resp = await handlers["/api/cost-analytics"](MagicMock())
        body = json.loads(resp.body)
        for key in (
            "cost_over_time",
            "cost_by_verdict",
            "cost_per_retry",
            "budget_utilization",
            "summary",
        ):
            assert key in body, f"Missing key: {key}"

    async def test_returns_json_response(self, handlers):
        with patch(
            "golem.core.dashboard.read_runs",
            return_value=[],
        ):
            with patch(
                "golem.core.dashboard.load_sessions",
                return_value={},
            ):
                resp = await handlers["/api/cost-analytics"](MagicMock())
        body = json.loads(resp.body)
        assert isinstance(body, dict)

    async def test_shutdown_returns_empty(self, handlers):
        with patch("golem.core.dashboard._safe_to_thread", return_value=None):
            resp = await handlers["/api/cost-analytics"](MagicMock())
        assert json.loads(resp.body) == {}


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

    @pytest.fixture(autouse=True)
    def _no_api_key_auth(self):
        """Bypass API key authentication for direct handler call tests."""
        with patch("golem.core.dashboard._require_api_key"):
            yield

    @pytest.fixture()
    def handlers(self):
        """Mount dashboard and capture route handlers."""
        app = MagicMock()
        routes: dict = {}

        def capture_route(path, **_kwargs):
            def decorator(fn):
                routes[path] = fn
                return fn

            return decorator

        app.get = capture_route
        with patch("golem.core.dashboard.FASTAPI_AVAILABLE", True):
            with patch(
                "golem.core.dashboard.Query", lambda default=None, **kw: default
            ):
                mount_dashboard(app, _config_snapshot={"model": "test"})
        return routes

    def setup_method(self):
        """Clear the parsed trace cache before each test."""
        _dashboard_module._parsed_trace_cache.clear()

    def teardown_method(self):
        """Clear the parsed trace cache after each test."""
        _dashboard_module._parsed_trace_cache.clear()

    async def test_404_for_missing_trace(self, handlers):
        with patch(
            "golem.core.dashboard._read_and_parse_trace",
            return_value=None,
        ):
            resp = await handlers["/api/trace-parsed/{event_id:path}"](
                MagicMock(), "golem-999"
            )
        assert resp.status_code == 404
        body = json.loads(resp.body)
        assert "error" in body

    async def test_200_with_valid_trace(self, handlers, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                resp = await handlers["/api/trace-parsed/{event_id:path}"](
                    MagicMock(), "golem-42-20260101"
                )

        assert resp.status_code == 200
        body = json.loads(resp.body)
        assert "phases" in body
        assert "totals" in body

    async def test_content_type_is_json(self, handlers, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        with patch("golem.core.dashboard.TRACES_DIR", tmp_path / "traces"):
            with patch("golem.core.dashboard.REPORTS_DIR", tmp_path / "reports"):
                resp = await handlers["/api/trace-parsed/{event_id:path}"](
                    MagicMock(), "golem-42-20260101"
                )

        assert "application/json" in resp.media_type

    async def test_since_event_query_param(self, handlers, tmp_path):
        traces = tmp_path / "traces" / "golem"
        traces.mkdir(parents=True)
        trace_file = traces / "golem-42-20260101.jsonl"
        _make_minimal_trace_jsonl(trace_file)

        captured_args: list = []

        def fake_read_and_parse(_event_id, since_event=0):
            captured_args.append(since_event)
            return {"phases": [], "totals": {}}

        with patch(
            "golem.core.dashboard._read_and_parse_trace",
            side_effect=fake_read_and_parse,
        ):
            resp = await handlers["/api/trace-parsed/{event_id:path}"](
                MagicMock(), "golem-42-20260101", since_event=1
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


# ---------------------------------------------------------------------------
# _safe_to_thread — shutdown-aware thread dispatch
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# UX-006: Toast notification system
# ---------------------------------------------------------------------------


class TestToastNotificationSystem:
    """Verify toast CSS classes, showToast function, and alert() removal."""

    @pytest.fixture(autouse=True)
    def _paths(self):
        core = Path(__file__).resolve().parent.parent / "core"
        self.css_path = core / "dashboard_shared.css"
        self.js_path = core / "dashboard_shared.js"
        self.html_path = core / "task_dashboard.html"
        self.timeline_path = core / "task_timeline.js"

    # --- CSS ---

    def test_toast_container_class_in_css(self):
        """CSS must define .toast-container positioned at bottom-right."""
        body = self.css_path.read_text(encoding="utf-8")
        assert ".toast-container" in body, "Missing .toast-container rule in CSS"

    def test_toast_class_in_css(self):
        """CSS must define .toast base class."""
        body = self.css_path.read_text(encoding="utf-8")
        assert ".toast{" in body or ".toast {" in body, "Missing .toast rule in CSS"

    @pytest.mark.parametrize("variant", ["success", "error", "info"])
    def test_toast_variant_class_in_css(self, variant):
        """CSS must define .toast.success, .toast.error, and .toast.info."""
        body = self.css_path.read_text(encoding="utf-8")
        assert f".toast.{variant}" in body, f"Missing .toast.{variant} rule in CSS"

    def test_toast_slide_in_animation_in_css(self):
        """CSS must define @keyframes toast-slide-in for the entrance animation."""
        body = self.css_path.read_text(encoding="utf-8")
        assert (
            "toast-slide-in" in body
        ), "Missing toast-slide-in keyframe animation in CSS"

    def test_toast_fixed_position_in_css(self):
        """Toast container must use position:fixed."""
        body = self.css_path.read_text(encoding="utf-8")
        # The rule is in the .toast-container block
        idx = body.find(".toast-container")
        assert idx != -1, "Missing .toast-container in CSS"
        block = body[idx : idx + 200]
        assert "position:fixed" in block, ".toast-container must use position:fixed"

    def test_btn_loading_class_in_css(self):
        """CSS must define .btn-loading for button loading states."""
        body = self.css_path.read_text(encoding="utf-8")
        assert ".btn-loading" in body, "Missing .btn-loading class in CSS"

    def test_btn_loading_spinner_animation(self):
        """CSS must define @keyframes btn-spin for the button loading spinner."""
        body = self.css_path.read_text(encoding="utf-8")
        assert "btn-spin" in body, "Missing btn-spin keyframe animation in CSS"

    # --- JS ---

    def test_show_toast_function_in_shared_js(self):
        """showToast must be declared in dashboard_shared.js."""
        body = self.js_path.read_text(encoding="utf-8")
        assert "function showToast(" in body, "Missing showToast function in shared JS"

    def test_show_toast_creates_toast_container(self):
        """showToast must reference the toast-container element."""
        body = self.js_path.read_text(encoding="utf-8")
        assert (
            "toast-container" in body
        ), "showToast must reference toast-container element"

    def test_show_toast_sets_role_alert(self):
        """showToast must set role=alert for accessibility."""
        body = self.js_path.read_text(encoding="utf-8")
        assert (
            "role" in body and "alert" in body
        ), "showToast must set role=alert on toast element"

    def test_show_toast_sets_aria_live(self):
        """showToast must set aria-live=assertive for accessibility."""
        body = self.js_path.read_text(encoding="utf-8")
        assert "aria-live" in body, "showToast must set aria-live attribute"
        assert "assertive" in body, "showToast must use aria-live=assertive"

    def test_show_toast_auto_removes_after_duration(self):
        """showToast must use setTimeout to auto-dismiss the toast."""
        body = self.js_path.read_text(encoding="utf-8")
        assert "setTimeout" in body, "showToast must use setTimeout for auto-dismiss"

    # --- HTML ---

    def test_html_has_toast_container_element(self):
        """task_dashboard.html must include the toast-container div."""
        body = self.html_path.read_text(encoding="utf-8")
        assert (
            'id="toast-container"' in body
        ), "Missing toast-container div in task_dashboard.html"

    def test_html_toast_container_has_aria_live(self):
        """Toast container element must have aria-live for screen readers."""
        body = self.html_path.read_text(encoding="utf-8")
        idx = body.find('id="toast-container"')
        assert idx != -1, "Missing toast-container in HTML"
        snippet = body[idx : idx + 150]
        assert "aria-live" in snippet, "toast-container must have aria-live attribute"

    # --- alert() removal ---

    def test_no_alert_calls_in_task_timeline(self):
        """All alert() calls must have been replaced by showToast() in task_timeline.js."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert (
            "alert(" not in body
        ), "Found alert() call in task_timeline.js — replace with showToast()"

    def test_no_alert_calls_in_task_dashboard_html(self):
        """No alert() calls should remain in task_dashboard.html inline JS."""
        body = self.html_path.read_text(encoding="utf-8")
        assert "alert(" not in body, "Found alert() call in task_dashboard.html"

    def test_show_toast_calls_in_task_timeline(self):
        """task_timeline.js must use showToast() for user feedback."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert "showToast(" in body, "No showToast() calls found in task_timeline.js"

    @pytest.mark.parametrize(
        "snippet",
        [
            "showToast('Cancel failed:",
            "showToast('Re-run failed:",
            "showToast('Submit failed:",
            "showToast('Could not fetch task prompt.",
            "showToast('Could not load task prompt:",
            "showToast('Prompt cannot be empty.",
        ],
        ids=[
            "cancel_error",
            "rerun_error",
            "submit_error",
            "prompt_fetch_error",
            "prompt_load_error",
            "empty_prompt_info",
        ],
    )
    def test_specific_toast_messages_present(self, snippet):
        """Each specific user-facing message must call showToast(), not alert()."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert snippet in body, f"Missing showToast call for: {snippet!r}"

    def test_error_toasts_use_error_type(self):
        """Error-type showToast calls must pass 'error' as the type argument."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert (
            "showToast('Cancel failed:" in body and "'error'" in body
        ), "Error toasts must use type 'error'"

    def test_btn_loading_class_applied_on_cancel(self):
        """Cancel button should gain btn-loading class during async operation."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert (
            "btn-loading" in body
        ), "Missing btn-loading class usage in task_timeline.js"

    @pytest.mark.parametrize(
        "snippet",
        [
            "showToast('Task cancelled', 'success')",
            "showToast('Task resubmitted', 'success')",
            "showToast('Task submitted', 'success')",
        ],
        ids=[
            "cancel_success",
            "rerun_success",
            "submit_success",
        ],
    )
    def test_success_toasts_present_in_task_timeline(self, snippet):
        """Each successful async operation must show a 'success' toast."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert snippet in body, f"Missing success showToast call: {snippet!r}"

    def test_success_toasts_precede_rerender(self):
        """Success toasts must be called before re-render (before fetchSessions)."""
        body = self.timeline_path.read_text(encoding="utf-8")
        # For cancel: showToast comes before fetchSessions in the ok branch
        cancel_ok_idx = body.index("showToast('Task cancelled', 'success')")
        cancel_fetch_idx = body.index(
            "S.sessions = await fetchSessions();", cancel_ok_idx
        )
        assert (
            cancel_ok_idx < cancel_fetch_idx
        ), "Cancel success toast must appear before fetchSessions call"
        # For rerun: showToast comes before fetchSessions in the ok branch
        rerun_ok_idx = body.index("showToast('Task resubmitted', 'success')")
        rerun_fetch_idx = body.index(
            "S.sessions = await fetchSessions();", rerun_ok_idx
        )
        assert (
            rerun_ok_idx < rerun_fetch_idx
        ), "Rerun success toast must appear before fetchSessions call"
        # For submit modal: showToast comes before _closeResubmitModal
        submit_ok_idx = body.index("showToast('Task submitted', 'success')")
        close_modal_idx = body.index("_closeResubmitModal();", submit_ok_idx)
        assert (
            submit_ok_idx < close_modal_idx
        ), "Submit success toast must appear before _closeResubmitModal call"


class TestSafeToThread:
    async def test_normal_call(self):
        result = await _safe_to_thread(lambda: 42)
        assert result == 42

    async def test_returns_none_when_shutting_down(self):
        def _raise():
            raise RuntimeError("Executor shutdown has been called")

        old = _dashboard_module._shutting_down
        _dashboard_module._shutting_down = True
        try:
            result = await _safe_to_thread(_raise)
            assert result is None
        finally:
            _dashboard_module._shutting_down = old

    async def test_propagates_runtime_error_when_not_shutting_down(self):
        def _raise():
            raise RuntimeError("some other error")

        old = _dashboard_module._shutting_down
        _dashboard_module._shutting_down = False
        try:
            with pytest.raises(RuntimeError, match="some other error"):
                await _safe_to_thread(_raise)
        finally:
            _dashboard_module._shutting_down = old

    async def test_passes_kwargs(self):
        def add(a, b=0):
            return a + b

        result = await _safe_to_thread(add, 3, b=7)
        assert result == 10


# ---------------------------------------------------------------------------
# Loading States (UX-007)
# ---------------------------------------------------------------------------


class TestLoadingStates:
    """Verify loading spinner CSS and JS wiring for dashboard loading states."""

    @pytest.fixture(autouse=True)
    def _paths(self):
        core = Path(__file__).resolve().parent.parent / "core"
        self.css_path = core / "dashboard_shared.css"
        self.overview_path = core / "task_overview.js"
        self.timeline_path = core / "task_timeline.js"
        self.analytics_path = core / "prompt_analytics.js"

    # --- CSS: loading-spinner ---

    def test_loading_spinner_class_in_css(self):
        """CSS must define .loading-spinner for inline loading indicators."""
        body = self.css_path.read_text(encoding="utf-8")
        assert ".loading-spinner" in body, "Missing .loading-spinner rule in CSS"

    def test_loading_spinner_uses_spin_keyframes(self):
        """CSS .loading-spinner must reference the spin animation."""
        body = self.css_path.read_text(encoding="utf-8")
        idx = body.find(".loading-spinner")
        assert idx != -1, "Missing .loading-spinner in CSS"
        block = body[idx : idx + 300]
        assert "spin" in block, ".loading-spinner must use spin animation"

    def test_spin_keyframes_defined(self):
        """CSS must define @keyframes spin for the spinner rotation."""
        body = self.css_path.read_text(encoding="utf-8")
        assert "@keyframes spin" in body, "Missing @keyframes spin in CSS"

    def test_spin_keyframes_rotate(self):
        """@keyframes spin must rotate to 360deg."""
        body = self.css_path.read_text(encoding="utf-8")
        idx = body.find("@keyframes spin")
        assert idx != -1, "Missing @keyframes spin"
        block = body[idx : idx + 80]
        assert "rotate(360deg)" in block, "@keyframes spin must rotate(360deg)"

    # --- CSS: loading-overlay ---

    def test_loading_overlay_class_in_css(self):
        """CSS must define .loading-overlay for page-level loading containers."""
        body = self.css_path.read_text(encoding="utf-8")
        assert ".loading-overlay" in body, "Missing .loading-overlay rule in CSS"

    def test_loading_overlay_is_flex(self):
        """.loading-overlay must use display:flex for centering."""
        body = self.css_path.read_text(encoding="utf-8")
        idx = body.find(".loading-overlay{")
        assert idx != -1, "Missing .loading-overlay{ in CSS"
        block = body[idx : idx + 200]
        assert "flex" in block, ".loading-overlay must use display:flex"

    # --- CSS: skeleton classes actually referenced in JS ---

    def test_skeleton_card_used_in_overview_js(self):
        """skeleton-card class must be used in task_overview.js task list loading."""
        body = self.overview_path.read_text(encoding="utf-8")
        assert (
            "skeleton-card" in body
        ), "skeleton-card class not used in task_overview.js"

    def test_skeleton_class_used_in_overview_js(self):
        """skeleton class must be applied to skeleton placeholder elements."""
        body = self.overview_path.read_text(encoding="utf-8")
        assert (
            '"skeleton ' in body or "'skeleton " in body or "skeleton skeleton" in body
        ), "skeleton class not applied in task_overview.js"

    # --- JS: loading spinner wired up in task_overview.js ---

    def test_overview_shows_loading_spinner_in_preview(self):
        """renderPreview must show loading-spinner while trace is loading."""
        body = self.overview_path.read_text(encoding="utf-8")
        assert (
            "loading-spinner" in body
        ), "Missing loading-spinner usage in task_overview.js renderPreview"

    def test_overview_shows_loading_overlay_in_preview(self):
        """renderPreview must wrap the spinner in loading-overlay."""
        body = self.overview_path.read_text(encoding="utf-8")
        assert (
            "loading-overlay" in body
        ), "Missing loading-overlay usage in task_overview.js"

    # --- JS: loading spinner wired up in task_timeline.js ---

    def test_timeline_shows_loading_spinner_on_trace_fetch(self):
        """renderDetail must show loading-spinner while trace data is loading."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert (
            "loading-spinner" in body
        ), "Missing loading-spinner usage in task_timeline.js"

    def test_timeline_shows_loading_overlay_on_trace_fetch(self):
        """renderDetail must wrap the spinner in loading-overlay."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert (
            "loading-overlay" in body
        ), "Missing loading-overlay usage in task_timeline.js"

    def test_timeline_loading_shown_only_when_no_cache(self):
        """renderDetail must skip spinner when trace is already cached."""
        body = self.timeline_path.read_text(encoding="utf-8")
        # The guard must reference parsedTraces so it only shows when no cache
        assert (
            "parsedTraces" in body
        ), "renderDetail loading guard must check S.parsedTraces cache"

    # --- JS: loading spinner wired up in prompt_analytics.js ---

    def test_analytics_shows_loading_spinner(self):
        """renderPromptAnalytics must show loading-spinner before the fetch."""
        body = self.analytics_path.read_text(encoding="utf-8")
        assert (
            "loading-spinner" in body
        ), "Missing loading-spinner usage in prompt_analytics.js"

    def test_analytics_shows_loading_overlay(self):
        """renderPromptAnalytics must wrap spinner in loading-overlay."""
        body = self.analytics_path.read_text(encoding="utf-8")
        assert (
            "loading-overlay" in body
        ), "Missing loading-overlay usage in prompt_analytics.js"

    def test_analytics_loading_shown_before_fetch(self):
        """loading-overlay must appear before the fetch() call in analytics JS."""
        body = self.analytics_path.read_text(encoding="utf-8")
        overlay_idx = body.find("loading-overlay")
        fetch_idx = body.find("fetch(")
        assert overlay_idx != -1, "Missing loading-overlay in analytics JS"
        assert fetch_idx != -1, "Missing fetch() in analytics JS"
        assert (
            overlay_idx < fetch_idx
        ), "loading-overlay must appear before fetch() in prompt_analytics.js"


# ---------------------------------------------------------------------------
# UX-010: Copy-to-clipboard
# ---------------------------------------------------------------------------


class TestCopyToClipboard:
    """Verify copy-to-clipboard function, CSS, and wiring across dashboard JS files."""

    @pytest.fixture(autouse=True)
    def _paths(self):
        core = Path(__file__).resolve().parent.parent / "core"
        self.shared_js_path = core / "dashboard_shared.js"
        self.shared_css_path = core / "dashboard_shared.css"
        self.overview_path = core / "task_overview.js"
        self.timeline_path = core / "task_timeline.js"
        self.analytics_path = core / "prompt_analytics.js"

    # --- JS: copyToClipboard function in shared JS ---

    def test_copy_to_clipboard_function_exists(self):
        """copyToClipboard function must be defined in dashboard_shared.js."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert (
            "function copyToClipboard(" in body
        ), "Missing copyToClipboard function in dashboard_shared.js"

    def test_copy_to_clipboard_uses_navigator_clipboard(self):
        """copyToClipboard must use navigator.clipboard.writeText."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert (
            "navigator.clipboard.writeText" in body
        ), "copyToClipboard must use navigator.clipboard.writeText"

    def test_copy_to_clipboard_calls_show_toast_on_success(self):
        """copyToClipboard must call showToast on success."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert "showToast(" in body, "copyToClipboard must call showToast for feedback"

    def test_copy_to_clipboard_shows_copied_message(self):
        """copyToClipboard success toast must say 'Copied!'."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert "Copied!" in body, "copyToClipboard success message must be 'Copied!'"

    def test_copy_to_clipboard_handles_error(self):
        """copyToClipboard must show an error toast on failure."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        # The rejection handler must reference 'error' type toast
        assert (
            "'error'" in body or '"error"' in body
        ), "copyToClipboard must show error toast on failure"

    # --- CSS: copy-target class ---

    def test_copy_target_class_exists_in_css(self):
        """.copy-target CSS class must be defined in dashboard_shared.css."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        assert (
            ".copy-target" in body
        ), "Missing .copy-target CSS class in dashboard_shared.css"

    def test_copy_target_has_pointer_cursor(self):
        """.copy-target must set cursor:pointer."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        idx = body.find(".copy-target")
        assert idx != -1, "Missing .copy-target in CSS"
        block = body[idx : idx + 100]
        assert "cursor:pointer" in block, ".copy-target must have cursor:pointer"

    # --- JS: copy-target elements wired up ---

    def test_copy_target_used_in_overview_js(self):
        """task_overview.js must contain copy-target elements for session IDs."""
        body = self.overview_path.read_text(encoding="utf-8")
        assert (
            "copy-target" in body
        ), "Missing copy-target class usage in task_overview.js"

    def test_overview_copy_target_calls_copy_to_clipboard(self):
        """copy-target elements in task_overview.js must call copyToClipboard."""
        body = self.overview_path.read_text(encoding="utf-8")
        assert (
            "copyToClipboard(" in body
        ), "task_overview.js copy-target must call copyToClipboard()"

    def test_copy_target_used_in_timeline_js(self):
        """task_timeline.js must contain copy-target elements for task IDs or error text."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert (
            "copy-target" in body
        ), "Missing copy-target class usage in task_timeline.js"

    def test_timeline_copy_target_calls_copy_to_clipboard(self):
        """copy-target elements in task_timeline.js must call copyToClipboard."""
        body = self.timeline_path.read_text(encoding="utf-8")
        assert (
            "copyToClipboard(" in body
        ), "task_timeline.js copy-target must call copyToClipboard()"

    def test_copy_target_used_in_analytics_js(self):
        """prompt_analytics.js must contain copy-target elements for prompt hashes."""
        body = self.analytics_path.read_text(encoding="utf-8")
        assert (
            "copy-target" in body
        ), "Missing copy-target class usage in prompt_analytics.js"

    def test_analytics_copy_target_calls_copy_to_clipboard(self):
        """copy-target elements in prompt_analytics.js must call copyToClipboard."""
        body = self.analytics_path.read_text(encoding="utf-8")
        assert (
            "copyToClipboard(" in body
        ), "prompt_analytics.js copy-target must call copyToClipboard()"

    def test_copy_target_has_title_attribute(self):
        """copy-target elements must have title='Click to copy' for discoverability."""
        for path in (self.overview_path, self.timeline_path, self.analytics_path):
            body = path.read_text(encoding="utf-8")
            assert (
                'title="Click to copy"' in body or "title='Click to copy'" in body
            ), f"Missing title='Click to copy' on copy-target in {path.name}"


# ---------------------------------------------------------------------------
# UX-011: Deep linking / URL sharing
# ---------------------------------------------------------------------------


class TestHashRouting:
    """Verify hash-based URL routing for deep linking and view sharing."""

    @pytest.fixture(autouse=True)
    def _paths(self):
        core = Path(__file__).resolve().parent.parent / "core"
        self.shared_js_path = core / "dashboard_shared.js"
        self.api_js_path = core / "task_api.js"
        self.live_js_path = core / "task_live.js"

    # --- updateHash / getHashRoute in shared JS ---

    def test_update_hash_function_exists(self):
        """updateHash must be defined in dashboard_shared.js."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert (
            "function updateHash(" in body
        ), "Missing updateHash function in dashboard_shared.js"

    def test_get_hash_route_function_exists(self):
        """getHashRoute must be defined in dashboard_shared.js."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert (
            "function getHashRoute(" in body
        ), "Missing getHashRoute function in dashboard_shared.js"

    def test_update_hash_uses_replace_state(self):
        """updateHash must use history.replaceState to avoid polluting history."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        # Locate updateHash definition and check replaceState appears inside it
        start = body.find("function updateHash(")
        assert start != -1, "updateHash not found"
        block = body[start : start + 300]
        assert "replaceState" in block, "updateHash must call history.replaceState"

    def test_get_hash_route_reads_location_hash(self):
        """getHashRoute must read window.location.hash."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("function getHashRoute(")
        assert start != -1, "getHashRoute not found"
        block = body[start : start + 200]
        assert "location.hash" in block, "getHashRoute must read location.hash"

    # --- task_api.js: showView updates hash to #task/<id> ---

    def test_show_view_uses_task_hash_format(self):
        """showView must update URL to #task/<id> when navigating to task detail."""
        body = self.api_js_path.read_text(encoding="utf-8")
        assert (
            "'#task/'" in body or '"#task/"' in body or "`#task/" in body
        ), "showView must produce #task/<id> hash for task detail view"

    def test_show_view_uses_merge_queue_hash(self):
        """showView must update URL to #merge-queue when switching to that tab."""
        body = self.api_js_path.read_text(encoding="utf-8")
        assert (
            "'#merge-queue'" in body or '"#merge-queue"' in body
        ), "showView must set #merge-queue hash"

    def test_show_view_uses_prompts_hash(self):
        """showView must update URL to #prompts when switching to the prompts tab."""
        body = self.api_js_path.read_text(encoding="utf-8")
        assert (
            "'#prompts'" in body or '"#prompts"' in body
        ), "showView must set #prompts hash"

    def test_show_view_uses_overview_hash(self):
        """showView must update URL to #overview for the overview tab."""
        body = self.api_js_path.read_text(encoding="utf-8")
        assert (
            "'#overview'" in body or '"#overview"' in body
        ), "showView must set #overview hash"

    # --- task_live.js: DOMContentLoaded reads hash and navigates ---

    def test_dom_content_loaded_reads_hash(self):
        """DOMContentLoaded handler must call getHashRoute to restore view from URL."""
        body = self.live_js_path.read_text(encoding="utf-8")
        assert (
            "getHashRoute(" in body
        ), "DOMContentLoaded handler must call getHashRoute() to restore the view"

    def test_dom_content_loaded_handles_task_hash(self):
        """DOMContentLoaded handler must restore task detail from #task/<id>."""
        body = self.live_js_path.read_text(encoding="utf-8")
        assert (
            "task/" in body
        ), "DOMContentLoaded handler must handle #task/<id> deep links"

    def test_dom_content_loaded_handles_prompts_hash(self):
        """DOMContentLoaded handler must restore prompts tab from #prompts."""
        body = self.live_js_path.read_text(encoding="utf-8")
        assert (
            "'prompts'" in body or '"prompts"' in body
        ), "DOMContentLoaded handler must handle #prompts hash"

    # --- task_live.js: hashchange event listener ---

    def test_hashchange_listener_present(self):
        """task_live.js must register a hashchange event listener."""
        body = self.live_js_path.read_text(encoding="utf-8")
        assert "hashchange" in body, "task_live.js must listen for the hashchange event"

    def test_hashchange_calls_get_hash_route(self):
        """hashchange handler must use getHashRoute() to read the new URL."""
        body = self.live_js_path.read_text(encoding="utf-8")
        # getHashRoute must appear in or after the hashchange registration
        hashchange_idx = body.find("hashchange")
        assert hashchange_idx != -1, "hashchange listener not found"
        # getHashRoute must appear somewhere after the hashchange keyword
        after = body[hashchange_idx:]
        assert "getHashRoute(" in after, "hashchange handler must call getHashRoute()"

    def test_hashchange_handles_task_hash(self):
        """hashchange handler must navigate to task detail on #task/<id>."""
        body = self.live_js_path.read_text(encoding="utf-8")
        # Find hashchange block and confirm task/ handling is inside it
        hashchange_idx = body.find("hashchange")
        assert hashchange_idx != -1, "hashchange listener not found"
        after = body[hashchange_idx:]
        assert "task/" in after, "hashchange handler must handle #task/<id> hash format"

    def test_hashchange_handles_prompts_hash(self):
        """hashchange handler must navigate to prompts tab on #prompts."""
        body = self.live_js_path.read_text(encoding="utf-8")
        hashchange_idx = body.find("hashchange")
        assert hashchange_idx != -1, "hashchange listener not found"
        after = body[hashchange_idx:]
        assert (
            "'prompts'" in after or '"prompts"' in after
        ), "hashchange handler must handle #prompts hash"

    @pytest.mark.parametrize(
        "tab_hash",
        ["overview", "merge-queue", "config", "prompts"],
        ids=["overview", "merge-queue", "config", "prompts"],
    )
    def test_all_tabs_have_hash_support(self, tab_hash):
        """Every top-level tab must have hash routing coverage in task_live.js."""
        body = self.live_js_path.read_text(encoding="utf-8")
        assert (
            tab_hash in body
        ), f"task_live.js missing hash route support for #{tab_hash}"


# ---------------------------------------------------------------------------
# UX-008: Keyboard shortcuts
# ---------------------------------------------------------------------------


class TestKeyboardShortcuts:
    """Verify keyboard shortcut handler in dashboard_shared.js."""

    @pytest.fixture(autouse=True)
    def _paths(self):
        core = Path(__file__).resolve().parent.parent / "core"
        self.shared_js_path = core / "dashboard_shared.js"

    def test_keydown_listener_registered(self):
        """dashboard_shared.js must register a keydown event listener."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert "keydown" in body, "Missing keydown listener in dashboard_shared.js"

    def test_escape_key_handled(self):
        """Escape key must be handled in the keydown listener."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("keydown")
        assert start != -1, "keydown listener not found"
        after = body[start:]
        assert "Escape" in after, "Escape key not handled in keydown listener"

    def test_arrow_down_handled(self):
        """ArrowDown key must be handled in the keydown listener."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("keydown")
        assert start != -1, "keydown listener not found"
        after = body[start:]
        assert "ArrowDown" in after, "ArrowDown key not handled in keydown listener"

    def test_arrow_up_handled(self):
        """ArrowUp key must be handled in the keydown listener."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("keydown")
        assert start != -1, "keydown listener not found"
        after = body[start:]
        assert "ArrowUp" in after, "ArrowUp key not handled in keydown listener"

    def test_ctrl_k_handled(self):
        """Ctrl+K shortcut must be handled in the keydown listener."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("keydown")
        assert start != -1, "keydown listener not found"
        after = body[start:]
        # Both ctrlKey/metaKey check and key 'k' must appear
        assert "ctrlKey" in after, "ctrlKey not checked in keydown listener"
        assert (
            "'k'" in after or '"k"' in after
        ), "Ctrl+K key not matched in keydown listener"

    def test_ctrl_k_focuses_search(self):
        """Ctrl+K handler must call focus() on the search input."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("ctrlKey")
        assert start != -1, "ctrlKey check not found"
        # The focus() call must appear after the ctrlKey check
        after = body[start:]
        assert ".focus()" in after, "Ctrl+K handler must call .focus() on search input"

    def test_digit_tab_switching_present(self):
        """Number keys 1–5 must switch tabs in the keydown listener."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("keydown")
        assert start != -1, "keydown listener not found"
        after = body[start:]
        # The tab-views array must include all five views
        assert (
            "overview" in after
        ), "Tab-switch: 'overview' not referenced in keydown handler"
        assert (
            "merge-queue" in after
        ), "Tab-switch: 'merge-queue' not referenced in keydown handler"
        assert (
            "prompts" in after
        ), "Tab-switch: 'prompts' not referenced in keydown handler"

    def test_input_guard_present(self):
        """Keydown handler must suppress arrow/digit shortcuts when an input is focused."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("keydown")
        assert start != -1, "keydown listener not found"
        after = body[start:]
        # The handler must check tagName to detect input fields
        assert (
            "INPUT" in after or "tagName" in after
        ), "keydown handler must guard against triggering shortcuts when an input is focused"

    @pytest.mark.parametrize(
        "view",
        ["overview", "detail", "merge-queue", "config", "prompts"],
        ids=["overview", "detail", "merge-queue", "config", "prompts"],
    )
    def test_tab_views_array_contains_all_tabs(self, view):
        """The tabViews array in the keydown handler must include every dashboard tab."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("keydown")
        assert start != -1, "keydown listener not found"
        after = body[start:]
        assert (
            view in after
        ), f"Tab view '{view}' missing from keydown tab-switch handler"

    def test_enter_key_opens_selected_task(self):
        """Enter key must be handled in the keydown listener to open the selected task."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("keydown")
        assert start != -1, "keydown listener not found"
        after = body[start:]
        assert "Enter" in after, "Enter key not handled in keydown listener"

    def test_enter_key_calls_select_task(self):
        """Enter key handler must call selectTask to open the task detail view."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        start = body.find("'Enter'")
        if start == -1:
            start = body.find('"Enter"')
        assert start != -1, "Enter key handler not found"
        # selectTask must appear after the Enter check
        after = body[start:]
        assert "selectTask" in after, "Enter handler must call selectTask"


# ---------------------------------------------------------------------------
# UX-008: Mobile responsive CSS
# ---------------------------------------------------------------------------


class TestMobileResponsiveCSS:
    """Verify @media queries and touch-target sizes in dashboard_shared.css."""

    @pytest.fixture(autouse=True)
    def _paths(self):
        core = Path(__file__).resolve().parent.parent / "core"
        self.shared_css_path = core / "dashboard_shared.css"

    def test_media_query_1024_present(self):
        """CSS must contain a @media query for max-width: 1024px."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        assert (
            "max-width" in body and "1024px" in body
        ), "dashboard_shared.css missing @media (max-width: 1024px)"

    def test_media_query_768_present(self):
        """CSS must contain a @media query for max-width: 768px."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        assert (
            "max-width" in body and "768px" in body
        ), "dashboard_shared.css missing @media (max-width: 768px)"

    def test_touch_target_min_height_44px_in_media_query(self):
        """Touch targets must have min-height: 44px inside a @media block."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        # Find first @media block and confirm 44px appears inside it
        media_idx = body.find("@media")
        assert media_idx != -1, "@media rule not found in dashboard_shared.css"
        after = body[media_idx:]
        assert (
            "44px" in after
        ), "No 44px touch-target min-height found inside @media blocks"

    def test_min_height_applied_to_nav_tab_in_media_query(self):
        """nav-tab must get min-height: 44px inside the tablet media query."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        media_idx = body.find("@media")
        assert media_idx != -1, "@media rule not found"
        after = body[media_idx:]
        assert ".nav-tab" in after, ".nav-tab not styled inside @media block"
        # Confirm 44px appears after .nav-tab mention within media section
        nav_tab_idx = after.find(".nav-tab")
        assert nav_tab_idx != -1
        near = after[nav_tab_idx : nav_tab_idx + 200]
        assert "44px" in near, ".nav-tab inside @media block must set min-height:44px"

    def test_min_height_applied_to_ov_task_in_media_query(self):
        """ov-task rows must get min-height: 44px inside the tablet media query."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        media_idx = body.find("@media")
        assert media_idx != -1, "@media rule not found"
        after = body[media_idx:]
        assert ".ov-task" in after, ".ov-task not styled inside @media block"
        ov_task_idx = after.find(".ov-task")
        assert ov_task_idx != -1
        near = after[ov_task_idx : ov_task_idx + 200]
        assert "44px" in near, ".ov-task inside @media block must set min-height:44px"

    def test_tab_bar_scrollable_in_media_query(self):
        """Tab bar must be horizontally scrollable inside a @media block."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        media_idx = body.find("@media")
        assert media_idx != -1, "@media rule not found"
        after = body[media_idx:]
        assert (
            "overflow-x" in after
        ), "Tab bar overflow-x scroll not set inside @media block"

    @pytest.mark.parametrize(
        "breakpoint",
        ["1024px", "768px"],
        ids=["tablet", "mobile"],
    )
    def test_both_breakpoints_present(self, breakpoint):
        """Both responsive breakpoints must exist in dashboard_shared.css."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        assert (
            breakpoint in body
        ), f"Breakpoint {breakpoint} missing from dashboard_shared.css"


class TestXssEscaping:
    """SEC-012: esc() must escape single and double quotes for safe attribute use."""

    shared_js_path = (
        Path(__file__).resolve().parent.parent / "core" / "dashboard_shared.js"
    )
    analytics_js_path = (
        Path(__file__).resolve().parent.parent / "core" / "prompt_analytics.js"
    )

    def test_esc_escapes_single_quote(self):
        """esc() must replace ' with &#39;."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert "&#39;" in body, "esc() must escape single quotes to &#39;"

    def test_esc_escapes_double_quote(self):
        """esc() must replace \" with &quot;."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert "&quot;" in body, "esc() must escape double quotes to &quot;"

    def test_analytics_esc_escapes_single_quote(self):
        """_esc() in prompt_analytics.js must also escape single quotes."""
        body = self.analytics_js_path.read_text(encoding="utf-8")
        assert "&#39;" in body, "_esc() must escape single quotes to &#39;"

    def test_analytics_esc_escapes_double_quote(self):
        """_esc() in prompt_analytics.js must also escape double quotes."""
        body = self.analytics_js_path.read_text(encoding="utf-8")
        assert "&quot;" in body, "_esc() must escape double quotes to &quot;"

    @pytest.mark.parametrize(
        "js_file",
        ["dashboard_shared.js", "prompt_analytics.js"],
        ids=["shared_esc", "analytics_esc"],
    )
    def test_esc_uses_replace_chain(self, js_file):
        """Both esc functions must chain .replace() for ' and \" after innerHTML."""
        path = Path(__file__).resolve().parent.parent / "core" / js_file
        body = path.read_text(encoding="utf-8")
        # Verify the replace chain pattern: innerHTML followed by .replace(...)
        assert ".replace(/'/g" in body, f"{js_file} must have single-quote replace"
        assert '.replace(/"/g' in body, f"{js_file} must have double-quote replace"


# ---------------------------------------------------------------------------
# UX-009: Data visualizations — sparkline, barChart, CSS, and overview stats
# ---------------------------------------------------------------------------


class TestDataVisualizations:
    """Verify sparkline, barChart functions and CSS in shared files; stats wiring
    in task_overview.js."""

    @pytest.fixture(autouse=True)
    def _paths(self):
        core = Path(__file__).resolve().parent.parent / "core"
        self.shared_js_path = core / "dashboard_shared.js"
        self.shared_css_path = core / "dashboard_shared.css"
        self.overview_path = core / "task_overview.js"
        self.html_path = core / "task_dashboard.html"

    # --- JS: sparkline function ---

    def test_sparkline_function_defined(self):
        """sparkline() must be defined in dashboard_shared.js."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert (
            "function sparkline(" in body
        ), "Missing sparkline function in dashboard_shared.js"

    def test_sparkline_returns_svg(self):
        """sparkline() must return an SVG element."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function sparkline(")
        assert idx != -1, "sparkline function not found"
        after = body[idx : idx + 600]
        assert "<svg" in after, "sparkline must return an SVG element"
        assert "polyline" in after, "sparkline must use a polyline element"

    def test_sparkline_uses_green_stroke(self):
        """sparkline() must use --green color for the line stroke."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function sparkline(")
        assert idx != -1, "sparkline function not found"
        after = body[idx : idx + 600]
        assert (
            "var(--green)" in after
        ), "sparkline must use var(--green) as stroke color"

    def test_sparkline_returns_empty_for_no_values(self):
        """sparkline() must return empty string when values array is empty."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function sparkline(")
        assert idx != -1, "sparkline function not found"
        # Must have an early-return guard for empty/falsy input
        after = body[idx : idx + 200]
        assert (
            "return ''" in after or 'return ""' in after
        ), "sparkline must return empty string for empty input"

    def test_sparkline_has_sparkline_class(self):
        """sparkline() SVG must have the 'sparkline' CSS class."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function sparkline(")
        assert idx != -1, "sparkline function not found"
        after = body[idx : idx + 600]
        assert 'class="sparkline"' in after, "sparkline SVG must have class='sparkline'"

    # --- JS: barChart function ---

    def test_bar_chart_function_defined(self):
        """barChart() must be defined in dashboard_shared.js."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        assert (
            "function barChart(" in body
        ), "Missing barChart function in dashboard_shared.js"

    def test_bar_chart_uses_bar_row_class(self):
        """barChart() must produce elements with class 'bar-row'."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function barChart(")
        assert idx != -1, "barChart function not found"
        after = body[idx : idx + 500]
        assert "bar-row" in after, "barChart must use .bar-row class"

    def test_bar_chart_uses_bar_track_class(self):
        """barChart() must produce elements with class 'bar-track'."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function barChart(")
        assert idx != -1, "barChart function not found"
        after = body[idx : idx + 500]
        assert "bar-track" in after, "barChart must use .bar-track class"

    def test_bar_chart_uses_bar_fill_class(self):
        """barChart() must produce elements with class 'bar-fill'."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function barChart(")
        assert idx != -1, "barChart function not found"
        after = body[idx : idx + 500]
        assert "bar-fill" in after, "barChart must use .bar-fill class"

    def test_bar_chart_uses_bar_label_class(self):
        """barChart() must produce elements with class 'bar-label'."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function barChart(")
        assert idx != -1, "barChart function not found"
        after = body[idx : idx + 500]
        assert "bar-label" in after, "barChart must use .bar-label class"

    def test_bar_chart_returns_empty_for_no_items(self):
        """barChart() must return empty string when items array is empty."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function barChart(")
        assert idx != -1, "barChart function not found"
        after = body[idx : idx + 200]
        assert (
            "return ''" in after or 'return ""' in after
        ), "barChart must return empty string for empty input"

    def test_bar_chart_applies_item_color(self):
        """barChart() must use item.color for bar fill background."""
        body = self.shared_js_path.read_text(encoding="utf-8")
        idx = body.find("function barChart(")
        assert idx != -1, "barChart function not found"
        after = body[idx : idx + 500]
        assert (
            "item.color" in after
        ), "barChart must apply item.color as bar-fill background"

    # --- CSS: visualization classes ---

    @pytest.mark.parametrize(
        "css_class",
        [
            ".sparkline",
            ".bar-row",
            ".bar-track",
            ".bar-fill",
            ".bar-label",
            ".bar-value",
        ],
        ids=["sparkline", "bar-row", "bar-track", "bar-fill", "bar-label", "bar-value"],
    )
    def test_visualization_css_class_present(self, css_class):
        """Each visualization CSS class must be defined in dashboard_shared.css."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        assert (
            css_class in body
        ), f"Missing {css_class} CSS class in dashboard_shared.css"

    def test_bar_track_has_background(self):
        """.bar-track must define a background color."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        idx = body.find(".bar-track")
        assert idx != -1, ".bar-track not found in CSS"
        after = body[idx : idx + 120]
        assert "background" in after, ".bar-track must set background color"

    def test_bar_fill_has_transition(self):
        """.bar-fill must define a CSS transition for smooth animation."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        idx = body.find(".bar-fill")
        assert idx != -1, ".bar-fill not found in CSS"
        after = body[idx : idx + 120]
        assert (
            "transition" in after
        ), ".bar-fill must have transition for width animation"

    def test_bar_row_uses_flex_layout(self):
        """.bar-row must use display:flex for alignment."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        idx = body.find(".bar-row")
        assert idx != -1, ".bar-row not found in CSS"
        after = body[idx : idx + 120]
        assert "flex" in after, ".bar-row must use flex layout"

    def test_ov_stats_section_class_defined(self):
        """.ov-stats-section must be defined for the analytics panel wrapper."""
        body = self.shared_css_path.read_text(encoding="utf-8")
        assert (
            ".ov-stats-section" in body
        ), "Missing .ov-stats-section class in dashboard_shared.css"

    # --- task_overview.js: renderOverviewStats wired up ---

    def test_render_overview_stats_function_defined(self):
        """renderOverviewStats() must be defined in task_overview.js."""
        body = self.overview_path.read_text(encoding="utf-8")
        assert (
            "function renderOverviewStats(" in body
        ), "Missing renderOverviewStats function in task_overview.js"

    def test_render_overview_stats_called_from_update_top_stats(self):
        """renderOverviewStats must be called from updateTopStats."""
        body = self.overview_path.read_text(encoding="utf-8")
        idx = body.find("function updateTopStats(")
        assert idx != -1, "updateTopStats function not found"
        # Find the next function definition to bound the search window
        next_fn = body.find("\nfunction ", idx + 10)
        end = next_fn if next_fn != -1 else idx + 1200
        after = body[idx:end]
        assert (
            "renderOverviewStats(" in after
        ), "updateTopStats must call renderOverviewStats to render analytics panel"

    def test_render_overview_stats_uses_sparkline(self):
        """renderOverviewStats must call sparkline() for the success trend chart."""
        body = self.overview_path.read_text(encoding="utf-8")
        idx = body.find("function renderOverviewStats(")
        assert idx != -1, "renderOverviewStats function not found"
        after = body[idx : idx + 2000]
        assert (
            "sparkline(" in after
        ), "renderOverviewStats must call sparkline() for success-rate trend"

    def test_render_overview_stats_uses_bar_chart(self):
        """renderOverviewStats must call barChart() for the cost and/or phase charts."""
        body = self.overview_path.read_text(encoding="utf-8")
        idx = body.find("function renderOverviewStats(")
        assert idx != -1, "renderOverviewStats function not found"
        after = body[idx : idx + 2000]
        assert (
            "barChart(" in after
        ), "renderOverviewStats must call barChart() for cost/phase charts"

    def test_render_overview_stats_targets_ov_stats_panel(self):
        """renderOverviewStats must target the ov-stats-panel DOM element."""
        body = self.overview_path.read_text(encoding="utf-8")
        idx = body.find("function renderOverviewStats(")
        assert idx != -1, "renderOverviewStats function not found"
        after = body[idx : idx + 2000]
        assert (
            "ov-stats-panel" in after
        ), "renderOverviewStats must target the 'ov-stats-panel' element"

    # --- task_dashboard.html: ov-stats-panel anchor present ---

    def test_html_has_ov_stats_panel(self):
        """task_dashboard.html must contain the ov-stats-panel element."""
        body = self.html_path.read_text(encoding="utf-8")
        assert (
            "ov-stats-panel" in body
        ), "task_dashboard.html must contain an element with id='ov-stats-panel'"

    def test_html_ov_stats_panel_has_aria_label(self):
        """ov-stats-panel must have an aria-label for accessibility."""
        body = self.html_path.read_text(encoding="utf-8")
        idx = body.find("ov-stats-panel")
        assert idx != -1, "ov-stats-panel not found"
        context = body[max(0, idx - 20) : idx + 80]
        assert (
            "aria-label" in context
        ), "ov-stats-panel must have an aria-label attribute"
