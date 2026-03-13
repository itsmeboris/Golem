"""Teams notification backend for the golem profile system.

Wraps the existing card builders in ``notifications.py`` and the
``TeamsClient`` behind the ``Notifier`` protocol.
"""

import logging
from typing import Any

from ..notifications import (
    build_health_alert_card,
    build_task_completed_card,
    build_task_escalation_card,
    build_task_failure_card,
    build_task_started_card,
)

logger = logging.getLogger("golem.backends.teams_notifier")


class TeamsNotifier:
    """Sends golem lifecycle cards to a Teams channel."""

    def __init__(self, teams_client: Any, channel: str = "golem"):
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
        fix_iteration: int = 0,
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
            fix_iteration=fix_iteration,
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
        fix_iteration: int = 0,
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
            fix_iteration=fix_iteration,
        )
        self._send(card)

    def notify_batch_submitted(self, group_id: str, task_count: int) -> None:
        """Send a batch-submitted card to Teams."""
        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "body": [
                            {
                                "type": "TextBlock",
                                "size": "Medium",
                                "weight": "Bolder",
                                "text": f"Batch Submitted: {group_id}",
                            },
                            {
                                "type": "TextBlock",
                                "text": f"Tasks: {task_count}",
                            },
                        ],
                    },
                }
            ],
        }
        self._send(card)

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
        """Send a batch-completed card to Teams."""
        facts = [
            {"title": "Status", "value": status},
            {"title": "Tasks", "value": str(task_count)},
            {"title": "Cost", "value": f"${total_cost_usd:.2f}"},
            {"title": "Duration", "value": f"{total_duration_s:.0f}s"},
        ]
        if validation_verdict:
            facts.append({"title": "Validation", "value": validation_verdict})
        card = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "body": [
                            {
                                "type": "TextBlock",
                                "size": "Medium",
                                "weight": "Bolder",
                                "text": f"Batch {status.title()}: {group_id}",
                            },
                            {
                                "type": "FactSet",
                                "facts": facts,
                            },
                        ],
                    },
                }
            ],
        }
        self._send(card)

    def notify_health_alert(
        self,
        alert_type: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Send a health alert card to Teams."""
        card = build_health_alert_card(
            alert_type=alert_type,
            message=message,
            details=details,
        )
        self._send(card)

    def _send(self, card: dict[str, Any]) -> None:
        try:
            self._teams.send_to_channel(self._channel, card)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to send Teams card: %s", exc)
