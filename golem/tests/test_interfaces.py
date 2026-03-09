# pylint: disable=too-few-public-methods
"""Tests for golem.interfaces — protocol interfaces and TaskStatus."""

from golem.interfaces import (
    Notifier,
    PromptProvider,
    StateBackend,
    TaskSource,
    TaskStatus,
    ToolProvider,
)


class TestTaskStatus:
    def test_constants(self):
        assert TaskStatus.IN_PROGRESS == "in_progress"
        assert TaskStatus.FIXED == "fixed"
        assert TaskStatus.CLOSED == "closed"


class DummyTaskSource:
    def poll_tasks(self, projects, detection_tag, timeout=30):
        return []

    def get_task_description(self, task_id):
        return ""

    def get_child_tasks(self, parent_id):
        return []

    def create_child_task(self, parent_id, subject, description):
        return None

    def get_task_subject(self, task_id):
        return ""

    def get_task_comments(self, task_id, *, since=""):
        return []


class DummyStateBackend:
    def update_status(self, task_id, status):
        return True

    def post_comment(self, task_id, text):
        return True

    def update_progress(self, task_id, percent):
        return True


class DummyNotifier:
    def notify_started(self, task_id, subject):
        pass

    def notify_completed(self, task_id, subject, **kwargs):
        pass

    def notify_failed(self, task_id, subject, reason, **kwargs):
        pass

    def notify_escalated(self, task_id, subject, verdict, summary, **kwargs):
        pass

    def notify_batch_submitted(self, group_id, task_count):
        pass

    def notify_batch_completed(self, group_id, status, **kwargs):
        pass

    def notify_health_alert(self, alert_type, message, **kwargs):
        pass


class DummyToolProvider:
    def base_servers(self):
        return []

    def servers_for_subject(self, subject):
        return []


class DummyPromptProvider:
    def format(self, template_name, **kwargs):
        return ""


class TestProtocolConformance:
    def test_task_source(self):
        assert isinstance(DummyTaskSource(), TaskSource)

    def test_state_backend(self):
        assert isinstance(DummyStateBackend(), StateBackend)

    def test_notifier(self):
        assert isinstance(DummyNotifier(), Notifier)

    def test_tool_provider(self):
        assert isinstance(DummyToolProvider(), ToolProvider)

    def test_prompt_provider(self):
        assert isinstance(DummyPromptProvider(), PromptProvider)
