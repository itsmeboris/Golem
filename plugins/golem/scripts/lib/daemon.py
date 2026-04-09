"""Daemon lifecycle helpers — check, start, ensure running."""

import json
import subprocess
import sys
from pathlib import Path


def is_golem_installed() -> bool:
    """Check if golem CLI is in PATH."""
    try:
        subprocess.run(
            ["golem", "--help"],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_daemon_running() -> bool:
    """Check if golem daemon is running by calling golem status."""
    try:
        result = subprocess.run(
            ["golem", "status", "--hours", "0"],
            capture_output=True,
            timeout=3,
            text=True,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def is_repo_attached(cwd: str | None = None) -> bool:
    """Check if cwd (or current dir) is in the golem repo registry."""
    target = Path(cwd or Path.cwd()).resolve()
    registry_path = Path.home() / ".golem" / "repos.json"
    if not registry_path.exists():
        return False
    try:
        import json
        repos = json.loads(registry_path.read_text())
        # Registry stores resolved absolute paths as keys
        return str(target) in repos
    except (json.JSONDecodeError, OSError):
        return False


def start_daemon() -> dict:
    """Start golem daemon in background. Returns status dict."""
    try:
        result = subprocess.run(
            ["golem", "daemon"],
            capture_output=True,
            timeout=10,
            text=True,
        )
        return {
            "started": result.returncode == 0,
            "detail": result.stdout.strip() or result.stderr.strip(),
        }
    except FileNotFoundError:
        return {"started": False, "detail": "golem not found in PATH"}
    except subprocess.TimeoutExpired:
        # Daemon may fork to background, timeout is expected
        return {"started": True, "detail": "daemon started (backgrounded)"}


def ensure_running() -> dict:
    """Start daemon if not already running. Returns status dict."""
    if is_daemon_running():
        return {"already_running": True, "started": False}
    result = start_daemon()
    return {"already_running": False, **result}


def attach_repo(path: str | None = None) -> dict:
    """Attach a repo with detection disabled. Returns status dict."""
    target = path or str(Path.cwd())
    try:
        result = subprocess.run(
            ["golem", "attach", "--no-detect", target],
            capture_output=True,
            timeout=10,
            text=True,
        )
        return {
            "attached": result.returncode == 0,
            "path": target,
            "detail": result.stdout.strip() or result.stderr.strip(),
        }
    except FileNotFoundError:
        return {"attached": False, "path": target, "detail": "golem not found"}
    except subprocess.TimeoutExpired:
        return {"attached": False, "path": target, "detail": "timeout"}
