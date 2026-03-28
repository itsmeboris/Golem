"""Repo registry for Golem attach/detach.

Tracks which directories are registered with the daemon.
Persisted to ~/.golem/repos.json (overridable via
GOLEM_REGISTRY_PATH env var).
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .core.config import GOLEM_HOME
from .types import RepoEntryDict

logger = logging.getLogger(__name__)

_DEFAULT_REGISTRY_PATH = GOLEM_HOME / "repos.json"


class RepoRegistry:
    """Manages the list of attached repositories."""

    def __init__(self, registry_path: Path | None = None) -> None:
        env_path = os.environ.get("GOLEM_REGISTRY_PATH")
        if registry_path is not None:
            self._registry_path = registry_path
        elif env_path:
            self._registry_path = Path(env_path)
        else:
            self._registry_path = _DEFAULT_REGISTRY_PATH
        self._repos: list[RepoEntryDict] = []
        self.load()

    def load(self) -> None:
        """Load registry from disk. Silently starts empty on error."""
        if not self._registry_path.exists():
            self._repos = []
            return
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load repo registry: %s", exc)
            self._repos = []
            return
        if not isinstance(data, dict) or "repos" not in data:
            self._repos = []
            return
        self._repos = [r for r in data["repos"] if isinstance(r, dict) and "path" in r]

    def save(self) -> None:
        """Persist registry to disk."""
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"repos": self._repos}
        self._registry_path.write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )

    def attach(self, path: str, heartbeat: bool = True) -> None:
        """Add or update a repo entry. Auto-saves."""
        resolved = str(Path(path).resolve())
        normalized = resolved if resolved == "/" else resolved.rstrip("/")
        for repo in self._repos:
            if repo["path"] == normalized:
                repo["heartbeat"] = heartbeat
                repo["attached_at"] = datetime.now(timezone.utc).isoformat()
                self.save()
                return
        entry: RepoEntryDict = {
            "path": normalized,
            "heartbeat": heartbeat,
            "attached_at": datetime.now(timezone.utc).isoformat(),
        }
        self._repos.append(entry)
        self.save()

    def detach(self, path: str) -> bool:
        """Remove a repo entry. Returns True if found. Auto-saves."""
        resolved = str(Path(path).resolve())
        normalized = resolved if resolved == "/" else resolved.rstrip("/")
        before = len(self._repos)
        self._repos = [r for r in self._repos if r["path"] != normalized]
        if len(self._repos) < before:
            self.save()
            return True
        return False

    def list_repos(self) -> list[RepoEntryDict]:
        """Return all registered repos."""
        return list(self._repos)

    def heartbeat_repos(self) -> list[RepoEntryDict]:
        """Return repos with heartbeat enabled."""
        return [r for r in self._repos if r.get("heartbeat", False)]
