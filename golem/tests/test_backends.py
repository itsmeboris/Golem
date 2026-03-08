# pylint: disable=too-few-public-methods
"""Tests for golem.backends — teams_notifier, redmine."""

from unittest.mock import MagicMock, patch

from golem.backends.redmine import (
    RedmineStateBackend,
    RedmineTaskSource,
    _DEFAULT_STATUS_MAP,
    _status_map,
    configure_status_ids,
)
from golem.backends.teams_notifier import TeamsNotifier
from golem.interfaces import TaskStatus


class TestConfigureStatusIds:
    def test_updates_map(self):
        original = dict(_status_map)
        try:
            configure_status_ids({TaskStatus.FIXED: 99})
            assert _status_map[TaskStatus.FIXED] == 99
        finally:
            _status_map.update(original)

    def test_default_map(self):
        assert TaskStatus.IN_PROGRESS in _DEFAULT_STATUS_MAP
        assert TaskStatus.FIXED in _DEFAULT_STATUS_MAP
        assert TaskStatus.CLOSED in _DEFAULT_STATUS_MAP


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
    def test_poll_tasks(self, mock_poll):
        source = RedmineTaskSource()
        result = source.poll_tasks(["proj"], "[AGENT]")
        assert len(result) == 1

    @patch("golem.poller.get_issue_subject", return_value="Subject")
    def test_get_task_subject(self, mock_subj):
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
    def test_get_child_tasks(self, mock_children):
        source = RedmineTaskSource()
        assert source.get_child_tasks(42) == []

    @patch("golem.backends.redmine._request_with_retry")
    @patch("golem.backends.redmine._get_parent_issue_info", return_value=("proj", 1))
    def test_create_child_task(self, mock_info, mock_req):
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

        def side_effect(*args, **kwargs):
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

        def side_effect(*args, **kwargs):
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


class TestTeamsNotifier:
    def test_notify_started(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_started(42, "Test task")
        client.send_to_channel.assert_called_once()

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

    def test_notify_failed(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_failed(42, "Test", "error")
        client.send_to_channel.assert_called_once()

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

    def test_notify_health_alert_basic(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_health_alert("queue_depth", "Queue too deep")
        client.send_to_channel.assert_called_once()
        card = client.send_to_channel.call_args[0][1]
        assert card["type"] == "AdaptiveCard"
        body_str = str(card)
        assert "Health Alert" in body_str

    def test_notify_health_alert_with_details(self):
        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_health_alert(
            "high_error_rate",
            "Rate exceeded",
            details={"value": 0.5, "threshold": 0.1},
        )
        client.send_to_channel.assert_called_once()
        body_str = str(client.send_to_channel.call_args[0][1])
        assert "0.5" in body_str
        assert "0.1" in body_str

    def test_send_error_logged(self):
        client = MagicMock()
        client.send_to_channel.side_effect = RuntimeError("network error")
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_started(42, "Test")


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
