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
from .detect_stack import detect_verify_config
from .types import RepoEntryDict
from .verify_config import load_verify_config, save_verify_config

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

    def attach(
        self,
        path: str,
        heartbeat: bool = True,
        *,
        run_detection: bool = False,
        force_detect: bool = False,
    ) -> None:
        """Add or update a repo entry. Auto-saves.

        If run_detection=True, runs buildpack-style stack detection and writes
        .golem/verify.yaml to the target repo. Detection failures are logged
        but do not block the attach operation.
        If force_detect=True, overwrites any existing verify.yaml.
        """
        resolved = str(Path(path).resolve())
        normalized = resolved if resolved == "/" else resolved.rstrip("/")
        for repo in self._repos:
            if repo["path"] == normalized:
                repo["heartbeat"] = heartbeat
                repo["attached_at"] = datetime.now(timezone.utc).isoformat()
                self.save()
                self._ensure_gitignore(normalized)
                if run_detection:
                    self._run_detection(normalized, force=force_detect)
                return
        entry: RepoEntryDict = {
            "path": normalized,
            "heartbeat": heartbeat,
            "attached_at": datetime.now(timezone.utc).isoformat(),
        }
        self._repos.append(entry)
        self.save()
        self._ensure_gitignore(normalized)
        if run_detection:
            self._run_detection(normalized, force=force_detect)

    @staticmethod
    def _ensure_gitignore(path: str) -> None:
        """Ensure .golem/ is in .gitignore so merges aren't blocked."""
        repo_dir = Path(path)
        if not repo_dir.is_dir():
            return
        gitignore = repo_dir / ".gitignore"
        entry = ".golem/"
        try:
            content = gitignore.read_text(encoding="utf-8")
            if entry in content.splitlines():
                return
        except FileNotFoundError:
            pass
        except OSError:
            logger.debug("Could not read .gitignore in %s", path)
            return
        try:
            with gitignore.open("a", encoding="utf-8") as f:
                f.write(f"\n{entry}\n")
            logger.info("Added %s to .gitignore in %s", entry, path)
        except OSError:
            logger.debug("Could not update .gitignore in %s", path)

    def _run_detection(self, path: str, *, force: bool = False) -> None:
        """Run stack detection and write .golem/verify.yaml. Non-fatal on error.

        If a verify.yaml already exists, detection is skipped to preserve
        user-maintained configuration.  Use ``golem attach --force-detect``
        to regenerate.
        """
        existing = load_verify_config(path)
        if existing is not None and existing.commands and not force:
            logger.info(
                "Valid verify.yaml with %d command(s) found for %s — skipping "
                "detection (use --force-detect to regenerate)",
                len(existing.commands),
                path,
            )
            return
        try:
            config = detect_verify_config(path, dry_run=True)
            save_verify_config(path, config)
            logger.info(
                "Detection complete for %s: stack=%s commands=%d",
                path,
                config.stack,
                len(config.commands),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning(
                "Stack detection failed for %s — verify.yaml not written",
                path,
                exc_info=True,
            )

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
