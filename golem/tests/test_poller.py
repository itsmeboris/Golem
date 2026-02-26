# pylint: disable=too-few-public-methods
"""Tests for golem.poller — Redmine issue detection."""

from unittest.mock import MagicMock, patch

from golem.poller import (
    get_agent_tasks,
    get_child_issues,
    get_issue_subject,
    is_agent_task,
)


class TestIsAgentTask:
    def test_default_tag(self):
        assert is_agent_task("[AGENT] Fix the bug") is True

    def test_case_insensitive(self):
        assert is_agent_task("[agent] lower case") is True
        assert is_agent_task("[Agent] mixed case") is True

    def test_no_tag(self):
        assert is_agent_task("Regular issue") is False

    def test_custom_tag(self):
        assert is_agent_task("[BOT] task", detection_tag="[BOT]") is True
        assert is_agent_task("[AGENT] task", detection_tag="[BOT]") is False


class TestGetAgentTasks:
    @patch("golem.poller.get_redmine_url", return_value="https://redmine.example.com")
    @patch(
        "golem.poller.get_redmine_headers", return_value={"X-Redmine-API-Key": "test"}
    )
    @patch("golem.poller.requests.get")
    def test_fetches_from_multiple_projects(self, mock_get, _, __):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "issues": [
                {"id": 1, "subject": "[AGENT] task 1"},
                {"id": 2, "subject": "[AGENT] task 2"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        tasks = get_agent_tasks(["proj-a", "proj-b"])
        assert len(tasks) == 2
        assert mock_get.call_count == 2

    @patch("golem.poller.get_redmine_url", return_value="https://redmine.example.com")
    @patch("golem.poller.get_redmine_headers", return_value={})
    @patch("golem.poller.requests.get")
    def test_deduplicates(self, mock_get, _, __):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"issues": [{"id": 1, "subject": "[AGENT] dup"}]}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        tasks = get_agent_tasks(["proj-a", "proj-b"])
        assert len(tasks) == 1

    @patch("golem.poller.get_redmine_url", return_value="https://redmine.example.com")
    @patch("golem.poller.get_redmine_headers", return_value={})
    @patch("golem.poller.requests.get")
    def test_handles_request_error(self, mock_get, _, __):
        import requests

        mock_get.side_effect = requests.RequestException("timeout")
        tasks = get_agent_tasks(["proj"])
        assert not tasks


class TestGetIssueSubject:
    @patch("golem.poller.get_redmine_url", return_value="https://redmine.example.com")
    @patch("golem.poller.get_redmine_headers", return_value={})
    @patch("golem.poller.requests.get")
    def test_success(self, mock_get, _, __):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"issue": {"subject": "My task"}}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        assert get_issue_subject(123) == "My task"

    @patch("golem.poller.get_redmine_url", return_value="https://redmine.example.com")
    @patch("golem.poller.get_redmine_headers", return_value={})
    @patch("golem.poller.requests.get")
    def test_fallback_on_error(self, mock_get, _, __):
        import requests

        mock_get.side_effect = requests.RequestException("fail")
        result = get_issue_subject(456)
        assert "#456" in result


class TestGetChildIssues:
    @patch("golem.poller.get_redmine_url", return_value="https://redmine.example.com")
    @patch("golem.poller.get_redmine_headers", return_value={})
    @patch("golem.poller.requests.get")
    def test_returns_children(self, mock_get, _, __):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "issues": [
                {"id": 10, "subject": "Sub 1"},
                {"id": 11, "subject": "Sub 2"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        children = get_child_issues(5)
        assert len(children) == 2

    @patch("golem.poller.get_redmine_url", return_value="https://redmine.example.com")
    @patch("golem.poller.get_redmine_headers", return_value={})
    @patch("golem.poller.requests.get")
    def test_error_returns_empty(self, mock_get, _, __):
        import requests

        mock_get.side_effect = requests.RequestException("boom")
        assert get_child_issues(5) == []
