# pylint: disable=too-few-public-methods
"""Tests for golem.backends — teams_notifier, redmine."""

from unittest.mock import MagicMock, patch

from golem.backends.redmine import (
    RedmineStateBackend,
    RedmineTaskSource,
    _status_map,
)
from golem.backends.teams_notifier import TeamsNotifier
from golem.interfaces import TaskStatus


class TestRedmineStateBackend:
    @patch("golem.backends.redmine._update_redmine_issue", return_value=True)
    def test_update_status(self, mock_update):
        backend = RedmineStateBackend()
        assert backend.update_status(42, TaskStatus.IN_PROGRESS) is True
        mock_update.assert_called_once_with(
            42, status_id=_status_map[TaskStatus.IN_PROGRESS]
        )

    @patch("golem.backends.redmine._update_redmine_issue", return_value=True)
    def test_post_comment(self, mock_update):
        backend = RedmineStateBackend()
        assert backend.post_comment(42, "hello") is True
        mock_update.assert_called_once_with(42, notes="hello")

    @patch("golem.backends.redmine._update_redmine_issue", return_value=True)
    def test_update_progress(self, mock_update):
        backend = RedmineStateBackend()
        assert backend.update_progress(42, 80) is True
        mock_update.assert_called_once_with(42, done_ratio=80)

    def test_unknown_status(self):
        backend = RedmineStateBackend()
        assert backend.update_status(42, "nonexistent") is False


class TestRedmineTaskSource:
    @patch("golem.poller.get_agent_tasks", return_value=[{"id": 1}])
    def test_poll_tasks(self, _mock_poll):
        source = RedmineTaskSource()
        result = source.poll_tasks(["proj"], "[AGENT]")
        assert len(result) == 1

    @patch("golem.poller.get_issue_subject", return_value="Subject")
    def test_get_task_subject(self, _mock_subj):
        source = RedmineTaskSource()
        assert source.get_task_subject(42) == "Subject"

    @patch("golem.backends.redmine._request_with_retry")
    def test_get_task_description(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"issue": {"description": "task desc"}}
        mock_resp.raise_for_status = MagicMock()
        mock_req.return_value = mock_resp

        source = RedmineTaskSource()
        assert source.get_task_description(42) == "task desc"

    @patch("golem.backends.redmine._request_with_retry")
    def test_get_task_description_error(self, mock_req):
        import requests

        mock_req.side_effect = requests.RequestException("fail")
        source = RedmineTaskSource()
        assert source.get_task_description(42) == ""

    @patch("golem.poller.get_child_issues", return_value=[])
    def test_get_child_tasks(self, _mock_children):
        source = RedmineTaskSource()
        assert source.get_child_tasks(42) == []

    @patch("golem.backends.redmine._request_with_retry")
    @patch("golem.backends.redmine._get_parent_issue_info", return_value=("proj", 1))
    def test_create_child_task(self, _mock_info, mock_req):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"issue": {"id": 100}}
        mock_resp.raise_for_status = MagicMock()
        mock_req.return_value = mock_resp

        source = RedmineTaskSource()
        result = source.create_child_task(42, "Sub", "Desc")
        assert result == 100

    @patch("golem.backends.redmine._get_parent_issue_info", return_value=(None, None))
    def test_create_child_task_no_project(self, _):
        source = RedmineTaskSource()
        assert source.create_child_task(42, "Sub", "Desc") is None

    @patch("golem.backends.redmine._request_with_retry")
    @patch("golem.backends.redmine._get_parent_issue_info", return_value=("proj", 1))
    def test_create_child_task_request_error(self, _, mock_req):
        import requests

        mock_req.side_effect = requests.RequestException("network error")
        source = RedmineTaskSource()
        assert source.create_child_task(42, "Sub", "Desc") is None


class TestRedmineGetTaskComments:
    @patch("golem.backends.redmine._request_with_retry")
    def test_returns_comments_from_journals(self, mock_req):
        mock_req.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "issue": {
                        "journals": [
                            {
                                "notes": "First comment",
                                "user": {"name": "Alice"},
                                "created_on": "2026-01-15T10:00:00Z",
                            },
                            {
                                "notes": "",  # empty notes — should be filtered
                                "user": {"name": "Bob"},
                                "created_on": "2026-01-15T11:00:00Z",
                            },
                            {
                                "notes": "Second comment",
                                "user": {"name": "Charlie"},
                                "created_on": "2026-01-15T12:00:00Z",
                            },
                        ]
                    }
                }
            ),
        )
        source = RedmineTaskSource()
        comments = source.get_task_comments(task_id=123)
        assert len(comments) == 2
        assert comments[0]["author"] == "Alice"
        assert comments[0]["body"] == "First comment"
        assert comments[1]["author"] == "Charlie"

    @patch("golem.backends.redmine._request_with_retry")
    def test_since_filter(self, mock_req):
        mock_req.return_value = MagicMock(
            status_code=200,
            json=MagicMock(
                return_value={
                    "issue": {
                        "journals": [
                            {
                                "notes": "Old comment",
                                "user": {"name": "Alice"},
                                "created_on": "2026-01-10T10:00:00Z",
                            },
                            {
                                "notes": "New comment",
                                "user": {"name": "Bob"},
                                "created_on": "2026-01-20T10:00:00Z",
                            },
                        ]
                    }
                }
            ),
        )
        source = RedmineTaskSource()
        comments = source.get_task_comments(task_id=123, since="2026-01-15T00:00:00Z")
        assert len(comments) == 1
        assert comments[0]["author"] == "Bob"

    @patch("golem.backends.redmine._request_with_retry")
    def test_empty_journals(self, mock_req):
        mock_req.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"issue": {"journals": []}}),
        )
        source = RedmineTaskSource()
        comments = source.get_task_comments(task_id=123)
        assert comments == []

    @patch("golem.backends.redmine._request_with_retry")
    def test_request_failure(self, mock_req):
        from requests.exceptions import RequestException

        mock_req.side_effect = RequestException("Connection refused")
        source = RedmineTaskSource()
        comments = source.get_task_comments(task_id=123)
        assert comments == []


class TestUpdateRedmineIssue:
    @patch("golem.backends.redmine._request_with_retry")
    def test_put_failure_returns_false(self, mock_req):
        import requests
        from golem.backends.redmine import _update_redmine_issue

        mock_req.side_effect = requests.RequestException("timeout")
        assert _update_redmine_issue(42, notes="hello") is False

    @patch("golem.backends.redmine._request_with_retry")
    def test_status_verify_request_error(self, mock_req):
        import requests
        from golem.backends.redmine import _update_redmine_issue

        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()

        call_count = [0]

        def side_effect(*_args, **_kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return put_resp
            raise requests.RequestException("verify failed")

        mock_req.side_effect = side_effect
        result = _update_redmine_issue(42, status_id=2)
        assert result is True

    @patch("golem.backends.redmine._request_with_retry")
    def test_status_transition_mismatch_returns_false(self, mock_req):
        from golem.backends.redmine import _update_redmine_issue

        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()

        get_resp = MagicMock()
        get_resp.raise_for_status = MagicMock()
        get_resp.json.return_value = {"issue": {"status": {"id": 99}}}

        call_count = [0]

        def side_effect(*_args, **_kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return put_resp
            return get_resp

        mock_req.side_effect = side_effect
        result = _update_redmine_issue(42, status_id=2)
        assert result is False


class TestGetParentIssueInfo:
    @patch("golem.backends.redmine._request_with_retry")
    def test_returns_project_and_tracker(self, mock_req):
        from golem.backends.redmine import _get_parent_issue_info

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "issue": {
                "project": {"identifier": "my-proj", "id": 10},
                "tracker": {"id": 3},
            }
        }
        mock_req.return_value = resp
        project_id, tracker_id = _get_parent_issue_info(42)
        assert project_id == "my-proj"
        assert tracker_id == 3

    @patch("golem.backends.redmine._request_with_retry")
    def test_falls_back_to_numeric_project_id(self, mock_req):
        from golem.backends.redmine import _get_parent_issue_info

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "issue": {
                "project": {"id": 10},
                "tracker": {"id": 1},
            }
        }
        mock_req.return_value = resp
        project_id, tracker_id = _get_parent_issue_info(42)
        assert project_id == "10"
        assert tracker_id == 1

    @patch("golem.backends.redmine._request_with_retry")
    def test_request_error_returns_none(self, mock_req):
        import requests
        from golem.backends.redmine import _get_parent_issue_info

        mock_req.side_effect = requests.RequestException("fail")
        project_id, tracker_id = _get_parent_issue_info(42)
        assert project_id is None
        assert tracker_id is None


class TestBuildRedmineProfile:
    def test_teams_disabled_uses_log_notifier(self):
        from golem.backends.local import LogNotifier
        from golem.backends.profiles import _build_redmine_profile
        from golem.core.config import Config

        config = Config()
        config.teams.enabled = False
        profile = _build_redmine_profile(config)
        assert isinstance(profile.notifier, LogNotifier)

    def test_teams_enabled_uses_teams_notifier(self):
        from golem.backends.profiles import _build_redmine_profile
        from golem.core.config import Config

        config = Config()
        config.teams.enabled = True
        config.teams.webhooks = {"default": "https://hook.example.com"}
        profile = _build_redmine_profile(config)
        assert isinstance(profile.notifier, TeamsNotifier)


def _get_card_body(client):
    """Extract card body from the send_to_channel call."""
    card = client.send_to_channel.call_args[0][1]
    return card.get("body", [])


def _get_card_facts(client):
    """Extract {title: value} dict from the first FactSet in the card."""
    body = _get_card_body(client)
    for item in body:
        if item.get("type") == "FactSet":
            return {f["title"]: f["value"] for f in item["facts"]}
    return {}


class TestTeamsNotifier:
    def test_notify_started(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_started(42, "Test task")
        client.send_to_channel.assert_called_once()
        body = _get_card_body(client)
        assert "42" in body[0]["text"]
        assert body[1]["text"] == "Test task"

    def test_notify_completed(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_completed(
            42,
            "Test",
            cost_usd=1.0,
            verdict="PASS",
            confidence=0.9,
        )
        client.send_to_channel.assert_called_once()
        body = _get_card_body(client)
        assert "42" in body[0]["text"]
        facts = _get_card_facts(client)
        assert facts["Cost"] == "$1.00"
        assert "PASS" in facts["Verdict"]

    def test_notify_failed(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_failed(42, "Test", "error")
        client.send_to_channel.assert_called_once()
        body = _get_card_body(client)
        assert "Failed" in body[0]["text"]
        facts = _get_card_facts(client)
        assert facts["Error"] == "error"

    def test_notify_escalated(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_escalated(
            42,
            "Test",
            "PARTIAL",
            "needs work",
            concerns=["issue"],
            cost_usd=0.5,
        )
        client.send_to_channel.assert_called_once()
        body = _get_card_body(client)
        assert "Needs Review" in body[0]["text"]
        facts = _get_card_facts(client)
        assert facts["Verdict"] == "PARTIAL"

    def test_notify_health_alert_basic(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_health_alert("queue_depth", "Queue too deep")
        client.send_to_channel.assert_called_once()
        card = client.send_to_channel.call_args[0][1]
        assert card["type"] == "AdaptiveCard"
        body = _get_card_body(client)
        assert "Health Alert" in body[0]["text"]

    def test_notify_health_alert_with_details(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_health_alert(
            "high_error_rate",
            "Rate exceeded",
            details={"value": 0.5, "threshold": 0.1},
        )
        client.send_to_channel.assert_called_once()
        facts = _get_card_facts(client)
        assert facts["Current"] == "0.5"
        assert facts["Threshold"] == "0.1"

    def test_send_succeeds_first_attempt_no_retry(self):
        """_send succeeds on first attempt and does not call send_to_channel again."""
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_started(42, "Test")
        assert client.send_to_channel.call_count == 1

    @patch("golem.backends.teams_notifier.time.sleep")
    def test_send_retries_on_transient_failure_and_succeeds(self, mock_sleep, caplog):
        """_send retries after a transient failure and succeeds on the second attempt."""
        import logging

        client = MagicMock()
        client.send_to_channel.side_effect = [RuntimeError("timeout"), None]
        notifier = TeamsNotifier(client, "chan")

        with caplog.at_level(logging.WARNING, logger="golem.backends.teams_notifier"):
            notifier.notify_started(42, "Test")

        assert client.send_to_channel.call_count == 2
        assert any("attempt 1" in r.message for r in caplog.records)
        assert not any(r.levelno == logging.ERROR for r in caplog.records)
        mock_sleep.assert_called_once_with(TeamsNotifier._SEND_RETRY_DELAY)

    @patch("golem.backends.teams_notifier.time.sleep")
    def test_send_logs_error_after_all_retries_exhausted(self, mock_sleep, caplog):
        """_send logs ERROR when all retry attempts are exhausted."""
        import logging

        client = MagicMock()
        client.send_to_channel.side_effect = RuntimeError("persistent failure")
        notifier = TeamsNotifier(client, "chan")

        with caplog.at_level(logging.ERROR, logger="golem.backends.teams_notifier"):
            notifier.notify_started(42, "Test")

        # 1 initial + 2 retries = 3 total attempts
        assert client.send_to_channel.call_count == 3
        # Sleep is called between retries (2 sleeps: after attempt 0 and after attempt 1)
        assert mock_sleep.call_count == TeamsNotifier._MAX_SEND_RETRIES
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert any("3 attempts" in r.message for r in caplog.records)


class TestLocalFileTaskSourceExtended:
    def test_poll_skips_non_json_yaml_files(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "readme.txt").write_text("ignore me")
        (tasks_dir / "001.json").write_text('{"id": "001", "subject": "[AGENT] Task"}')
        src = LocalFileTaskSource(tasks_dir)
        results = src.poll_tasks(["any"], "[AGENT]")
        assert len(results) == 1

    def test_poll_skips_none_from_load(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "bad.json").write_text("not valid json {{{")
        src = LocalFileTaskSource(tasks_dir)
        results = src.poll_tasks(["any"], "[AGENT]")
        assert not results

    def test_get_task_subject(self, tmp_path):
        import json
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001.json").write_text(
            json.dumps({"id": "001", "subject": "My Subject"})
        )
        src = LocalFileTaskSource(tasks_dir)
        assert src.get_task_subject("001") == "My Subject"

    def test_get_task_subject_not_found(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        src = LocalFileTaskSource(tmp_path)
        assert src.get_task_subject("999") == ""

    def test_get_child_tasks_by_parent_id_files(self, tmp_path):
        import json
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "parent.json").write_text(
            json.dumps({"id": "parent", "subject": "[AGENT] Parent"})
        )
        (tasks_dir / "child1.json").write_text(
            json.dumps(
                {
                    "id": "child1",
                    "subject": "Child 1",
                    "parent_id": "parent",
                }
            )
        )
        (tasks_dir / "child2.json").write_text(
            json.dumps(
                {
                    "subject": "Child 2",
                    "parent_id": "parent",
                }
            )
        )
        src = LocalFileTaskSource(tasks_dir)
        children = src.get_child_tasks("parent")
        assert len(children) == 2
        ids = {c["id"] for c in children}
        assert "child1" in ids
        assert "child2" in ids

    def test_get_child_tasks_skips_non_json_yaml(self, tmp_path):
        import json
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "parent.json").write_text(
            json.dumps({"id": "parent", "subject": "P"})
        )
        (tasks_dir / "notes.txt").write_text("not a task")
        src = LocalFileTaskSource(tasks_dir)
        children = src.get_child_tasks("parent")
        assert not children

    def test_find_task_fallback_scan(self, tmp_path):
        import json
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "weird_name.json").write_text(
            json.dumps({"id": "myid", "subject": "Found it"})
        )
        src = LocalFileTaskSource(tasks_dir)
        assert src.get_task_subject("myid") == "Found it"

    def test_load_yaml_file(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001.yaml").write_text("id: '001'\nsubject: '[AGENT] YAML task'\n")
        src = LocalFileTaskSource(tasks_dir)
        results = src.poll_tasks(["any"], "[AGENT]")
        assert len(results) == 1
        assert results[0]["subject"] == "[AGENT] YAML task"

    def test_load_yaml_import_error(self, tmp_path, monkeypatch):
        import builtins
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001.yaml").write_text("id: '001'\nsubject: 'test'\n")

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("no yaml")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        src = LocalFileTaskSource(tasks_dir)
        results = src.poll_tasks(["any"], "[AGENT]")
        assert not results

    def test_load_file_exception(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        bad_file = tasks_dir / "001.json"
        bad_file.write_text("not json at all")
        src = LocalFileTaskSource(tasks_dir)
        results = src.poll_tasks(["any"], "[AGENT]")
        assert not results

    def test_create_child_with_existing_files(self, tmp_path):
        import json
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "existing.json").write_text(
            json.dumps({"id": "existing", "subject": "old"})
        )
        src = LocalFileTaskSource(tasks_dir)
        child_id = src.create_child_task("parent", "New Sub", "desc")
        assert child_id is not None
        files = list(tasks_dir.glob("*.json"))
        assert len(files) == 2


class TestLocalPollUntaggedTasks:
    """Tests for LocalFileTaskSource.poll_untagged_tasks."""

    def test_poll_untagged_filters_tagged_out(self, tmp_path):
        import json

        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001.json").write_text(
            json.dumps(
                {"id": "001", "subject": "[AGENT] Tagged task", "description": "d1"}
            )
        )
        (tasks_dir / "002.json").write_text(
            json.dumps(
                {"id": "002", "subject": "Plain untagged task", "description": "d2"}
            )
        )
        (tasks_dir / "003.json").write_text(
            json.dumps(
                {"id": "003", "subject": "Another plain task", "description": "d3"}
            )
        )
        src = LocalFileTaskSource(tasks_dir)
        result = src.poll_untagged_tasks(["any"], "[AGENT]")
        ids = [r["id"] for r in result]
        assert "001" not in ids
        assert "002" in ids
        assert "003" in ids

    def test_poll_untagged_respects_limit(self, tmp_path):
        import json

        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        for i in range(5):
            (tasks_dir / f"{i:03d}.json").write_text(
                json.dumps({"id": str(i), "subject": f"Task {i}", "description": ""})
            )
        src = LocalFileTaskSource(tasks_dir)
        result = src.poll_untagged_tasks(["any"], "[AGENT]", limit=2)
        assert len(result) == 2

    def test_poll_untagged_empty_dir(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        src = LocalFileTaskSource(tasks_dir)
        result = src.poll_untagged_tasks(["any"], "[AGENT]")
        assert result == []

    def test_poll_untagged_missing_dir(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        src = LocalFileTaskSource(tmp_path / "nonexistent")
        result = src.poll_untagged_tasks(["any"], "[AGENT]")
        assert result == []

    def test_poll_untagged_returns_correct_keys(self, tmp_path):
        import json

        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001.json").write_text(
            json.dumps(
                {"id": "001", "subject": "Plain task", "description": "body text"}
            )
        )
        src = LocalFileTaskSource(tasks_dir)
        result = src.poll_untagged_tasks(["any"], "[AGENT]")
        assert len(result) == 1
        assert result[0]["id"] == "001"
        assert result[0]["subject"] == "Plain task"
        assert result[0]["body"] == "body text"

    def test_poll_untagged_skips_invalid_files(self, tmp_path):
        import json

        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "bad.json").write_text("not valid json {{{")
        (tasks_dir / "good.json").write_text(
            json.dumps({"id": "good", "subject": "Good task", "description": "ok"})
        )
        src = LocalFileTaskSource(tasks_dir)
        result = src.poll_untagged_tasks(["any"], "[AGENT]")
        assert len(result) == 1
        assert result[0]["id"] == "good"

    def test_poll_untagged_skips_non_yaml_json(self, tmp_path):
        import json

        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "readme.txt").write_text("not a task file")
        (tasks_dir / "good.json").write_text(
            json.dumps({"id": "good", "subject": "Good task", "description": "ok"})
        )
        src = LocalFileTaskSource(tasks_dir)
        result = src.poll_untagged_tasks(["any"], "[AGENT]")
        assert len(result) == 1
        assert result[0]["id"] == "good"


class TestRedminePollUntaggedTasks:
    """Tests for RedmineTaskSource.poll_untagged_tasks."""

    @patch("golem.backends.redmine._request_with_retry")
    def test_poll_untagged_filters_tagged_out(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "issues": [
                {"id": 1, "subject": "[AGENT] Tagged issue", "description": "d1"},
                {"id": 2, "subject": "Plain untagged issue", "description": "d2"},
                {"id": 3, "subject": "Another untagged issue", "description": "d3"},
            ]
        }
        mock_req.return_value = mock_resp

        source = RedmineTaskSource()
        result = source.poll_untagged_tasks(["my-project"], "[AGENT]")
        ids = [r["id"] for r in result]
        assert 1 not in ids
        assert 2 in ids
        assert 3 in ids

    @patch("golem.backends.redmine._request_with_retry")
    def test_poll_untagged_respects_limit(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "issues": [
                {"id": i, "subject": f"Plain issue {i}", "description": ""}
                for i in range(10)
            ]
        }
        mock_req.return_value = mock_resp

        source = RedmineTaskSource()
        result = source.poll_untagged_tasks(["my-project"], "[AGENT]", limit=3)
        assert len(result) == 3

    @patch("golem.backends.redmine._request_with_retry")
    def test_poll_untagged_error_returns_empty(self, mock_req):
        import requests

        mock_req.side_effect = requests.RequestException("network error")
        source = RedmineTaskSource()
        result = source.poll_untagged_tasks(["my-project"], "[AGENT]")
        assert result == []

    @patch("golem.backends.redmine._request_with_retry")
    def test_poll_untagged_returns_correct_keys(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "issues": [
                {"id": 42, "subject": "Fix the bug", "description": "Bug details"}
            ]
        }
        mock_req.return_value = mock_resp

        source = RedmineTaskSource()
        result = source.poll_untagged_tasks(["proj"], "[AGENT]")
        assert len(result) == 1
        assert result[0]["id"] == 42
        assert result[0]["subject"] == "Fix the bug"
        assert result[0]["body"] == "Bug details"

    @patch("golem.backends.redmine._request_with_retry")
    def test_poll_untagged_empty_project(self, mock_req):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"issues": []}
        mock_req.return_value = mock_resp

        source = RedmineTaskSource()
        result = source.poll_untagged_tasks(["proj"], "[AGENT]")
        assert result == []

    @patch("golem.backends.redmine._request_with_retry")
    def test_poll_untagged_multiple_projects(self, mock_req):
        """Issues from multiple projects are merged and limited."""
        call_count = [0]

        def side_effect(*_args, **_kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if call_count[0] == 1:
                resp.json.return_value = {
                    "issues": [{"id": 1, "subject": "Issue 1", "description": ""}]
                }
            else:
                resp.json.return_value = {
                    "issues": [{"id": 2, "subject": "Issue 2", "description": ""}]
                }
            return resp

        mock_req.side_effect = side_effect
        source = RedmineTaskSource()
        result = source.poll_untagged_tasks(["proj-a", "proj-b"], "[AGENT]", limit=10)
        ids = [r["id"] for r in result]
        assert 1 in ids
        assert 2 in ids
