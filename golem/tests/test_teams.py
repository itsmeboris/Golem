# pylint: disable=too-few-public-methods
"""Tests for golem.core.teams — card building helpers."""

from golem.core.teams import (
    _card_envelope,
    _fact_set,
    _header_block,
    _open_url_action,
    _text_block,
)


class TestCardEnvelope:
    def test_basic(self):
        body = [_text_block("Hello")]
        card = _card_envelope(body)
        assert card["type"] == "AdaptiveCard"
        assert card["body"] == body
        assert "actions" not in card

    def test_with_actions(self):
        body = [_text_block("Hi")]
        actions = [_open_url_action("Click", "https://example.com")]
        card = _card_envelope(body, actions)
        assert "actions" in card
        assert len(card["actions"]) == 1


class TestHeaderBlock:
    def test_defaults(self):
        h = _header_block("Title")
        assert h["text"] == "Title"
        assert h["type"] == "TextBlock"
        assert h["weight"] == "Bolder"

    def test_custom_color(self):
        h = _header_block("Title", color="good")
        assert h["color"] == "good"


class TestFactSet:
    def test_basic(self):
        fs = _fact_set([("Key1", "Val1"), ("Key2", "Val2")])
        assert fs["type"] == "FactSet"
        assert len(fs["facts"]) == 2

    def test_filters_empty_values(self):
        fs = _fact_set([("Key1", "Val1"), ("Key2", "")])
        assert len(fs["facts"]) == 1


class TestTextBlock:
    def test_basic(self):
        tb = _text_block("Hello")
        assert tb["text"] == "Hello"
        assert tb["wrap"] is True

    def test_subtle(self):
        tb = _text_block("Note", is_subtle=True)
        assert tb["isSubtle"] is True

    def test_no_wrap(self):
        tb = _text_block("Data", wrap=False)
        assert tb["wrap"] is False


class TestOpenUrlAction:
    def test_structure(self):
        a = _open_url_action("Visit", "https://example.com")
        assert a["type"] == "Action.OpenUrl"
        assert a["title"] == "Visit"
        assert a["url"] == "https://example.com"


class TestTeamsClient:
    def test_get_webhook_url(self):
        from golem.core.teams import TeamsClient

        client = TeamsClient(webhooks={"chan1": "https://hook.example.com"})
        assert client.get_webhook_url("chan1") == "https://hook.example.com"
        assert client.get_webhook_url("missing") is None

    def test_send_to_channel_missing(self):
        from golem.core.teams import TeamsClient

        client = TeamsClient(webhooks={})
        assert client.send_to_channel("missing", {}) is False

    def test_send_card_success(self):
        from unittest.mock import MagicMock, patch

        from golem.core.teams import TeamsClient

        client = TeamsClient()
        mock_resp = MagicMock(status_code=200)
        with patch("golem.core.teams.requests.post", return_value=mock_resp) as p:
            result = client.send_card("https://hook.example.com", {"body": []})
            assert result is True
            p.assert_called_once()

    def test_send_card_http_error(self):
        from unittest.mock import MagicMock, patch

        from golem.core.teams import TeamsClient

        client = TeamsClient()
        mock_resp = MagicMock(status_code=400, text="Bad Request")
        with patch("golem.core.teams.requests.post", return_value=mock_resp):
            assert client.send_card("https://hook.example.com", {}) is False

    def test_send_card_request_exception(self):
        from unittest.mock import patch

        import requests

        from golem.core.teams import TeamsClient

        client = TeamsClient()
        with patch(
            "golem.core.teams.requests.post",
            side_effect=requests.RequestException("boom"),
        ):
            assert client.send_card("https://hook.example.com", {}) is False

    def test_send_to_channel_delegates(self):
        from unittest.mock import MagicMock, patch

        from golem.core.teams import TeamsClient

        client = TeamsClient(webhooks={"dev": "https://hook.example.com"})
        mock_resp = MagicMock(status_code=200)
        with patch("golem.core.teams.requests.post", return_value=mock_resp):
            assert client.send_to_channel("dev", {"body": []}) is True
