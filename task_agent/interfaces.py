"""Protocol interfaces for pluggable task-agent backends.

Five protocols define the contract between the task-agent orchestration engine
and external services.  Each protocol can be satisfied by any class that
implements the required methods (structural subtyping via ``typing.Protocol``).

Protocols:
    TaskSource      — discovers and reads tasks (replaces Redmine polling)
    StateBackend    — updates task status, comments, progress (replaces Redmine REST)
    Notifier        — sends lifecycle notifications (replaces Teams cards)
    ToolProvider    — determines MCP servers for a task (replaces mcp_scope.py)
    PromptProvider  — loads and formats prompt templates (replaces prompts.py)
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Canonical task status strings
# ---------------------------------------------------------------------------


class TaskStatus:
    """Canonical status values used across all backends.

    Each ``StateBackend`` maps these to its system-specific IDs/names.
    """

    IN_PROGRESS = "in_progress"
    FIXED = "fixed"  # work done, pending validation
    CLOSED = "closed"  # fully done


# ---------------------------------------------------------------------------
# TaskSource — where tasks come from
# ---------------------------------------------------------------------------


@runtime_checkable
class TaskSource(Protocol):
    """Discovers and reads task definitions from an external system."""

    def poll_tasks(
        self,
        projects: list[str],
        detection_tag: str,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """Return open tasks matching *detection_tag*.

        Each dict must have at minimum ``{"id": <str|int>, "subject": str}``.
        """
        ...

    def get_task_description(self, task_id: int | str) -> str:
        """Fetch the full description/body text of a task."""
        ...

    def get_child_tasks(self, parent_id: int | str) -> list[dict[str, Any]]:
        """Fetch child/sub-tasks of *parent_id*.

        Returns list of dicts with at minimum ``{"id": <str|int>, "subject": str}``.
        """
        ...

    def create_child_task(
        self,
        parent_id: int | str,
        subject: str,
        description: str,
    ) -> int | str | None:
        """Create a child task under *parent_id*.  Returns new task ID or ``None``."""
        ...


# ---------------------------------------------------------------------------
# StateBackend — task state persistence in an external tracker
# ---------------------------------------------------------------------------


@runtime_checkable
class StateBackend(Protocol):
    """Updates task state in an external tracking system."""

    def update_status(self, task_id: int | str, status: str) -> bool:
        """Transition task to a canonical ``TaskStatus`` value.  Returns success."""
        ...

    def post_comment(self, task_id: int | str, text: str) -> bool:
        """Post a comment/note on the task.  Returns success."""
        ...

    def update_progress(self, task_id: int | str, percent: int) -> bool:
        """Update completion percentage (0-100).  Returns success."""
        ...


# ---------------------------------------------------------------------------
# Notifier — lifecycle notifications
# ---------------------------------------------------------------------------


@runtime_checkable
class Notifier(Protocol):
    """Sends lifecycle notifications to an external channel."""

    def notify_started(self, task_id: int | str, subject: str) -> None:
        ...

    def notify_completed(
        self,
        task_id: int | str,
        subject: str,
        *,
        cost_usd: float = 0.0,
        duration_s: float = 0.0,
        steps: int = 0,
        verdict: str = "",
        confidence: float = 0.0,
        concerns: list[str] | None = None,
        commit_sha: str = "",
        retry_count: int = 0,
    ) -> None:
        ...

    def notify_failed(
        self,
        task_id: int | str,
        subject: str,
        reason: str,
        *,
        cost_usd: float = 0.0,
        duration_s: float = 0.0,
    ) -> None:
        ...

    def notify_escalated(
        self,
        task_id: int | str,
        subject: str,
        verdict: str,
        summary: str,
        *,
        concerns: list[str] | None = None,
        cost_usd: float = 0.0,
        duration_s: float = 0.0,
        retry_count: int = 0,
    ) -> None:
        ...


# ---------------------------------------------------------------------------
# ToolProvider — MCP server selection
# ---------------------------------------------------------------------------


@runtime_checkable
class ToolProvider(Protocol):
    """Determines which MCP tool servers are available for a given task."""

    def base_servers(self) -> list[str]:
        """Servers always included (e.g. ``["redmine"]`` or ``[]``)."""
        ...

    def servers_for_subject(self, subject: str) -> list[str]:
        """Return full list of MCP servers for the given task subject."""
        ...


# ---------------------------------------------------------------------------
# PromptProvider — prompt template loading
# ---------------------------------------------------------------------------


@runtime_checkable
class PromptProvider(Protocol):
    """Loads and formats prompt templates."""

    def format(self, template_name: str, **kwargs: Any) -> str:
        """Load a template by name and fill placeholders."""
        ...
