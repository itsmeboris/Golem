"""Tests for golem.core.log_context — SessionLogAdapter."""

import logging

from golem.core.log_context import SessionLogAdapter


class TestSessionLogAdapter:
    def test_process_prepends_task_prefix(self):
        base = logging.getLogger("test.log_context.prefix")
        adapter = SessionLogAdapter(base, session_id=42, subject="Fix bug")
        msg, _kwargs = adapter.process("hello", {})
        assert msg == "[task-42] hello"

    def test_process_injects_extra_fields(self):
        base = logging.getLogger("test.log_context.extra")
        adapter = SessionLogAdapter(base, session_id=7, subject="Add feature")
        _, kwargs = adapter.process("msg", {})
        assert kwargs["extra"]["session_id"] == 7
        assert kwargs["extra"]["task_subject"] == "Add feature"

    def test_process_preserves_existing_extra(self):
        base = logging.getLogger("test.log_context.preserve")
        adapter = SessionLogAdapter(base, session_id=1, subject="s")
        _, kwargs = adapter.process("msg", {"extra": {"custom": "val"}})
        assert kwargs["extra"]["custom"] == "val"
        assert kwargs["extra"]["session_id"] == 1

    def test_string_session_id(self):
        base = logging.getLogger("test.log_context.str_id")
        adapter = SessionLogAdapter(base, session_id="abc-123", subject="")
        msg, _ = adapter.process("test", {})
        assert msg == "[task-abc-123] test"

    def test_empty_subject(self):
        base = logging.getLogger("test.log_context.empty_subj")
        adapter = SessionLogAdapter(base, session_id=5, subject="")
        _, kwargs = adapter.process("msg", {})
        assert kwargs["extra"]["task_subject"] == ""

    def test_actual_log_output(self, caplog):
        base = logging.getLogger("test.log_context.output")
        adapter = SessionLogAdapter(base, session_id=99, subject="Test task")
        with caplog.at_level(logging.INFO, logger="test.log_context.output"):
            adapter.info("starting agent")
        assert len(caplog.records) == 1
        assert "[task-99] starting agent" in caplog.records[0].message

    def test_log_record_has_extra_attrs(self, caplog):
        base = logging.getLogger("test.log_context.attrs")
        adapter = SessionLogAdapter(base, session_id=10, subject="My task")
        with caplog.at_level(logging.DEBUG, logger="test.log_context.attrs"):
            adapter.warning("something happened")
        record = caplog.records[0]
        assert record.session_id == 10
        assert record.task_subject == "My task"

    def test_format_with_percent_args(self, caplog):
        base = logging.getLogger("test.log_context.pct")
        adapter = SessionLogAdapter(base, session_id=3, subject="x")
        with caplog.at_level(logging.INFO, logger="test.log_context.pct"):
            adapter.info("cost $%.2f, took %ds", 1.5, 42)
        assert "[task-3] cost $1.50, took 42s" in caplog.records[0].message
