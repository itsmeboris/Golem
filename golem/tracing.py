"""OpenTelemetry tracing integration with a silent no-op fallback.

When the ``opentelemetry-api`` and ``opentelemetry-sdk`` packages are installed
the real OTel tracer is returned.  When they are absent every call silently
does nothing so application code that imports this module never needs to guard
against an ``ImportError``.
"""

from contextlib import contextmanager
from typing import Any, Generator

try:
    from opentelemetry import trace

    _OTEL_AVAILABLE = True
except ImportError:
    trace = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False

__all__ = ["get_tracer", "start_span"]


# ---------------------------------------------------------------------------
# No-op helpers used when opentelemetry is not installed
# ---------------------------------------------------------------------------


class _NoOpSpan:
    """Minimal span substitute that accepts attribute writes without effect."""

    def set_attribute(self, _key: str, _value: Any) -> None:
        """Accept attribute writes silently."""


class _NoOpTracer:
    """Minimal tracer substitute returned when otel is unavailable."""

    def start_as_current_span(self, _name: str, **_kwargs: Any) -> Any:
        """Return a context manager that yields a _NoOpSpan."""
        return _noop_span_cm()


@contextmanager
def _noop_span_cm() -> Generator[_NoOpSpan, None, None]:
    """Context manager that yields a _NoOpSpan and does nothing else."""
    yield _NoOpSpan()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_tracer(name: str) -> Any:
    """Return an OpenTelemetry Tracer for the given instrumentation scope.

    Parameters
    ----------
    name:
        Instrumentation scope name, typically ``__name__`` of the calling
        module (e.g. ``"golem.task_agent"``).

    Returns
    -------
    opentelemetry.trace.Tracer or _NoOpTracer
        A real tracer when ``opentelemetry-api`` is installed; a no-op
        substitute otherwise.
    """
    if _OTEL_AVAILABLE:
        return trace.get_tracer_provider().get_tracer(name)
    return _NoOpTracer()


@contextmanager
def start_span(
    name: str, attributes: dict[str, Any] | None = None
) -> Generator[Any, None, None]:
    """Context manager that creates and activates an OTel span.

    Parameters
    ----------
    name:
        The span name.
    attributes:
        Optional mapping of span attributes (string keys, primitive values).

    Yields
    ------
    opentelemetry.trace.Span or _NoOpSpan
        The active span.  In no-op mode yields a :class:`_NoOpSpan` that
        accepts ``set_attribute`` calls without effect.

    Example
    -------
    ::

        with start_span("my-operation", attributes={"user.id": "42"}) as span:
            span.set_attribute("result", "ok")
    """
    tracer = get_tracer(__name__)
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        yield span
