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
        "Which issue tracker to poll for tasks",
        choices=["github", "redmine", "local", "mcp"],
    ),
    "golem.projects": FieldMeta(
        "profile", "list", "Projects to watch, e.g. owner/repo for GitHub"
    ),
    "golem.detection_tag": FieldMeta(
        "profile", "str", "Label or tag that marks an issue for Golem pickup"
    ),
    "golem.default_work_dir": FieldMeta(
        "profile", "str", "Working directory where Golem clones and builds"
    ),
    # -- budget --
    "golem.budget_per_task_usd": FieldMeta(
        "budget",
        "float",
        "Max USD to spend on a single task before aborting (0 = unlimited)",
        min_val=0.0,
    ),
    "claude.max_budget_usd": FieldMeta(
        "budget",
        "float",
        "Hard cap on total Claude API spend across all tasks (0 = unlimited)",
        min_val=0.0,
    ),
    # -- models --
    "golem.task_model": FieldMeta(
        "models",
        "choice",
        "Claude model used for the main build agent",
        choices=["opus", "sonnet", "haiku"],
    ),
    "golem.validation_model": FieldMeta(
        "models",
        "choice",
        "Claude model for spec-compliance validation",
        choices=["opus", "sonnet", "haiku"],
    ),
    "golem.orchestrate_model": FieldMeta(
        "models",
        "choice",
        "Claude model for the orchestrator that plans task phases",
        choices=["opus", "sonnet", "haiku"],
    ),
    "claude.model": FieldMeta(
        "models",
        "choice",
        "Default model when no task-specific override is set",
        choices=["opus", "sonnet", "haiku"],
    ),
    # -- heartbeat --
    "golem.heartbeat_enabled": FieldMeta(
        "heartbeat",
        "bool",
        "Auto-discover improvements when idle (code smells, test gaps, etc.)",
    ),
    "golem.heartbeat_interval_seconds": FieldMeta(
        "heartbeat",
        "int",
        "How often the heartbeat scanner looks for candidates",
        min_val=1,
    ),
    "golem.heartbeat_idle_threshold_seconds": FieldMeta(
        "heartbeat",
        "int",
        "Seconds of inactivity before heartbeat tasks start spawning",
        min_val=1,
    ),
    "golem.heartbeat_daily_budget_usd": FieldMeta(
        "heartbeat",
        "float",
        "Max daily spend on heartbeat-discovered tasks",
        min_val=0.0,
    ),
    "golem.heartbeat_max_inflight": FieldMeta(
        "heartbeat",
        "int",
        "Max heartbeat tasks running in parallel",
        min_val=1,
    ),
    "golem.heartbeat_candidate_limit": FieldMeta(
        "heartbeat",
        "int",
        "Max improvement candidates evaluated per scan",
        min_val=1,
    ),
    "golem.heartbeat_dedup_ttl_days": FieldMeta(
        "heartbeat",
        "int",
        "Days before a previously attempted improvement can be retried",
        min_val=1,
    ),
    # -- self_update --
    "golem.self_update_enabled": FieldMeta(
        "self_update",
        "bool",
        "Watch for upstream changes and auto-restart on new commits",
    ),
    "golem.self_update_branch": FieldMeta(
        "self_update", "str", "Git branch to track for updates (e.g. main, master)"
    ),
    "golem.self_update_interval_seconds": FieldMeta(
        "self_update",
        "int",
        "How often to check the remote branch for new commits",
        min_val=60,
    ),
    "golem.self_update_strategy": FieldMeta(
        "self_update",
        "choice",
        "merged_only = only merged PRs; any_commit = any new commit triggers update",
        choices=["merged_only", "any_commit"],
    ),
    # -- integrations --
    "slack.enabled": FieldMeta(
        "integrations", "bool", "Post task results and alerts to Slack channels"
    ),
    "slack.webhooks": FieldMeta(
        "integrations",
        "str",
        'JSON map of channel name to webhook URL, e.g. {"#dev": "https://..."}',
    ),
    "teams.enabled": FieldMeta(
        "integrations", "bool", "Post task results and alerts to Microsoft Teams"
    ),
    "teams.webhooks": FieldMeta(
        "integrations",
        "str",
        'JSON map of channel name to webhook URL, e.g. {"General": "https://..."}',
    ),
    "webhook.enabled": FieldMeta(
        "integrations",
        "bool",
        "Listen for inbound webhooks to trigger tasks",
    ),
    "webhook.port": FieldMeta(
        "integrations",
        "int",
        "TCP port for the inbound webhook listener",
        min_val=1,
        max_val=65535,
    ),
    "webhook.secret": FieldMeta(
        "integrations",
        "str",
        "HMAC secret for webhook signature verification",
        sensitive=True,
    ),
    # -- dashboard --
    "dashboard.port": FieldMeta(
        "dashboard", "int", "TCP port for this web dashboard", min_val=1, max_val=65535
    ),
    "dashboard.admin_token": FieldMeta(
        "dashboard",
        "str",
        "Bearer token required for config changes (empty = open access)",
        sensitive=True,
    ),
    "dashboard.api_key": FieldMeta(
        "dashboard",
        "str",
        "API key for external dashboard integrations",
        sensitive=True,
    ),
    # -- daemon --
    "daemon.drain_timeout_seconds": FieldMeta(
        "daemon",
        "int",
        "Seconds to wait for running tasks to finish during a graceful reload",
        min_val=1,
    ),
    # -- logging --
    "logging.log_file": FieldMeta(
        "logging", "str", "Path to the daemon log file (empty = stderr only)"
    ),
    "logging.log_level": FieldMeta(
        "logging",
        "choice",
        "Minimum severity to log; DEBUG is verbose, ERROR is quiet",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    ),
    "logging.store_agent_traces": FieldMeta(
        "logging",
        "bool",
        "Save full Claude agent traces to disk for later inspection",
    ),
    "logging.store_thinking": FieldMeta(
        "logging",
        "bool",
        "Include extended thinking blocks in stored traces",
    ),
    # -- health --
    "health.check_interval_seconds": FieldMeta(
        "health",
        "int",
        "Seconds between internal health checks (error rate, queue depth)",
        min_val=1,
    ),
    "health.error_rate_threshold": FieldMeta(
        "health",
        "float",
        "Fraction of failed tasks (0.0\u20131.0) that triggers a health alert",
        min_val=0.0,
        max_val=1.0,
    ),
    "health.queue_depth_threshold": FieldMeta(
        "health",
        "int",
        "Pending task count that triggers a queue-depth alert",
        min_val=1,
    ),
    # -- polling --
    "polling.error_threshold": FieldMeta(
        "polling",
        "int",
        "Consecutive poll failures before exponential backoff kicks in",
        min_val=1,
    ),
    "polling.max_backoff_seconds": FieldMeta(
        "polling",
        "int",
        "Maximum delay between poll retries during backoff",
        min_val=1,
    ),
    "polling.recent_items_limit": FieldMeta(
        "polling",
        "int",
        "How many recent issues to fetch per poll cycle",
        min_val=1,
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
        except OSError as exc:
            logger.debug("Failed to clean up temp file: %s", exc)
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
