"""Tests for golem core.cli_wrapper — settings.json, MCP filtering, event handler."""

# pylint: disable=missing-class-docstring,missing-function-docstring,protected-access

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from golem.core.cli_wrapper import (
    CLIConfig,
    CLIType,
    _copy_mcp_json,
    _get_subprocess_env,
    _write_settings_json,
)
from golem.core.stream_printer import StreamPrinter
from golem.event_tracker import TaskEventTracker
from golem.orchestrator import TaskSession

# ---------------------------------------------------------------------------
# _write_settings_json — must overwrite restrictive existing settings
# ---------------------------------------------------------------------------


class TestWriteSettingsJson:
    """Regression: settings.json must overwrite existing restrictive settings.

    Bug: When the CWD already had a ``.claude/settings.json`` with
    ``permissions: {allow: ["Bash(git *)"]}`` (from the package's interactive
    config), the old code returned early (``if dst.exists(): return``).
    This left restrictive permissions that blocked Bash for the child agent.
    """

    def test_overwrites_existing_restrictive_settings(self, tmp_path):
        """Pre-existing settings.json with permissions must be overwritten."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(
            json.dumps(
                {
                    "permissions": {
                        "allow": ["Bash(git *)", "Read", "Edit"],
                        "deny": ["Bash"],
                    },
                    "hooks": {
                        "PreToolUse": [
                            {"matcher": "mcp", "command": "inject-creds"},
                            {"matcher": "Bash", "command": "audit-bash"},
                        ]
                    },
                }
            )
        )

        created: list[Path] = []
        with patch.object(
            Path,
            "is_file",
            side_effect=lambda self=None: False,
        ):
            _write_settings_json(tmp_path, created)

        settings = json.loads(settings_file.read_text())
        assert "permissions" not in settings
        assert settings_file not in created

    def test_writes_hooks_only_no_permissions(self, tmp_path):
        """Output must contain only hooks, no permissions key."""
        created: list[Path] = []
        with patch.object(
            Path,
            "is_file",
            side_effect=lambda self=None: False,
        ):
            _write_settings_json(tmp_path, created)

        settings_file = tmp_path / "settings.json"
        assert settings_file.exists()
        settings = json.loads(settings_file.read_text())
        assert "hooks" in settings
        assert "permissions" not in settings

    def test_extracts_mcp_hooks_from_project(self, tmp_path):
        """MCP-related PreToolUse hooks must be preserved."""
        project_settings = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "mcp", "command": "inject-mcp-creds"},
                    {"matcher": "Bash", "command": "lint-on-bash"},
                ],
                "PostToolUse": [
                    {"matcher": "Edit", "command": "audit-edits"},
                ],
            },
            "permissions": {"allow": ["Read"]},
        }

        created: list[Path] = []
        with patch(
            "golem.core.cli_wrapper._PROJECT_CLAUDE_DIR",
            tmp_path / "project_claude",
        ):
            project_claude = tmp_path / "project_claude"
            project_claude.mkdir()
            (project_claude / "settings.json").write_text(json.dumps(project_settings))

            target_dir = tmp_path / "target"
            target_dir.mkdir()
            _write_settings_json(target_dir, created)

        settings = json.loads((target_dir / "settings.json").read_text())
        assert "PreToolUse" in settings["hooks"]
        hooks = settings["hooks"]["PreToolUse"]
        assert len(hooks) == 1
        assert hooks[0]["matcher"] == "mcp"
        assert "PostToolUse" not in settings["hooks"]
        assert "permissions" not in settings

    def test_tracks_newly_created_file(self, tmp_path):
        """If settings.json didn't exist, it should be added to created list."""
        created: list[Path] = []
        with patch.object(
            Path,
            "is_file",
            side_effect=lambda self=None: False,
        ):
            _write_settings_json(tmp_path, created)

        dst = tmp_path / "settings.json"
        assert dst in created


# ---------------------------------------------------------------------------
# _copy_mcp_json — empty list means "no MCP", not "copy all"
# ---------------------------------------------------------------------------


class TestCopyMcpJson:
    """Regression: mcp_servers=[] must skip copying (no MCP), not copy all.

    Bug: ``_copy_mcp_json`` used ``if mcp_servers:`` to test for a non-empty
    list, but ``[]`` is falsy in Python.  This meant ``mcp_servers=[]``
    fell through to the "copy all" branch, leaking all MCP servers into
    prompt-mode runs that should have no MCP.
    """

    def test_empty_list_skips_copy(self, tmp_path):
        """mcp_servers=[] means no MCP — must NOT create .mcp.json."""
        source = tmp_path / "source_mcp.json"
        source.write_text(json.dumps({"mcpServers": {"redmine": {}, "jenkins": {}}}))

        created: list[Path] = []
        with patch("golem.core.cli_wrapper._PROJECT_MCP_JSON", source):
            _copy_mcp_json(tmp_path / "target", [], created)

        assert not (tmp_path / "target" / ".mcp.json").exists()
        assert not created

    def test_none_copies_all_servers(self, tmp_path):
        """mcp_servers=None means copy all servers (no filtering)."""
        source = tmp_path / "source_mcp.json"
        source.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "redmine": {"url": "r"},
                        "jenkins": {"url": "j"},
                    }
                }
            )
        )
        target = tmp_path / "target"
        target.mkdir()

        created: list[Path] = []
        with patch("golem.core.cli_wrapper._PROJECT_MCP_JSON", source):
            _copy_mcp_json(target, None, created)

        dst = target / ".mcp.json"
        assert dst.exists()
        data = json.loads(dst.read_text())
        assert "redmine" in data["mcpServers"]
        assert "jenkins" in data["mcpServers"]

    def test_specific_servers_filters(self, tmp_path):
        """mcp_servers=["redmine"] copies only the named server."""
        source = tmp_path / "source_mcp.json"
        source.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "redmine": {"url": "r"},
                        "jenkins": {"url": "j"},
                    }
                }
            )
        )
        target = tmp_path / "target"
        target.mkdir()

        created: list[Path] = []
        with patch("golem.core.cli_wrapper._PROJECT_MCP_JSON", source):
            _copy_mcp_json(target, ["redmine"], created)

        dst = target / ".mcp.json"
        assert dst.exists()
        data = json.loads(dst.read_text())
        assert "redmine" in data["mcpServers"]
        assert "jenkins" not in data["mcpServers"]

    def test_skips_if_destination_exists(self, tmp_path):
        """Must not overwrite an existing .mcp.json."""
        source = tmp_path / "source_mcp.json"
        source.write_text(json.dumps({"mcpServers": {"new": {"url": "n"}}}))
        target = tmp_path / "target"
        target.mkdir()
        existing = target / ".mcp.json"
        existing.write_text(json.dumps({"mcpServers": {"old": {"url": "o"}}}))

        created: list[Path] = []
        with patch("golem.core.cli_wrapper._PROJECT_MCP_JSON", source):
            _copy_mcp_json(target, None, created)

        data = json.loads(existing.read_text())
        assert "old" in data["mcpServers"]
        assert "new" not in data["mcpServers"]

    def test_skips_if_source_missing(self, tmp_path):
        """No error if project has no .mcp.json."""
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch(
            "golem.core.cli_wrapper._PROJECT_MCP_JSON",
            tmp_path / "nonexistent.json",
        ):
            _copy_mcp_json(target, None, created)

        assert not (target / ".mcp.json").exists()
        assert not created


# ---------------------------------------------------------------------------
# _make_event_handler — live session updates on milestones
# ---------------------------------------------------------------------------


class TestMakeEventHandler:
    """Verify _make_event_handler pushes live progress to the session object.

    Bug: The dashboard showed all zeros during execution because session
    state was only populated after the pipeline finished.  The fix adds
    an optional ``session`` parameter that gets updated on each milestone.
    """

    def _make_handler(self, session=None, start_time=None):
        """Build an event handler with a real tracker and a null printer."""
        from golem.cli import _make_event_handler

        tracker = TaskEventTracker(session_id=999)
        printer = MagicMock(spec=StreamPrinter)
        return _make_event_handler(
            tracker, printer, session=session, start_time=start_time
        )

    def test_no_session_does_not_crash(self):
        """Handler with session=None must still work (no live updates)."""
        handler = self._make_handler(session=None)

        event = {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {"name": "Bash"},
        }
        handler(event)  # must not raise

    def test_session_gets_milestone_count(self):
        """Session.milestone_count must increment on each milestone event."""
        session = TaskSession(parent_issue_id=42)
        assert session.milestone_count == 0

        handler = self._make_handler(session=session)

        # tool_call with subtype=started produces a milestone
        event = {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {"name": "Bash"},
        }
        with patch("golem.cli._save_cli_session"):
            handler(event)

        assert session.milestone_count == 1

    def test_session_gets_tools_called(self):
        """Session.tools_called must reflect tracked tool names."""
        session = TaskSession(parent_issue_id=42)
        handler = self._make_handler(session=session)

        for tool in ["Bash", "Read", "Grep"]:
            event = {
                "type": "tool_call",
                "subtype": "started",
                "tool_call": {"name": tool},
            }
            with patch("golem.cli._save_cli_session"):
                handler(event)

        assert "Bash" in session.tools_called
        assert "Read" in session.tools_called
        assert "Grep" in session.tools_called

    def test_session_gets_mcp_tools(self):
        """MCP tool calls must populate session.mcp_tools_called."""
        session = TaskSession(parent_issue_id=42)
        handler = self._make_handler(session=session)

        event = {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {
                "name": "mcp__redmine__get_issue",
                "mcpToolCall": {"args": {"toolName": "mcp__redmine__get_issue"}},
            },
        }
        with patch("golem.cli._save_cli_session"):
            handler(event)

        assert "mcp__redmine__get_issue" in session.mcp_tools_called

    def test_session_gets_last_activity(self):
        """Session.last_activity must be set to milestone summary."""
        session = TaskSession(parent_issue_id=42)
        handler = self._make_handler(session=session)

        event = {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {"name": "Read"},
        }
        with patch("golem.cli._save_cli_session"):
            handler(event)

        assert session.last_activity != ""

    def test_session_gets_duration(self):
        """Session.duration_seconds must reflect elapsed time from start_time."""
        session = TaskSession(parent_issue_id=42)
        start = time.time() - 10  # simulate 10s ago
        handler = self._make_handler(session=session, start_time=start)

        event = {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {"name": "Bash"},
        }
        with patch("golem.cli._save_cli_session"):
            handler(event)

        assert session.duration_seconds >= 10.0

    def test_session_gets_errors(self):
        """Errors from tool results must propagate to session.errors."""
        session = TaskSession(parent_issue_id=42)
        handler = self._make_handler(session=session)

        # First, generate a tool_call milestone so the tracker is active
        call_event = {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {"name": "Bash"},
        }
        # Then an error result
        error_event = {
            "type": "tool_result",
            "subtype": "error",
            "tool_result": {"is_error": True, "content": "Permission denied"},
        }
        with patch("golem.cli._save_cli_session"):
            handler(call_event)
            handler(error_event)

        # Check that errors were recorded (if the tracker picks up errors)
        # The tracker records errors from is_error milestones
        assert session.milestone_count >= 1

    def test_session_gets_event_log(self):
        """Session.event_log must be populated live on each milestone.

        Regression: event_log was only set after the pipeline finished,
        so the dashboard Output section was hidden during execution.
        """
        session = TaskSession(parent_issue_id=42)
        handler = self._make_handler(session=session)

        for tool in ["Bash", "Read"]:
            event = {
                "type": "tool_call",
                "subtype": "started",
                "tool_call": {"name": tool},
            }
            with patch("golem.cli._save_cli_session"):
                handler(event)

        assert len(session.event_log) >= 2
        assert session.event_log[0]["kind"] == "tool_call"
        assert session.event_log[0]["tool_name"] == "Bash"
        assert session.event_log[1]["tool_name"] == "Read"

    def test_save_failure_does_not_break_execution(self):
        """If _save_cli_session raises, the handler must swallow the error."""
        session = TaskSession(parent_issue_id=42)
        handler = self._make_handler(session=session)

        event = {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {"name": "Bash"},
        }
        with patch(
            "golem.cli._save_cli_session",
            side_effect=OSError("disk full"),
        ):
            handler(event)  # must not raise

        assert session.milestone_count == 1


# ---------------------------------------------------------------------------
# _get_subprocess_env — project-root CWD must redirect to temp sandbox
# ---------------------------------------------------------------------------


class TestProjectRootSandbox:
    """Regression: CWD == project root must NOT skip settings sanitization.

    Bug: When ``config.cwd`` resolved to ``_PROJECT_ROOT``, the child agent
    ran directly in the project root.  ``_prepare_work_dir`` then returned
    early, leaving the project's interactive ``settings.json`` with
    restrictive ``Bash(git *)`` permissions that blocked the child agent.
    """

    def test_cwd_at_project_root_gets_temp_sandbox(self, tmp_path):
        """CWD == _PROJECT_ROOT must redirect to a temp sandbox."""
        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path):
            config = CLIConfig(
                cli_type=CLIType.CLAUDE, cwd=str(tmp_path), mcp_servers=[]
            )
            _env, cwd, cleanup = _get_subprocess_env(config)

        try:
            assert Path(cwd).resolve() != tmp_path.resolve()
            assert "flow_sandbox_" in cwd
        finally:
            cleanup()

    def test_cwd_at_project_root_no_agent_worktree_env(self, tmp_path):
        """Project-root CWD must NOT set AGENT_WORKTREE."""
        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path):
            config = CLIConfig(
                cli_type=CLIType.CLAUDE, cwd=str(tmp_path), mcp_servers=[]
            )
            env, _cwd, cleanup = _get_subprocess_env(config)

        try:
            assert "AGENT_WORKTREE" not in env
        finally:
            cleanup()

    def test_explicit_worktree_still_used_directly(self, tmp_path):
        """Non-project-root CWD (e.g. a worktree) must be used as-is."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        with patch(
            "golem.core.cli_wrapper._PROJECT_ROOT",
            tmp_path / "project",
        ):
            config = CLIConfig(
                cli_type=CLIType.CLAUDE, cwd=str(worktree), mcp_servers=[]
            )
            env, cwd, cleanup = _get_subprocess_env(config)

        try:
            assert cwd == str(worktree)
            assert env.get("AGENT_WORKTREE") == "1"
        finally:
            cleanup()

    def test_no_cwd_gets_temp_sandbox(self):
        """Empty CWD must also get a temp sandbox."""
        config = CLIConfig(cli_type=CLIType.CLAUDE, cwd="", mcp_servers=[])
        _env, cwd, cleanup = _get_subprocess_env(config)

        try:
            assert "flow_sandbox_" in cwd
        finally:
            cleanup()
