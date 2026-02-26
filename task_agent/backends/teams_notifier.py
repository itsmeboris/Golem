"""Teams notification backend for the task-agent profile system.

Wraps the existing card builders in ``notifications.py`` and the
``TeamsClient`` behind the ``Notifier`` protocol.
"""

import logging
from typing import Any

from ..notifications import (
    build_task_completed_card,
    build_task_escalation_card,
    build_task_failure_card,
    build_task_started_card,
)

logger = logging.getLogger("Tools.AgentAutomation.Backends.TeamsNotifier")


class TeamsNotifier:
    """Sends task-agent lifecycle cards to a Teams channel."""

    def __init__(self, teams_client: Any, channel: str = "task_agent"):
        self._teams = teams_client
        self._channel = channel

    def notify_started(self, task_id: int | str, subject: str) -> None:
        """Send a task-started card to Teams."""
        card = build_task_started_card(
            parent_id=int(task_id),
            subject=subject,
        )
        self._send(card)

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
        """Send a task-completed card to Teams."""
        card = build_task_completed_card(
            parent_id=int(task_id),
            subject=subject,
            total_cost_usd=cost_usd,
            duration_s=duration_s,
            steps=steps,
            verdict=verdict,
            confidence=confidence,
            concerns=concerns,
            commit_sha=commit_sha,
            retry_count=retry_count,
        )
        self._send(card)

    def notify_failed(
        self,
        task_id: int | str,
        subject: str,
        reason: str,
        *,
        cost_usd: float = 0.0,
        duration_s: float = 0.0,
    ) -> None:
        """Send a task-failure card to Teams."""
        card = build_task_failure_card(
            parent_id=int(task_id),
            subject=subject,
            reason=reason,
            cost_usd=cost_usd,
            duration_s=duration_s,
        )
        self._send(card)

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
        """Send a task-escalation card to Teams."""
        card = build_task_escalation_card(
            parent_id=int(task_id),
            subject=subject,
            verdict=verdict,
            summary=summary,
            concerns=concerns,
            cost_usd=cost_usd,
            duration_s=duration_s,
            retry_count=retry_count,
        )
        self._send(card)

    def _send(self, card: dict[str, Any]) -> None:
        try:
            self._teams.send_to_channel(self._channel, card)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to send Teams card: %s", exc)
