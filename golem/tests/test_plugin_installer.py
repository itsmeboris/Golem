"""Tests for golem/plugin_installer.py."""

import json
import os
from unittest.mock import patch

from golem.plugin_installer import (
    _create_marketplace,
    _enable_plugin_in_settings,
    _register_marketplace,
    _register_plugin,
    detect_ai_tools,
    get_plugin_source_dir,
    install_plugin,
)


class TestDetectAITools:
    """Test AI tool detection."""

    def test_detects_claude_code(self, tmp_path):
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir()
        with patch.dict(os.environ, {"HOME": str(tmp_path)}):
            tools = detect_ai_tools(home=tmp_path)
        assert "claude" in tools
        assert tools["claude"] == tmp_path / ".claude" / "plugins"

    def test_no_tools_detected(self, tmp_path):
        with patch.dict(os.environ, {"HOME": str(tmp_path)}):
            tools = detect_ai_tools(home=tmp_path)
        assert tools == {}

    def test_wsl_windows_home(self, tmp_path):
        win_home = tmp_path / "mnt" / "c" / "Users" / "testuser"
        win_claude = win_home / ".claude"
        win_claude.mkdir(parents=True)

        with patch(
            "golem.plugin_installer._wsl_windows_homes", return_value=[win_home]
        ):
            tools = detect_ai_tools(home=tmp_path)
        assert "claude" in tools

    def test_plugin_dir_override(self, tmp_path):
        override = tmp_path / "custom"
        tools = detect_ai_tools(home=tmp_path, plugin_dir=override)
        assert "custom" in tools
        assert tools["custom"] == override


class TestGetPluginSourceDir:
    """Test plugin source directory resolution."""

    def test_finds_plugin_in_package(self):
        source = get_plugin_source_dir()
        assert source.name == "golem"
        assert source.parent.name == "plugins"

    def test_source_contains_plugin_json(self):
        source = get_plugin_source_dir()
        plugin_json = source / ".claude-plugin" / "plugin.json"
        assert plugin_json.exists(), f"Expected {plugin_json} to exist"

    @patch("golem.plugin_installer.__file__", new="__does_not_exist__")
    def test_installed_mode_fallback(self, tmp_path):
        """When repo-relative path lacks plugin.json, falls back to _plugin_data."""
        from pathlib import Path

        fake_pkg = tmp_path / "golem"
        fake_pkg.mkdir()
        plugin_data = fake_pkg / "_plugin_data" / ".claude-plugin"
        plugin_data.mkdir(parents=True)
        (plugin_data / "plugin.json").write_text('{"name": "golem"}')

        fake_file = fake_pkg / "plugin_installer.py"
        fake_file.touch()

        import golem.plugin_installer as mod

        with patch.object(mod, "Path", wraps=Path):
            old_file = mod.__file__
            mod.__file__ = str(fake_file)
            try:
                result = mod.get_plugin_source_dir()
                assert result == fake_pkg / "_plugin_data"
            finally:
                mod.__file__ = old_file

    def test_not_found_returns_repo_path(self, tmp_path):
        """When neither path has plugin.json, returns repo path anyway."""
        fake_pkg = tmp_path / "golem"
        fake_pkg.mkdir()
        fake_file = fake_pkg / "plugin_installer.py"
        fake_file.touch()

        import golem.plugin_installer as mod

        old_file = mod.__file__
        mod.__file__ = str(fake_file)
        try:
            result = mod.get_plugin_source_dir()
            assert result == tmp_path / "plugins" / "golem"
        finally:
            mod.__file__ = old_file


def _make_source(tmp_path, version="0.1.0"):
    """Helper: create a fake plugin source directory."""
    source = tmp_path / "source" / "golem"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "golem", "version": version})
    )
    (source / "commands").mkdir()
    (source / "commands" / "setup.md").write_text("# setup")
    return source


class TestInstallPlugin:
    """Test plugin installation to cache + registry."""

    def test_installs_to_cache_and_registers(self, tmp_path):
        source = _make_source(tmp_path, "0.1.0")
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()
        # settings.json lives in parent of plugins_base
        (tmp_path / "settings.json").write_text("{}")

        result = install_plugin(source, plugins_base)

        assert result["ok"] is True
        cache_path = plugins_base / "cache" / "golem-local" / "golem" / "0.1.0"
        assert (cache_path / ".claude-plugin" / "plugin.json").exists()
        assert (cache_path / "commands" / "setup.md").exists()

        # Check registry
        registry = json.loads((plugins_base / "installed_plugins.json").read_text())
        assert "golem@golem-local" in registry["plugins"]
        entry = registry["plugins"]["golem@golem-local"][0]
        assert entry["version"] == "0.1.0"
        assert entry["installPath"] == str(cache_path)
        assert entry["scope"] == "user"

        # Check marketplace
        mp_json = plugins_base / "marketplaces" / "golem-local" / ".claude-plugin" / "marketplace.json"
        assert mp_json.exists()
        mp = json.loads(mp_json.read_text())
        assert mp["name"] == "golem-local"
        assert mp["plugins"][0]["name"] == "golem"

        # Check symlink
        link = plugins_base / "marketplaces" / "golem-local" / "plugins" / "golem"
        assert link.is_symlink()
        assert link.resolve() == cache_path.resolve()

    def test_overwrites_existing_version(self, tmp_path):
        source_v1 = _make_source(tmp_path / "v1", "0.1.0")
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()

        install_plugin(source_v1, plugins_base)

        source_v2 = _make_source(tmp_path / "v2", "0.1.0")
        (source_v2 / "commands" / "run.md").write_text("# run v2")

        result = install_plugin(source_v2, plugins_base)

        assert result["ok"] is True
        cache_path = plugins_base / "cache" / "golem-local" / "golem" / "0.1.0"
        assert (cache_path / "commands" / "run.md").exists()
        assert (cache_path / "commands" / "run.md").read_text() == "# run v2"

    def test_source_not_found(self, tmp_path):
        source = tmp_path / "nonexistent"
        plugins_base = tmp_path / "plugins"
        result = install_plugin(source, plugins_base)
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_preserves_existing_on_copy_failure(self, tmp_path):
        source = _make_source(tmp_path, "0.1.0")
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()

        # Pre-install so there's something to preserve
        install_plugin(source, plugins_base)
        cache_path = plugins_base / "cache" / "golem-local" / "golem" / "0.1.0"
        original_json = (cache_path / ".claude-plugin" / "plugin.json").read_text()

        with patch(
            "golem.plugin_installer.shutil.copytree", side_effect=OSError("disk full")
        ):
            result = install_plugin(source, plugins_base)

        assert result["ok"] is False
        # Original should be restored
        assert (cache_path / ".claude-plugin" / "plugin.json").exists()
        assert (cache_path / ".claude-plugin" / "plugin.json").read_text() == original_json

    def test_install_oserror(self, tmp_path):
        source = tmp_path / "source"
        source.mkdir()
        (source / ".claude-plugin").mkdir()
        (source / ".claude-plugin" / "plugin.json").write_text('{"version": "0.1.0"}')
        plugins_base = tmp_path / "plugins"

        with patch(
            "golem.plugin_installer.shutil.copytree", side_effect=OSError("disk full")
        ):
            result = install_plugin(source, plugins_base)

        assert result["ok"] is False
        assert "disk full" in result["error"]

    def test_cleans_preexisting_staging_and_backup(self, tmp_path):
        source = _make_source(tmp_path, "0.1.0")
        plugins_base = tmp_path / "plugins"
        cache_parent = plugins_base / "cache" / "golem-local" / "golem"
        cache_parent.mkdir(parents=True)

        staging = cache_parent / "0.1.0.staging"
        staging.mkdir()
        (staging / "old.txt").write_text("stale")
        backup = cache_parent / "0.1.0.backup"
        backup.mkdir()
        (backup / "old.txt").write_text("stale")

        result = install_plugin(source, plugins_base)

        assert result["ok"] is True
        assert not staging.exists()
        assert not backup.exists()

    def test_no_leftover_staging_on_failure(self, tmp_path):
        source = _make_source(tmp_path, "0.1.0")
        plugins_base = tmp_path / "plugins"
        cache_parent = plugins_base / "cache" / "golem-local" / "golem"
        cache_parent.mkdir(parents=True)
        staging = cache_parent / "0.1.0.staging"
        staging.mkdir()

        with patch(
            "golem.plugin_installer.shutil.copytree", side_effect=OSError("disk full")
        ):
            install_plugin(source, plugins_base)

        assert not staging.exists()

    def test_restores_backup_on_swap_failure(self, tmp_path):
        source = _make_source(tmp_path, "0.1.0")
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()

        install_plugin(source, plugins_base)

        rename_calls = [0]
        original_rename = os.rename

        def selective_rename(src, dst):
            rename_calls[0] += 1
            if rename_calls[0] == 2:
                raise OSError("rename failed")
            return original_rename(src, dst)

        with patch("os.rename", side_effect=selective_rename):
            result = install_plugin(source, plugins_base)

        assert result["ok"] is False

    def test_backup_restore_failure_is_swallowed(self, tmp_path):
        source = _make_source(tmp_path, "0.1.0")
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()

        install_plugin(source, plugins_base)

        rename_calls = [0]
        original_rename = os.rename

        def always_fail_rename(src, dst):
            rename_calls[0] += 1
            if rename_calls[0] == 1:
                return original_rename(src, dst)
            raise OSError("rename failed")

        with patch("os.rename", side_effect=always_fail_rename):
            result = install_plugin(source, plugins_base)

        assert result["ok"] is False

    def test_staging_rmtree_failure_is_swallowed(self, tmp_path):
        source = _make_source(tmp_path, "0.1.0")
        plugins_base = tmp_path / "plugins"
        cache_parent = plugins_base / "cache" / "golem-local" / "golem"
        cache_parent.mkdir(parents=True)
        staging = cache_parent / "0.1.0.staging"
        staging.mkdir()

        original_rmtree = __import__("shutil").rmtree

        def fail_staging_rmtree(path, *a, **kw):
            if ".staging" in str(path):
                raise OSError("rmtree failed")
            return original_rmtree(path, *a, **kw)

        with patch(
            "golem.plugin_installer.shutil.copytree", side_effect=OSError("disk full")
        ):
            with patch(
                "golem.plugin_installer.shutil.rmtree",
                side_effect=fail_staging_rmtree,
            ):
                result = install_plugin(source, plugins_base)

        assert result["ok"] is False


class TestReadPluginVersion:
    """Test _read_plugin_version fallback."""

    def test_returns_version_from_plugin_json(self, tmp_path):
        from golem.plugin_installer import _read_plugin_version

        source = tmp_path / "golem"
        (source / ".claude-plugin").mkdir(parents=True)
        (source / ".claude-plugin" / "plugin.json").write_text('{"version": "1.2.3"}')
        assert _read_plugin_version(source) == "1.2.3"

    def test_returns_fallback_on_missing_file(self, tmp_path):
        from golem.plugin_installer import _read_plugin_version

        source = tmp_path / "golem"
        source.mkdir()
        assert _read_plugin_version(source) == "0.0.0"

    def test_returns_fallback_on_invalid_json(self, tmp_path):
        from golem.plugin_installer import _read_plugin_version

        source = tmp_path / "golem"
        (source / ".claude-plugin").mkdir(parents=True)
        (source / ".claude-plugin" / "plugin.json").write_text("not json{{{")
        assert _read_plugin_version(source) == "0.0.0"


class TestRegisterPlugin:
    """Test installed_plugins.json registry management."""

    def test_creates_registry_if_missing(self, tmp_path):
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()
        install_path = plugins_base / "cache" / "golem-local" / "golem" / "0.1.0"

        _register_plugin(plugins_base, install_path, "0.1.0")

        registry = json.loads((plugins_base / "installed_plugins.json").read_text())
        assert registry["version"] == 2
        assert "golem@golem-local" in registry["plugins"]

    def test_preserves_existing_plugins(self, tmp_path):
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()
        existing = {
            "version": 2,
            "plugins": {
                "other@marketplace": [{"scope": "user", "version": "1.0.0"}]
            },
        }
        (plugins_base / "installed_plugins.json").write_text(json.dumps(existing))

        install_path = plugins_base / "cache" / "golem-local" / "golem" / "0.1.0"
        _register_plugin(plugins_base, install_path, "0.1.0")

        registry = json.loads((plugins_base / "installed_plugins.json").read_text())
        assert "other@marketplace" in registry["plugins"]
        assert "golem@golem-local" in registry["plugins"]

    def test_handles_corrupt_registry(self, tmp_path):
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()
        (plugins_base / "installed_plugins.json").write_text("not json{{{")

        install_path = plugins_base / "cache" / "golem-local" / "golem" / "0.1.0"
        _register_plugin(plugins_base, install_path, "0.1.0")

        registry = json.loads((plugins_base / "installed_plugins.json").read_text())
        assert "golem@golem-local" in registry["plugins"]


class TestEnablePluginInSettings:
    """Test settings.json enabledPlugins management."""

    def test_creates_settings_if_missing(self, tmp_path):
        _enable_plugin_in_settings(tmp_path, "golem@golem-local")

        settings = json.loads((tmp_path / "settings.json").read_text())
        assert settings["enabledPlugins"]["golem@golem-local"] is True

    def test_adds_to_existing_settings(self, tmp_path):
        existing = {"permissions": {"allow": ["Read"]}, "enabledPlugins": {"other@mp": True}}
        (tmp_path / "settings.json").write_text(json.dumps(existing))

        _enable_plugin_in_settings(tmp_path, "golem@golem-local")

        settings = json.loads((tmp_path / "settings.json").read_text())
        assert settings["enabledPlugins"]["golem@golem-local"] is True
        assert settings["enabledPlugins"]["other@mp"] is True
        assert settings["permissions"]["allow"] == ["Read"]

    def test_skips_if_already_enabled(self, tmp_path):
        existing = {"enabledPlugins": {"golem@golem-local": True}}
        (tmp_path / "settings.json").write_text(json.dumps(existing))

        _enable_plugin_in_settings(tmp_path, "golem@golem-local")

        settings = json.loads((tmp_path / "settings.json").read_text())
        assert settings["enabledPlugins"]["golem@golem-local"] is True

    def test_handles_corrupt_settings(self, tmp_path):
        (tmp_path / "settings.json").write_text("not json{{{")

        _enable_plugin_in_settings(tmp_path, "golem@golem-local")

        settings = json.loads((tmp_path / "settings.json").read_text())
        assert settings["enabledPlugins"]["golem@golem-local"] is True


class TestCreateMarketplace:
    """Test marketplace directory creation."""

    def test_creates_marketplace_structure(self, tmp_path):
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()
        install_path = plugins_base / "cache" / "golem-local" / "golem" / "0.1.0"
        install_path.mkdir(parents=True)

        _create_marketplace(plugins_base, install_path, "0.1.0")

        mp_json = plugins_base / "marketplaces" / "golem-local" / ".claude-plugin" / "marketplace.json"
        assert mp_json.exists()
        mp = json.loads(mp_json.read_text())
        assert mp["name"] == "golem-local"
        assert mp["plugins"][0]["name"] == "golem"
        assert mp["plugins"][0]["version"] == "0.1.0"

        link = plugins_base / "marketplaces" / "golem-local" / "plugins" / "golem"
        assert link.is_symlink()

    def test_overwrites_existing_symlink(self, tmp_path):
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()

        old_path = plugins_base / "cache" / "golem-local" / "golem" / "0.0.1"
        old_path.mkdir(parents=True)
        _create_marketplace(plugins_base, old_path, "0.0.1")

        new_path = plugins_base / "cache" / "golem-local" / "golem" / "0.1.0"
        new_path.mkdir(parents=True)
        _create_marketplace(plugins_base, new_path, "0.1.0")

        link = plugins_base / "marketplaces" / "golem-local" / "plugins" / "golem"
        assert link.is_symlink()
        assert link.resolve() == new_path.resolve()

    def test_registers_known_marketplace(self, tmp_path):
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()

        _register_marketplace(plugins_base)

        km = json.loads((plugins_base / "known_marketplaces.json").read_text())
        assert "golem-local" in km
        assert km["golem-local"]["source"]["source"] == "local"
        mp_dir = plugins_base / "marketplaces" / "golem-local"
        assert km["golem-local"]["installLocation"] == str(mp_dir)

    def test_handles_corrupt_known_marketplaces(self, tmp_path):
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()
        (plugins_base / "known_marketplaces.json").write_text("not json{{{")

        _register_marketplace(plugins_base)

        km = json.loads((plugins_base / "known_marketplaces.json").read_text())
        assert "golem-local" in km

    def test_preserves_existing_marketplaces(self, tmp_path):
        plugins_base = tmp_path / "plugins"
        plugins_base.mkdir()
        existing = {"other-mp": {"source": {"source": "git"}, "installLocation": "/x"}}
        (plugins_base / "known_marketplaces.json").write_text(json.dumps(existing))

        _register_marketplace(plugins_base)

        km = json.loads((plugins_base / "known_marketplaces.json").read_text())
        assert "other-mp" in km
        assert "golem-local" in km

    def test_replaces_directory_with_symlink(self, tmp_path):
        plugins_base = tmp_path / "plugins"
        mp_plugin_dir = plugins_base / "marketplaces" / "golem-local" / "plugins" / "golem"
        mp_plugin_dir.mkdir(parents=True)
        (mp_plugin_dir / "old_file.txt").write_text("old")

        install_path = plugins_base / "cache" / "golem-local" / "golem" / "0.1.0"
        install_path.mkdir(parents=True)

        _create_marketplace(plugins_base, install_path, "0.1.0")

        assert mp_plugin_dir.is_symlink()
