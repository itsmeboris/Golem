"""Shared backend for config viewing and editing.

Both the CLI (``golem config``) and the dashboard Config tab call into
this module.  It owns the field registry, validation, atomic YAML
writes, and daemon reload signalling.
"""

from __future__ import annotations

import logging
import os
import signal
import tempfile
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from golem.core.config import (
    ClaudeConfig,
    Config,
    DaemonConfig,
    DashboardConfig,
    GolemFlowConfig,
    HealthConfig,
    LoggingConfig,
    PollingConfig,
    SlackConfig,
    TeamsConfig,
    WebhookConfig,
    load_config,
    validate_config,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldMeta:
    """Declarative metadata for a single config field."""

    category: str
    field_type: str  # "choice" | "bool" | "int" | "float" | "str" | "list"
    description: str
    choices: list[str] | None = None
    min_val: float | None = None
    max_val: float | None = None
    sensitive: bool = False


@dataclass
class FieldInfo:
    """A config field's current value plus its registry metadata."""

    key: str  # dotted path, e.g. "golem.task_model"
    value: Any
    meta: FieldMeta


# ---------------------------------------------------------------------------
# Field registry — maps dotted config paths to metadata
# ---------------------------------------------------------------------------

FIELD_REGISTRY: dict[str, FieldMeta] = {
    # -- profile --
    "golem.profile": FieldMeta(
        "profile",
        "choice",
        "Issue tracker backend",
        choices=["github", "redmine", "local", "mcp"],
    ),
    "golem.projects": FieldMeta("profile", "list", "Comma-separated project list"),
    "golem.detection_tag": FieldMeta("profile", "str", "Detection tag for issues"),
    "golem.default_work_dir": FieldMeta("profile", "str", "Default working directory"),
    # -- budget --
    "golem.budget_per_task_usd": FieldMeta(
        "budget", "float", "Max spend per task", min_val=0.0
    ),
    "claude.max_budget_usd": FieldMeta(
        "budget", "float", "Global Claude budget cap", min_val=0.0
    ),
    # -- models --
    "golem.task_model": FieldMeta(
        "models",
        "choice",
        "Model for task execution",
        choices=["opus", "sonnet", "haiku"],
    ),
    "golem.validation_model": FieldMeta(
        "models",
        "choice",
        "Model for validation",
        choices=["opus", "sonnet", "haiku"],
    ),
    "golem.orchestrate_model": FieldMeta(
        "models",
        "choice",
        "Model for orchestration",
        choices=["opus", "sonnet", "haiku"],
    ),
    "claude.model": FieldMeta(
        "models",
        "choice",
        "Default Claude model",
        choices=["opus", "sonnet", "haiku"],
    ),
    # -- heartbeat --
    "golem.heartbeat_enabled": FieldMeta(
        "heartbeat", "bool", "Enable heartbeat discovery"
    ),
    "golem.heartbeat_interval_seconds": FieldMeta(
        "heartbeat", "int", "Seconds between heartbeat scans", min_val=1
    ),
    "golem.heartbeat_idle_threshold_seconds": FieldMeta(
        "heartbeat", "int", "Idle seconds before heartbeat activates", min_val=1
    ),
    "golem.heartbeat_daily_budget_usd": FieldMeta(
        "heartbeat", "float", "Daily heartbeat budget", min_val=0.0
    ),
    "golem.heartbeat_max_inflight": FieldMeta(
        "heartbeat", "int", "Max concurrent heartbeat tasks", min_val=1
    ),
    "golem.heartbeat_candidate_limit": FieldMeta(
        "heartbeat", "int", "Max candidates per scan", min_val=1
    ),
    "golem.heartbeat_dedup_ttl_days": FieldMeta(
        "heartbeat", "int", "Dedup TTL in days", min_val=1
    ),
    # -- self_update --
    "golem.self_update_enabled": FieldMeta(
        "self_update", "bool", "Enable self-update monitoring"
    ),
    "golem.self_update_branch": FieldMeta(
        "self_update", "str", "Remote branch to watch"
    ),
    "golem.self_update_interval_seconds": FieldMeta(
        "self_update", "int", "Poll frequency in seconds", min_val=60
    ),
    "golem.self_update_strategy": FieldMeta(
        "self_update",
        "choice",
        "Update strategy",
        choices=["merged_only", "any_commit"],
    ),
    # -- integrations --
    "slack.enabled": FieldMeta("integrations", "bool", "Enable Slack notifications"),
    "slack.webhooks": FieldMeta(
        "integrations", "str", "Slack webhooks (JSON dict of channel:url)"
    ),
    "teams.enabled": FieldMeta("integrations", "bool", "Enable Teams notifications"),
    "teams.webhooks": FieldMeta(
        "integrations", "str", "Teams webhooks (JSON dict of channel:url)"
    ),
    "webhook.enabled": FieldMeta(
        "integrations", "bool", "Enable webhook notifications"
    ),
    "webhook.port": FieldMeta(
        "integrations", "int", "Webhook listener port", min_val=1, max_val=65535
    ),
    "webhook.secret": FieldMeta(
        "integrations", "str", "Webhook secret", sensitive=True
    ),
    # -- dashboard --
    "dashboard.port": FieldMeta(
        "dashboard", "int", "Dashboard port", min_val=1, max_val=65535
    ),
    "dashboard.admin_token": FieldMeta(
        "dashboard", "str", "Admin token", sensitive=True
    ),
    "dashboard.api_key": FieldMeta("dashboard", "str", "API key", sensitive=True),
    # -- daemon --
    "daemon.drain_timeout_seconds": FieldMeta(
        "daemon", "int", "Drain timeout for reload", min_val=1
    ),
    # -- logging --
    "logging.log_file": FieldMeta("logging", "str", "Log file path"),
    "logging.log_level": FieldMeta(
        "logging",
        "choice",
        "Log level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    ),
    "logging.store_agent_traces": FieldMeta("logging", "bool", "Store agent traces"),
    "logging.store_thinking": FieldMeta("logging", "bool", "Store thinking blocks"),
    # -- health --
    "health.check_interval_seconds": FieldMeta(
        "health", "int", "Health check interval", min_val=1
    ),
    "health.error_rate_threshold": FieldMeta(
        "health", "float", "Error rate threshold", min_val=0.0, max_val=1.0
    ),
    "health.queue_depth_threshold": FieldMeta(
        "health", "int", "Queue depth threshold", min_val=1
    ),
    # -- polling --
    "polling.error_threshold": FieldMeta(
        "polling", "int", "Error threshold", min_val=1
    ),
    "polling.max_backoff_seconds": FieldMeta(
        "polling", "int", "Max backoff seconds", min_val=1
    ),
    "polling.recent_items_limit": FieldMeta(
        "polling", "int", "Recent items limit", min_val=1
    ),
}

# Map dotted-path section prefix to the Config attribute and dataclass.
_SECTION_MAP: dict[str, tuple[str, type]] = {
    "golem": ("golem", GolemFlowConfig),
    "claude": ("claude", ClaudeConfig),
    "daemon": ("daemon", DaemonConfig),
    "dashboard": ("dashboard", DashboardConfig),
    "webhook": ("webhook", WebhookConfig),
    "logging": ("logging", LoggingConfig),
    "health": ("health", HealthConfig),
    "polling": ("polling", PollingConfig),
    "slack": ("slack", SlackConfig),
    "teams": ("teams", TeamsConfig),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_config_by_category(config: Config) -> dict[str, list[FieldInfo]]:
    """Return current config values grouped by category.

    Each entry includes the field's current value, its dotted key path,
    and the full ``FieldMeta``.
    """
    result: dict[str, list[FieldInfo]] = {}
    for key, meta in FIELD_REGISTRY.items():
        section_prefix, field_name = key.split(".", 1)
        attr_name, _ = _SECTION_MAP[section_prefix]
        section_obj = getattr(config, attr_name)
        value = getattr(section_obj, field_name, None)
        result.setdefault(meta.category, []).append(
            FieldInfo(key=key, value=value, meta=meta)
        )
    return result


def _resolve_value(field_type: str, raw: Any) -> Any:
    """Coerce a raw value to the expected Python type."""
    if field_type == "bool":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str):
            return raw.lower() in ("true", "1", "yes", "on")
        return bool(raw)
    if field_type == "int":
        return int(raw)
    if field_type == "float":
        return float(raw)
    if field_type == "list":
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            return [s.strip() for s in raw.split(",") if s.strip()]
        return list(raw)
    return raw  # str, choice


def _validate_field(key: str, value: Any, meta: FieldMeta) -> list[str]:
    """Validate a single field value against its FieldMeta."""
    errors: list[str] = []
    if meta.choices is not None and value not in meta.choices:
        errors.append("%s must be one of %s, got %r" % (key, meta.choices, value))
    if meta.min_val is not None and value < meta.min_val:
        errors.append("%s must be >= %s, got %s" % (key, meta.min_val, value))
    if meta.max_val is not None and value > meta.max_val:
        errors.append("%s must be <= %s, got %s" % (key, meta.max_val, value))
    return errors


def update_config(config_path: Path, updates: dict[str, Any]) -> list[str]:
    """Validate *updates*, write YAML atomically, return errors (empty=OK).

    Validation is all-or-nothing: if any field fails, nothing is written.
    """
    # --- validate each field individually ---
    errors: list[str] = []
    resolved: dict[str, Any] = {}
    for key, raw in updates.items():
        meta = FIELD_REGISTRY.get(key)
        if meta is not None:
            # Registry field — full validation
            try:
                value = _resolve_value(meta.field_type, raw)
            except (ValueError, TypeError) as exc:
                errors.append("Invalid value for %s: %s" % (key, exc))
                continue
            field_errors = _validate_field(key, value, meta)
            errors.extend(field_errors)
            resolved[key] = value
        else:
            # Non-registry field — validate section prefix exists,
            # field exists on the dataclass, and type-coerce from string.
            section_prefix = key.split(".", 1)[0] if "." in key else ""
            if section_prefix not in _SECTION_MAP:
                errors.append("Unknown config section: %s" % key)
                continue
            _, dc_cls = _SECTION_MAP[section_prefix]
            field_name = key.split(".", 1)[1]
            if field_name not in {f.name for f in dataclasses.fields(dc_cls)}:
                errors.append("Unknown field %s on %s" % (field_name, dc_cls.__name__))
                continue
            resolved[key] = raw

    if errors:
        return errors

    # --- load current YAML, patch, write to temp, re-validate full config ---
    with open(config_path, "r", encoding="utf-8") as fh:
        raw_yaml = yaml.safe_load(fh) or {}

    for key, value in resolved.items():
        _set_yaml_value(raw_yaml, key, value)

    # Write patched YAML to temp file, then validate by loading it
    parent = config_path.parent
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".yaml.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(raw_yaml, fh, default_flow_style=False, sort_keys=False)
        patched_config = load_config(tmp_path)
        full_errors = validate_config(patched_config)
        if full_errors:
            os.unlink(tmp_path)
            return full_errors
        # Validation passed — atomic rename
        os.rename(tmp_path, config_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return []


def _set_yaml_value(raw_yaml: dict, dotted_key: str, value: Any) -> None:
    """Set a value in the raw YAML dict using a dotted config key.

    Translates dotted paths like ``golem.task_model`` into the YAML
    structure ``flows.golem.task_model``.
    """
    section_prefix, field_name = dotted_key.split(".", 1)
    if section_prefix == "golem":
        raw_yaml.setdefault("flows", {}).setdefault("golem", {})[field_name] = value
    else:
        raw_yaml.setdefault(section_prefix, {})[field_name] = value


def signal_daemon_reload(pid_file: Path) -> bool:
    """Send SIGHUP to the running daemon.  Returns True if sent."""
    if not pid_file.exists():
        logger.info("No PID file at %s — daemon not running", pid_file)
        return False
    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, OSError) as exc:
        logger.warning("Cannot read PID file %s: %s", pid_file, exc)
        return False
    try:
        os.kill(pid, signal.SIGHUP)
        logger.info("Sent SIGHUP to daemon PID %d", pid)
        return True
    except ProcessLookupError:
        logger.warning("Daemon PID %d not found (stale PID file)", pid)
        return False
    except OSError as exc:
        logger.warning("Failed to send SIGHUP to PID %d: %s", pid, exc)
        return False
