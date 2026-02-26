"""Microsoft Teams integration — send Adaptive Cards via Incoming Webhooks."""

import json
import logging
from typing import Any

import requests

from .defaults import POST_TIMEOUT

logger = logging.getLogger("Tools.AgentAutomation.Teams")

ADAPTIVE_CARD_SCHEMA = "http://adaptivecards.io/schemas/adaptive-card.json"
ADAPTIVE_CARD_VERSION = "1.4"
CARD_CONTENT_TYPE = "application/vnd.microsoft.card.adaptive"


class TeamsClientError(Exception):
    """Raised when a Teams webhook operation fails unrecoverably."""


class TeamsClient:
    """HTTP client for posting Adaptive Cards to Teams Incoming Webhooks."""

    def __init__(
        self, webhooks: dict[str, str] | None = None, timeout: int = POST_TIMEOUT
    ):
        self._webhooks = webhooks or {}
        self._timeout = timeout

    def get_webhook_url(self, channel: str) -> str | None:
        """Return the webhook URL for *channel*, or ``None``."""
        return self._webhooks.get(channel)

    def send_card(self, webhook_url: str, card: dict[str, Any]) -> bool:
        """POST *card* to *webhook_url*.  Returns ``True`` on 2xx."""
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": CARD_CONTENT_TYPE,
                    "contentUrl": None,
                    "content": json.dumps(card),
                }
            ],
        }
        try:
            resp = requests.post(
                webhook_url,
                json=payload,
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                logger.error(
                    "Teams webhook returned %d: %s", resp.status_code, resp.text[:300]
                )
                return False
            logger.info("Card sent successfully (status=%d)", resp.status_code)
            return True
        except requests.RequestException:
            logger.exception("Failed to send Teams card")
            return False

    def send_to_channel(self, channel: str, card: dict[str, Any]) -> bool:
        """Resolve *channel* to a URL and send *card*."""
        url = self.get_webhook_url(channel)
        if not url:
            logger.warning("No webhook URL configured for channel '%s'", channel)
            return False
        return self.send_card(url, card)


def _card_envelope(
    body: list[dict], actions: list[dict] | None = None
) -> dict[str, Any]:
    card: dict[str, Any] = {
        "$schema": ADAPTIVE_CARD_SCHEMA,
        "type": "AdaptiveCard",
        "version": ADAPTIVE_CARD_VERSION,
        "body": body,
    }
    if actions:
        card["actions"] = actions
    return card


def _header_block(text: str, color: str = "attention") -> dict[str, Any]:
    return {
        "type": "TextBlock",
        "size": "Medium",
        "weight": "Bolder",
        "text": text,
        "color": color,
        "wrap": True,
    }


def _fact_set(facts: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "type": "FactSet",
        "facts": [{"title": k, "value": v} for k, v in facts if v],
    }


def _text_block(
    text: str, *, wrap: bool = True, is_subtle: bool = False
) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "TextBlock", "text": text, "wrap": wrap}
    if is_subtle:
        block["isSubtle"] = True
    return block


def _open_url_action(title: str, url: str) -> dict[str, Any]:
    return {"type": "Action.OpenUrl", "title": title, "url": url}
