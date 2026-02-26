# pylint: disable=too-few-public-methods,too-many-lines
"""Tests for golem.core.cli_wrapper — full coverage."""
import json
import signal
import subprocess
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from golem.core.cli_wrapper import (
    CLIConfig,
    CLIError,
    CLIResult,
    CLIType,
    _ScopedHome,
    _active_procs,
    _active_procs_lock,
    _copy_claude_dir,
    _copy_hooks_filtered,
    _copy_mcp_env,
    _copy_mcp_json,
    _copy_subdir,
    _cwd_for_cli,
    _extract_error_from_stream_output,
    _get_subprocess_env,
    _invoke_cli_quiet,
    _invoke_cli_verbose,
    _parse_stream_output,
    _prepare_work_dir,
    _track_proc,
    _untrack_proc,
    _write_settings_json,
    _write_settings_local,
    invoke_cli,
    invoke_cli_monitored,
    invoke_cli_raw,
    invoke_cli_streaming,
    kill_all_active,
)


class TestTrackUntrack:
    def test_track_and_untrack(self):
        proc = MagicMock()
        proc.pid = 99999
        _track_proc(proc)
        with _active_procs_lock:
            assert 99999 in _active_procs
        _untrack_proc(99999)
        with _active_procs_lock:
            assert 99999 not in _active_procs

    def test_untrack_missing_pid(self):
        _untrack_proc(88888)


class TestKillAllActive:
    def test_empty_returns_zero(self):
        with _active_procs_lock:
            saved = dict(_active_procs)
            _active_procs.clear()
        try:
            assert kill_all_active() == 0
        finally:
            with _active_procs_lock:
                _active_procs.update(saved)

    def test_kills_tracked_processes(self):
        p1 = MagicMock()
        p1.pid = 70001
        p1.poll.return_value = 0
        p2 = MagicMock()
        p2.pid = 70002
        p2.poll.return_value = 0

        with _active_procs_lock:
            saved = dict(_active_procs)
            _active_procs.clear()
            _active_procs[70001] = p1
            _active_procs[70002] = p2
        try:
            count = kill_all_active(timeout=0.1)
            assert count == 2
            p1.send_signal.assert_called_once_with(signal.SIGTERM)
            p2.send_signal.assert_called_once_with(signal.SIGTERM)
        finally:
            with _active_procs_lock:
                _active_procs.clear()
                _active_procs.update(saved)

    def test_sigkill_on_timeout(self):
        proc = MagicMock()
        proc.pid = 70003
        proc.poll.return_value = None

        with _active_procs_lock:
            saved = dict(_active_procs)
            _active_procs.clear()
            _active_procs[70003] = proc
        try:
            kill_all_active(timeout=0.01)
            proc.kill.assert_called_once()
        finally:
            with _active_procs_lock:
                _active_procs.clear()
                _active_procs.update(saved)

    def test_sigterm_oserror_ignored(self):
        proc = MagicMock()
        proc.pid = 70004
        proc.send_signal.side_effect = OSError("gone")
        proc.poll.return_value = 0

        with _active_procs_lock:
            saved = dict(_active_procs)
            _active_procs.clear()
            _active_procs[70004] = proc
        try:
            count = kill_all_active(timeout=0.01)
            assert count == 1
        finally:
            with _active_procs_lock:
                _active_procs.clear()
                _active_procs.update(saved)

    def test_sigkill_oserror_ignored(self):
        proc = MagicMock()
        proc.pid = 70005
        proc.poll.return_value = None
        proc.kill.side_effect = OSError("gone")

        with _active_procs_lock:
            saved = dict(_active_procs)
            _active_procs.clear()
            _active_procs[70005] = proc
        try:
            kill_all_active(timeout=0.01)
        finally:
            with _active_procs_lock:
                _active_procs.clear()
                _active_procs.update(saved)


class TestCwdForCli:
    def test_claude_prefix(self):
        cwd, cleanup = _cwd_for_cli(CLIType.CLAUDE)
        try:
            assert "flow_sandbox_" in cwd
            assert Path(cwd).is_dir()
        finally:
            cleanup()
        assert not Path(cwd).exists()

    def test_agent_prefix(self):
        cwd, cleanup = _cwd_for_cli(CLIType.AGENT)
        try:
            assert "agent_sandbox_" in cwd
            assert Path(cwd).is_dir()
        finally:
            cleanup()


class TestScopedHome:
    def test_no_source_mcp_returns_clean_env(self):
        with patch(
            "golem.core.cli_wrapper._SOURCE_MCP_JSON", Path("/nonexistent/mcp.json")
        ):
            with _ScopedHome(["server1"]) as env:
                assert "PATH" in env

    def test_filters_mcp_servers(self, tmp_path):
        real_home = tmp_path / "real_home"
        real_cursor = real_home / ".cursor"
        real_cursor.mkdir(parents=True)
        source_mcp = real_cursor / "mcp.json"
        source_mcp.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "redmine": {"url": "r"},
                        "jenkins": {"url": "j"},
                        "slack": {"url": "s"},
                    }
                }
            )
        )
        (real_cursor / "extensions.json").write_text("{}")

        with patch("golem.core.cli_wrapper._SOURCE_MCP_JSON", source_mcp), patch(
            "pathlib.Path.home", return_value=real_home
        ):
            with _ScopedHome(["redmine", "jenkins"]) as env:
                assert "HOME" in env
                fake_home = Path(env["HOME"])
                fake_mcp = fake_home / ".cursor" / "mcp.json"
                assert fake_mcp.exists()
                data = json.loads(fake_mcp.read_text())
                assert "redmine" in data["mcpServers"]
                assert "jenkins" in data["mcpServers"]
                assert "slack" not in data["mcpServers"]

    def test_symlinks_cursor_items(self, tmp_path):
        real_home = tmp_path / "real_home"
        real_cursor = real_home / ".cursor"
        real_cursor.mkdir(parents=True)
        source_mcp = real_cursor / "mcp.json"
        source_mcp.write_text(json.dumps({"mcpServers": {"a": {}}}))
        (real_cursor / "settings.json").write_text("{}")

        with patch("golem.core.cli_wrapper._SOURCE_MCP_JSON", source_mcp), patch(
            "pathlib.Path.home", return_value=real_home
        ):
            with _ScopedHome(["a"]) as env:
                fake_home = Path(env["HOME"])
                assert (fake_home / ".cursor" / "settings.json").exists()

    def test_symlink_config_dir(self, tmp_path):
        real_home = tmp_path / "real_home"
        real_cursor = real_home / ".cursor"
        real_cursor.mkdir(parents=True)
        real_config = real_home / ".config"
        real_config.mkdir()
        source_mcp = real_cursor / "mcp.json"
        source_mcp.write_text(json.dumps({"mcpServers": {"a": {}}}))

        with patch("golem.core.cli_wrapper._SOURCE_MCP_JSON", source_mcp), patch(
            "pathlib.Path.home", return_value=real_home
        ):
            with _ScopedHome(["a"]) as env:
                fake_home = Path(env["HOME"])
                assert (fake_home / ".config").is_symlink()

    def test_exit_without_tmpdir(self):
        scope = _ScopedHome(["x"])
        scope._tmpdir = None
        scope.__exit__(None, None, None)

    def test_symlink_failure_copies_file(self, tmp_path):
        real_home = tmp_path / "real_home"
        real_cursor = real_home / ".cursor"
        real_cursor.mkdir(parents=True)
        source_mcp = real_cursor / "mcp.json"
        source_mcp.write_text(json.dumps({"mcpServers": {"a": {}}}))
        (real_cursor / "other.txt").write_text("content")

        original_symlink = Path.symlink_to

        def patched_symlink(self_path, target):
            if self_path.name == "other.txt":
                raise OSError("symlink failed")
            return original_symlink(self_path, target)

        with patch("golem.core.cli_wrapper._SOURCE_MCP_JSON", source_mcp), patch(
            "pathlib.Path.home", return_value=real_home
        ), patch.object(Path, "symlink_to", patched_symlink):
            with _ScopedHome(["a"]) as env:
                fake_home = Path(env["HOME"])
                assert (fake_home / ".cursor" / "other.txt").read_text() == "content"


class TestPrepareWorkDir:
    def test_project_root_returns_noop(self, tmp_path):
        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path):
            cleanup = _prepare_work_dir(str(tmp_path), [])
            cleanup()

    def test_cleanup_handles_oserror(self, tmp_path):
        target = tmp_path / "work"
        target.mkdir()

        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path / "proj"), patch(
            "golem.core.cli_wrapper._copy_mcp_json"
        ), patch("golem.core.cli_wrapper._copy_mcp_env"), patch(
            "golem.core.cli_wrapper._copy_claude_dir"
        ) as mock_claude:
            sentinel_file = target / "sentinel.txt"
            sentinel_file.write_text("x")

            def side_effect(cwd_path, created):
                created.append(sentinel_file)

            mock_claude.side_effect = side_effect

            cleanup = _prepare_work_dir(str(target), [])
            sentinel_file.unlink()
            cleanup()


class TestPrepareWorkDirCleanup:
    def test_cleanup_unlinks_files_and_removes_dirs(self, tmp_path):
        target = tmp_path / "work"
        target.mkdir()
        created_file = target / "testfile.txt"
        created_file.write_text("x")
        created_dir = target / "testdir"
        created_dir.mkdir()

        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path / "proj"), patch(
            "golem.core.cli_wrapper._copy_mcp_json"
        ), patch("golem.core.cli_wrapper._copy_mcp_env"), patch(
            "golem.core.cli_wrapper._copy_claude_dir"
        ) as mock_cd:

            def populate(cwd_path, created):
                created.append(created_dir)
                created.append(created_file)

            mock_cd.side_effect = populate
            cleanup = _prepare_work_dir(str(target), [])

        cleanup()
        assert not created_file.exists()
        assert not created_dir.exists()

    def test_cleanup_rmdir_oserror_ignored(self, tmp_path):
        target = tmp_path / "work"
        target.mkdir()
        created_dir = target / "nonempty"
        created_dir.mkdir()
        (created_dir / "child.txt").write_text("x")

        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path / "proj"), patch(
            "golem.core.cli_wrapper._copy_mcp_json"
        ), patch("golem.core.cli_wrapper._copy_mcp_env"), patch(
            "golem.core.cli_wrapper._copy_claude_dir"
        ) as mock_cd:

            def populate(cwd_path, created):
                created.append(created_dir)

            mock_cd.side_effect = populate
            cleanup = _prepare_work_dir(str(target), [])

        cleanup()
        assert created_dir.exists()

    def test_cleanup_outer_oserror_ignored(self, tmp_path):
        target = tmp_path / "work"
        target.mkdir()

        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path / "proj"), patch(
            "golem.core.cli_wrapper._copy_mcp_json"
        ), patch("golem.core.cli_wrapper._copy_mcp_env"), patch(
            "golem.core.cli_wrapper._copy_claude_dir"
        ) as mock_cd:
            bad_path = MagicMock(spec=Path)
            bad_path.is_symlink.side_effect = OSError("broken")

            def populate(cwd_path, created):
                created.append(bad_path)

            mock_cd.side_effect = populate
            cleanup = _prepare_work_dir(str(target), [])

        cleanup()

    def test_cleanup_symlink_path(self, tmp_path):
        target = tmp_path / "work"
        target.mkdir()
        real_file = tmp_path / "real.txt"
        real_file.write_text("x")
        link = target / "link.txt"
        link.symlink_to(real_file)

        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path / "proj"), patch(
            "golem.core.cli_wrapper._copy_mcp_json"
        ), patch("golem.core.cli_wrapper._copy_mcp_env"), patch(
            "golem.core.cli_wrapper._copy_claude_dir"
        ) as mock_cd:

            def populate(cwd_path, created):
                created.append(link)

            mock_cd.side_effect = populate
            cleanup = _prepare_work_dir(str(target), [])

        cleanup()
        assert not link.exists()
        assert real_file.exists()


class TestCopyClaudeDirFull:
    def test_creates_dir_and_calls_helpers(self, tmp_path):
        proj_claude = tmp_path / "proj_claude"
        proj_claude.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude), patch(
            "golem.core.cli_wrapper._write_settings_local"
        ) as m1, patch("golem.core.cli_wrapper._write_settings_json") as m2, patch(
            "golem.core.cli_wrapper._copy_hooks_filtered"
        ) as m3, patch(
            "golem.core.cli_wrapper._copy_subdir"
        ) as m4:
            _copy_claude_dir(target, created)

        claude_dir = target / ".claude"
        assert claude_dir.is_dir()
        assert claude_dir in created
        m1.assert_called_once_with(claude_dir, created)
        m2.assert_called_once_with(claude_dir, created)
        m3.assert_called_once_with(claude_dir, created)
        m4.assert_called_once_with(claude_dir, "skills", created)

    def test_existing_claude_dir_not_recreated(self, tmp_path):
        proj_claude = tmp_path / "proj_claude"
        proj_claude.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        (target / ".claude").mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude), patch(
            "golem.core.cli_wrapper._write_settings_local"
        ) as m1, patch("golem.core.cli_wrapper._write_settings_json"), patch(
            "golem.core.cli_wrapper._copy_hooks_filtered"
        ), patch(
            "golem.core.cli_wrapper._copy_subdir"
        ):
            _copy_claude_dir(target, created)

        assert (target / ".claude") not in created
        m1.assert_called_once()


class TestCopyMcpJsonEdgeCases:
    def test_json_decode_error(self, tmp_path):
        source = tmp_path / "bad.json"
        source.write_text("not valid json{{{")
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_MCP_JSON", source):
            _copy_mcp_json(target, None, created)

        assert not (target / ".mcp.json").exists()
        assert not created

    def test_empty_servers_after_filter(self, tmp_path):
        source = tmp_path / "src.json"
        source.write_text(json.dumps({"mcpServers": {"a": {"url": "a"}}}))
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_MCP_JSON", source):
            _copy_mcp_json(target, ["nonexistent"], created)

        assert not (target / ".mcp.json").exists()

    def test_write_oserror(self, tmp_path):
        source = tmp_path / "src.json"
        source.write_text(json.dumps({"mcpServers": {"a": {"url": "a"}}}))
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_MCP_JSON", source), patch.object(
            Path, "write_text", side_effect=OSError("disk full")
        ):
            _copy_mcp_json(target, None, created)

        assert not created


class TestCopyMcpEnv:
    def test_skips_if_dst_exists(self, tmp_path):
        dst = tmp_path / ".mcp.env"
        dst.write_text("existing")
        created: list[Path] = []
        _copy_mcp_env(tmp_path, created)
        assert not created

    def test_skips_if_dst_is_symlink(self, tmp_path):
        target = tmp_path / "work"
        target.mkdir()
        dst = target / ".mcp.env"
        real = tmp_path / "real.env"
        real.write_text("x")
        dst.symlink_to(real)
        created: list[Path] = []
        _copy_mcp_env(target, created)
        assert not created

    def test_skips_if_src_missing(self, tmp_path):
        created: list[Path] = []
        with patch("golem.core.cli_wrapper._PROJECT_ROOT", tmp_path / "nope"):
            _copy_mcp_env(tmp_path, created)
        assert not created

    def test_creates_symlink(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        src = project / ".mcp.env"
        src.write_text("KEY=val")
        target = tmp_path / "work"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_ROOT", project):
            _copy_mcp_env(target, created)

        dst = target / ".mcp.env"
        assert dst.is_symlink()
        assert dst in created

    def test_symlink_oserror(self, tmp_path):
        project = tmp_path / "proj"
        project.mkdir()
        src = project / ".mcp.env"
        src.write_text("KEY=val")
        target = tmp_path / "work"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_ROOT", project), patch.object(
            Path, "symlink_to", side_effect=OSError("nope")
        ):
            _copy_mcp_env(target, created)

        assert not created


class TestCopyClaudeDir:
    def test_no_project_claude_dir(self, tmp_path):
        created: list[Path] = []
        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", tmp_path / "nope"):
            _copy_claude_dir(tmp_path, created)
        assert not created

    def test_mkdir_oserror(self, tmp_path):
        proj_claude = tmp_path / "proj_claude"
        proj_claude.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch(
            "golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude
        ), patch.object(Path, "mkdir", side_effect=OSError("no perm")):
            _copy_claude_dir(target, created)

        assert not created


class TestWriteSettingsLocal:
    def test_writes_settings(self, tmp_path):
        created: list[Path] = []
        _write_settings_local(tmp_path, created)
        dst = tmp_path / "settings.local.json"
        assert dst.exists()
        data = json.loads(dst.read_text())
        assert data["enableAllProjectMcpServers"] is True
        assert dst in created

    def test_existing_not_tracked_again(self, tmp_path):
        dst = tmp_path / "settings.local.json"
        dst.write_text("{}")
        created: list[Path] = []
        _write_settings_local(tmp_path, created)
        assert dst not in created
        data = json.loads(dst.read_text())
        assert "enableAllProjectMcpServers" in data

    def test_oserror_on_write(self, tmp_path):
        created: list[Path] = []
        with patch.object(Path, "write_text", side_effect=OSError("fail")):
            _write_settings_local(tmp_path, created)
        assert not created


class TestWriteSettingsJsonEdgeCases:
    def test_json_decode_error_in_source(self, tmp_path):
        proj_claude = tmp_path / "proj_claude"
        proj_claude.mkdir()
        (proj_claude / "settings.json").write_text("not json{{{")
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude):
            _write_settings_json(target, created)

        dst = target / "settings.json"
        assert dst.exists()
        data = json.loads(dst.read_text())
        assert "hooks" in data

    def test_oserror_on_write(self, tmp_path):
        created: list[Path] = []
        with patch.object(Path, "is_file", return_value=False), patch.object(
            Path, "write_text", side_effect=OSError("disk full")
        ):
            _write_settings_json(tmp_path, created)
        assert not created


class TestCopyHooksFiltered:
    def test_no_src_dir(self, tmp_path):
        created: list[Path] = []
        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", tmp_path / "nope"):
            _copy_hooks_filtered(tmp_path, created)
        assert not created

    def test_dst_already_exists(self, tmp_path):
        proj_claude = tmp_path / "proj"
        hooks_src = proj_claude / "hooks"
        hooks_src.mkdir(parents=True)
        target = tmp_path / "target"
        hooks_dst = target / "hooks"
        hooks_dst.mkdir(parents=True)
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude):
            _copy_hooks_filtered(target, created)

        assert not created

    def test_mkdir_oserror(self, tmp_path):
        proj_claude = tmp_path / "proj"
        hooks_src = proj_claude / "hooks"
        hooks_src.mkdir(parents=True)
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        original_mkdir = Path.mkdir

        def patched_mkdir(self_path, *args, **kwargs):
            if self_path.name == "hooks":
                raise OSError("no perm")
            return original_mkdir(self_path, *args, **kwargs)

        with patch(
            "golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude
        ), patch.object(Path, "mkdir", patched_mkdir):
            _copy_hooks_filtered(target, created)

        assert not created

    def test_skips_unsafe_hooks(self, tmp_path):
        proj_claude = tmp_path / "proj"
        hooks_src = proj_claude / "hooks"
        hooks_src.mkdir(parents=True)
        (hooks_src / "unsafe_hook.py").write_text("print('bad')")
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude):
            _copy_hooks_filtered(target, created)

        hooks_dir = target / "hooks"
        assert hooks_dir in created
        assert not (hooks_dir / "unsafe_hook.py").exists()

    def test_links_safe_hooks(self, tmp_path):
        proj_claude = tmp_path / "proj"
        hooks_src = proj_claude / "hooks"
        hooks_src.mkdir(parents=True)
        (hooks_src / "mcp_inject_credentials.py").write_text("print('ok')")
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude):
            _copy_hooks_filtered(target, created)

        dst = target / "hooks" / "mcp_inject_credentials.py"
        assert dst.is_symlink()
        assert dst in created

    def test_symlink_oserror(self, tmp_path):
        proj_claude = tmp_path / "proj"
        hooks_src = proj_claude / "hooks"
        hooks_src.mkdir(parents=True)
        (hooks_src / "mcp_inject_credentials.py").write_text("print('ok')")
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        original_symlink = Path.symlink_to

        def patched_symlink(self_path, tgt):
            if self_path.name == "mcp_inject_credentials.py":
                raise OSError("nope")
            return original_symlink(self_path, tgt)

        with patch(
            "golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude
        ), patch.object(Path, "symlink_to", patched_symlink):
            _copy_hooks_filtered(target, created)

        hooks_dir = target / "hooks"
        assert hooks_dir in created
        dst = hooks_dir / "mcp_inject_credentials.py"
        assert dst not in created


class TestCopySubdir:
    def test_skips_if_dst_exists(self, tmp_path):
        proj_claude = tmp_path / "proj"
        (proj_claude / "skills").mkdir(parents=True)
        target = tmp_path / "target"
        (target / "skills").mkdir(parents=True)
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude):
            _copy_subdir(target, "skills", created)

        assert not created

    def test_skips_if_src_missing(self, tmp_path):
        proj_claude = tmp_path / "proj"
        proj_claude.mkdir()
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude):
            _copy_subdir(target, "skills", created)

        assert not created

    def test_copies_subdir(self, tmp_path):
        proj_claude = tmp_path / "proj"
        skills_src = proj_claude / "skills"
        skills_src.mkdir(parents=True)
        (skills_src / "skill1.md").write_text("content")
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude):
            _copy_subdir(target, "skills", created)

        dst = target / "skills"
        assert dst.is_dir()
        assert (dst / "skill1.md").read_text() == "content"
        assert dst in created

    def test_copytree_oserror(self, tmp_path):
        proj_claude = tmp_path / "proj"
        skills_src = proj_claude / "skills"
        skills_src.mkdir(parents=True)
        target = tmp_path / "target"
        target.mkdir()
        created: list[Path] = []

        with patch("golem.core.cli_wrapper._PROJECT_CLAUDE_DIR", proj_claude), patch(
            "shutil.copytree", side_effect=OSError("fail")
        ):
            _copy_subdir(target, "skills", created)

        assert not created


class TestGetSubprocessEnvAgent:
    def test_agent_no_mcp(self):
        config = CLIConfig(cli_type=CLIType.AGENT, mcp_servers=[])
        env, cwd, cleanup = _get_subprocess_env(config)
        try:
            assert env is None
            assert "agent_sandbox_" in cwd
        finally:
            cleanup()

    def test_agent_with_mcp(self, tmp_path):
        real_home = tmp_path / "home"
        real_cursor = real_home / ".cursor"
        real_cursor.mkdir(parents=True)
        source_mcp = real_cursor / "mcp.json"
        source_mcp.write_text(json.dumps({"mcpServers": {"s1": {}}}))

        with patch("golem.core.cli_wrapper._SOURCE_MCP_JSON", source_mcp), patch(
            "pathlib.Path.home", return_value=real_home
        ):
            config = CLIConfig(cli_type=CLIType.AGENT, mcp_servers=["s1"])
            env, cwd, cleanup = _get_subprocess_env(config)

        try:
            assert env is not None
            assert "agent_sandbox_" in cwd
        finally:
            cleanup()


def _make_mock_popen(stdout_text="", stderr_text="", returncode=0):
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.communicate.return_value = (stdout_text, stderr_text)
    proc.stdin = MagicMock()
    proc.stdout = StringIO(stdout_text)
    proc.stderr = MagicMock()
    proc.stderr.read.return_value = stderr_text
    proc.wait.return_value = returncode
    proc.__enter__ = MagicMock(return_value=proc)
    proc.__exit__ = MagicMock(return_value=False)
    return proc


class TestInvokeCli:
    def test_verbose_dispatches(self):
        with patch("golem.core.cli_wrapper._invoke_cli_verbose") as mock_v:
            mock_v.return_value = CLIResult()
            invoke_cli("hello", CLIConfig(), verbose=True)
            mock_v.assert_called_once()

    def test_quiet_dispatches(self):
        with patch("golem.core.cli_wrapper._invoke_cli_quiet") as mock_q:
            mock_q.return_value = CLIResult()
            invoke_cli("hello", CLIConfig(), verbose=False)
            mock_q.assert_called_once()


class TestInvokeCliQuiet:
    def test_success(self):
        result_json = json.dumps(
            {
                "type": "result",
                "result": "done",
                "cost_usd": 0.5,
                "input_tokens": 100,
                "output_tokens": 50,
                "duration_ms": 2000,
            }
        )
        proc = _make_mock_popen(stdout_text=result_json + "\n", returncode=0)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            result = _invoke_cli_quiet("test prompt", CLIConfig())

        assert result.cost_usd == 0.5
        assert result.input_tokens == 100

    def test_timeout(self):
        proc = MagicMock()
        proc.pid = 11111
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired("cmd", 5),
            ("", ""),
        ]
        proc.kill.return_value = None

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="timed out"):
                _invoke_cli_quiet("test", CLIConfig(timeout_seconds=5))

    def test_file_not_found(self):
        with patch(
            "subprocess.Popen", side_effect=FileNotFoundError("not found")
        ), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="not found"):
                _invoke_cli_quiet("test", CLIConfig())

    def test_nonzero_exit(self):
        proc = _make_mock_popen(
            stdout_text="err output\n", stderr_text="bad", returncode=1
        )

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="exit code 1"):
                _invoke_cli_quiet("test", CLIConfig())

    def test_is_error_in_response(self):
        result_json = json.dumps(
            {
                "type": "result",
                "is_error": True,
                "result": "something went wrong",
            }
        )
        proc = _make_mock_popen(stdout_text=result_json + "\n", returncode=0)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="something went wrong"):
                _invoke_cli_quiet("test", CLIConfig())

    def test_cleanup_always_called(self):
        cleanup = MagicMock()
        proc = _make_mock_popen(returncode=0, stdout_text='{"type":"result"}\n')

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", cleanup),
        ):
            _invoke_cli_quiet("test", CLIConfig())

        cleanup.assert_called_once()

    def test_cleanup_called_on_error(self):
        cleanup = MagicMock()
        with patch("subprocess.Popen", side_effect=FileNotFoundError()), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", cleanup),
        ):
            with pytest.raises(CLIError):
                _invoke_cli_quiet("test", CLIConfig())

        cleanup.assert_called_once()


class TestInvokeCliVerbose:
    def _make_streaming_proc(self, lines, returncode=0, stderr_text=""):
        proc = MagicMock()
        proc.pid = 22222
        proc.returncode = returncode
        proc.stdin = MagicMock()
        proc.stdout = iter(lines)
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = stderr_text
        proc.wait.return_value = returncode
        proc.__enter__ = MagicMock(return_value=proc)
        proc.__exit__ = MagicMock(return_value=False)
        return proc

    def test_success(self):
        lines = [
            json.dumps({"type": "assistant", "content": "hi"}) + "\n",
            "\n",
            json.dumps(
                {
                    "type": "result",
                    "result": "done",
                    "cost_usd": 0.3,
                    "input_tokens": 50,
                    "output_tokens": 25,
                    "duration_ms": 1000,
                }
            )
            + "\n",
        ]
        proc = self._make_streaming_proc(lines, returncode=0)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ), patch("golem.core.cli_wrapper._StreamPrinter"):
            result = _invoke_cli_verbose("test", CLIConfig())

        assert result.cost_usd == 0.3
        assert result.output["result"] == "done"

    def test_nonzero_exit(self):
        lines = [
            json.dumps({"type": "assistant"}) + "\n",
        ]
        proc = self._make_streaming_proc(lines, returncode=2, stderr_text="bad stuff")

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ), patch("golem.core.cli_wrapper._StreamPrinter"):
            with pytest.raises(CLIError, match="exit code 2"):
                _invoke_cli_verbose("test", CLIConfig())

    def test_file_not_found(self):
        with patch("subprocess.Popen", side_effect=FileNotFoundError()), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ), patch("golem.core.cli_wrapper._StreamPrinter"):
            with pytest.raises(CLIError, match="not found"):
                _invoke_cli_verbose("test", CLIConfig())

    def test_no_result_event(self):
        lines = [
            json.dumps({"type": "assistant", "content": "thinking"}) + "\n",
        ]
        proc = self._make_streaming_proc(lines, returncode=0)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ), patch("golem.core.cli_wrapper._StreamPrinter"):
            result = _invoke_cli_verbose("test", CLIConfig())

        assert result.output.get("parse_error") is True

    def test_stdin_none(self):
        proc = MagicMock()
        proc.pid = 22223
        proc.returncode = 0
        proc.stdin = None
        proc.stdout = None
        proc.stderr = MagicMock()
        proc.wait.return_value = 0
        proc.__enter__ = MagicMock(return_value=proc)
        proc.__exit__ = MagicMock(return_value=False)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ), patch("golem.core.cli_wrapper._StreamPrinter"):
            with pytest.raises(CLIError, match="stdout"):
                _invoke_cli_verbose("test", CLIConfig())

    def test_invalid_json_lines_skipped(self):
        lines = [
            "not json at all\n",
            json.dumps({"type": "result", "result": "ok", "cost_usd": 0.1}) + "\n",
        ]
        proc = self._make_streaming_proc(lines, returncode=0)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ), patch("golem.core.cli_wrapper._StreamPrinter"):
            result = _invoke_cli_verbose("test", CLIConfig())

        assert result.output["result"] == "ok"
        assert len(result.trace_events) == 1


class TestInvokeCliRaw:
    def test_success(self):
        proc = _make_mock_popen(stdout_text="raw output here", returncode=0)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            result = invoke_cli_raw("test", CLIConfig())

        assert result == "raw output here"

    def test_timeout(self):
        proc = MagicMock()
        proc.pid = 33333
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired("cmd", 5),
            ("", ""),
        ]
        proc.kill.return_value = None

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="timed out"):
                invoke_cli_raw("test", CLIConfig())

    def test_file_not_found(self):
        with patch("subprocess.Popen", side_effect=FileNotFoundError()), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="not found"):
                invoke_cli_raw("test", CLIConfig())

    def test_nonzero_exit(self):
        proc = _make_mock_popen(stdout_text="", stderr_text="error msg", returncode=1)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="error msg"):
                invoke_cli_raw("test", CLIConfig())

    def test_cleanup_on_timeout(self):
        cleanup = MagicMock()
        proc = MagicMock()
        proc.pid = 33334
        proc.communicate.side_effect = [
            subprocess.TimeoutExpired("cmd", 5),
            ("", ""),
        ]
        proc.kill.return_value = None

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", cleanup),
        ):
            with pytest.raises(CLIError):
                invoke_cli_raw("test", CLIConfig())

        cleanup.assert_called_once()


class TestInvokeCliStreaming:
    def _make_streaming_proc(self, lines, returncode=0, stderr_text=""):
        proc = MagicMock()
        proc.pid = 44444
        proc.returncode = returncode
        proc.stdin = MagicMock()
        proc.stdout = iter(lines)
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = stderr_text
        proc.wait.return_value = returncode
        proc.__enter__ = MagicMock(return_value=proc)
        proc.__exit__ = MagicMock(return_value=False)
        return proc

    def test_success_with_callback(self):
        lines = [
            json.dumps({"type": "assistant", "content": "hi"}) + "\n",
            "\n",
            json.dumps({"type": "result", "result": "done"}) + "\n",
        ]
        proc = self._make_streaming_proc(lines)
        events = []

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            result = invoke_cli_streaming("test", CLIConfig(), callback=events.append)

        parsed = json.loads(result)
        assert len(parsed) == 2
        assert len(events) == 2

    def test_no_callback(self):
        lines = [
            json.dumps({"type": "result"}) + "\n",
        ]
        proc = self._make_streaming_proc(lines)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            result = invoke_cli_streaming("test", CLIConfig(), callback=None)

        parsed = json.loads(result)
        assert len(parsed) == 1

    def test_invalid_json_captured_as_raw(self):
        lines = [
            "not json\n",
            json.dumps({"type": "result"}) + "\n",
        ]
        proc = self._make_streaming_proc(lines)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            result = invoke_cli_streaming("test", CLIConfig())

        parsed = json.loads(result)
        assert len(parsed) == 2
        assert "raw" in parsed[0]

    def test_nonzero_exit(self):
        lines = [json.dumps({"type": "assistant"}) + "\n"]
        proc = self._make_streaming_proc(lines, returncode=1, stderr_text="fail")

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="exit code 1"):
                invoke_cli_streaming("test", CLIConfig())

    def test_file_not_found(self):
        with patch("subprocess.Popen", side_effect=FileNotFoundError()), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="not found"):
                invoke_cli_streaming("test", CLIConfig())

    def test_stdin_none(self):
        proc = MagicMock()
        proc.pid = 44445
        proc.returncode = 0
        proc.stdin = None
        proc.stdout = None
        proc.stderr = MagicMock()
        proc.wait.return_value = 0
        proc.__enter__ = MagicMock(return_value=proc)
        proc.__exit__ = MagicMock(return_value=False)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="stdout"):
                invoke_cli_streaming("test", CLIConfig())

    def test_cleanup_called(self):
        cleanup = MagicMock()
        lines = [json.dumps({"type": "result"}) + "\n"]
        proc = self._make_streaming_proc(lines)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._cwd_for_cli",
            return_value=("/tmp/sandbox", cleanup),
        ):
            invoke_cli_streaming("test", CLIConfig())

        cleanup.assert_called_once()


class TestInvokeCliMonitored:
    def _make_streaming_proc(self, lines, returncode=0, stderr_text=""):
        proc = MagicMock()
        proc.pid = 55555
        proc.returncode = returncode
        proc.stdin = MagicMock()
        proc.stdout = iter(lines)
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = stderr_text
        proc.wait.return_value = returncode
        proc.__enter__ = MagicMock(return_value=proc)
        proc.__exit__ = MagicMock(return_value=False)
        return proc

    def test_success_with_callback(self):
        lines = [
            json.dumps({"type": "assistant", "content": "hi"}) + "\n",
            "\n",
            json.dumps(
                {
                    "type": "result",
                    "result": "done",
                    "cost_usd": 0.2,
                    "input_tokens": 80,
                    "output_tokens": 40,
                    "duration_ms": 500,
                }
            )
            + "\n",
        ]
        proc = self._make_streaming_proc(lines)
        events = []

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            result = invoke_cli_monitored("test", CLIConfig(), callback=events.append)

        assert result.cost_usd == 0.2
        assert result.input_tokens == 80
        assert len(events) == 2
        assert result.output["result"] == "done"

    def test_no_callback(self):
        lines = [
            json.dumps({"type": "result", "result": "ok", "cost_usd": 0.1}) + "\n",
        ]
        proc = self._make_streaming_proc(lines)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            result = invoke_cli_monitored("test", CLIConfig(), callback=None)

        assert result.output["result"] == "ok"

    def test_nonzero_exit(self):
        lines = [json.dumps({"type": "assistant"}) + "\n"]
        proc = self._make_streaming_proc(lines, returncode=3, stderr_text="oops")

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="exit code 3"):
                invoke_cli_monitored("test", CLIConfig())

    def test_file_not_found(self):
        with patch("subprocess.Popen", side_effect=FileNotFoundError()), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="not found"):
                invoke_cli_monitored("test", CLIConfig())

    def test_no_result_event_returns_parse_error(self):
        lines = [
            json.dumps({"type": "assistant", "content": "thinking"}) + "\n",
        ]
        proc = self._make_streaming_proc(lines)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            result = invoke_cli_monitored("test", CLIConfig())

        assert result.output.get("parse_error") is True

    def test_stdin_none(self):
        proc = MagicMock()
        proc.pid = 55556
        proc.returncode = 0
        proc.stdin = None
        proc.stdout = None
        proc.stderr = MagicMock()
        proc.wait.return_value = 0
        proc.__enter__ = MagicMock(return_value=proc)
        proc.__exit__ = MagicMock(return_value=False)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            with pytest.raises(CLIError, match="stdout"):
                invoke_cli_monitored("test", CLIConfig())

    def test_invalid_json_lines_skipped(self):
        lines = [
            "garbage\n",
            json.dumps({"type": "result", "result": "ok", "cost_usd": 0.1}) + "\n",
        ]
        proc = self._make_streaming_proc(lines)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", lambda: None),
        ):
            result = invoke_cli_monitored("test", CLIConfig())

        assert len(result.trace_events) == 1

    def test_cleanup_called(self):
        cleanup = MagicMock()
        lines = [json.dumps({"type": "result", "cost_usd": 0.1}) + "\n"]
        proc = self._make_streaming_proc(lines)

        with patch("subprocess.Popen", return_value=proc), patch(
            "golem.core.cli_wrapper._get_subprocess_env",
            return_value=({}, "/tmp/sandbox", cleanup),
        ):
            invoke_cli_monitored("test", CLIConfig())

        cleanup.assert_called_once()


class TestParseStreamOutputEdgeCases:
    def test_empty_lines_skipped(self):
        stdout = "\n\n" + json.dumps({"type": "result", "cost_usd": 0.1}) + "\n\n"
        data, traces = _parse_stream_output(stdout)
        assert data["type"] == "result"
        assert len(traces) == 1

    def test_all_empty_lines(self):
        data, _traces = _parse_stream_output("\n\n\n")
        assert data.get("parse_error") is True


class TestExtractErrorEdgeCases:
    def test_empty_lines_skipped(self):
        stdout = "\n\nError: bad\n\n"
        result = _extract_error_from_stream_output(stdout, "")
        assert "bad" in result
        assert result.strip() == "Error: bad"

    def test_non_init_json_kept(self):
        stdout = json.dumps({"type": "error", "message": "fail"}) + "\n"
        result = _extract_error_from_stream_output(stdout, "")
        assert "fail" in result

    def test_stderr_only(self):
        result = _extract_error_from_stream_output("", "only stderr here")
        assert "only stderr here" in result

    def test_combined_stdout_stderr(self):
        result = _extract_error_from_stream_output("stdout line", "stderr line")
        assert "stdout line" in result
        assert "stderr line" in result
