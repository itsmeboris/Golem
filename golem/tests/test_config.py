# pylint: disable=too-few-public-methods
"""Tests for golem.core.config."""
from unittest.mock import patch

from golem.core.config import (
    Config,
    ClaudeConfig,
    DaemonConfig,
    GolemFlowConfig,
    FlowConfig,
    _expand_env_vars,
    _find_config_path,
    _is_valid_model,
    _load_system_prompt,
    _parse_claude_config,
    _parse_daemon_config,
    load_config,
    validate_config,
    validate_flow_config,
)
from golem.core.cli_wrapper import CLIType


class TestExpandEnvVars:
    def test_expands_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_VAR", "hello")
        assert _expand_env_vars("${MY_TEST_VAR}") == "hello"

    def test_missing_env_var_returns_empty(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR_XYZ", raising=False)
        assert _expand_env_vars("${NONEXISTENT_VAR_XYZ}") == ""

    def test_plain_string_unchanged(self):
        assert _expand_env_vars("plain") == "plain"

    def test_dict_recursion(self, monkeypatch):
        monkeypatch.setenv("K", "val")
        result = _expand_env_vars({"key": "${K}"})
        assert result == {"key": "val"}

    def test_list_recursion(self, monkeypatch):
        monkeypatch.setenv("K", "val")
        result = _expand_env_vars(["${K}", "static"])
        assert result == ["val", "static"]


class TestLoadSystemPrompt:
    def test_loads_from_file(self, tmp_path):
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("System prompt content", encoding="utf-8")
        with patch("golem.core.config.PROJECT_ROOT", tmp_path):
            result = _load_system_prompt({"system_prompt_file": "prompt.txt"})
        assert result == "System prompt content"

    def test_returns_inline_prompt(self):
        result = _load_system_prompt({"system_prompt": "inline prompt"})
        assert result == "inline prompt"

    def test_returns_empty_when_neither(self):
        assert _load_system_prompt({}) == ""


class TestParseClaudeConfig:
    def test_with_cli_type(self):
        config = _parse_claude_config({"cli_type": "claude"})
        assert config.cli_type == CLIType.CLAUDE

    def test_default_cli_type(self):
        config = _parse_claude_config({})
        assert config.cli_type == CLIType.AGENT


class TestParseDaemonConfig:
    def test_defaults(self):
        config = _parse_daemon_config({})
        assert config.health_check_timeout == 3
        assert config.startup_max_iterations == 30
        assert config.startup_poll_seconds == 0.5
        assert config.http_submit_timeout == 10
        assert config.fallback_budget_usd == 10.0
        assert config.fallback_task_timeout_seconds == 1800

    def test_custom_values(self):
        config = _parse_daemon_config(
            {
                "health_check_timeout": 5,
                "startup_max_iterations": 60,
                "startup_poll_seconds": 1.0,
                "http_submit_timeout": 30,
                "fallback_budget_usd": 20.0,
                "fallback_task_timeout_seconds": 3600,
            }
        )
        assert config.health_check_timeout == 5
        assert config.startup_max_iterations == 60
        assert config.startup_poll_seconds == 1.0
        assert config.http_submit_timeout == 30
        assert config.fallback_budget_usd == 20.0
        assert config.fallback_task_timeout_seconds == 3600


class TestFindConfigPath:
    def test_explicit_path(self, tmp_path):
        p = tmp_path / "my.yaml"
        result = _find_config_path(p)
        assert result == p

    def test_finds_cwd_config(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.touch()
        monkeypatch.chdir(tmp_path)
        result = _find_config_path(None)
        assert result == config_file

    def test_finds_cwd_yml(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yml"
        config_file.touch()
        monkeypatch.chdir(tmp_path)
        result = _find_config_path(None)
        assert result == config_file

    def test_returns_none_when_nothing_found(self, tmp_path, monkeypatch):
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(empty)
        result = _find_config_path(None)
        if result is not None:
            assert not result.exists()


class TestLoadConfig:
    def test_returns_default_when_no_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with patch("golem.core.config._find_config_path", return_value=None):
            config = load_config()
        assert isinstance(config, Config)

    def test_returns_default_for_nonexistent_path(self, tmp_path):
        config = load_config(tmp_path / "nonexistent.yaml")
        assert isinstance(config, Config)

    def test_permission_error_on_dotenv(self, tmp_path, monkeypatch):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "flows:\n  golem:\n    projects: [test]\n", encoding="utf-8"
        )

        def mock_dotenv(path):
            raise PermissionError("no access")

        monkeypatch.setattr("golem.core.config.load_dotenv", mock_dotenv)
        config = load_config(config_file)
        assert isinstance(config, Config)

    def test_loads_full_config(self, tmp_path, monkeypatch):
        config_content = """\
flows:
  golem:
    projects:
      - test-project
    budget_per_task_usd: 5.0
claude:
  model: opus
  timeout_seconds: 600
daemon:
  health_check_timeout: 5
  http_submit_timeout: 20
dashboard:
  port: 9090
webhook:
  enabled: true
  port: 8888
logging:
  log_level: DEBUG
polling:
  error_threshold: 10
teams:
  enabled: false
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content, encoding="utf-8")
        monkeypatch.setattr("golem.core.config.load_dotenv", lambda p: None)
        config = load_config(config_file)
        assert config.golem.projects == ["test-project"]
        assert config.claude.model == "opus"
        assert config.daemon.health_check_timeout == 5
        assert config.daemon.http_submit_timeout == 20
        assert config.dashboard.port == 9090


class TestIsValidModel:
    def test_empty_is_valid(self):
        assert _is_valid_model("") is True

    def test_known_model(self):
        assert _is_valid_model("sonnet") is True

    def test_model_with_suffix(self):
        assert _is_valid_model("sonnet-3.5") is True

    def test_unknown_model(self):
        assert _is_valid_model("gpt-4") is False


class TestValidateFlowConfig:
    def test_valid_config(self):
        config = GolemFlowConfig(projects=["proj"], poll_interval=60)
        errors = validate_flow_config("golem", config)
        assert not errors

    def test_invalid_poll_interval(self):
        config = FlowConfig(poll_interval=-1)
        errors = validate_flow_config("test", config)
        assert any("poll_interval" in e for e in errors)

    def test_invalid_model(self):
        config = FlowConfig(model="unknown-model")
        errors = validate_flow_config("test", config)
        assert any("unknown model" in e for e in errors)

    def test_empty_projects(self):
        config = GolemFlowConfig(projects=[])
        errors = validate_flow_config("golem", config)
        assert any("projects" in e for e in errors)


class TestValidateConfig:
    def test_valid_config(self):
        config = Config(golem=GolemFlowConfig(projects=["p"], enabled=True))
        errors = validate_config(config)
        assert not errors

    def test_invalid_claude_model(self):
        config = Config(claude=ClaudeConfig(model="bad-model"))
        errors = validate_config(config)
        assert any("claude.model" in e for e in errors)

    def test_invalid_claude_timeout(self):
        config = Config(claude=ClaudeConfig(timeout_seconds=-1))
        errors = validate_config(config)
        assert any("timeout_seconds" in e for e in errors)

    def test_invalid_dashboard_port(self):
        from golem.core.config import DashboardConfig

        config = Config(dashboard=DashboardConfig(port=0))
        errors = validate_config(config)
        assert any("dashboard.port" in e for e in errors)

    def test_invalid_webhook_port(self):
        from golem.core.config import WebhookConfig

        config = Config(webhook=WebhookConfig(port=99999))
        errors = validate_config(config)
        assert any("webhook.port" in e for e in errors)

    def test_disabled_golem_skips_validation(self):
        config = Config(golem=GolemFlowConfig(enabled=False, projects=[]))
        errors = validate_config(config)
        assert not any("projects" in e for e in errors)
