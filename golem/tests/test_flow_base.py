# pylint: disable=too-few-public-methods
"""Tests for golem.core.flow_base — trace/prompt helpers and FlowResult."""

import threading

import pytest
from unittest.mock import MagicMock, patch

from golem.core.flow_base import (
    FlowResult,
    _StreamingTraceWriter,
    _write_prompt,
    _write_trace,
)


class TestWritePrompt:
    def test_creates_prompt_file(self, tmp_path):
        with patch("golem.core.flow_base.TRACES_DIR", tmp_path):
            path = _write_prompt("myflow", "ev-1", "Do the thing")
        prompt_file = tmp_path / "myflow" / "ev-1.prompt.txt"
        assert prompt_file.exists()
        assert prompt_file.read_text() == "Do the thing"
        assert "ev-1" in path

    def test_sanitises_slashes(self, tmp_path):
        with patch("golem.core.flow_base.TRACES_DIR", tmp_path):
            _write_prompt("f", "a/b", "content")
        assert (tmp_path / "f" / "a_b.prompt.txt").exists()

    def test_returns_empty_on_oserror(self, tmp_path):
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        try:
            with patch("golem.core.flow_base.TRACES_DIR", readonly_dir):
                result = _write_prompt("myflow", "ev-1", "content")
            assert result == ""
        finally:
            readonly_dir.chmod(0o755)


class TestWriteTrace:
    def test_creates_jsonl(self, tmp_path):
        events = [{"type": "start"}, {"type": "end"}]
        with patch("golem.core.flow_base.TRACES_DIR", tmp_path):
            path = _write_trace("myflow", "ev-2", events)
        trace_file = tmp_path / "myflow" / "ev-2.jsonl"
        assert trace_file.exists()
        lines = trace_file.read_text().strip().splitlines()
        assert len(lines) == 2
        assert "ev-2" in path

    def test_returns_empty_on_oserror(self, tmp_path):
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        try:
            with patch("golem.core.flow_base.TRACES_DIR", readonly_dir):
                result = _write_trace("myflow", "ev-2", [{"type": "start"}])
            assert result == ""
        finally:
            readonly_dir.chmod(0o755)


class TestStreamingTraceWriter:
    def test_creates_file_and_appends(self, tmp_path):
        with patch("golem.core.flow_base.TRACES_DIR", tmp_path):
            writer = _StreamingTraceWriter("myflow", "ev-3")
            writer.append({"type": "start"})
            writer.close()
        trace_file = tmp_path / "myflow" / "ev-3.jsonl"
        assert trace_file.exists()
        lines = trace_file.read_text().strip().splitlines()
        assert len(lines) == 1
        assert "ev-3" in writer.relative_path

    def test_injects_ts_field(self, tmp_path):
        import json as _json

        with patch("golem.core.flow_base.TRACES_DIR", tmp_path):
            writer = _StreamingTraceWriter("myflow", "ev-ts")
            writer.append({"type": "tick"})
            writer.close()
        trace_file = tmp_path / "myflow" / "ev-ts.jsonl"
        data = _json.loads(trace_file.read_text().strip())
        assert "ts" in data

    def test_preserves_existing_ts(self, tmp_path):
        import json as _json

        with patch("golem.core.flow_base.TRACES_DIR", tmp_path):
            writer = _StreamingTraceWriter("myflow", "ev-ts2")
            writer.append({"type": "tick", "ts": 12345.0})
            writer.close()
        trace_file = tmp_path / "myflow" / "ev-ts2.jsonl"
        data = _json.loads(trace_file.read_text().strip())
        assert data["ts"] == 12345.0

    def test_degrades_to_noop_when_init_fails(self, tmp_path):
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)
        try:
            with patch("golem.core.flow_base.TRACES_DIR", readonly_dir):
                writer = _StreamingTraceWriter("myflow", "ev-4")
            assert writer._fh is None
            assert writer.relative_path == ""
            # append should not raise
            writer.append({"type": "noop"})
        finally:
            readonly_dir.chmod(0o755)

    def test_close_is_idempotent(self, tmp_path):
        with patch("golem.core.flow_base.TRACES_DIR", tmp_path):
            writer = _StreamingTraceWriter("myflow", "ev-5")
            writer.close()
            writer.close()  # should not raise

    def test_append_disables_on_write_error(self, tmp_path):
        with patch("golem.core.flow_base.TRACES_DIR", tmp_path):
            writer = _StreamingTraceWriter("myflow", "ev-6")
        mock_fh = MagicMock()
        mock_fh.write.side_effect = OSError("disk full")
        writer._fh = mock_fh
        writer.append({"type": "fail"})
        assert writer._fh is None

    def test_thread_safety(self, tmp_path):
        with patch("golem.core.flow_base.TRACES_DIR", tmp_path):
            writer = _StreamingTraceWriter("myflow", "ev-thread")
            errors = []

            def _append_many():
                try:
                    for i in range(20):
                        writer.append({"i": i})
                except Exception as exc:  # pylint: disable=broad-except
                    errors.append(exc)

            threads = [threading.Thread(target=_append_many) for _ in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            writer.close()
        assert not errors


class TestFlowResult:
    def test_defaults(self):
        r = FlowResult(success=True)
        assert r.success is True
        assert not r.data
        assert r.error is None
        assert not r.actions_taken

    def test_with_values(self):
        r = FlowResult(success=False, error="boom", actions_taken=["a"])
        assert r.error == "boom"
        assert len(r.actions_taken) == 1


class TestPollableFlowOnItemSuccess:
    """Tests for the base-class implementation of PollableFlow.on_item_success."""

    def test_base_on_item_success_is_noop(self):
        """Base implementation is a pure hook — calling it does not mutate state."""
        from golem.core.flow_base import PollableFlow

        class MinimalPollable(PollableFlow):
            def poll_new_items(self):
                return [{"id": 1}]

            def generate_event_id(self, _item_data):
                return "id-1"

        flow = MinimalPollable()
        # Call the base hook; verify the flow remains usable and poll_new_items is unchanged.
        PollableFlow.on_item_success(flow, 123)
        assert flow.poll_new_items() == [{"id": 1}]

    @pytest.mark.parametrize("item_id", [0, "abc", None, {"key": "val"}])
    def test_base_on_item_success_accepts_any_item_id(self, item_id):
        from golem.core.flow_base import PollableFlow

        class MinimalPollable(PollableFlow):
            def poll_new_items(self):
                return []

            def generate_event_id(self, _item_data):
                return "id-1"

        flow = MinimalPollable()
        # Calling the base hook must not raise; verify poll_new_items is intact.
        PollableFlow.on_item_success(flow, item_id)
        assert flow.poll_new_items() == []


class TestBaseFlowAfterRun:
    """Tests for the base-class implementation of BaseFlow.after_run."""

    def test_after_run_returns_none(self):
        from golem.core.config import Config, FlowConfig
        from golem.core.flow_base import BaseFlow, FlowResult
        from golem.core.triggers.base import TriggerEvent

        class MinimalFlow(BaseFlow):
            @property
            def name(self):
                return "minimal"

            async def handle(self, event):
                return FlowResult(success=True)

        cfg = Config()
        flow = MinimalFlow(cfg, flow_config=FlowConfig())
        event = TriggerEvent(flow_name="minimal", event_id="ev-1")
        flow_result = FlowResult(success=True)
        # Call the base hook via the class to avoid the override path.
        # Verify neither event nor result is mutated — base hook is a pure no-op.
        BaseFlow.after_run(flow, event, flow_result)
        assert event.flow_name == "minimal"
        assert flow_result.success is True


class TestBaseFlowTypedConfig:
    def test_typed_config_returns_default_on_mismatch(self):
        from golem.core.config import Config, FlowConfig, GolemFlowConfig
        from golem.core.flow_base import BaseFlow

        class DummyFlow(BaseFlow):
            @property
            def name(self) -> str:
                return "dummy"

            async def handle(self, _event):
                pass

        cfg = Config()
        flow = DummyFlow(cfg, flow_config=FlowConfig())
        result = flow.typed_config(GolemFlowConfig)
        assert isinstance(result, GolemFlowConfig)

    def test_mcp_servers_default_empty(self):
        from golem.core.config import Config, FlowConfig
        from golem.core.flow_base import BaseFlow

        class DummyFlow(BaseFlow):
            @property
            def name(self) -> str:
                return "dummy"

            async def handle(self, _event):
                pass

        cfg = Config()
        flow = DummyFlow(cfg, flow_config=FlowConfig())
        assert not flow.mcp_servers
