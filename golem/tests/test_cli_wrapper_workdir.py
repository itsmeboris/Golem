"""Tests for cli_wrapper work-dir helpers: settings.json overwrite, MCP filtering."""

# pylint: disable=missing-class-docstring,missing-function-docstring

import json
from pathlib import Path
from unittest.mock import patch

from golem.core.cli_wrapper import (
    CLIConfig,
    CLIType,
    _copy_mcp_json,
    _get_subprocess_env,
    _write_settings_json,
)


class TestWriteSettingsJson:
    """Regression: settings.json must overwrite existing restrictive settings."""

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
        # Must NOT have permissions key — that would override settings.local.json
        assert "permissions" not in settings
        # created list must NOT include the file (it already existed)
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


class TestCopyMcpJson:
    """Regression: mcp_servers=[] must skip copying (no MCP), not copy all."""

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
                {"mcpServers": {"redmine": {"url": "r"}, "jenkins": {"url": "j"}}}
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
                {"mcpServers": {"redmine": {"url": "r"}, "jenkins": {"url": "j"}}}
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


class TestProjectRootSandbox:
    """Regression: CWD == project root must NOT skip settings sanitization.

    Bug: When ``config.cwd`` resolved to ``_PROJECT_ROOT``, the old code
    used it directly.  ``_prepare_work_dir`` then returned early (it skips
    when CWD is the project root), leaving the project's interactive-mode
    ``settings.json`` with restrictive ``Bash(git *)`` permissions.  The
    spawned child agent couldn't use Bash freely.

    Fix: ``_get_subprocess_env`` now detects this case and forces a temp
    sandbox directory so ``_prepare_work_dir`` always runs its sanitization.
    """

    def test_cwd_at_project_root_gets_temp_sandbox(self, tmp_path):
        """CWD == _PROJECT_ROOT must redirect to a temp sandbox."""
        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path):
            config = CLIConfig(
                cli_type=CLIType.CLAUDE, cwd=str(tmp_path), mcp_servers=[]
            )
            _env, cwd, cleanup = _get_subprocess_env(config)

        try:
            # Must NOT be the project root — must be a temp dir
            assert Path(cwd).resolve() != tmp_path.resolve()
            assert "flow_sandbox_" in cwd
        finally:
            cleanup()

    def test_cwd_at_project_root_no_agent_worktree_env(self, tmp_path):
        """Project-root CWD must NOT set AGENT_WORKTREE (it's not a worktree)."""
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

        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path / "project"):
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
