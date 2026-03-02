"""Session-aware structured logging adapter.

Injects session ID and task subject into log messages so concurrent
sessions can be filtered apart.
"""

import logging


class SessionLogAdapter(logging.LoggerAdapter):
    """Prepends ``[task-{session_id}]`` to every log message.

    Usage::

        slog = SessionLogAdapter(logger, session_id=42, subject="Fix bug")
        slog.info("starting agent")
        # => "[task-42] starting agent"
    """

    def __init__(
        self,
        logger: logging.Logger,
        *,
        session_id: int | str,
        subject: str = "",
    ):
        super().__init__(logger, {"session_id": session_id, "task_subject": subject})

    def process(self, msg, kwargs):
        extra = kwargs.get("extra", {})
        extra.update(self.extra)
        kwargs["extra"] = extra
        session_id = self.extra["session_id"]
        return f"[task-{session_id}] {msg}", kwargs
