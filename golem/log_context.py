"""Structured logging context for task correlation.

Uses contextvars to propagate task_id and phase through the async
call stack, enabling cross-task log correlation.
"""

import contextvars
import json
import logging

task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="")
phase_var: contextvars.ContextVar[str] = contextvars.ContextVar("phase", default="")


class TaskContextFilter(logging.Filter):
    """Injects task_id and phase into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.task_id = task_id_var.get("")
        record.phase = phase_var.get("")
        return True


def set_task_context(task_id: str, phase: str = "") -> None:
    """Set the current task context for logging."""
    task_id_var.set(task_id)
    phase_var.set(phase)


def clear_task_context() -> None:
    """Clear the current task context."""
    task_id_var.set("")
    phase_var.set("")


class JsonFormatter(logging.Formatter):
    """JSON log formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "task_id": getattr(record, "task_id", ""),
            "phase": getattr(record, "phase", ""),
        }
        if record.exc_info and record.exc_info[0]:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data)


def setup_logging(json_mode: bool = False) -> None:
    """Install TaskContextFilter on the root logger (idempotent).

    If json_mode is True, also replace the formatter on all existing
    handlers with JsonFormatter.  Safe to call multiple times — only
    adds the filter once.
    """
    root = logging.getLogger()
    if not any(isinstance(f, TaskContextFilter) for f in root.filters):
        root.addFilter(TaskContextFilter())
    if json_mode:
        fmt = JsonFormatter()
        for handler in root.handlers:
            handler.setFormatter(fmt)
