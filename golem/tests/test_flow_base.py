# pylint: disable=too-few-public-methods
"""Tests for golem.core.flow_base — trace/prompt helpers and FlowResult."""

from unittest.mock import patch

from golem.core.flow_base import FlowResult, _write_prompt, _write_trace


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


class TestBaseFlowTypedConfig:
    def test_typed_config_returns_default_on_mismatch(self):
        from golem.core.config import Config, FlowConfig, GolemFlowConfig
        from golem.core.flow_base import BaseFlow

        class DummyFlow(BaseFlow):
            @property
            def name(self) -> str:
                return "dummy"

            async def handle(self, event):
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

            async def handle(self, event):
                pass

        cfg = Config()
        flow = DummyFlow(cfg, flow_config=FlowConfig())
        assert not flow.mcp_servers
