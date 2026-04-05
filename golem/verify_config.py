# golem/verify_config.py
"""Per-repo verification configuration loader/saver.

Config is stored at {repo_root}/.golem/verify.yaml.
Schema version 1:

  version: 1
  detected_at: ISO-8601 UTC timestamp
  stack: [python, javascript, ...]
  commands:
    - role: format | lint | test | typecheck
      cmd: [executable, arg1, arg2, ...]
      source: auto-detected | ci-parsed | agent-discovered | user
      timeout: 120  # optional
  coverage_threshold: 80.0  # optional

Key functions:
  load_verify_config(repo_root) -> VerifyConfig | None
  save_verify_config(repo_root, config) -> None
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .types import VerifyCommandDict, VerifyConfigDict

logger = logging.getLogger("golem.verify_config")

_SCHEMA_VERSION = 1
_CONFIG_RELPATH = ".golem/verify.yaml"
_VALID_ROLES = frozenset({"format", "lint", "test", "typecheck"})
_VALID_SOURCES = frozenset({"auto-detected", "ci-parsed", "agent-discovered", "user"})


@dataclass
class VerifyCommand:
    """A single verification command."""

    role: str
    cmd: list[str] = field(default_factory=list)
    source: str = "auto-detected"
    timeout: int | None = None

    def to_dict(self) -> VerifyCommandDict:
        """Serialize to TypedDict for YAML persistence."""
        d: VerifyCommandDict = {
            "role": self.role,
            "cmd": list(self.cmd),
            "source": self.source,
        }
        if self.timeout is not None:
            d["timeout"] = self.timeout
        return d


@dataclass
class VerifyConfig:
    """Parsed .golem/verify.yaml content."""

    version: int
    commands: list[VerifyCommand] = field(default_factory=list)
    detected_at: str = ""
    stack: list[str] = field(default_factory=list)
    coverage_threshold: float | None = None

    def to_dict(self) -> VerifyConfigDict:
        """Serialize to TypedDict for YAML persistence."""
        d: VerifyConfigDict = {
            "version": self.version,
            "commands": [c.to_dict() for c in self.commands],
            "detected_at": self.detected_at,
            "stack": list(self.stack),
        }
        if self.coverage_threshold is not None:
            d["coverage_threshold"] = self.coverage_threshold
        return d


def _resolve_config_path(repo_root: str) -> Path | None:
    """Return resolved path to .golem/verify.yaml inside repo_root.

    Returns None if the file does not exist or resolves outside repo_root
    (symlink attack prevention via resolve() + relative_to()).
    """
    root = Path(repo_root).resolve()
    cfg_path = root / _CONFIG_RELPATH
    if not cfg_path.exists():
        return None
    if cfg_path.is_symlink():
        logger.warning(
            "verify.yaml at %s is a symlink — ignoring (potential path traversal)",
            cfg_path,
        )
        return None
    try:
        resolved = cfg_path.resolve()
        resolved.relative_to(root)
    except ValueError:
        logger.warning(
            "verify.yaml at %s resolves outside repo root %s — ignoring (symlink?)",
            cfg_path,
            root,
        )
        return None
    return resolved


def _parse_command(raw: Any) -> VerifyCommand | None:
    """Parse one command entry from YAML. Returns None if malformed."""
    if not isinstance(raw, dict):
        return None
    role = raw.get("role", "")
    cmd = raw.get("cmd", [])
    source = raw.get("source", "auto-detected")
    timeout = raw.get("timeout")

    if role not in _VALID_ROLES:
        logger.warning("Unknown verify command role %r — skipping", role)
        return None
    if not isinstance(cmd, list) or not cmd:
        logger.warning("verify command 'cmd' must be a non-empty list — skipping")
        return None
    if source not in _VALID_SOURCES:
        source = "user"

    return VerifyCommand(
        role=role,
        cmd=[str(c) for c in cmd],
        source=source,
        timeout=int(timeout) if timeout is not None else None,
    )


def load_verify_config(repo_root: str) -> "VerifyConfig | None":
    """Load .golem/verify.yaml from repo_root.

    Returns None if absent, unreadable, or fails schema validation.
    """
    cfg_path = _resolve_config_path(repo_root)
    if cfg_path is None:
        return None
    try:
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError) as exc:
        logger.warning("Failed to load verify.yaml from %s: %s", repo_root, exc)
        return None

    if not isinstance(raw, dict):
        logger.warning("verify.yaml in %s is not a YAML mapping — ignoring", repo_root)
        return None

    version = raw.get("version", 0)
    if version != _SCHEMA_VERSION:
        logger.warning(
            "verify.yaml in %s has unsupported version %s (expected %d) — ignoring",
            repo_root,
            version,
            _SCHEMA_VERSION,
        )
        return None

    commands = []
    for entry in raw.get("commands", []):
        parsed = _parse_command(entry)
        if parsed is not None:
            commands.append(parsed)

    threshold = raw.get("coverage_threshold")
    return VerifyConfig(
        version=version,
        commands=commands,
        detected_at=str(raw.get("detected_at", "")),
        stack=list(raw.get("stack", [])),
        coverage_threshold=float(threshold) if threshold is not None else None,
    )


def save_verify_config(repo_root: str, config: VerifyConfig) -> None:
    """Write config to {repo_root}/.golem/verify.yaml, creating the directory."""
    root = Path(repo_root).resolve()
    golem_dir = root / ".golem"
    golem_dir.mkdir(exist_ok=True)
    cfg_path = golem_dir / "verify.yaml"
    data = config.to_dict()
    cfg_path.write_text(
        yaml.dump(data, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    logger.info(
        "Saved verify config to %s (%d commands)", cfg_path, len(config.commands)
    )
