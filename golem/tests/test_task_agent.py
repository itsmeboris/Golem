"""Tests for the golem flow v2 — event tracker, state machine, orchestrator, config."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests as _req

from golem.core.config import (
    Config,
    GolemFlowConfig,
    load_config,
    _parse_golem_config,
)
from golem.core.triggers.base import TriggerEvent
from golem.event_tracker import Milestone, TaskEventTracker, TrackerState
from golem.flow import GolemFlow
from golem.mcp_scope import determine_mcp_scope
from golem.notifications import (
    _fmt_duration,
    build_task_activity_card,
    build_task_completed_card,
    build_task_failure_card,
    build_task_started_card,
)
from golem.orchestrator import (
    TaskOrchestrator,
    TaskSession,
    TaskSessionState,
    load_sessions,
    recover_sessions,
    save_sessions,
)
from golem.poller import get_issue_subject, is_agent_task
from golem.prompts import compute_prompt_hash, format_prompt, load_prompt

# -- Config parsing ---------------------------------------------------------


class TestGolemFlowConfig:
    def test_defaults(self):
        config = GolemFlowConfig()
        assert config.enabled is True
        assert config.poll_interval == 300
        assert config.tick_interval == 30
        assert config.grace_period_seconds == 120
        assert config.budget_per_task_usd == 10.0
        assert config.max_active_sessions == 3
        assert config.detection_tag == "[AGENT]"
        assert config.default_work_dir == ""
        assert config.task_model == "sonnet"
        assert config.task_timeout_seconds == 3600
        assert config.progress_interval_seconds == 60

    def test_custom_values(self):
        config = GolemFlowConfig(
            tick_interval=60,
            budget_per_task_usd=25.0,
            detection_tag="[BOT]",
            default_work_dir="/opt/workspace",
            task_model="opus",
            task_timeout_seconds=3600,
            progress_interval_seconds=120,
        )
        assert config.tick_interval == 60
        assert config.budget_per_task_usd == 25.0
        assert config.detection_tag == "[BOT]"
        assert config.default_work_dir == "/opt/workspace"
        assert config.task_model == "opus"
        assert config.task_timeout_seconds == 3600
        assert config.progress_interval_seconds == 120


class TestParseGolemConfig:
    def test_empty_data(self):
        config = _parse_golem_config({})
        assert config.enabled is True
        assert config.tick_interval == 30
        assert config.detection_tag == "[AGENT]"
        assert config.task_model == "sonnet"
        assert config.task_timeout_seconds == 3600
        assert config.progress_interval_seconds == 60

    def test_github_profile_defaults_to_golem_tag(self):
        config = _parse_golem_config({"profile": "github"})
        assert config.detection_tag == "golem"

    def test_github_profile_explicit_tag_preserved(self):
        config = _parse_golem_config({"profile": "github", "detection_tag": "custom"})
        assert config.detection_tag == "custom"

    def test_redmine_profile_defaults_to_agent_tag(self):
        config = _parse_golem_config({"profile": "redmine"})
        assert config.detection_tag == "[AGENT]"

    def test_full_data(self):
        data = {
            "enabled": False,
            "poll_interval": 180,
            "tick_interval": 45,
            "projects": ["my-project", "my-project-ext"],
            "grace_period_seconds": 300,
            "budget_per_task_usd": 20.0,
            "max_active_sessions": 5,
            "detection_tag": "[BOT]",
            "default_work_dir": "/tmp/repo",
            "task_model": "opus",
            "task_timeout_seconds": 3600,
            "progress_interval_seconds": 120,
        }
        config = _parse_golem_config(data)
        assert config.enabled is False
        assert config.poll_interval == 180
        assert config.tick_interval == 45
        assert config.projects == ["my-project", "my-project-ext"]
        assert config.grace_period_seconds == 300
        assert config.budget_per_task_usd == 20.0
        assert config.detection_tag == "[BOT]"
        assert config.default_work_dir == "/tmp/repo"
        assert config.task_model == "opus"
        assert config.task_timeout_seconds == 3600
        assert config.progress_interval_seconds == 120


class TestConfigIntegration:
    def test_config_has_golem(self):
        config = Config()
        assert isinstance(config.golem, GolemFlowConfig)

    def test_get_flow_config(self):
        config = Config()
        fc = config.get_flow_config("golem")
        assert isinstance(fc, GolemFlowConfig)

    def test_load_config_with_golem(self, temp_config_file):
        content = """
flows:
  golem:
    enabled: true
    tick_interval: 45
    detection_tag: "[BOT]"
    projects:
      - my-project
    budget_per_task_usd: 20.0
    task_model: opus
    progress_interval_seconds: 90
"""
        temp_config_file.write_text(content)
        config = load_config(temp_config_file)
        assert config.golem.enabled is True
        assert config.golem.tick_interval == 45
        assert config.golem.detection_tag == "[BOT]"
        assert config.golem.projects == ["my-project"]
        assert config.golem.budget_per_task_usd == 20.0
        assert config.golem.task_model == "opus"
        assert config.golem.progress_interval_seconds == 90


# -- Event tracker ----------------------------------------------------------


class TestMilestone:
    def test_defaults(self):
        m = Milestone(kind="tool_call")
        assert m.kind == "tool_call"
        assert m.tool_name == ""
        assert m.summary == ""
        assert m.is_error is False

    def test_custom(self):
        m = Milestone(
            kind="error",
            tool_name="Bash",
            summary="Command failed",
            timestamp=123.0,
            is_error=True,
        )
        assert m.kind == "error"
        assert m.is_error is True
        assert m.timestamp == 123.0


class TestTrackerState:
    def test_defaults(self):
        s = TrackerState()
        assert not s.tools_called
        assert not s.mcp_tools_called
        assert not s.errors
        assert s.last_text == ""
        assert s.cost_usd == 0.0
        assert s.milestone_count == 0
        assert s.finished is False


class TestTaskEventTracker:
    def test_handle_tool_call_started(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {
                "mcpToolCall": {
                    "args": {"toolName": "redmine_get_issue"},
                },
            },
        }
        milestone = tracker.handle_event(event)
        assert milestone is not None
        assert milestone.kind == "tool_call"
        assert milestone.tool_name == "redmine_get_issue"
        assert "redmine_get_issue" in tracker.state.mcp_tools_called

    def test_handle_tool_call_rejected(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "tool_call",
            "subtype": "completed",
            "tool_call": {
                "mcpToolCall": {
                    "result": {
                        "rejected": {"reason": "permission denied"},
                    },
                },
            },
        }
        milestone = tracker.handle_event(event)
        assert milestone is not None
        assert milestone.kind == "error"
        assert milestone.is_error is True
        assert "permission denied" in milestone.summary
        assert len(tracker.state.errors) == 1

    def test_handle_assistant_tool_use(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Edit",
                        "input": {"file_path": "/project/core/run_log.py"},
                    },
                ],
            },
        }
        milestone = tracker.handle_event(event)
        assert milestone is not None
        assert milestone.tool_name == "Edit"
        assert "Edit" in milestone.summary
        assert "run_log.py" in milestone.summary
        assert "Edit" in tracker.state.tools_called

    def test_handle_tool_result_error(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "tool_result",
            "is_error": True,
            "content": "File not found",
        }
        milestone = tracker.handle_event(event)
        assert milestone is not None
        assert milestone.kind == "error"
        assert milestone.is_error is True
        assert len(tracker.state.errors) == 1

    def test_handle_tool_result_ok(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "tool_result",
            "is_error": False,
            "content": "File edited successfully",
        }
        milestone = tracker.handle_event(event)
        assert milestone is not None
        assert milestone.kind == "result"
        assert milestone.is_error is False

    def test_handle_result_event(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "result",
            "cost_usd": 1.23,
            "duration_ms": 45000,
        }
        milestone = tracker.handle_event(event)
        assert milestone is not None
        assert milestone.kind == "result"
        assert tracker.state.cost_usd == 1.23
        assert tracker.state.finished is True

    def test_milestone_count_increments(self):
        tracker = TaskEventTracker(session_id=1)

        # Tool call
        tracker.handle_event(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
            }
        )
        # Tool result
        tracker.handle_event(
            {
                "type": "tool_result",
                "is_error": False,
                "content": "OK",
            }
        )

        assert tracker.state.milestone_count == 2
        assert len(tracker.state.event_log) == 2

    def test_callback_invoked(self):
        callbacks = []
        tracker = TaskEventTracker(
            session_id=1,
            on_milestone=lambda m, s: callbacks.append((m, s)),
        )
        tracker.handle_event(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read"}]},
            }
        )
        assert len(callbacks) == 1
        milestone, state = callbacks[0]
        assert milestone.tool_name == "Read"
        assert state.milestone_count == 1

    def test_dedup_tool_names(self):
        tracker = TaskEventTracker(session_id=1)
        for _ in range(3):
            tracker.handle_event(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "Edit"}]},
                }
            )
        # Tool is only recorded once in the tools_called list
        assert tracker.state.tools_called == ["Edit"]
        # Each call still produces a milestone (all 3 are tracked in the log)
        assert tracker.state.milestone_count == 3

    def test_to_dict(self):
        tracker = TaskEventTracker(session_id=42)
        tracker.handle_event(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
            }
        )
        d = tracker.to_dict()
        assert d["session_id"] == 42
        assert "Bash" in d["tools_called"]
        assert d["milestone_count"] == 1
        assert len(d["event_log"]) == 1
        assert d["last_text"] == ""

    def test_mcp_tool_detection(self):
        tracker = TaskEventTracker(session_id=1)
        tracker.handle_event(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "name": "mcp__redmine__get_issue"}]
                },
            }
        )
        assert "mcp__redmine__get_issue" in tracker.state.mcp_tools_called
        assert not tracker.state.tools_called

    def test_empty_tool_result_ignored(self):
        tracker = TaskEventTracker(session_id=1)
        milestone = tracker.handle_event(
            {
                "type": "tool_result",
                "is_error": False,
                "content": "",
            }
        )
        assert milestone is None
        assert tracker.state.milestone_count == 0

    def test_assistant_text_captured_in_last_text(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "text",
                        "text": "Now updating  config.yaml\n"
                        " with improved comments...",
                    },
                ],
            },
        }
        milestone = tracker.handle_event(event)
        # Text blocks create milestones for live dashboard visibility
        assert milestone is not None
        assert milestone.kind == "text"
        assert tracker.state.milestone_count == 1
        # last_text is a truncated summary (first line)
        assert tracker.state.last_text == "Now updating  config.yaml"

    def test_assistant_text_does_not_overwrite_with_empty(self):
        tracker = TaskEventTracker(session_id=1)
        tracker.handle_event(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "First message"}],
                },
            }
        )
        assert tracker.state.last_text == "First message"
        assert tracker.state.milestone_count == 1
        # Empty text should not overwrite and should not create a milestone
        m = tracker.handle_event(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "   "}],
                },
            }
        )
        assert m is None
        assert tracker.state.last_text == "First message"
        assert tracker.state.milestone_count == 1


# -- State machine — TaskSession (v2) ---------------------------------------


class TestTaskSession:
    def test_defaults(self):
        s = TaskSession(parent_issue_id=100)
        assert s.state == TaskSessionState.DETECTED
        assert s.total_cost_usd == 0.0
        assert s.budget_usd == 10.0
        assert not s.tools_called
        assert not s.mcp_tools_called
        assert s.milestone_count == 0
        assert not s.event_log
        assert s.result_summary == ""
        assert s.duration_seconds == 0.0

    def test_to_dict_roundtrip(self):
        s = TaskSession(
            parent_issue_id=200,
            parent_subject="Test task",
            state=TaskSessionState.RUNNING,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:05:00+00:00",
            grace_deadline="2026-01-01T00:02:00+00:00",
            total_cost_usd=1.23,
            budget_usd=10.0,
            tools_called=["Edit", "Bash"],
            mcp_tools_called=["redmine_get_issue"],
            milestone_count=5,
            result_summary="All done",
            duration_seconds=120.0,
        )

        d = s.to_dict()
        assert d["state"] == "running"
        assert d["tools_called"] == ["Edit", "Bash"]

        restored = TaskSession.from_dict(d)
        assert restored.parent_issue_id == 200
        assert restored.state == TaskSessionState.RUNNING
        assert restored.total_cost_usd == 1.23
        assert restored.tools_called == ["Edit", "Bash"]
        assert restored.milestone_count == 5
        assert restored.duration_seconds == 120.0


class TestSessionPersistence:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "sessions.json"
        sessions = {
            1: TaskSession(
                parent_issue_id=1,
                state=TaskSessionState.RUNNING,
                created_at="2026-01-01T00:00:00+00:00",
            ),
            2: TaskSession(
                parent_issue_id=2,
                state=TaskSessionState.COMPLETED,
                created_at="2026-01-01T00:00:00+00:00",
            ),
        }

        save_sessions(sessions, path)
        assert path.exists()

        loaded = load_sessions(path)
        assert 1 in loaded
        assert loaded[1].state == TaskSessionState.RUNNING
        assert 2 in loaded
        assert loaded[2].state == TaskSessionState.COMPLETED

    def test_load_missing_file(self, tmp_path):
        loaded = load_sessions(tmp_path / "missing.json")
        assert not loaded

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        loaded = load_sessions(path)
        assert not loaded


class TestRecoverSessions:
    def test_resets_running_to_detected(self):
        sessions = {
            1: TaskSession(
                parent_issue_id=1,
                state=TaskSessionState.RUNNING,
            ),
            2: TaskSession(
                parent_issue_id=2,
                state=TaskSessionState.DETECTED,
            ),
        }

        count = recover_sessions(sessions)
        assert count == 1
        assert sessions[1].state == TaskSessionState.DETECTED
        assert sessions[2].state == TaskSessionState.DETECTED

    def test_skips_completed_sessions(self):
        sessions = {
            1: TaskSession(
                parent_issue_id=1,
                state=TaskSessionState.COMPLETED,
            ),
            2: TaskSession(
                parent_issue_id=2,
                state=TaskSessionState.FAILED,
            ),
        }

        count = recover_sessions(sessions)
        assert count == 0
        assert sessions[1].state == TaskSessionState.COMPLETED
        assert sessions[2].state == TaskSessionState.FAILED


# -- State transitions (v2) -------------------------------------------------


class TestStateTransitions:
    def test_detected_stays_during_grace_period(self):
        future = (datetime.now(timezone.utc) + timedelta(seconds=300)).isoformat()
        session = TaskSession(
            parent_issue_id=100,
            state=TaskSessionState.DETECTED,
            grace_deadline=future,
        )

        config = MagicMock()
        task_config = GolemFlowConfig()
        orch = TaskOrchestrator(session, config, task_config)

        asyncio.run(orch._tick_detected())

        assert session.state == TaskSessionState.DETECTED

    def test_detected_to_running_after_grace(self):
        """After grace period, DETECTED transitions to RUNNING (then agent runs)."""
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        session = TaskSession(
            parent_issue_id=100,
            parent_subject="[AGENT] Test task",
            state=TaskSessionState.DETECTED,
            grace_deadline=past,
            budget_usd=10.0,
        )

        config = MagicMock()
        task_config = GolemFlowConfig()
        orch = TaskOrchestrator(session, config, task_config)

        with patch.object(orch, "_run_agent", new=AsyncMock()):
            asyncio.run(orch._tick_detected())

        # After tick_detected, state should be RUNNING (at minimum)
        # _run_agent would normally transition to COMPLETED/FAILED
        assert session.state == TaskSessionState.RUNNING


# -- Poller -----------------------------------------------------------------


class TestIsAgentTask:
    def test_matches_default_tag(self):
        assert is_agent_task("[AGENT] Fix parser regression") is True
        assert is_agent_task("[Agent] Fix parser regression") is True
        assert is_agent_task("Fix parser [AGENT]") is True
        assert is_agent_task("Fix parser regression") is False

    def test_custom_tag(self):
        assert is_agent_task("[BOT] Fix it", detection_tag="[BOT]") is True
        assert is_agent_task("[AGENT] Fix it", detection_tag="[BOT]") is False


class TestGetIssueSubject:
    def test_returns_subject_on_success(self, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "issue": {"id": 123, "subject": "[AGENT] Add tick.py --teams flag"}
        }
        monkeypatch.setattr("golem.poller.requests.get", lambda *a, **kw: mock_resp)

        assert get_issue_subject(123) == "[AGENT] Add tick.py --teams flag"

    def test_returns_fallback_on_failure(self, monkeypatch):
        monkeypatch.setattr(
            "golem.poller.requests.get",
            MagicMock(side_effect=_req.RequestException("timeout")),
        )

        result = get_issue_subject(456)
        assert result == "[AGENT] task #456"

    def test_returns_fallback_on_empty_subject(self, monkeypatch):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"issue": {"id": 789, "subject": ""}}
        monkeypatch.setattr("golem.poller.requests.get", lambda *a, **kw: mock_resp)

        assert get_issue_subject(789) == "[AGENT] task #789"


# -- MCP scope --------------------------------------------------------------


class TestMCPScope:
    def test_no_base_servers_by_default(self):
        result = determine_mcp_scope("Some generic task")
        assert result == []

    def test_jenkins_keywords(self):
        result = determine_mcp_scope("Investigate Jenkins CI failure")
        assert "jenkins" in result

    def test_gerrit_keywords(self):
        result = determine_mcp_scope("Review the gerrit change")
        assert "gerrit" in result

    def test_confluence_keywords(self):
        result = determine_mcp_scope("Update the wiki documentation")
        assert "confluence" in result

    def test_multiple_keywords(self):
        result = determine_mcp_scope("Jenkins build review on gerrit")
        assert "jenkins" in result
        assert "gerrit" in result


# -- Notifications (card builders) ------------------------------------------


def _get_facts(card):
    """Extract {title: value} dict from the first FactSet in card body."""
    for item in card["body"]:
        if item.get("type") == "FactSet":
            return {f["title"]: f["value"] for f in item["facts"]}
    return {}


class TestNotifications:
    def test_build_task_started_card(self):
        card = build_task_started_card(
            parent_id=100,
            subject="Fix everything",
        )
        assert card["type"] == "AdaptiveCard"
        assert "Started" in card["body"][0]["text"]
        assert "#100" in card["body"][0]["text"]

    def test_build_task_completed_card(self):
        card = build_task_completed_card(
            parent_id=100,
            subject="Fix everything",
            total_cost_usd=1.30,
            duration_s=220.0,
            steps=29,
        )
        assert card["type"] == "AdaptiveCard"
        assert "Completed" in card["body"][0]["text"]
        facts = _get_facts(card)
        assert facts["Cost"] == "$1.30"
        assert facts["Duration"] == "3m 40s"
        assert facts["Steps"] == "29"

    def test_build_task_completed_card_short_duration(self):
        card = build_task_completed_card(
            parent_id=100,
            subject="Quick task",
            total_cost_usd=0.10,
            duration_s=45.0,
            steps=5,
        )
        facts = _get_facts(card)
        assert facts["Duration"] == "45s"

    def test_build_task_activity_card(self):
        card = build_task_activity_card(
            parent_id=100,
            subject="Fix everything",
            status_text="Now updating config.yaml with improved comments...",
            elapsed_s=62.0,
            milestone_count=14,
        )
        assert card["type"] == "AdaptiveCard"
        assert "In Progress" in card["body"][0]["text"]
        assert "config.yaml" in card["body"][2]["text"]
        facts = _get_facts(card)
        assert facts["Elapsed"] == "1m 2s"

    def test_build_task_failure_card(self):
        card = build_task_failure_card(
            parent_id=100,
            subject="Fix everything",
            reason="Budget exceeded",
            cost_usd=12.0,
            duration_s=72.0,
        )
        assert card["type"] == "AdaptiveCard"
        assert "Failed" in card["body"][0]["text"]
        facts = _get_facts(card)
        assert facts["Error"] == "Budget exceeded"
        assert facts["Duration"] == "1m 12s"

    def test_fmt_duration(self):
        assert _fmt_duration(0) == "0s"
        assert _fmt_duration(45) == "45s"
        assert _fmt_duration(60) == "1m 0s"
        assert _fmt_duration(220) == "3m 40s"


# -- Prompts ----------------------------------------------------------------


class TestPrompts:
    def test_load_prompt(self):
        text = load_prompt("orchestrate_task.txt")
        assert "orchestrator" in text.lower()

    def test_load_run_task_prompt(self):
        text = load_prompt("run_task.txt")
        assert "autonomous" in text.lower()
        assert "{task_description}" in text

    def test_load_prompt_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_prompt("nonexistent.txt")

    def test_format_prompt(self):
        text = format_prompt(
            "run_task.txt",
            issue_id=100,
        )
        assert "#100" in text

    def test_format_prompt_orchestrate(self):
        text = format_prompt(
            "orchestrate_task.txt",
            issue_id=100,
            parent_subject="Test task",
            task_description="Do something",
            work_dir="/tmp/test",
            inner_retry_max=3,
        )
        assert "#100" in text
        assert "Test task" in text

    def test_orchestrate_prompt_has_new_roles(self):
        text = load_prompt("orchestrate_task.txt")
        for role in (
            "Builder",
            "Spec Reviewer",
            "Quality Reviewer",
            "Verifier",
            "Scout",
        ):
            assert role in text, f"Missing role: {role}"
        # Old roles should not appear
        for old_role in ("Explorer", "Implementer", "Tester"):
            assert old_role not in text, f"Old role still present: {old_role}"

    def test_format_prompt_empty_description_guard(self):
        """Empty task_description gets a fallback."""
        result = format_prompt(
            "orchestrate_task.txt",
            issue_id=42,
            parent_subject="Add feature X",
            task_description="",
            work_dir="/work",
            inner_retry_max=3,
        )
        assert "Add feature X" in result
        assert "task_description" not in result  # placeholder should not remain

    def test_compute_prompt_hash_returns_12_chars(self):
        h = compute_prompt_hash("hello world")
        assert len(h) == 12

    def test_compute_prompt_hash_is_hex(self):
        h = compute_prompt_hash("hello world")
        assert all(c in "0123456789abcdef" for c in h)

    def test_compute_prompt_hash_deterministic(self):
        assert compute_prompt_hash("same text") == compute_prompt_hash("same text")

    def test_compute_prompt_hash_differs_for_different_text(self):
        assert compute_prompt_hash("text A") != compute_prompt_hash("text B")

    def test_compute_prompt_hash_empty_string(self):
        h = compute_prompt_hash("")
        assert len(h) == 12

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("hello world", "b94d27b9934d"),
            ("", "e3b0c44298fc"),
        ],
    )
    def test_compute_prompt_hash_known_values(self, text, expected):
        assert compute_prompt_hash(text) == expected


# -- Flow -------------------------------------------------------------------


class TestGolemFlow:
    def _make_flow(self, monkeypatch, tmp_path):
        sessions_path = tmp_path / "sessions.json"
        monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

        config = Config(
            golem=GolemFlowConfig(
                enabled=True,
                projects=["my-project"],
            ),
        )
        return GolemFlow(config)

    def test_flow_name(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        assert flow.name == "golem"

    def test_mcp_servers(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        assert flow.mcp_servers == []

    @pytest.mark.asyncio
    async def test_handle_creates_session(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        event = TriggerEvent(
            flow_name="golem",
            event_id="test-1",
            data={"issue_id": 999, "subject": "[AGENT] Test task"},
            timestamp=datetime.now(),
            source="test",
        )
        result = await flow.handle(event)
        assert result.success
        assert result.data.get("session_created") is True
        assert 999 in flow._sessions

    @pytest.mark.asyncio
    async def test_handle_skips_duplicate(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        event = TriggerEvent(
            flow_name="golem",
            event_id="test-1",
            data={"issue_id": 999, "subject": "[AGENT] Test task"},
            timestamp=datetime.now(),
            source="test",
        )
        await flow.handle(event)
        result = await flow.handle(event)
        assert result.success
        assert result.data.get("skipped") is True

    def test_poll_new_items(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        fake_issues = [
            {"id": 100, "subject": "[AGENT] Task 1"},
            {"id": 101, "subject": "[AGENT] Task 2"},
        ]
        # Patch the profile's task source
        monkeypatch.setattr(
            flow._profile.task_source,
            "poll_tasks",
            lambda *a, **kw: fake_issues,
        )
        items = flow.poll_new_items()
        assert len(items) == 2
        assert items[0]["issue_id"] == 100

    def test_generate_event_id(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        eid = flow.generate_event_id({"issue_id": 100})
        assert eid.startswith("golem-100-")

    def test_reset_state(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        flow._sessions[1] = TaskSession(
            parent_issue_id=1, state=TaskSessionState.RUNNING
        )
        flow._save_state()
        flow.reset_state()
        assert not flow._sessions

    def test_clear_failed_sessions(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        flow._sessions[1] = TaskSession(
            parent_issue_id=1, state=TaskSessionState.FAILED
        )
        flow._sessions[2] = TaskSession(
            parent_issue_id=2, state=TaskSessionState.COMPLETED
        )
        flow._sessions[3] = TaskSession(
            parent_issue_id=3, state=TaskSessionState.RUNNING
        )
        cleared = flow.clear_failed_sessions()
        assert cleared == [1]
        assert 1 not in flow._sessions
        assert 2 in flow._sessions
        assert 3 in flow._sessions

    def test_clear_failed_sessions_none_failed(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        flow._sessions[1] = TaskSession(
            parent_issue_id=1, state=TaskSessionState.COMPLETED
        )
        cleared = flow.clear_failed_sessions()
        assert cleared == []
        assert 1 in flow._sessions
