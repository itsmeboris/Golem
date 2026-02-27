"""Slack integration — send Block Kit messages via Incoming Webhooks."""

import logging
from typing import Any

import requests

from .defaults import POST_TIMEOUT

logger = logging.getLogger("golem.core.slack")


class SlackClientError(Exception):
    """Raised when a Slack webhook operation fails unrecoverably."""


class SlackClient:
    """HTTP client for posting Block Kit messages to Slack Incoming Webhooks."""

    def __init__(
        self, webhooks: dict[str, str] | None = None, timeout: int = POST_TIMEOUT
    ):
        self._webhooks = webhooks or {}
        self._timeout = timeout

    def get_webhook_url(self, channel: str) -> str | None:
        return self._webhooks.get(channel)

    def send_message(self, webhook_url: str, payload: dict[str, Any]) -> bool:
        """POST *payload* to *webhook_url*.  Returns ``True`` on 2xx."""
        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                logger.error(
                    "Slack webhook returned %d: %s", resp.status_code, resp.text[:300]
                )
                return False
            logger.info("Slack message sent successfully (status=%d)", resp.status_code)
            return True
        except requests.RequestException:
            logger.exception("Failed to send Slack message")
            return False

    def send_to_channel(self, channel: str, payload: dict[str, Any]) -> bool:
        """Resolve *channel* to a URL and send *payload*."""
        url = self.get_webhook_url(channel)
        if not url:
            logger.warning("No webhook URL configured for Slack channel '%s'", channel)
            return False
        return self.send_message(url, payload)
