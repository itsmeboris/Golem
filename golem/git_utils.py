"""Git utility functions for Golem.

Helpers for detecting git repos and parsing remote URLs.
"""

import logging
import re
import subprocess

from golem.sandbox import make_sandbox_preexec

logger = logging.getLogger(__name__)

# Matches github.com SSH (git@github.com:owner/repo.git)
_SSH_COLON_RE = re.compile(r"github\.com:([^/]+)/([^/\s]+?)(?:\.git)?$")
# Matches github.com HTTPS/SSH-scheme (https://github.com/owner/repo.git)
_URL_SLASH_RE = re.compile(r"github\.com/([^/]+)/([^/\s]+?)(?:\.git)?$")


def is_git_repo(path: str) -> bool:
    """Check if *path* is inside a git working tree."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=5,
            preexec_fn=make_sandbox_preexec(),
        )
        return result.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def detect_github_remote(repo_path: str) -> str | None:
    """Return 'owner/repo' from git origin, or None.

    Parses SSH (git@github.com:owner/repo.git), HTTPS
    (https://github.com/owner/repo), and SSH-scheme
    (ssh://git@github.com/owner/repo) formats.
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=5,
            preexec_fn=make_sandbox_preexec(),
        )
        if result.returncode != 0:
            return None
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None

    url = result.stdout.strip()

    match = _SSH_COLON_RE.search(url)
    if match:
        return f"{match.group(1)}/{match.group(2)}"

    match = _URL_SLASH_RE.search(url)
    if match:
        return f"{match.group(1)}/{match.group(2)}"

    return None
