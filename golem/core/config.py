"""YAML configuration loader with environment variable expansion."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .cli_wrapper import CLIType

logger: logging.Logger = logging.getLogger("golem.core.config")

# .parent chain: core/ → golem/ (the package itself)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / os.environ.get("GOLEM_DATA_DIR", "data")


@dataclass
class FlowConfig:
    """Base configuration shared by all flows."""

    enabled: bool = True
    poll_interval: int = 300
    use_polling: bool = True
    model: str = ""
    mcp_servers: list[str] = field(default_factory=list)
    mcp_enabled: bool = True
    http_timeout: int = 30
    fetch_limit: int = 25
    max_tracked_items: int = 500


@dataclass
class GolemFlowConfig(FlowConfig):
    """Golem orchestration settings."""

    projects: list[str] = field(default_factory=list)
    tick_interval: int = 30
    grace_period_seconds: int = 120
    budget_per_task_usd: float = 10.0
    max_active_sessions: int = 3
    detection_tag: str = "[AGENT]"
    default_work_dir: str = ""
    work_dirs: dict[str, str] = field(default_factory=dict)
    task_model: str = "sonnet"
    task_timeout_seconds: int = 1800
    progress_interval_seconds: int = 60
    # v2 validation & commit
    validation_model: str = "opus"
    validation_budget_usd: float = 0.50
    validation_timeout_seconds: int = 120
    retry_budget_usd: float = 5.0
    max_retries: int = 1
    auto_commit: bool = True
    # Supervisor mode (per-subtask settings)
    supervisor_mode: bool = True
    subtask_model: str = ""
    subtask_budget_usd: float = 5.0
    subtask_timeout_seconds: int = 900
    decompose_model: str = ""
    decompose_budget_usd: float = 1.0
    summarize_model: str = "haiku"
    summarize_budget_usd: float = 0.50
    max_subtask_retries: int = 1
    # Worktree isolation (prevents concurrent sessions from conflicting)
    use_worktrees: bool = True
    # Skip per-subtask validation in supervisor mode (validate only at end)
    skip_subtask_validation: bool = True
    # Profile system: selects which backends (task source, state, notifier, tools, prompts)
    profile: str = "redmine"
    profile_config: dict = field(default_factory=dict)
    prompts_dir: str = ""
    # Infrastructure resilience
    max_infra_retries: int = 2


@dataclass
class ClaudeConfig:
    """Claude CLI invocation settings (model, budget, timeout, concurrency)."""

    cli_type: CLIType = CLIType.AGENT
    model: str = "sonnet"
    max_budget_usd: float = 0.0
    timeout_seconds: int = 300
    max_concurrent: int = 5
    system_prompt: str = ""


@dataclass
class DaemonConfig:
    """Daemon startup and CLI-level timeout/fallback settings."""

    health_check_timeout: int = 3
    startup_max_iterations: int = 30
    startup_poll_seconds: float = 0.5
    http_submit_timeout: int = 10
    fallback_budget_usd: float = 10.0
    fallback_task_timeout_seconds: int = 1800


@dataclass
class DashboardConfig:
    """Standalone dashboard server settings."""

    port: int = 8081
    admin_token: str = ""  # empty = admin features disabled


@dataclass
class WebhookConfig:
    """Webhook server settings (port, HMAC secret)."""

    enabled: bool = False
    port: int = 8080
    secret: str = ""


@dataclass
class LoggingConfig:
    """Logging settings (file output, rotation, agent traces)."""

    log_file: str = ""
    log_level: str = "INFO"
    max_bytes: int = 10_485_760
    backup_count: int = 5
    store_agent_traces: bool = True
    store_thinking: bool = False


@dataclass
class PollingConfig:
    """Polling daemon behaviour — error backoff and item history."""

    error_threshold: int = 5
    max_backoff_seconds: int = 3600
    recent_items_limit: int = 100


@dataclass
class TeamsConfig:
    """Teams incoming webhook integration settings."""

    enabled: bool = False
    webhooks: dict[str, str] = field(default_factory=dict)


@dataclass
class SlackConfig:
    """Slack incoming webhook integration settings."""

    enabled: bool = False
    webhooks: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    """Top-level application configuration."""

    golem: GolemFlowConfig = field(default_factory=GolemFlowConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    webhook: WebhookConfig = field(default_factory=WebhookConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    polling: PollingConfig = field(default_factory=PollingConfig)
    teams: TeamsConfig = field(default_factory=TeamsConfig)
    slack: SlackConfig = field(default_factory=SlackConfig)
    dry_run: bool = False

    def get_flow_config(self, flow_name: str) -> FlowConfig | None:
        """Return the FlowConfig for *flow_name*, or None."""
        cfg = getattr(self, flow_name, None)
        return cfg if isinstance(cfg, FlowConfig) else None


def _expand_env_vars(value: Any) -> Any:
    if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
        env_var = value[2:-1]
        return os.environ.get(env_var, "")
    if isinstance(value, dict):
        return {k: _expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_vars(item) for item in value]
    return value


def _parse_flow_common(
    data: dict[str, Any], defaults: dict[str, Any]
) -> dict[str, Any]:
    """Extract fields shared by all FlowConfig subclasses."""
    return {
        "enabled": data.get("enabled", defaults.get("enabled", True)),
        "poll_interval": data.get("poll_interval", defaults.get("poll_interval", 300)),
        "use_polling": data.get("use_polling", True),
        "model": data.get("model", ""),
        "mcp_servers": data.get("mcp_servers", []),
        "mcp_enabled": data.get("mcp_enabled", True),
        "http_timeout": data.get("http_timeout", 30),
        "fetch_limit": data.get("fetch_limit", 25),
        "max_tracked_items": data.get("max_tracked_items", 500),
    }


def _parse_golem_config(data: dict[str, Any]) -> GolemFlowConfig:
    common = _parse_flow_common(data, {"poll_interval": 120})
    return GolemFlowConfig(
        **common,
        projects=data.get("projects", []),
        tick_interval=data.get("tick_interval", 30),
        grace_period_seconds=data.get("grace_period_seconds", 120),
        budget_per_task_usd=data.get("budget_per_task_usd", 10.0),
        max_active_sessions=data.get("max_active_sessions", 3),
        detection_tag=data.get("detection_tag", "[AGENT]"),
        default_work_dir=data.get("default_work_dir", ""),
        work_dirs=data.get("work_dirs", {}),
        task_model=data.get("task_model", "sonnet"),
        task_timeout_seconds=data.get("task_timeout_seconds", 1800),
        progress_interval_seconds=data.get("progress_interval_seconds", 60),
        # v2 validation & commit
        validation_model=data.get("validation_model", "opus"),
        validation_budget_usd=data.get("validation_budget_usd", 0.50),
        validation_timeout_seconds=data.get("validation_timeout_seconds", 120),
        retry_budget_usd=data.get("retry_budget_usd", 5.0),
        max_retries=data.get("max_retries", 1),
        auto_commit=data.get("auto_commit", True),
        # Supervisor mode
        supervisor_mode=data.get("supervisor_mode", True),
        subtask_model=data.get("subtask_model", ""),
        subtask_budget_usd=data.get("subtask_budget_usd", 5.0),
        subtask_timeout_seconds=data.get("subtask_timeout_seconds", 900),
        decompose_model=data.get("decompose_model", ""),
        decompose_budget_usd=data.get("decompose_budget_usd", 1.0),
        summarize_model=data.get("summarize_model", "haiku"),
        summarize_budget_usd=data.get("summarize_budget_usd", 0.50),
        max_subtask_retries=data.get("max_subtask_retries", 1),
        use_worktrees=data.get("use_worktrees", True),
        skip_subtask_validation=data.get("skip_subtask_validation", True),
        profile=data.get("profile", "redmine"),
        profile_config=data.get("profile_config", {}),
        prompts_dir=data.get("prompts_dir", ""),
        max_infra_retries=data.get("max_infra_retries", 2),
    )


def _load_system_prompt(data: dict[str, Any]) -> str:
    if file_path := data.get("system_prompt_file"):
        resolved = (PROJECT_ROOT / file_path).resolve()
        return resolved.read_text(encoding="utf-8")
    return data.get("system_prompt", "")


def _parse_claude_config(data: dict[str, Any]) -> ClaudeConfig:
    cli_type = CLIType.AGENT
    if cli_type_str := data.get("cli_type"):
        cli_type = CLIType(cli_type_str)
    return ClaudeConfig(
        cli_type=cli_type,
        model=data.get("model", "sonnet"),
        max_budget_usd=data.get("max_budget_usd", 0.0),
        timeout_seconds=data.get("timeout_seconds", 300),
        max_concurrent=data.get("max_concurrent", 5),
        system_prompt=_load_system_prompt(data),
    )


def _parse_daemon_config(data: dict[str, Any]) -> DaemonConfig:
    return DaemonConfig(
        health_check_timeout=data.get("health_check_timeout", 3),
        startup_max_iterations=data.get("startup_max_iterations", 30),
        startup_poll_seconds=data.get("startup_poll_seconds", 0.5),
        http_submit_timeout=data.get("http_submit_timeout", 10),
        fallback_budget_usd=data.get("fallback_budget_usd", 10.0),
        fallback_task_timeout_seconds=data.get("fallback_task_timeout_seconds", 1800),
    )


def _parse_dashboard_config(data: dict[str, Any]) -> DashboardConfig:
    return DashboardConfig(
        port=data.get("port", 8081),
        admin_token=data.get("admin_token", ""),
    )


def _parse_webhook_config(data: dict[str, Any]) -> WebhookConfig:
    return WebhookConfig(
        enabled=data.get("enabled", False),
        port=data.get("port", 8080),
        secret=data.get("secret", ""),
    )


def _parse_logging_config(data: dict[str, Any]) -> LoggingConfig:
    return LoggingConfig(
        log_file=data.get("log_file", ""),
        log_level=data.get("log_level", "INFO"),
        max_bytes=data.get("max_bytes", 10_485_760),
        backup_count=data.get("backup_count", 5),
        store_agent_traces=data.get("store_agent_traces", True),
        store_thinking=data.get("store_thinking", False),
    )


def _parse_polling_config(data: dict[str, Any]) -> PollingConfig:
    return PollingConfig(
        error_threshold=data.get("error_threshold", 5),
        max_backoff_seconds=data.get("max_backoff_seconds", 3600),
        recent_items_limit=data.get("recent_items_limit", 100),
    )


def _parse_teams_config(data: dict[str, Any]) -> TeamsConfig:
    return TeamsConfig(
        enabled=data.get("enabled", False),
        webhooks=data.get("webhooks", {}),
    )


def _parse_slack_config(data: dict[str, Any]) -> SlackConfig:
    return SlackConfig(
        enabled=data.get("enabled", False),
        webhooks=data.get("webhooks", {}),
    )


def _find_config_path(config_path: str | Path | None) -> Path | None:
    if config_path is not None:
        return Path(config_path)

    default_paths = [
        Path.cwd() / "config.yaml",
        Path.cwd() / "config.yml",
        Path(__file__).resolve().parent.parent / "config.yaml",
    ]
    for path in default_paths:
        if path.exists():
            return path
    return None


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration from YAML file with .env and ${VAR} expansion."""
    project_root = Path(__file__).resolve().parent.parent
    for env_file in (".env", ".mcp.env"):
        path = project_root / env_file
        try:
            load_dotenv(path)
        except PermissionError:
            logger.warning(
                "Cannot read %s (permission denied) — env vars from this file will be missing",
                path,
            )

    resolved_path = _find_config_path(config_path)
    if resolved_path is None or not resolved_path.exists():
        return Config()

    with open(
        resolved_path, encoding="utf-8"
    ) as config_file:  # pylint: disable=unspecified-encoding
        raw_config = yaml.safe_load(config_file) or {}

    raw_config = _expand_env_vars(raw_config)
    flows_data = raw_config.get("flows", {})

    return Config(
        golem=_parse_golem_config(flows_data.get("golem", {})),
        claude=_parse_claude_config(raw_config.get("claude", {})),
        daemon=_parse_daemon_config(raw_config.get("daemon", {})),
        dashboard=_parse_dashboard_config(raw_config.get("dashboard", {})),
        webhook=_parse_webhook_config(raw_config.get("webhook", {})),
        logging=_parse_logging_config(raw_config.get("logging", {})),
        polling=_parse_polling_config(raw_config.get("polling", {})),
        teams=_parse_teams_config(raw_config.get("teams", {})),
        slack=_parse_slack_config(raw_config.get("slack", {})),
    )


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

KNOWN_MODELS: frozenset[str] = frozenset({"sonnet", "opus", "haiku"})


def _is_valid_model(model: str) -> bool:
    """Return True if *model* is empty or starts with a known base name."""
    if not model:
        return True
    return any(model == base or model.startswith(f"{base}-") for base in KNOWN_MODELS)


def validate_flow_config(name: str, flow_config: FlowConfig) -> list[str]:
    """Validate a single flow configuration, returning a list of error strings."""
    errors: list[str] = []

    if not isinstance(flow_config.poll_interval, int) or flow_config.poll_interval <= 0:
        errors.append(
            f"{name}: poll_interval must be a positive integer, got {flow_config.poll_interval!r}"
        )

    if flow_config.model and not _is_valid_model(flow_config.model):
        errors.append(
            f"{name}: unknown model {flow_config.model!r}, expected one of {sorted(KNOWN_MODELS)}"
        )

    if hasattr(flow_config, "projects") and not flow_config.projects:
        errors.append(f"{name}: projects list must not be empty")

    return errors


def validate_config(config: Config) -> list[str]:
    """Validate the full Config, returning a list of error strings."""
    errors: list[str] = []

    # Validate golem if enabled
    if config.golem.enabled:
        errors.extend(validate_flow_config("golem", config.golem))

    # Claude config validation
    if not _is_valid_model(config.claude.model):
        errors.append(
            f"claude.model: unknown model {config.claude.model!r}, "
            f"expected one of {sorted(KNOWN_MODELS)}"
        )

    timeout = config.claude.timeout_seconds
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        errors.append(f"claude.timeout_seconds must be positive, got {timeout!r}")

    # Port validation
    if not 1 <= config.dashboard.port <= 65535:
        errors.append(f"dashboard.port must be 1-65535, got {config.dashboard.port}")

    if not 1 <= config.webhook.port <= 65535:
        errors.append(f"webhook.port must be 1-65535, got {config.webhook.port}")

    return errors
