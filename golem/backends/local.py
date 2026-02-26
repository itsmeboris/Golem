"""Local / null backend implementations for the golem profile system.

Provides zero-dependency backends that work without any external services:
- ``LocalFileTaskSource`` reads tasks from YAML files in a directory
- ``NullStateBackend`` logs transitions without calling any API
- ``LogNotifier`` logs notifications without sending them anywhere
- ``NullToolProvider`` returns empty MCP server lists
"""

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("golem.backends.local")


# ---------------------------------------------------------------------------
# LocalFileTaskSource
# ---------------------------------------------------------------------------


class LocalFileTaskSource:
    """Read tasks from YAML/JSON files in a directory.

    Task file format (YAML or JSON)::

        id: "001"
        subject: "[AGENT] Refactor config parser"
        description: |
          The config parser needs to be split into smaller functions.
        children: []  # optional, omit for auto-decomposition

    Files are named ``<id>.yaml``, ``<id>.yml``, or ``<id>.json``.
    """

    def __init__(self, tasks_dir: str | Path):
        self._tasks_dir = Path(tasks_dir)

    def poll_tasks(
        self,
        projects: list[str],
        detection_tag: str,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """Scan the tasks directory for files whose subject matches *detection_tag*."""
        del projects, timeout  # Not used for local files
        if not self._tasks_dir.is_dir():
            logger.warning("Tasks directory does not exist: %s", self._tasks_dir)
            return []

        results: list[dict[str, Any]] = []
        for task_file in sorted(self._tasks_dir.iterdir()):
            if task_file.suffix not in (".yaml", ".yml", ".json"):
                continue
            task = self._load_file(task_file)
            if task is None:
                continue
            subject = task.get("subject", "")
            if detection_tag.upper() in subject.upper():
                results.append(task)
        return results

    def get_task_subject(self, task_id: int | str) -> str:
        """Return the subject field from the task file."""
        task = self._find_task(str(task_id))
        return task.get("subject", "") if task else ""

    def get_task_description(self, task_id: int | str) -> str:
        """Return the description field from the task file."""
        task = self._find_task(str(task_id))
        return task.get("description", "") if task else ""

    def get_child_tasks(self, parent_id: int | str) -> list[dict[str, Any]]:
        """Return child tasks: embedded in parent file OR files with matching parent_id.

        When the supervisor decomposes a task, ``create_child_task`` writes
        child JSON files with a ``parent_id`` field.  This method finds those
        files so the supervisor can iterate over them.
        """
        # 1. Check embedded children in the parent file
        task = self._find_task(str(parent_id))
        if task:
            embedded = [
                c for c in task.get("children", []) if isinstance(c, dict) and "id" in c
            ]
            if embedded:
                return embedded

        # 2. Scan directory for files whose parent_id matches
        if not self._tasks_dir.is_dir():
            return []
        results: list[dict[str, Any]] = []
        for task_file in sorted(self._tasks_dir.iterdir()):
            if task_file.suffix not in (".yaml", ".yml", ".json"):
                continue
            child = self._load_file(task_file)
            if child and str(child.get("parent_id", "")) == str(parent_id):
                if "id" not in child:
                    child["id"] = task_file.stem
                results.append(child)
        return results

    def create_child_task(
        self,
        parent_id: int | str,
        subject: str,
        description: str,
    ) -> int | str | None:
        """Create a child task JSON file under the tasks directory."""
        self._tasks_dir.mkdir(parents=True, exist_ok=True)
        # Generate a simple incremental ID
        existing = set()
        for f in self._tasks_dir.iterdir():
            existing.add(f.stem)
        child_id = f"{parent_id}-sub{len(existing) + 1}"

        task_data = {
            "id": child_id,
            "subject": subject,
            "description": description,
            "parent_id": str(parent_id),
        }
        out_file = self._tasks_dir / f"{child_id}.json"
        out_file.write_text(json.dumps(task_data, indent=2), encoding="utf-8")
        logger.info("Created local child task: %s", out_file)
        return child_id

    def _find_task(self, task_id: str) -> dict[str, Any] | None:
        for ext in (".yaml", ".yml", ".json"):
            candidate = self._tasks_dir / f"{task_id}{ext}"
            if candidate.exists():
                return self._load_file(candidate)
        # Fall back to scanning all files
        for task_file in self._tasks_dir.iterdir():
            if task_file.suffix not in (".yaml", ".yml", ".json"):
                continue
            task = self._load_file(task_file)
            if task and str(task.get("id", "")) == task_id:
                return task
        return None

    @staticmethod
    def _load_file(path: Path) -> dict[str, Any] | None:
        try:
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                return json.loads(text)
            # Try YAML
            try:
                import yaml  # pylint: disable=import-outside-toplevel

                return yaml.safe_load(text) or {}
            except ImportError:
                logger.warning(
                    "PyYAML not installed — cannot read %s; use JSON instead",
                    path,
                )
                return None
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to load task file %s: %s", path, exc)
            return None


# ---------------------------------------------------------------------------
# NullStateBackend
# ---------------------------------------------------------------------------


class NullStateBackend:
    """No-op state backend — logs transitions but makes no external calls."""

    def update_status(self, task_id: int | str, status: str) -> bool:
        """Log a status transition."""
        logger.info("State: task %s -> %s", task_id, status)
        return True

    def post_comment(self, task_id: int | str, text: str) -> bool:
        """Log a comment."""
        logger.info("Comment on %s: %s", task_id, text[:120])
        return True

    def update_progress(self, task_id: int | str, percent: int) -> bool:
        """Log a progress update."""
        logger.debug("Progress on %s: %d%%", task_id, percent)
        return True


# ---------------------------------------------------------------------------
# LogNotifier
# ---------------------------------------------------------------------------


class LogNotifier:
    """Logs lifecycle notifications instead of sending them."""

    def notify_started(self, task_id: int | str, subject: str) -> None:
        """Log a task-started notification."""
        logger.info("NOTIFY: Task %s started: %s", task_id, subject)

    def notify_completed(  # pylint: disable=unused-argument
        self,
        task_id: int | str,
        subject: str,
        **kwargs: Any,
    ) -> None:
        """Log a task-completed notification."""
        logger.info(
            "NOTIFY: Task %s completed: %s (cost=$%.2f)",
            task_id,
            subject,
            kwargs.get("cost_usd", 0.0),
        )

    def notify_failed(  # pylint: disable=unused-argument
        self,
        task_id: int | str,
        subject: str,
        reason: str,
        **kwargs: Any,
    ) -> None:
        """Log a task-failed notification."""
        logger.info("NOTIFY: Task %s failed: %s — %s", task_id, subject, reason[:120])

    def notify_escalated(  # pylint: disable=unused-argument
        self,
        task_id: int | str,
        subject: str,
        verdict: str,
        summary: str,
        **kwargs: Any,
    ) -> None:
        """Log a task-escalated notification."""
        logger.info(
            "NOTIFY: Task %s escalated (%s): %s", task_id, verdict, summary[:120]
        )


# ---------------------------------------------------------------------------
# NullToolProvider
# ---------------------------------------------------------------------------


class NullToolProvider:
    """No MCP servers — agent uses only built-in tools."""

    def base_servers(self) -> list[str]:
        """Return an empty server list."""
        return []

    def servers_for_subject(  # pylint: disable=unused-argument
        self, subject: str
    ) -> list[str]:
        """Return an empty server list (no MCP tools)."""
        return []
