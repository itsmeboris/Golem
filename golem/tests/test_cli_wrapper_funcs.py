# pylint: disable=too-few-public-methods,implicit-str-concat
"""Tests for pure functions in golem.core.cli_wrapper."""

from golem.core.cli_wrapper import (
    CLIConfig,
    CLIError,
    CLIResult,
    CLIType,
    _build_agent_command,
    _build_claude_command,
    _build_command,
    _clean_env,
    _extract_error_from_stream_output,
    _extract_metrics,
    _parse_stream_output,
    active_process_count,
)


class TestCLIConfig:
    def test_defaults(self):
        c = CLIConfig()
        assert c.cli_type == CLIType.AGENT
        assert c.model == "sonnet"
        assert c.timeout_seconds == 300
        assert not c.mcp_servers

    def test_custom(self):
        c = CLIConfig(cli_type=CLIType.CLAUDE, model="opus", max_budget_usd=5.0)
        assert c.cli_type == CLIType.CLAUDE
        assert c.model == "opus"
        assert c.max_budget_usd == 5.0


class TestCLIResult:
    def test_defaults(self):
        r = CLIResult()
        assert not r.output
        assert r.cost_usd == 0.0
        assert not r.trace_events


class TestCLIError:
    def test_basic(self):
        err = CLIError("failed", returncode=2)
        assert "failed" in str(err)
        assert err.returncode == 2

    def test_with_stderr(self):
        err = CLIError("failed", stderr="some error output")
        assert "some error output" in str(err)


class TestCLIType:
    def test_values(self):
        assert CLIType.AGENT.value == "agent"
        assert CLIType.CLAUDE.value == "claude"


class TestBuildAgentCommand:
    def test_basic(self):
        config = CLIConfig(cli_type=CLIType.AGENT, model="sonnet")
        cmd = _build_agent_command(config)
        assert cmd[0] == "agent"
        assert "--model" in cmd
        assert "sonnet" in cmd
        assert "-p" in cmd
        assert "--trust" in cmd

    def test_stream_json(self):
        config = CLIConfig(cli_type=CLIType.AGENT, model="sonnet")
        cmd = _build_agent_command(config, "stream-json")
        assert "--stream-partial-output" in cmd


class TestBuildClaudeCommand:
    def test_basic(self):
        config = CLIConfig(cli_type=CLIType.CLAUDE, model="opus")
        cmd = _build_claude_command(config)
        assert cmd[0] == "claude"
        assert "opus" in cmd
        assert "--dangerously-skip-permissions" in cmd

    def test_with_budget(self):
        config = CLIConfig(cli_type=CLIType.CLAUDE, max_budget_usd=2.0)
        cmd = _build_claude_command(config)
        assert "--max-budget-usd" in cmd
        assert "2.0" in cmd

    def test_without_budget(self):
        config = CLIConfig(cli_type=CLIType.CLAUDE, max_budget_usd=0)
        cmd = _build_claude_command(config)
        assert "--max-budget-usd" not in cmd

    def test_with_system_prompt(self):
        config = CLIConfig(cli_type=CLIType.CLAUDE, system_prompt="Be helpful")
        cmd = _build_claude_command(config)
        assert "--append-system-prompt" in cmd
        assert "Be helpful" in cmd

    def test_with_resume_session_id(self):
        config = CLIConfig(cli_type=CLIType.CLAUDE, resume_session_id="sess-123")
        cmd = _build_claude_command(config)
        assert "--resume" in cmd
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "sess-123"

    def test_without_resume_session_id(self):
        config = CLIConfig(cli_type=CLIType.CLAUDE, resume_session_id="")
        cmd = _build_claude_command(config)
        assert "--resume" not in cmd

    def test_stream_json_verbose(self):
        config = CLIConfig(cli_type=CLIType.CLAUDE)
        cmd = _build_claude_command(config, "stream-json")
        assert "--verbose" in cmd


class TestBuildCommand:
    def test_agent_type(self):
        config = CLIConfig(cli_type=CLIType.AGENT)
        cmd = _build_command(config)
        assert cmd[0] == "agent"

    def test_claude_type(self):
        config = CLIConfig(cli_type=CLIType.CLAUDE)
        cmd = _build_command(config)
        assert cmd[0] == "claude"


class TestCleanEnv:
    def test_strips_nesting_guards(self):
        env = {
            "PATH": "/usr/bin",
            "CLAUDECODE": "1",
            "CLAUDE_CODE_SESSION": "abc",
            "CLAUDE_API_KEY": "secret",
            "HOME": "/home/user",
        }
        cleaned = _clean_env(env)
        assert "CLAUDECODE" not in cleaned
        assert "CLAUDE_CODE_SESSION" not in cleaned
        assert "CLAUDE_API_KEY" in cleaned
        assert "PATH" in cleaned

    def test_no_nesting_vars(self):
        env = {"PATH": "/usr/bin", "HOME": "/home"}
        cleaned = _clean_env(env)
        assert cleaned == env

    def test_uses_os_environ_default(self):
        cleaned = _clean_env()
        assert "PATH" in cleaned


class TestExtractMetrics:
    def test_agent_format(self):
        data = {
            "cost_usd": 0.50,
            "input_tokens": 1000,
            "output_tokens": 500,
            "duration_ms": 5000,
        }
        m = _extract_metrics(data)
        assert m["cost_usd"] == 0.50
        assert m["input_tokens"] == 1000
        assert m["output_tokens"] == 500
        assert m["duration_ms"] == 5000

    def test_claude_format(self):
        data = {
            "total_cost_usd": 1.25,
            "usage": {"input_tokens": 2000, "output_tokens": 800},
        }
        m = _extract_metrics(data)
        assert m["cost_usd"] == 1.25
        assert m["input_tokens"] == 2000
        assert m["output_tokens"] == 800

    def test_empty(self):
        m = _extract_metrics({})
        assert m["cost_usd"] == 0
        assert m["input_tokens"] == 0


class TestParseStreamOutput:
    def test_jsonl_with_result(self):
        stdout = (
            '{"type": "system", "subtype": "init"}\n'
            '{"type": "assistant", "content": "hello"}\n'
            '{"type": "result", "cost_usd": 0.1, "result": "done"}\n'
        )
        data, traces = _parse_stream_output(stdout)
        assert data["type"] == "result"
        assert len(traces) == 3

    def test_single_json(self):
        stdout = '{"result": "ok", "cost_usd": 0.5}'
        data, _traces = _parse_stream_output(stdout)
        assert data["result"] == "ok"

    def test_invalid_json_fallback(self):
        stdout = "not json at all"
        data, _traces = _parse_stream_output(stdout)
        assert data["parse_error"] is True

    def test_mixed_lines(self):
        stdout = "not json\n" '{"type": "result", "cost_usd": 0.1}\n' "also not json\n"
        data, traces = _parse_stream_output(stdout)
        assert data["type"] == "result"
        assert len(traces) == 1


class TestExtractErrorFromStreamOutput:
    def test_filters_init_events(self):
        stdout = (
            '{"type": "system", "subtype": "init", "tools": ["huge", "list"]}\n'
            "Error: something bad happened\n"
        )
        result = _extract_error_from_stream_output(stdout, "")
        assert "something bad" in result
        assert "huge" not in result

    def test_includes_stderr(self):
        result = _extract_error_from_stream_output("", "stderr error")
        assert "stderr error" in result

    def test_empty_output(self):
        result = _extract_error_from_stream_output("", "")
        assert "no error details" in result

    def test_preserves_long_output(self):
        stdout = "x" * 5000
        result = _extract_error_from_stream_output(stdout, "")
        assert len(result) == 5000


class TestActiveProcessCount:
    def test_initial_zero(self):
        assert active_process_count() >= 0
