"""Slack notification backend for the golem profile system.

Builds Slack Block Kit messages for each lifecycle event and posts them
via ``SlackClient``.
"""

import logging
import time
from typing import Any

from ..core.defaults import _fmt_duration

logger = logging.getLogger("golem.backends.slack_notifier")


def _header(text: str, emoji: str = "") -> dict[str, Any]:
    prefix = f"{emoji} " if emoji else ""
    return {
        "type": "header",
        "text": {"type": "plain_text", "text": f"{prefix}{text}"[:150], "emoji": True},
    }


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _fields(pairs: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "type": "section",
        "fields": [{"type": "mrkdwn", "text": f"*{k}:*\n{v}"} for k, v in pairs if v],
    }


def _divider() -> dict[str, Any]:
    return {"type": "divider"}


class SlackNotifier:
    """Sends golem lifecycle messages to a Slack channel."""

    def __init__(self, slack_client: Any, channel: str = "golem"):
        self._slack = slack_client
        self._channel = channel

    def notify_started(self, task_id: int | str, subject: str) -> None:
        blocks = [
            _header(f"Golem Started: #{task_id}", ":rocket:"),
            _section(subject),
        ]
        self._send(blocks, f"Golem started #{task_id}")

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
        emoji = (
            ":white_check_mark:"
            if retry_count == 0 and fix_iteration == 0
            else ":arrows_counterclockwise:"
        )
        blocks: list[dict[str, Any]] = [
            _header(f"Golem Completed: #{task_id}", emoji),
            _section(subject),
        ]

        facts: list[tuple[str, str]] = [
            ("Cost", f"${cost_usd:.2f}"),
            ("Duration", _fmt_duration(duration_s)),
            ("Steps", str(steps)),
        ]
        if verdict:
            facts.append(("Verdict", f"{verdict} ({confidence:.0%})"))
        if commit_sha:
            facts.append(("Commit", f"`{commit_sha}`"))
        if fix_iteration:
            facts.append(("Fix iterations", str(fix_iteration)))
        if retry_count:
            facts.append(("Full retries", str(retry_count)))
        blocks.append(_fields(facts))

        if concerns:
            items = "\n".join(f"• {c}" for c in concerns)
            blocks.extend([_divider(), _section(f"*Concerns*\n{items}")])

        self._send(blocks, f"Golem completed #{task_id}")

    def notify_failed(
        self,
        task_id: int | str,
        subject: str,
        reason: str,
        *,
        cost_usd: float = 0.0,
        duration_s: float = 0.0,
    ) -> None:
        blocks = [
            _header(f"Golem Failed: #{task_id}", ":x:"),
            _section(subject),
            _fields(
                [
                    ("Error", reason),
                    ("Cost", f"${cost_usd:.2f}"),
                    ("Duration", _fmt_duration(duration_s)),
                ]
            ),
        ]
        self._send(blocks, f"Golem failed #{task_id}")

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
        blocks: list[dict[str, Any]] = [
            _header(f"Golem Needs Review: #{task_id}", ":warning:"),
            _section(subject),
            _fields(
                [
                    ("Verdict", verdict),
                    ("Cost", f"${cost_usd:.2f}"),
                    ("Duration", _fmt_duration(duration_s)),
                    ("Fix iterations", str(fix_iteration)),
                    ("Full retries", str(retry_count)),
                ]
            ),
        ]
        if summary:
            blocks.append(_section(f"*Summary*: {summary}"))
        if concerns:
            items = "\n".join(f"• {c}" for c in concerns)
            blocks.extend([_divider(), _section(f"*Concerns*\n{items}")])

        self._send(blocks, f"Golem needs review #{task_id}")

    def notify_batch_submitted(self, group_id: str, task_count: int) -> None:
        blocks = [
            _header(f"Batch Submitted: {group_id}", ":package:"),
            _fields([("Tasks", str(task_count))]),
        ]
        self._send(blocks, f"Batch submitted: {group_id}")

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
        emoji = ":white_check_mark:" if status == "completed" else ":x:"
        facts: list[tuple[str, str]] = [
            ("Status", status),
            ("Tasks", str(task_count)),
            ("Cost", f"${total_cost_usd:.2f}"),
            ("Duration", _fmt_duration(total_duration_s)),
        ]
        if validation_verdict:
            facts.append(("Validation", validation_verdict))
        blocks: list[dict[str, Any]] = [
            _header(f"Batch {status.title()}: {group_id}", emoji),
            _fields(facts),
        ]
        self._send(blocks, f"Batch {status}: {group_id}")

    def notify_health_alert(
        self,
        alert_type: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        from ..health import ALERT_LABELS

        label = ALERT_LABELS.get(alert_type, alert_type.replace("_", " ").title())
        blocks: list[dict[str, Any]] = [
            _header(f"Health Alert: {label}", ":rotating_light:"),
            _section(message),
        ]
        if details:
            facts: list[tuple[str, str]] = []
            if "value" in details and details["value"] is not None:
                facts.append(("Current", str(details["value"])))
            if "threshold" in details and details["threshold"] is not None:
                facts.append(("Threshold", str(details["threshold"])))
            if facts:
                blocks.append(_fields(facts))
        self._send(blocks, f"Health alert: {label}")

    _MAX_SEND_RETRIES = 2
    _SEND_RETRY_DELAY = 1.0  # seconds

    def _send(self, blocks: list[dict[str, Any]], fallback_text: str) -> None:
        payload = {"text": fallback_text, "blocks": blocks}
        for attempt in range(1 + self._MAX_SEND_RETRIES):
            try:
                self._slack.send_to_channel(self._channel, payload)
                return
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if attempt < self._MAX_SEND_RETRIES:
                    logger.warning(
                        "Failed to send Slack message (attempt %d/%d): %s — retrying",
                        attempt + 1,
                        1 + self._MAX_SEND_RETRIES,
                        exc,
                    )
                    time.sleep(self._SEND_RETRY_DELAY)
                else:
                    logger.error(
                        "Failed to send Slack message after %d attempts: %s",
                        1 + self._MAX_SEND_RETRIES,
                        exc,
                    )
