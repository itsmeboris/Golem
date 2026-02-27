"""Slack notification backend for the golem profile system.

Builds Slack Block Kit messages for each lifecycle event and posts them
via ``SlackClient``.
"""

import logging
from typing import Any

logger = logging.getLogger("golem.backends.slack_notifier")


def _fmt_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


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
            _section(subject[:300]),
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
    ) -> None:
        emoji = (
            ":white_check_mark:" if retry_count == 0 else ":arrows_counterclockwise:"
        )
        blocks: list[dict[str, Any]] = [
            _header(f"Golem Completed: #{task_id}", emoji),
            _section(subject[:300]),
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
        if retry_count:
            facts.append(("Retries", str(retry_count)))
        blocks.append(_fields(facts))

        if concerns:
            items = "\n".join(f"• {c}" for c in concerns[:5])
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
            _section(subject[:300]),
            _fields(
                [
                    ("Error", reason[:200]),
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
    ) -> None:
        blocks: list[dict[str, Any]] = [
            _header(f"Golem Needs Review: #{task_id}", ":warning:"),
            _section(subject[:300]),
            _fields(
                [
                    ("Verdict", verdict),
                    ("Cost", f"${cost_usd:.2f}"),
                    ("Duration", _fmt_duration(duration_s)),
                    ("Retried", "Yes" if retry_count else "No"),
                ]
            ),
        ]
        if summary:
            blocks.append(_section(f"*Summary*: {summary[:300]}"))
        if concerns:
            items = "\n".join(f"• {c}" for c in concerns[:5])
            blocks.extend([_divider(), _section(f"*Concerns*\n{items}")])

        self._send(blocks, f"Golem needs review #{task_id}")

    def _send(self, blocks: list[dict[str, Any]], fallback_text: str) -> None:
        payload = {"text": fallback_text, "blocks": blocks}
        try:
            self._slack.send_to_channel(self._channel, payload)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to send Slack message: %s", exc)
