# pylint: disable=too-few-public-methods
"""Tests for golem.core.service_clients — HTTP retry logic and auth helpers."""

import os
from unittest.mock import MagicMock, patch

import requests

from golem.core.service_clients import (
    _request_with_retry,
    get_redmine_headers,
    get_redmine_url,
)


class TestGetRedmineUrl:
    def test_default(self):
        url = get_redmine_url()
        assert isinstance(url, str)

    def test_env_override(self):
        with patch.dict(os.environ, {"REDMINE_URL": "https://custom.example.com"}):
            assert get_redmine_url() == "https://custom.example.com"


class TestGetRedmineHeaders:
    def test_no_key(self):
        with patch.dict(os.environ, {}, clear=True):
            headers = get_redmine_headers()
            assert "Content-Type" in headers
            assert "X-Redmine-API-Key" not in headers

    def test_with_key(self):
        with patch.dict(os.environ, {"REDMINE_API_KEY": "abc123"}):
            headers = get_redmine_headers()
            assert headers["X-Redmine-API-Key"] == "abc123"


class TestRequestWithRetry:
    def test_success_first_try(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_method = MagicMock(return_value=mock_resp)
        result = _request_with_retry(mock_method, "http://example.com", retries=2)
        assert result.status_code == 200
        assert mock_method.call_count == 1

    @patch("golem.core.service_clients.time.sleep")
    def test_retry_on_503(self, mock_sleep):
        resp_503 = MagicMock()
        resp_503.status_code = 503
        resp_200 = MagicMock()
        resp_200.status_code = 200
        mock_method = MagicMock(side_effect=[resp_503, resp_200])

        result = _request_with_retry(mock_method, "http://example.com", retries=2)
        assert result.status_code == 200
        assert mock_method.call_count == 2
        mock_sleep.assert_called_once()

    @patch("golem.core.service_clients.time.sleep")
    def test_retry_on_connection_error(self, _mock_sleep):
        resp_ok = MagicMock()
        resp_ok.status_code = 200
        mock_method = MagicMock(side_effect=[requests.ConnectionError("fail"), resp_ok])

        result = _request_with_retry(mock_method, "http://example.com", retries=2)
        assert result.status_code == 200

    def test_connection_error_exhausted(self):
        mock_method = MagicMock(side_effect=requests.ConnectionError("fail"))
        import pytest

        with pytest.raises(requests.ConnectionError):
            _request_with_retry(mock_method, "http://example.com", retries=0)

    def test_non_retryable_status_returned(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_method = MagicMock(return_value=mock_resp)
        result = _request_with_retry(mock_method, "http://example.com")
        assert result.status_code == 404
        assert mock_method.call_count == 1

    def test_non_connection_error_reraised(self):
        import pytest

        mock_method = MagicMock(side_effect=requests.Timeout("timed out"))
        with pytest.raises(requests.Timeout):
            _request_with_retry(mock_method, "http://example.com", retries=2)
