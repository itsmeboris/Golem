"""Protocol interfaces for pluggable golem backends.

Five protocols define the contract between the golem orchestration engine
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
        ...  # pragma: no cover

    def get_task_subject(self, task_id: int | str) -> str:
        """Fetch the short subject/title of a task."""
        ...  # pragma: no cover

    def get_task_description(self, task_id: int | str) -> str:
        """Fetch the full description/body text of a task."""
        ...  # pragma: no cover

    def get_child_tasks(self, parent_id: int | str) -> list[dict[str, Any]]:
        """Fetch child/sub-tasks of *parent_id*.

        Returns list of dicts with at minimum ``{"id": <str|int>, "subject": str}``.
        """
        ...  # pragma: no cover

    def create_child_task(
        self,
        parent_id: int | str,
        subject: str,
        description: str,
    ) -> int | str | None:
        """Create a child task under *parent_id*.  Returns new task ID or ``None``."""
        ...  # pragma: no cover

    def get_task_comments(
        self, task_id: int | str, *, since: str = ""
    ) -> list[dict[str, Any]]:
        """Return comments posted on the task.

        Each dict has keys: author, body, created_at.
        If *since* is provided, only return comments after that ISO timestamp.
        """
        return []  # default: no comments


# ---------------------------------------------------------------------------
# StateBackend — task state persistence in an external tracker
# ---------------------------------------------------------------------------


@runtime_checkable
class StateBackend(Protocol):
    """Updates task state in an external tracking system."""

    def update_status(self, task_id: int | str, status: str) -> bool:
        """Transition task to a canonical ``TaskStatus`` value.  Returns success."""
        ...  # pragma: no cover

    def post_comment(self, task_id: int | str, text: str) -> bool:
        """Post a comment/note on the task.  Returns success."""
        ...  # pragma: no cover

    def update_progress(self, task_id: int | str, percent: int) -> bool:
        """Update completion percentage (0-100).  Returns success."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# Notifier — lifecycle notifications
# ---------------------------------------------------------------------------


@runtime_checkable
class Notifier(Protocol):
    """Sends lifecycle notifications to an external channel."""

    def notify_started(
        self, task_id: int | str, subject: str
    ) -> None: ...  # pragma: no cover

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
        fix_iteration: int = 0,
    ) -> None: ...  # pragma: no cover

    def notify_failed(
        self,
        task_id: int | str,
        subject: str,
        reason: str,
        *,
        cost_usd: float = 0.0,
        duration_s: float = 0.0,
    ) -> None: ...  # pragma: no cover

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
        fix_iteration: int = 0,
    ) -> None: ...  # pragma: no cover

    def notify_batch_submitted(self, group_id: str, task_count: int) -> None:
        """Notify that a batch of tasks has been submitted."""
        ...  # pragma: no cover

    def notify_batch_completed(
        self,
        group_id: str,
        status: str,
        *,
        total_cost_usd: float = 0.0,
        total_duration_s: float = 0.0,
        task_count: int = 0,
        validation_verdict: str = "",
    ) -> None:
        """Notify that a batch of tasks has completed (or failed)."""
        ...  # pragma: no cover

    def notify_health_alert(
        self,
        alert_type: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Notify about a daemon health alert."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# ToolProvider — MCP server selection
# ---------------------------------------------------------------------------


@runtime_checkable
class ToolProvider(Protocol):
    """Determines which MCP tool servers are available for a given task."""

    def base_servers(self) -> list[str]:
        """Servers always included (e.g. ``["redmine"]`` or ``[]``)."""
        ...  # pragma: no cover

    def servers_for_subject(self, subject: str) -> list[str]:
        """Return full list of MCP servers for the given task subject."""
        ...  # pragma: no cover


# ---------------------------------------------------------------------------
# PromptProvider — prompt template loading
# ---------------------------------------------------------------------------


@runtime_checkable
class PromptProvider(Protocol):
    """Loads and formats prompt templates."""

    def format(self, template_name: str, **kwargs: Any) -> str:
        """Load a template by name and fill placeholders."""
        ...  # pragma: no cover
