"""Plugin installer — copies golem plugin to detected AI tool directories."""

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Plugin identity — must match .claude-plugin/plugin.json
_PLUGIN_NAME = "golem"
_MARKETPLACE_NAME = "golem-local"


def detect_ai_tools(
    home: Path | None = None,
    plugin_dir: Path | None = None,
) -> dict[str, Path]:
    """Detect installed AI tools and return their plugin base directories.

    Args:
        home: Home directory override (default: Path.home())
        plugin_dir: Manual override — skips detection, uses this path directly

    Returns:
        Dict mapping tool name to the .claude/plugins base directory.
    """
    if plugin_dir is not None:
        return {"custom": plugin_dir}

    home = home or Path.home()
    tools: dict[str, Path] = {}

    # Claude Code
    claude_dir = home / ".claude"
    if claude_dir.is_dir():
        tools["claude"] = claude_dir / "plugins"

    # Check WSL Windows home directories
    for win_home in _wsl_windows_homes():
        win_claude = win_home / ".claude"
        if win_claude.is_dir() and "claude" not in tools:
            tools["claude"] = win_claude / "plugins"

    # Future: Cursor, Codex, etc.

    return tools


def _wsl_windows_homes() -> list[Path]:
    """Detect Windows home directories when running under WSL."""
    homes = []
    mnt_c_users = Path("/mnt/c/Users")
    if mnt_c_users.is_dir():
        for user_dir in mnt_c_users.iterdir():
            if user_dir.is_dir() and user_dir.name not in (
                "Public",
                "Default",
                "Default User",
                "All Users",
            ):
                homes.append(user_dir)
    return homes


def get_plugin_source_dir() -> Path:
    """Resolve the plugin source directory within the golem package."""
    # 1. Development mode: repo-relative plugins/golem/
    package_root = Path(__file__).parent.parent
    repo_path = package_root / "plugins" / "golem"
    if repo_path.is_dir() and (repo_path / ".claude-plugin" / "plugin.json").exists():
        return repo_path

    # 2. Installed mode: golem/_plugin_data/ (copied during build)
    installed_path = Path(__file__).parent / "_plugin_data"
    if (
        installed_path.is_dir()
        and (installed_path / ".claude-plugin" / "plugin.json").exists()
    ):
        return installed_path

    # 3. Not found — return repo path (will fail with clear error in install_plugin)
    return repo_path


def _read_plugin_version(source: Path) -> str:
    """Read version from plugin.json."""
    try:
        data = json.loads((source / ".claude-plugin" / "plugin.json").read_text())
        return data.get("version", "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def install_plugin(source: Path, plugins_base: Path) -> dict:
    """Install golem plugin to a Claude Code plugins directory.

    Copies to the cache directory and registers in installed_plugins.json,
    matching Claude Code's plugin discovery system.

    Args:
        source: Path to plugin source (e.g., plugins/golem/)
        plugins_base: Path to the plugins base dir (e.g., ~/.claude/plugins/)

    Returns:
        Status dict with 'ok' and optional 'error'.
    """
    if not source.is_dir():
        return {"ok": False, "error": f"Plugin source not found: {source}"}

    version = _read_plugin_version(source)

    # Target: ~/.claude/plugins/cache/golem-local/golem/<version>/
    cache_dir = plugins_base / "cache" / _MARKETPLACE_NAME / _PLUGIN_NAME / version
    staging = cache_dir.with_name(cache_dir.name + ".staging")
    backup = cache_dir.with_name(cache_dir.name + ".backup")

    try:
        cache_dir.parent.mkdir(parents=True, exist_ok=True)

        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(source, staging)
        if backup.exists():
            shutil.rmtree(backup)

        if cache_dir.exists():
            cache_dir.rename(backup)
        staging.rename(cache_dir)

        if backup.exists():
            shutil.rmtree(backup)

        # Register in installed_plugins.json
        _register_plugin(plugins_base, cache_dir, version)

        logger.info("Installed golem plugin to %s", cache_dir)
        return {"ok": True, "target": str(cache_dir)}
    except OSError as exc:
        if not cache_dir.exists() and backup.exists():
            try:
                backup.rename(cache_dir)
            except OSError:
                pass
        if staging.exists():
            try:
                shutil.rmtree(staging)
            except OSError:
                pass
        logger.error("Failed to install plugin to %s: %s", cache_dir, exc)
        return {"ok": False, "error": str(exc)}


def _register_plugin(plugins_base: Path, install_path: Path, version: str) -> None:
    """Register the plugin: marketplace dir, installed_plugins.json, settings.json."""
    plugin_key = f"{_PLUGIN_NAME}@{_MARKETPLACE_NAME}"

    # 1. Create marketplace directory structure
    _create_marketplace(plugins_base, install_path, version)

    # 2. Register in installed_plugins.json
    registry_path = plugins_base / "installed_plugins.json"
    registry = {"version": 2, "plugins": {}}

    if registry_path.exists():
        try:
            registry = json.loads(registry_path.read_text())
        except (json.JSONDecodeError, OSError):
            registry = {"version": 2, "plugins": {}}

    now = datetime.now(timezone.utc).isoformat()

    registry["plugins"][plugin_key] = [
        {
            "scope": "user",
            "installPath": str(install_path),
            "version": version,
            "installedAt": now,
            "lastUpdated": now,
        }
    ]

    registry_path.write_text(json.dumps(registry, indent=2))

    # 3. Register marketplace in known_marketplaces.json
    _register_marketplace(plugins_base)

    # 4. Enable in settings.json
    _enable_plugin_in_settings(plugins_base.parent, plugin_key)


def _create_marketplace(plugins_base: Path, install_path: Path, version: str) -> None:
    """Create the marketplace directory that Claude Code needs to discover the plugin."""
    mp_dir = plugins_base / "marketplaces" / _MARKETPLACE_NAME
    mp_meta = mp_dir / ".claude-plugin"
    mp_meta.mkdir(parents=True, exist_ok=True)

    # Write marketplace.json
    marketplace = {
        "name": _MARKETPLACE_NAME,
        "owner": {"name": "Golem"},
        "metadata": {
            "description": "Golem autonomous agent plugin for Claude Code.",
            "version": version,
        },
        "plugins": [
            {
                "name": _PLUGIN_NAME,
                "description": "Delegate complex tasks to Golem's autonomous agent pipeline.",
                "version": version,
                "author": {"name": "Golem"},
                "source": f"./plugins/{_PLUGIN_NAME}",
            }
        ],
    }
    (mp_meta / "marketplace.json").write_text(json.dumps(marketplace, indent=2))

    # Create plugins/<name> as a symlink to the cached install
    mp_plugins = mp_dir / "plugins"
    mp_plugins.mkdir(exist_ok=True)
    link_path = mp_plugins / _PLUGIN_NAME
    if link_path.is_symlink() or link_path.exists():
        if link_path.is_symlink():
            link_path.unlink()
        else:
            shutil.rmtree(link_path)
    link_path.symlink_to(install_path)


def _register_marketplace(plugins_base: Path) -> None:
    """Register the marketplace in known_marketplaces.json."""
    km_path = plugins_base / "known_marketplaces.json"
    km = {}

    if km_path.exists():
        try:
            km = json.loads(km_path.read_text())
        except (json.JSONDecodeError, OSError):
            km = {}

    mp_dir = plugins_base / "marketplaces" / _MARKETPLACE_NAME
    now = datetime.now(timezone.utc).isoformat()

    km[_MARKETPLACE_NAME] = {
        "source": {
            "source": "local",
            "path": str(mp_dir),
        },
        "installLocation": str(mp_dir),
        "lastUpdated": now,
    }

    km_path.write_text(json.dumps(km, indent=2))


def _enable_plugin_in_settings(claude_dir: Path, plugin_key: str) -> None:
    """Add the plugin to enabledPlugins in settings.json."""
    settings_path = claude_dir / "settings.json"
    settings = {}

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}

    enabled = settings.get("enabledPlugins", {})
    if enabled.get(plugin_key) is True:
        return  # Already enabled

    enabled[plugin_key] = True
    settings["enabledPlugins"] = enabled
    settings_path.write_text(json.dumps(settings, indent=2))
