"""Tests for golem.tracing — OpenTelemetry integration with no-op fallback."""

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest


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


class TestPublicOtelAvailableAlias:
    """OTEL_AVAILABLE (public alias) matches the private _OTEL_AVAILABLE flag."""

    def test_alias_matches_private_flag(self):
        """OTEL_AVAILABLE is set equal to _OTEL_AVAILABLE at module load time."""
        import golem.tracing as tracing

        assert tracing.OTEL_AVAILABLE == tracing._OTEL_AVAILABLE

    def test_alias_exported_in_all(self):
        """OTEL_AVAILABLE appears in __all__."""
        import golem.tracing as tracing

        assert "OTEL_AVAILABLE" in tracing.__all__


class TestNoOpSpanExtendedMethods:
    """Tests for the extended _NoOpSpan API methods."""

    def test_set_status_does_not_raise(self):
        """_NoOpSpan.set_status accepts any value without raising."""
        import golem.tracing as tracing

        span = tracing._NoOpSpan()
        span.set_status("ok")

    def test_record_exception_does_not_raise(self):
        """_NoOpSpan.record_exception accepts a BaseException without raising."""
        import golem.tracing as tracing

        span = tracing._NoOpSpan()
        span.record_exception(ValueError("boom"))

    @pytest.mark.parametrize(
        "name,attrs",
        [
            ("event-with-attrs", {"key": "value"}),
            ("event-no-attrs", None),
            ("event-empty-attrs", {}),
        ],
        ids=["with_attrs", "no_attrs", "empty_attrs"],
    )
    def test_add_event_does_not_raise(self, name, attrs):
        """_NoOpSpan.add_event accepts event name and optional attributes without raising."""
        import golem.tracing as tracing

        span = tracing._NoOpSpan()
        span.add_event(name, attrs)


class TestInitTracingNoOtel:
    """Tests for init_tracing() when opentelemetry is NOT installed."""

    def test_returns_false_when_otel_unavailable(self):
        """init_tracing() returns False when _OTEL_AVAILABLE is False."""
        import golem.tracing as tracing

        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            result = tracing.init_tracing()
        assert result is False

    def test_does_not_set_initialized_when_otel_unavailable(self):
        """init_tracing() leaves _initialized unchanged when OTel is absent."""
        import golem.tracing as tracing

        original = tracing._initialized
        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            tracing.init_tracing()
        assert tracing._initialized == original

    def test_init_tracing_in_all(self):
        """init_tracing appears in __all__."""
        import golem.tracing as tracing

        assert "init_tracing" in tracing.__all__


def _sdk_modules(mocks):
    """Return fake sys.modules entries for the OTel SDK."""
    import types

    resources_mod = types.ModuleType("opentelemetry.sdk.resources")
    resources_mod.Resource = mocks["Resource"]

    sdk_trace_mod = types.ModuleType("opentelemetry.sdk.trace")
    sdk_trace_mod.TracerProvider = mocks["TracerProvider"]

    export_mod = types.ModuleType("opentelemetry.sdk.trace.export")
    export_mod.BatchSpanProcessor = mocks["BatchSpanProcessor"]
    export_mod.ConsoleSpanExporter = mocks["ConsoleSpanExporter"]

    return {
        "opentelemetry.sdk.resources": resources_mod,
        "opentelemetry.sdk.trace": sdk_trace_mod,
        "opentelemetry.sdk.trace.export": export_mod,
    }


def _make_sdk_mocks():
    """Return mock classes for the OTel SDK used inside init_tracing."""
    mock_provider = MagicMock()
    mock_provider_cls = MagicMock(return_value=mock_provider)
    mock_resource_cls = MagicMock()
    mock_resource_cls.create = MagicMock(return_value=MagicMock())
    mock_batch_cls = MagicMock(return_value=MagicMock())
    mock_console_cls = MagicMock(return_value=MagicMock())
    return {
        "Resource": mock_resource_cls,
        "TracerProvider": mock_provider_cls,
        "BatchSpanProcessor": mock_batch_cls,
        "ConsoleSpanExporter": mock_console_cls,
        "provider": mock_provider,
    }


class TestInitTracingWithOtel:
    """Tests for init_tracing() when opentelemetry IS available (mocked)."""

    def test_returns_true_when_otel_available(self):
        """init_tracing() returns True when OTel SDK is present."""
        import golem.tracing as tracing

        mocks = _make_sdk_mocks()
        with (
            patch.object(tracing, "_OTEL_AVAILABLE", True),
            patch.object(tracing, "_initialized", False),
            patch("golem.tracing.trace"),
            patch.dict(sys.modules, _sdk_modules(mocks)),
        ):
            result = tracing.init_tracing(service_name="test-svc")
        assert result is True

    def test_sets_initialized_flag(self):
        """init_tracing() sets _initialized to True on success."""
        import golem.tracing as tracing

        mocks = _make_sdk_mocks()
        with (
            patch.object(tracing, "_OTEL_AVAILABLE", True),
            patch.object(tracing, "_initialized", False),
            patch("golem.tracing.trace"),
            patch.dict(sys.modules, _sdk_modules(mocks)),
        ):
            tracing.init_tracing(service_name="test-svc")
            assert tracing._initialized is True

    def test_returns_true_when_already_initialized(self):
        """init_tracing() returns True immediately when already initialized."""
        import golem.tracing as tracing

        with (
            patch.object(tracing, "_OTEL_AVAILABLE", True),
            patch.object(tracing, "_initialized", True),
        ):
            result = tracing.init_tracing()
        assert result is True

    def test_console_export_adds_processor(self):
        """init_tracing(console_export=True) adds a BatchSpanProcessor."""
        import golem.tracing as tracing

        mocks = _make_sdk_mocks()
        with (
            patch.object(tracing, "_OTEL_AVAILABLE", True),
            patch.object(tracing, "_initialized", False),
            patch("golem.tracing.trace"),
            patch.dict(sys.modules, _sdk_modules(mocks)),
        ):
            tracing.init_tracing(console_export=True)
        mocks["provider"].add_span_processor.assert_called_once()

    def test_otlp_endpoint_adds_otlp_exporter(self):
        """init_tracing(otlp_endpoint=...) constructs an OTLPSpanExporter."""
        import types

        import golem.tracing as tracing

        mocks = _make_sdk_mocks()
        mock_otlp_exporter_cls = MagicMock()
        otlp_mod = types.ModuleType(
            "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
        )
        otlp_mod.OTLPSpanExporter = mock_otlp_exporter_cls

        extra = dict(
            _sdk_modules(mocks),
            **{
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": otlp_mod,
            },
        )
        with (
            patch.object(tracing, "_OTEL_AVAILABLE", True),
            patch.object(tracing, "_initialized", False),
            patch("golem.tracing.trace"),
            patch.dict(sys.modules, extra),
        ):
            tracing.init_tracing(otlp_endpoint="localhost:4317")
        mock_otlp_exporter_cls.assert_called_once_with(endpoint="localhost:4317")

    def test_otlp_import_error_logs_warning_and_returns_true(self):
        """init_tracing(otlp_endpoint=...) logs a warning when OTLP exporter missing."""
        import golem.tracing as tracing

        mocks = _make_sdk_mocks()
        extra = dict(
            _sdk_modules(mocks),
            **{
                "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": None,
            },
        )
        with (
            patch.object(tracing, "_OTEL_AVAILABLE", True),
            patch.object(tracing, "_initialized", False),
            patch("golem.tracing.trace"),
            patch.dict(sys.modules, extra),
        ):
            result = tracing.init_tracing(otlp_endpoint="localhost:4317")
        assert result is True

    def test_sets_tracer_provider(self):
        """init_tracing() calls trace.set_tracer_provider with the constructed provider."""
        import golem.tracing as tracing

        mocks = _make_sdk_mocks()
        with (
            patch.object(tracing, "_OTEL_AVAILABLE", True),
            patch.object(tracing, "_initialized", False),
            patch("golem.tracing.trace") as mock_trace,
            patch.dict(sys.modules, _sdk_modules(mocks)),
        ):
            tracing.init_tracing()
        mock_trace.set_tracer_provider.assert_called_once_with(mocks["provider"])


class TestTraceSpan:
    """Tests for the trace_span() context manager."""

    def test_noop_mode_yields_noop_span(self):
        """trace_span() yields a _NoOpSpan when _OTEL_AVAILABLE is False."""
        import golem.tracing as tracing

        noop_tracer = tracing._NoOpTracer()
        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            with tracing.trace_span(noop_tracer, "my-span") as span:
                assert isinstance(span, tracing._NoOpSpan)

    def test_noop_mode_span_accepts_set_attribute(self):
        """trace_span() no-op span accepts set_attribute without raising."""
        import golem.tracing as tracing

        noop_tracer = tracing._NoOpTracer()
        with patch.object(tracing, "_OTEL_AVAILABLE", False):
            with tracing.trace_span(noop_tracer, "my-span", key="val") as span:
                span.set_attribute("result", "ok")

    def test_otel_mode_uses_tracer_start_as_current_span(self):
        """trace_span() delegates to tracer.start_as_current_span when OTel is on."""
        import golem.tracing as tracing

        mock_span = MagicMock()
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=mock_span)
        mock_cm.__exit__ = MagicMock(return_value=False)
        mock_tracer = MagicMock()
        mock_tracer.start_as_current_span.return_value = mock_cm

        with patch.object(tracing, "_OTEL_AVAILABLE", True):
            with tracing.trace_span(mock_tracer, "op", task_id=42) as span:
                assert span is mock_span

        mock_tracer.start_as_current_span.assert_called_once_with(
            "op", attributes={"task_id": 42}
        )

    def test_trace_span_in_all(self):
        """trace_span appears in __all__."""
        import golem.tracing as tracing

        assert "trace_span" in tracing.__all__

    def test_tracer_without_start_as_current_span_yields_noop_span(self):
        """trace_span() falls back to _NoOpSpan for objects lacking the OTel method."""
        import golem.tracing as tracing

        class MinimalTracer:
            """Tracer without start_as_current_span."""

        minimal = MinimalTracer()
        with patch.object(tracing, "_OTEL_AVAILABLE", True):
            with tracing.trace_span(minimal, "op") as span:
                assert isinstance(span, tracing._NoOpSpan)
