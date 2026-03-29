"""Tests for golem.log_context — contextvars-based task correlation.

Tests verify:
- set_task_context / clear_task_context modify the context vars
- TaskContextFilter injects task_id and phase into log records
- JsonFormatter produces valid JSON with all required fields
- Context isolation between tasks (contextvars)
"""

import asyncio
import json
import logging

import pytest

from golem.log_context import (
    JsonFormatter,
    TaskContextFilter,
    clear_task_context,
    phase_var,
    set_task_context,
    setup_logging,
    task_id_var,
)


class TestSetAndClearTaskContext:
    def test_set_task_context_updates_task_id(self):
        set_task_context("task-42")
        assert task_id_var.get() == "task-42"

    def test_set_task_context_updates_phase(self):
        set_task_context("task-1", phase="BUILD")
        assert phase_var.get() == "BUILD"

    def test_set_task_context_empty_phase_default(self):
        set_task_context("task-99")
        assert phase_var.get() == ""

    def test_clear_task_context_resets_task_id(self):
        set_task_context("task-5", phase="PLAN")
        clear_task_context()
        assert task_id_var.get() == ""

    def test_clear_task_context_resets_phase(self):
        set_task_context("task-5", phase="PLAN")
        clear_task_context()
        assert phase_var.get() == ""

    def test_set_task_context_overwrites_previous(self):
        set_task_context("task-1", phase="UNDERSTAND")
        set_task_context("task-2", phase="BUILD")
        assert task_id_var.get() == "task-2"
        assert phase_var.get() == "BUILD"


class TestTaskContextFilter:
    def _make_record(self, msg: str = "test") -> logging.LogRecord:
        return logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_filter_injects_task_id(self):
        set_task_context("task-7", phase="REVIEW")
        f = TaskContextFilter()
        record = self._make_record()
        result = f.filter(record)
        assert result is True
        assert record.task_id == "task-7"

    def test_filter_injects_phase(self):
        set_task_context("task-7", phase="REVIEW")
        f = TaskContextFilter()
        record = self._make_record()
        f.filter(record)
        assert record.phase == "REVIEW"

    def test_filter_injects_empty_when_no_context_set(self):
        clear_task_context()
        f = TaskContextFilter()
        record = self._make_record()
        f.filter(record)
        assert record.task_id == ""
        assert record.phase == ""

    def test_filter_returns_true(self):
        """filter() must return True so the record is not dropped."""
        f = TaskContextFilter()
        record = self._make_record()
        assert f.filter(record) is True

    @pytest.mark.parametrize(
        "task_id,phase",
        [
            ("abc-123", "BUILD"),
            ("", ""),
            ("task-0", "VERIFY"),
            ("long-task-id-with-dashes", "UNDERSTAND"),
        ],
        ids=["normal", "empty", "verify_phase", "long_id"],
    )
    def test_filter_injects_various_contexts(self, task_id, phase):
        set_task_context(task_id, phase=phase)
        f = TaskContextFilter()
        record = self._make_record()
        f.filter(record)
        assert record.task_id == task_id
        assert record.phase == phase


class TestJsonFormatter:
    def _make_record(
        self, msg: str = "hello", level: int = logging.INFO
    ) -> logging.LogRecord:
        record = logging.LogRecord(
            name="golem.test",
            level=level,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        # Simulate TaskContextFilter having been applied
        record.task_id = "task-5"
        record.phase = "BUILD"
        return record

    def test_format_returns_valid_json(self):
        fmt = JsonFormatter()
        record = self._make_record()
        output = fmt.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_format_includes_message(self):
        fmt = JsonFormatter()
        record = self._make_record("test message")
        parsed = json.loads(fmt.format(record))
        assert parsed["message"] == "test message"

    def test_format_includes_level(self):
        fmt = JsonFormatter()
        record = self._make_record(level=logging.WARNING)
        parsed = json.loads(fmt.format(record))
        assert parsed["level"] == "WARNING"

    def test_format_includes_logger_name(self):
        fmt = JsonFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert parsed["logger"] == "golem.test"

    def test_format_includes_task_id(self):
        fmt = JsonFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert parsed["task_id"] == "task-5"

    def test_format_includes_phase(self):
        fmt = JsonFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert parsed["phase"] == "BUILD"

    def test_format_includes_timestamp(self):
        fmt = JsonFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert "timestamp" in parsed
        assert isinstance(parsed["timestamp"], str)

    def test_format_missing_task_id_defaults_to_empty(self):
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="no context",
            args=(),
            exc_info=None,
        )
        # No task_id/phase attributes set
        parsed = json.loads(fmt.format(record))
        assert parsed["task_id"] == ""
        assert parsed["phase"] == ""

    def test_format_includes_exception_when_present(self):
        import sys

        fmt = JsonFormatter()
        exc_info = None
        try:
            raise ValueError("oops")
        except ValueError:
            exc_info = sys.exc_info()
        assert exc_info is not None
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="error occurred",
            args=(),
            exc_info=exc_info,
        )
        record.task_id = ""
        record.phase = ""
        output = fmt.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "ValueError" in parsed["exception"]

    def test_format_no_exception_field_when_no_exc_info(self):
        fmt = JsonFormatter()
        record = self._make_record()
        parsed = json.loads(fmt.format(record))
        assert "exception" not in parsed


class TestContextIsolation:
    """Verify contextvars are isolated between concurrent async tasks."""

    async def test_context_isolation_between_tasks(self):
        """asyncio.create_task copies context so each task has its own scope."""
        results: dict[str, str] = {}

        async def task_a():
            set_task_context("task-A", phase="BUILD")
            await asyncio.sleep(0)
            results["a_task_id"] = task_id_var.get()
            results["a_phase"] = phase_var.get()

        async def task_b():
            set_task_context("task-B", phase="VERIFY")
            await asyncio.sleep(0)
            results["b_task_id"] = task_id_var.get()
            results["b_phase"] = phase_var.get()

        t_a = asyncio.create_task(task_a())
        t_b = asyncio.create_task(task_b())
        await asyncio.gather(t_a, t_b)

        # Each task should see its own context
        assert results["a_task_id"] == "task-A"
        assert results["a_phase"] == "BUILD"
        assert results["b_task_id"] == "task-B"
        assert results["b_phase"] == "VERIFY"

    async def test_context_not_shared_with_copy_context(self):
        """contextvars.copy_context() creates true isolation."""
        import contextvars as _cv

        set_task_context("parent-task", phase="PLAN")

        captured: dict[str, str] = {}

        def isolated_fn():
            captured["task_id"] = task_id_var.get()
            captured["phase"] = phase_var.get()
            # Change in copy doesn't affect parent
            task_id_var.set("child-task")

        ctx = _cv.copy_context()
        ctx.run(isolated_fn)

        # The copy sees the parent's values at copy time
        assert captured["task_id"] == "parent-task"
        assert captured["phase"] == "PLAN"

        # But the parent's context is unaffected by mutation in the copy
        assert task_id_var.get() == "parent-task"


class TestSetupLogging:
    """setup_logging() installs TaskContextFilter and optionally JsonFormatter."""

    def setup_method(self):
        """Snapshot root logger state before each test so we can restore it."""
        root = logging.getLogger()
        self._original_filters = list(root.filters)
        self._original_formatters = {h: h.formatter for h in root.handlers}

    def teardown_method(self):
        """Restore root logger to pre-test state."""
        root = logging.getLogger()
        root.filters = list(self._original_filters)
        for handler, fmt in self._original_formatters.items():
            handler.setFormatter(fmt)

    def test_setup_logging_adds_task_context_filter(self):
        """setup_logging() must install TaskContextFilter on the root logger."""
        root = logging.getLogger()
        before = sum(1 for f in root.filters if isinstance(f, TaskContextFilter))
        setup_logging()
        after = sum(1 for f in root.filters if isinstance(f, TaskContextFilter))
        assert after == before + 1

    def test_setup_logging_json_mode_false_does_not_change_formatters(self):
        """setup_logging(json_mode=False) must not replace existing formatters."""
        root = logging.getLogger()
        original_formatters = {h: h.formatter for h in root.handlers}
        setup_logging(json_mode=False)
        for handler, original_fmt in original_formatters.items():
            assert handler.formatter is original_fmt

    def test_setup_logging_json_mode_true_replaces_formatters_with_json(self):
        """setup_logging(json_mode=True) must replace all handler formatters."""
        root = logging.getLogger()
        if not root.handlers:
            # Ensure there is at least one handler to verify against
            pytest.skip(
                "Root logger has no handlers — cannot test formatter replacement"
            )
        setup_logging(json_mode=True)
        for handler in root.handlers:
            assert isinstance(handler.formatter, JsonFormatter), (
                f"Handler {handler!r} did not get a JsonFormatter; "
                f"got {handler.formatter!r}"
            )

    def test_setup_logging_default_is_not_json_mode(self):
        """setup_logging() called without arguments must not install JsonFormatter."""
        root = logging.getLogger()
        if not root.handlers:
            pytest.skip(
                "Root logger has no handlers — cannot test formatter preservation"
            )
        original_formatters = {h: h.formatter for h in root.handlers}
        setup_logging()
        for handler, original_fmt in original_formatters.items():
            assert handler.formatter is original_fmt
