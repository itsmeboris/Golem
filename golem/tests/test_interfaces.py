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
    def poll_tasks(self, _projects, _detection_tag, _timeout=30):
        return []

    def get_task_description(self, _task_id):
        return ""

    def get_child_tasks(self, _parent_id):
        return []

    def create_child_task(self, _parent_id, _subject, _description):
        return None

    def get_task_subject(self, _task_id):
        return ""

    def get_task_comments(self, _task_id, *, since=""):
        del since  # protocol-required
        return []

    def poll_untagged_tasks(self, _projects, _exclude_tag, _limit=20, _timeout=30):
        return []


class DummyStateBackend:
    def update_status(self, _task_id, _status):
        return True

    def post_comment(self, _task_id, _text):
        return True

    def update_progress(self, _task_id, _percent):
        return True


class DummyNotifier:
    def notify_started(self, _task_id, _subject):
        pass

    def notify_completed(self, _task_id, _subject, **_kwargs):
        pass

    def notify_failed(self, _task_id, _subject, _reason, **_kwargs):
        pass

    def notify_escalated(self, _task_id, _subject, _verdict, _summary, **_kwargs):
        pass

    def notify_batch_submitted(self, _group_id, _task_count):
        pass

    def notify_batch_completed(self, _group_id, _status, **_kwargs):
        pass

    def notify_health_alert(self, _alert_type, _message, **_kwargs):
        pass


class DummyToolProvider:
    def base_servers(self):
        return []

    def servers_for_subject(self, _subject, *, role: str = ""):
        del role  # protocol-required
        return []


class DummyPromptProvider:
    def format(self, _template_name, **_kwargs):
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


class TestRealImplementationConformance:
    """Verify actual backend implementations satisfy protocol contracts.

    These complement the Dummy* tests above — Dummy tests verify the protocol
    definition is stable, these verify real implementations conform to it.
    """

    def test_local_task_source_satisfies_protocol(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        source = LocalFileTaskSource(str(tmp_path))
        assert isinstance(source, TaskSource)

    def test_null_state_backend_satisfies_protocol(self):
        from golem.backends.local import NullStateBackend

        backend = NullStateBackend()
        assert isinstance(backend, StateBackend)

    def test_log_notifier_satisfies_protocol(self):
        from golem.backends.local import LogNotifier

        notifier = LogNotifier()
        assert isinstance(notifier, Notifier)

    def test_null_tool_provider_satisfies_protocol(self):
        from golem.backends.local import NullToolProvider

        provider = NullToolProvider()
        assert isinstance(provider, ToolProvider)

    def test_file_prompt_provider_satisfies_protocol(self):
        from golem.prompts import FilePromptProvider

        provider = FilePromptProvider()
        assert isinstance(provider, PromptProvider)
