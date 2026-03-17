"""Tests for golem.tracing — OpenTelemetry integration with no-op fallback."""

import importlib
import sys
from unittest.mock import MagicMock, patch


class TestGetTracerWithOtel:
    """Tests for get_tracer() when opentelemetry is available."""

    def test_returns_tracer_object(self):
        """get_tracer() returns a tracer with the given name when otel is available."""
        import golem.tracing as tracing

        with patch.object(tracing, "_OTEL_AVAILABLE", True):
            mock_tracer = MagicMock()
            mock_provider = MagicMock()
            mock_provider.get_tracer.return_value = mock_tracer

            with patch("golem.tracing.trace") as mock_trace:
                mock_trace.get_tracer_provider.return_value = mock_provider
                result = tracing.get_tracer("my.service")

            mock_trace.get_tracer_provider.assert_called_once()
            mock_provider.get_tracer.assert_called_once_with("my.service")
            assert result is mock_tracer

    def test_get_tracer_uses_provided_name(self):
        """get_tracer() passes the exact name string to the provider."""
        import golem.tracing as tracing

        with patch.object(tracing, "_OTEL_AVAILABLE", True):
            mock_provider = MagicMock()
            with patch("golem.tracing.trace") as mock_trace:
                mock_trace.get_tracer_provider.return_value = mock_provider
                tracing.get_tracer("golem.custom")
            mock_provider.get_tracer.assert_called_once_with("golem.custom")


class TestGetTracerNoOp:
    """Tests for get_tracer() when opentelemetry is not installed."""

    def test_returns_usable_object_when_otel_unavailable(self):
        """get_tracer() returns a no-op tracer (not None) when otel is absent."""
        import golem.tracing as tracing

        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            result = tracing.get_tracer("any.name")
        assert isinstance(result, tracing._NoOpTracer)

    def test_noop_tracer_has_start_as_current_span(self):
        """The no-op tracer exposes a start_as_current_span callable."""
        import golem.tracing as tracing

        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            tracer = tracing.get_tracer("any.name")
        assert callable(getattr(tracer, "start_as_current_span", None))


class TestStartSpanWithOtel:
    """Tests for start_span() when opentelemetry is available."""

    def test_context_manager_yields_span(self):
        """start_span() is a context manager that yields a span object."""
        import golem.tracing as tracing

        mock_span = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_span)
        mock_cm.__exit__ = MagicMock(return_value=False)

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_cm

        with patch.object(tracing, "_OTEL_AVAILABLE", True):
            with patch("golem.tracing.trace") as mock_trace:
                mock_provider = MagicMock()
                mock_provider.get_tracer.return_value = mock_tracer
                mock_trace.get_tracer_provider.return_value = mock_provider

                with tracing.start_span("my-operation") as span:
                    assert span is mock_span

        mock_tracer.start_as_current_span.assert_called_once_with(
            "my-operation", attributes=None
        )

    def test_context_manager_passes_attributes(self):
        """start_span() forwards the attributes dict to the underlying span."""
        import golem.tracing as tracing

        mock_span = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_span)
        mock_cm.__exit__ = MagicMock(return_value=False)

        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_cm

        attrs = {"http.method": "GET", "http.status": 200}

        with patch.object(tracing, "_OTEL_AVAILABLE", True):
            with patch("golem.tracing.trace") as mock_trace:
                mock_provider = MagicMock()
                mock_provider.get_tracer.return_value = mock_tracer
                mock_trace.get_tracer_provider.return_value = mock_provider

                with tracing.start_span("my-op", attributes=attrs) as span:
                    assert span is mock_span

        mock_tracer.start_as_current_span.assert_called_once_with(
            "my-op", attributes=attrs
        )


class TestStartSpanNoOp:
    """Tests for start_span() when opentelemetry is not installed."""

    def test_context_manager_works_without_error(self):
        """start_span() succeeds as a context manager when otel is absent."""
        import golem.tracing as tracing

        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            with tracing.start_span("no-otel-op") as span:
                # Must not raise; span must be a _NoOpSpan instance
                assert isinstance(span, tracing._NoOpSpan)

    def test_noop_span_yields_something_with_set_attribute(self):
        """The no-op span supports set_attribute without raising."""
        import golem.tracing as tracing

        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            with tracing.start_span("no-otel-op") as span:
                # set_attribute is the canonical OTel span API; calling it
                # on the no-op must not raise an exception.
                span.set_attribute("key", "value")
        assert hasattr(span, "set_attribute")

    def test_noop_span_with_attributes_kwarg(self):
        """start_span(attributes=...) works silently in no-op mode."""
        import golem.tracing as tracing

        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            with tracing.start_span("op", attributes={"a": 1}) as span:
                assert isinstance(span, tracing._NoOpSpan)

    def test_noop_span_yields_distinct_object_per_call(self):
        """Each start_span() call in no-op mode yields a fresh span instance."""
        import golem.tracing as tracing

        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            with tracing.start_span("op-a") as span_a:
                with tracing.start_span("op-b") as span_b:
                    assert span_a is not span_b


class TestOtelAvailableFlag:
    """Tests that _OTEL_AVAILABLE reflects actual import state."""

    def test_flag_true_when_otel_importable(self):
        """_OTEL_AVAILABLE is True when opentelemetry.trace can be imported."""
        import types

        import golem.tracing as tracing_mod

        # Create a fake opentelemetry.trace module
        fake_trace = types.ModuleType("opentelemetry.trace")
        fake_otel = types.ModuleType("opentelemetry")
        fake_otel.trace = fake_trace

        saved = {}
        for key in list(sys.modules):
            if key.startswith("opentelemetry"):
                saved[key] = sys.modules.pop(key)
        try:
            with patch.dict(
                sys.modules,
                {"opentelemetry": fake_otel, "opentelemetry.trace": fake_trace},
            ):
                importlib.reload(tracing_mod)
                assert tracing_mod._OTEL_AVAILABLE is True
                assert tracing_mod.trace is fake_trace
        finally:
            sys.modules.update(saved)
            importlib.reload(tracing_mod)

    def test_flag_false_when_otel_not_importable(self):
        """_OTEL_AVAILABLE is False when opentelemetry cannot be imported."""
        import golem.tracing as tracing_mod

        saved = {}
        for key in list(sys.modules):
            if key.startswith("opentelemetry"):
                saved[key] = sys.modules.pop(key)
        try:
            with patch.dict(
                sys.modules, {"opentelemetry": None, "opentelemetry.trace": None}
            ):
                importlib.reload(tracing_mod)
                assert tracing_mod._OTEL_AVAILABLE is False
                assert tracing_mod.trace is None
        finally:
            sys.modules.update(saved)
            importlib.reload(tracing_mod)
