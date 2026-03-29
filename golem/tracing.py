"""OpenTelemetry tracing integration with a silent no-op fallback.

When the ``opentelemetry-api`` and ``opentelemetry-sdk`` packages are installed
the real OTel tracer is returned.  When they are absent every call silently
does nothing so application code that imports this module never needs to guard
against an ``ImportError``.

Usage::

    from golem.tracing import get_tracer, trace_span

    tracer = get_tracer("golem.flow")

    with trace_span(tracer, "detect_tasks") as span:
        span.set_attribute("task_count", 5)
"""

import contextlib
import logging
from typing import Any, Generator

logger = logging.getLogger("golem.tracing")

try:
    from opentelemetry import trace

    _OTEL_AVAILABLE = True
except ImportError:
    trace = None  # type: ignore[assignment]
    _OTEL_AVAILABLE = False

# Public alias for the availability flag (used by tests and external callers)
OTEL_AVAILABLE = _OTEL_AVAILABLE

__all__ = ["get_tracer", "start_span", "init_tracing", "trace_span", "OTEL_AVAILABLE"]

_initialized = False


# ---------------------------------------------------------------------------
# No-op helpers used when opentelemetry is not installed
# ---------------------------------------------------------------------------


class _NoOpSpan:
    """Minimal span substitute that accepts all OTel span API calls without effect."""

    def set_attribute(self, _key: str, _value: Any) -> None:
        """Accept attribute writes silently."""

    def set_status(self, _status: Any) -> None:
        """Accept status writes silently."""

    def record_exception(self, _exception: BaseException) -> None:
        """Accept exception recording silently."""

    def add_event(self, _name: str, _attributes: dict | None = None) -> None:
        """Accept event recording silently."""


class _NoOpTracer:
    """Minimal tracer substitute returned when otel is unavailable."""

    def start_as_current_span(self, _name: str, **_kwargs: Any) -> Any:
        """Return a context manager that yields a _NoOpSpan."""
        return _noop_span_cm()


@contextlib.contextmanager
def _noop_span_cm() -> Generator[_NoOpSpan, None, None]:
    """Context manager that yields a _NoOpSpan and does nothing else."""
    yield _NoOpSpan()


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


def init_tracing(
    service_name: str = "golem",
    *,
    console_export: bool = False,
    otlp_endpoint: str = "",
) -> bool:
    """Initialize OpenTelemetry tracing.

    Parameters
    ----------
    service_name:
        The OTel ``service.name`` resource attribute.
    console_export:
        When ``True``, export spans to stdout via ``ConsoleSpanExporter``.
    otlp_endpoint:
        If non-empty, export spans to this OTLP/gRPC endpoint.

    Returns
    -------
    bool
        ``True`` if OTel was successfully initialised, ``False`` if the
        ``opentelemetry`` packages are not installed.
    """
    global _initialized  # pylint: disable=global-statement

    if not _OTEL_AVAILABLE:
        logger.debug("OpenTelemetry not available — tracing disabled")
        return False

    if _initialized:
        return True

    # pylint: disable=import-error  # optional OTel SDK — absent when not installed
    from opentelemetry.sdk.resources import Resource  # type: ignore[import-untyped]
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
    from opentelemetry.sdk.trace.export import (  # type: ignore[import-untyped]
        BatchSpanProcessor,
        ConsoleSpanExporter,
    )

    # pylint: enable=import-error

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if console_export:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # type: ignore[import-untyped]
                OTLPSpanExporter,
            )

            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except ImportError:
            logger.warning(
                "OTLP exporter not available" " — install opentelemetry-exporter-otlp"
            )

    trace.set_tracer_provider(provider)
    _initialized = True
    logger.info("OpenTelemetry tracing initialized (service=%s)", service_name)
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_tracer(name: str = "golem") -> Any:
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


@contextlib.contextmanager
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


@contextlib.contextmanager
def trace_span(
    tracer: Any,
    name: str,
    **attributes: Any,
) -> Generator[Any, None, None]:
    """Context manager for creating a traced span using an explicit tracer.

    Unlike :func:`start_span`, this takes an explicit ``tracer`` argument so
    callers can pass a module-scoped tracer obtained via :func:`get_tracer`.
    Keyword arguments beyond *tracer* and *name* are forwarded as span
    attributes.

    Works with both real OTel tracers and the :class:`_NoOpTracer` fallback.

    Parameters
    ----------
    tracer:
        A tracer returned by :func:`get_tracer` (real or no-op).
    name:
        The span name.
    **attributes:
        Span attributes passed directly to the underlying span.

    Yields
    ------
    opentelemetry.trace.Span or _NoOpSpan

    Example
    -------
    ::

        tracer = get_tracer("golem.flow")

        with trace_span(tracer, "detect_tasks", task_count=5) as span:
            span.set_attribute("source", "github")
    """
    if _OTEL_AVAILABLE and hasattr(tracer, "start_as_current_span"):
        with tracer.start_as_current_span(name, attributes=attributes) as span:
            yield span
    else:
        yield _NoOpSpan()
