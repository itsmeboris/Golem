# pylint: disable=too-few-public-methods
"""Tests for batch notification backends and flow-level batch integration."""

from unittest.mock import MagicMock, patch

import pytest

from golem.core.config import Config, GolemFlowConfig
from golem.orchestrator import TaskSessionState

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_facts(card):
    """Extract {title: value} dict from the first FactSet in card body."""
    for item in card["body"]:
        if item.get("type") == "FactSet":
            return {f["title"]: f["value"] for f in item["facts"]}
    return {}


def _make_test_profile():
    from golem.backends.local import (
        LocalFileTaskSource,
        LogNotifier,
        NullStateBackend,
        NullToolProvider,
    )
    from golem.profile import GolemProfile
    from golem.prompts import FilePromptProvider

    return GolemProfile(
        name="test",
        task_source=LocalFileTaskSource("/tmp/test-tasks"),
        state_backend=NullStateBackend(),
        notifier=LogNotifier(),
        tool_provider=NullToolProvider(),
        prompt_provider=FilePromptProvider(None),
    )


def _make_flow(monkeypatch, tmp_path, profile=None, **flow_kwargs):
    from golem.flow import GolemFlow

    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

    profile = profile or _make_test_profile()
    fc_kwargs = {"enabled": True, "projects": ["test-project"], "profile": "test"}
    fc_kwargs.update(flow_kwargs)
    config = Config(golem=GolemFlowConfig(**fc_kwargs))
    monkeypatch.setattr(
        "golem.flow.build_profile",
        lambda _name, _cfg: profile,
    )
    return GolemFlow(config)


# ---------------------------------------------------------------------------
# Slack notifier batch method tests
# ---------------------------------------------------------------------------


class TestSlackNotifierBatch:
    def test_notify_batch_submitted(self):
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        notifier = SlackNotifier(client, "test-chan")
        notifier.notify_batch_submitted("grp-1", 5)
        client.send_to_channel.assert_called_once()
        payload = client.send_to_channel.call_args[0][1]
        assert payload["text"] == "Batch submitted: grp-1"
        blocks = payload["blocks"]
        # header block has group_id
        assert "grp-1" in blocks[0]["text"]["text"]
        # fields block has task count
        field_texts = [f["text"] for f in blocks[1]["fields"]]
        assert any("5" in t for t in field_texts)

    def test_notify_batch_completed_success(self):
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        notifier = SlackNotifier(client, "test-chan")
        notifier.notify_batch_completed(
            "grp-2",
            "completed",
            total_cost_usd=3.50,
            total_duration_s=600.0,
            task_count=10,
            validation_verdict="PASS",
        )
        client.send_to_channel.assert_called_once()
        payload = client.send_to_channel.call_args[0][1]
        assert "Batch completed: grp-2" in payload["text"]
        blocks = payload["blocks"]
        # Check header contains status title
        assert "Completed" in blocks[0]["text"]["text"]
        # Check fields contain validation verdict
        field_texts = [f["text"] for f in blocks[1]["fields"]]
        assert any("PASS" in t for t in field_texts)

    def test_notify_batch_completed_failed_no_verdict(self):
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        notifier = SlackNotifier(client, "test-chan")
        notifier.notify_batch_completed(
            "grp-3",
            "failed",
            total_cost_usd=1.00,
            total_duration_s=30.0,
            task_count=2,
        )
        client.send_to_channel.assert_called_once()
        payload = client.send_to_channel.call_args[0][1]
        assert "Batch failed: grp-3" in payload["text"]
        # No validation field when empty
        field_texts = [f["text"] for f in payload["blocks"][1]["fields"]]
        assert not any("Validation" in t for t in field_texts)


# ---------------------------------------------------------------------------
# Slack notifier _send retry tests
# ---------------------------------------------------------------------------


class TestSlackNotifierSendRetry:
    def test_send_succeeds_first_attempt_no_retry(self):
        """_send succeeds on first attempt without extra calls."""
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        notifier = SlackNotifier(client, "test-chan")
        notifier.notify_started(1, "Task")
        assert client.send_to_channel.call_count == 1

    @patch("golem.backends.slack_notifier.time.sleep")
    def test_send_retries_on_transient_failure_and_succeeds(self, mock_sleep, caplog):
        """_send retries after a transient error and succeeds on second attempt."""
        import logging
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        client.send_to_channel.side_effect = [RuntimeError("timeout"), None]
        notifier = SlackNotifier(client, "test-chan")

        with caplog.at_level(logging.WARNING, logger="golem.backends.slack_notifier"):
            notifier.notify_started(1, "Task")

        assert client.send_to_channel.call_count == 2
        assert any("attempt 1" in r.message for r in caplog.records)
        assert not any(r.levelno == logging.ERROR for r in caplog.records)
        mock_sleep.assert_called_once_with(SlackNotifier._SEND_RETRY_DELAY)

    @patch("golem.backends.slack_notifier.time.sleep")
    def test_send_logs_error_after_all_retries_exhausted(self, mock_sleep, caplog):
        """_send logs ERROR when all retry attempts are exhausted."""
        import logging
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        client.send_to_channel.side_effect = RuntimeError("persistent failure")
        notifier = SlackNotifier(client, "test-chan")

        with caplog.at_level(logging.ERROR, logger="golem.backends.slack_notifier"):
            notifier.notify_started(1, "Task")

        # 1 initial + 2 retries = 3 total attempts
        assert client.send_to_channel.call_count == 3
        # Sleep is called between retries (2 sleeps: after attempt 0 and after attempt 1)
        assert mock_sleep.call_count == SlackNotifier._MAX_SEND_RETRIES
        assert any(r.levelno == logging.ERROR for r in caplog.records)
        assert any("3 attempts" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Teams notifier batch method tests
# ---------------------------------------------------------------------------


class TestTeamsNotifierBatch:
    def test_notify_batch_submitted(self):
        from golem.backends.teams_notifier import TeamsNotifier

        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_batch_submitted("grp-t1", 3)
        client.send_to_channel.assert_called_once()
        card = client.send_to_channel.call_args[0][1]
        assert card["type"] == "message"
        body = card["attachments"][0]["content"]["body"]
        assert "grp-t1" in body[0]["text"]
        assert "3" in body[1]["text"]

    def test_notify_batch_completed_with_verdict(self):
        from golem.backends.teams_notifier import TeamsNotifier

        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_batch_completed(
            "grp-t2",
            "completed",
            total_cost_usd=5.00,
            total_duration_s=300.0,
            task_count=4,
            validation_verdict="PASS",
        )
        client.send_to_channel.assert_called_once()
        card = client.send_to_channel.call_args[0][1]
        body = card["attachments"][0]["content"]["body"]
        facts = body[1]["facts"]
        fact_titles = [f["title"] for f in facts]
        assert "Validation" in fact_titles

    def test_notify_batch_completed_no_verdict(self):
        from golem.backends.teams_notifier import TeamsNotifier

        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_batch_completed(
            "grp-t3",
            "failed",
            total_cost_usd=0.50,
            total_duration_s=10.0,
            task_count=1,
        )
        client.send_to_channel.assert_called_once()
        card = client.send_to_channel.call_args[0][1]
        body = card["attachments"][0]["content"]["body"]
        facts = body[1]["facts"]
        fact_titles = [f["title"] for f in facts]
        assert "Validation" not in fact_titles


# ---------------------------------------------------------------------------
# LogNotifier batch method tests
# ---------------------------------------------------------------------------


class TestLogNotifierBatch:
    def test_notify_batch_submitted(self, caplog):
        import logging
        from golem.backends.local import LogNotifier

        with caplog.at_level(logging.INFO, logger="golem.backends.local"):
            notifier = LogNotifier()
            notifier.notify_batch_submitted("grp-log", 7)
        assert "grp-log" in caplog.text
        assert "7 tasks" in caplog.text

    def test_notify_batch_completed(self, caplog):
        import logging
        from golem.backends.local import LogNotifier

        with caplog.at_level(logging.INFO, logger="golem.backends.local"):
            notifier = LogNotifier()
            notifier.notify_batch_completed(
                "grp-log2",
                "completed",
                total_cost_usd=2.50,
                total_duration_s=100.0,
                task_count=3,
                validation_verdict="PASS",
            )
        assert "grp-log2" in caplog.text
        assert "completed" in caplog.text
        assert "3 tasks" in caplog.text


# ---------------------------------------------------------------------------
# Health alert notification tests
# ---------------------------------------------------------------------------


class TestSlackNotifierHealthAlert:
    def test_notify_health_alert_basic(self):
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        notifier = SlackNotifier(client, "test-chan")
        notifier.notify_health_alert("queue_depth", "Queue too deep")
        client.send_to_channel.assert_called_once()
        payload = client.send_to_channel.call_args[0][1]
        assert "Health alert: Queue Backlog" in payload["text"]
        blocks = payload["blocks"]
        assert "Queue Backlog" in blocks[0]["text"]["text"]
        assert "Queue too deep" in blocks[1]["text"]["text"]

    def test_notify_health_alert_with_details(self):
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        notifier = SlackNotifier(client, "test-chan")
        notifier.notify_health_alert(
            "high_error_rate",
            "Error rate too high",
            details={"value": 0.5, "threshold": 0.1},
        )
        client.send_to_channel.assert_called_once()
        payload = client.send_to_channel.call_args[0][1]
        blocks = payload["blocks"]
        field_texts = [f["text"] for f in blocks[2]["fields"]]
        assert any("0.5" in t for t in field_texts)
        assert any("0.1" in t for t in field_texts)

    def test_notify_health_alert_unknown_type(self):
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        notifier = SlackNotifier(client, "test-chan")
        notifier.notify_health_alert("custom_alert", "Something happened")
        payload = client.send_to_channel.call_args[0][1]
        assert "Custom Alert" in payload["text"]

    @pytest.mark.parametrize(
        "details, expected_block_count",
        [
            (None, 2),
            ({"value": None, "threshold": None}, 2),
            ({"value": 10, "threshold": None}, 3),
            ({"value": None, "threshold": 5}, 3),
            ({"value": 10, "threshold": 5}, 3),
        ],
    )
    def test_health_alert_block_count(self, details, expected_block_count):
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        notifier = SlackNotifier(client, "test-chan")
        notifier.notify_health_alert("stale_daemon", "Daemon idle", details=details)
        payload = client.send_to_channel.call_args[0][1]
        assert len(payload["blocks"]) == expected_block_count


class TestTeamsNotifierHealthAlert:
    def test_notify_health_alert_basic(self):
        from golem.backends.teams_notifier import TeamsNotifier

        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_health_alert("stale_daemon", "Daemon is idle")
        client.send_to_channel.assert_called_once()
        card = client.send_to_channel.call_args[0][1]
        assert "Health Alert" in card["body"][0]["text"]
        assert "Daemon Idle" in card["body"][0]["text"]

    def test_notify_health_alert_with_details(self):
        from golem.backends.teams_notifier import TeamsNotifier

        client = MagicMock()
        notifier = TeamsNotifier(client, "chan")
        notifier.notify_health_alert(
            "consecutive_failures",
            "Too many failures",
            details={"value": 7, "threshold": 5},
        )
        card = client.send_to_channel.call_args[0][1]
        facts = _get_facts(card)
        assert facts["Current"] == "7"
        assert facts["Threshold"] == "5"


class TestLogNotifierHealthAlert:
    def test_notify_health_alert_logs(self, caplog):
        import logging
        from golem.backends.local import LogNotifier

        with caplog.at_level(logging.INFO, logger="golem.backends.local"):
            notifier = LogNotifier()
            notifier.notify_health_alert(
                "queue_depth",
                "Queue is full",
                details={"value": 100, "threshold": 50},
            )
        assert "queue_depth" in caplog.text
        assert "Queue is full" in caplog.text

    def test_notify_health_alert_no_details(self, caplog):
        import logging
        from golem.backends.local import LogNotifier

        with caplog.at_level(logging.INFO, logger="golem.backends.local"):
            notifier = LogNotifier()
            notifier.notify_health_alert("stale_daemon", "Idle too long")
        assert "stale_daemon" in caplog.text
        assert "Idle too long" in caplog.text


# ---------------------------------------------------------------------------
# Flow batch completion notification tests
# ---------------------------------------------------------------------------


class TestFlowBatchNotification:
    """Tests that batch notification fires on terminal states."""

    def test_notify_batch_completed_on_terminal(self, monkeypatch, tmp_path):
        """When all tasks complete, notify_batch_completed should be called."""
        from golem.backends.local import LogNotifier

        notifier = MagicMock(spec=LogNotifier)
        profile = _make_test_profile()
        # Replace the notifier with our mock
        object.__setattr__(profile, "notifier", notifier)

        flow = _make_flow(monkeypatch, tmp_path, profile=profile)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch(
            [{"prompt": "A", "subject": "A"}], group_id="grp-notify"
        )
        tid = result["tasks"][0]["task_id"]
        session = flow._sessions[tid]
        prev_state = session.state
        session.state = TaskSessionState.COMPLETED
        session.validation_verdict = "PASS"

        flow._handle_state_transition(session, prev_state)

        notifier.notify_batch_completed.assert_called_once()
        call_args = notifier.notify_batch_completed.call_args
        assert call_args[0][0] == "grp-notify"
        assert call_args[0][1] in ("completed", "failed")

    def test_notify_batch_not_called_for_non_terminal(self, monkeypatch, tmp_path):
        """Notification should NOT fire if not all tasks are terminal."""
        from golem.backends.local import LogNotifier

        notifier = MagicMock(spec=LogNotifier)
        profile = _make_test_profile()
        object.__setattr__(profile, "notifier", notifier)

        flow = _make_flow(monkeypatch, tmp_path, profile=profile)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch(
            [
                {"prompt": "A", "subject": "A"},
                {"prompt": "B", "subject": "B"},
            ],
            group_id="grp-partial",
        )
        tid_a = result["tasks"][0]["task_id"]
        session_a = flow._sessions[tid_a]
        prev_state = session_a.state
        session_a.state = TaskSessionState.COMPLETED
        session_a.validation_verdict = "PASS"

        flow._handle_state_transition(session_a, prev_state)

        # Only one of two tasks completed, batch is still in_progress
        notifier.notify_batch_completed.assert_not_called()

    def test_notify_batch_fires_once_not_twice(self, monkeypatch, tmp_path):
        """Batch notification should fire once even if _handle_state_transition
        is called again for the same terminal batch.

        This tests the _notified_batches dedup guard if present, or verifies
        that the batch status no longer transitions (so no duplicate).
        """
        from golem.backends.local import LogNotifier

        notifier = MagicMock(spec=LogNotifier)
        profile = _make_test_profile()
        object.__setattr__(profile, "notifier", notifier)

        flow = _make_flow(monkeypatch, tmp_path, profile=profile)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch(
            [{"prompt": "A", "subject": "A"}], group_id="grp-once"
        )
        tid = result["tasks"][0]["task_id"]
        session = flow._sessions[tid]
        prev_state = session.state
        session.state = TaskSessionState.COMPLETED
        session.validation_verdict = "PASS"

        # First transition - should fire notification
        flow._handle_state_transition(session, prev_state)
        assert notifier.notify_batch_completed.call_count == 1

        # Second transition with same state - should NOT fire again
        flow._handle_state_transition(session, TaskSessionState.COMPLETED)
        # The guard (either _notified_batches or status check) should prevent
        # a second notification. If the dedup guard doesn't exist yet,
        # the second call has prev_state == COMPLETED == new_state, so the
        # flow may skip batch update entirely. Either way, count stays at 1.
        assert notifier.notify_batch_completed.call_count == 1

    def test_failed_batch_skips_integration_validation(self, monkeypatch, tmp_path):
        """When batch completes with 'failed' status, integration validation
        should NOT be triggered (flow.py early return on non-completed batch)."""
        import asyncio
        from golem.backends.local import LogNotifier

        notifier = MagicMock(spec=LogNotifier)
        profile = _make_test_profile()
        object.__setattr__(profile, "notifier", notifier)

        flow = _make_flow(monkeypatch, tmp_path, profile=profile)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch(
            [{"prompt": "A", "subject": "A"}], group_id="grp-fail"
        )
        tid = result["tasks"][0]["task_id"]
        session = flow._sessions[tid]
        session.base_work_dir = "/tmp/work"
        prev_state = session.state
        session.state = TaskSessionState.FAILED

        mock_loop = MagicMock()
        monkeypatch.setattr(asyncio, "get_running_loop", lambda: mock_loop)

        flow._handle_state_transition(session, prev_state)

        # Batch completed notification fires, but integration validation should NOT
        notifier.notify_batch_completed.assert_called_once()
        mock_loop.create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Flow auto-trigger integration validation on batch completion
# ---------------------------------------------------------------------------


class TestFlowBatchIntegrationValidation:
    """Test that integration validation is triggered on batch completion."""

    def test_integration_validation_triggered(self, monkeypatch, tmp_path):
        """When batch completes successfully, run_integration_validation
        should be scheduled via loop.create_task."""
        import asyncio
        from golem.backends.local import LogNotifier

        notifier = MagicMock(spec=LogNotifier)
        profile = _make_test_profile()
        object.__setattr__(profile, "notifier", notifier)

        flow = _make_flow(monkeypatch, tmp_path, profile=profile)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch(
            [{"prompt": "A", "subject": "A"}], group_id="grp-val"
        )
        tid = result["tasks"][0]["task_id"]
        session = flow._sessions[tid]
        session.base_work_dir = "/tmp/work"
        prev_state = session.state
        session.state = TaskSessionState.COMPLETED
        session.validation_verdict = "PASS"

        # Mock asyncio.get_running_loop to return a mock loop
        mock_loop = MagicMock()
        monkeypatch.setattr(asyncio, "get_running_loop", lambda: mock_loop)

        flow._handle_state_transition(session, prev_state)

        mock_loop.create_task.assert_called_once()

    def test_integration_validation_skipped_no_work_dir(self, monkeypatch, tmp_path):
        """When no session has a work_dir, validation is not triggered."""
        import asyncio
        from golem.backends.local import LogNotifier

        notifier = MagicMock(spec=LogNotifier)
        profile = _make_test_profile()
        object.__setattr__(profile, "notifier", notifier)

        flow = _make_flow(monkeypatch, tmp_path, profile=profile)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch(
            [{"prompt": "A", "subject": "A"}], group_id="grp-nodir"
        )
        tid = result["tasks"][0]["task_id"]
        session = flow._sessions[tid]
        session.base_work_dir = ""
        prev_state = session.state
        session.state = TaskSessionState.COMPLETED
        session.validation_verdict = "PASS"

        mock_loop = MagicMock()
        monkeypatch.setattr(asyncio, "get_running_loop", lambda: mock_loop)

        flow._handle_state_transition(session, prev_state)

        mock_loop.create_task.assert_not_called()

    def test_integration_validation_no_event_loop(self, monkeypatch, tmp_path):
        """If no event loop is running, RuntimeError is caught gracefully."""
        import asyncio
        from golem.backends.local import LogNotifier

        notifier = MagicMock(spec=LogNotifier)
        profile = _make_test_profile()
        object.__setattr__(profile, "notifier", notifier)

        flow = _make_flow(monkeypatch, tmp_path, profile=profile)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch(
            [{"prompt": "A", "subject": "A"}], group_id="grp-noloop"
        )
        tid = result["tasks"][0]["task_id"]
        session = flow._sessions[tid]
        session.base_work_dir = "/tmp/work"
        prev_state = session.state
        session.state = TaskSessionState.COMPLETED
        session.validation_verdict = "PASS"

        def no_loop():
            raise RuntimeError("no running event loop")

        monkeypatch.setattr(asyncio, "get_running_loop", no_loop)

        # Should not raise - RuntimeError is caught
        flow._handle_state_transition(session, prev_state)

    def test_integration_validation_exception_caught(self, monkeypatch, tmp_path):
        """If create_task raises a non-RuntimeError, it is caught and logged."""
        import asyncio
        from golem.backends.local import LogNotifier

        notifier = MagicMock(spec=LogNotifier)
        profile = _make_test_profile()
        object.__setattr__(profile, "notifier", notifier)

        flow = _make_flow(monkeypatch, tmp_path, profile=profile)
        monkeypatch.setattr(flow, "_spawn_session_task", lambda sid: None)

        result = flow.submit_batch(
            [{"prompt": "A", "subject": "A"}], group_id="grp-exc"
        )
        tid = result["tasks"][0]["task_id"]
        session = flow._sessions[tid]
        session.base_work_dir = "/tmp/work"
        prev_state = session.state
        session.state = TaskSessionState.COMPLETED
        session.validation_verdict = "PASS"

        mock_loop = MagicMock()
        mock_loop.create_task.side_effect = TypeError("bad coroutine")
        monkeypatch.setattr(asyncio, "get_running_loop", lambda: mock_loop)

        # Should not raise - exception is caught
        flow._handle_state_transition(session, prev_state)
