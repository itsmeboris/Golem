"""Interactive first-run config wizard for Golem."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

from .core.config import KNOWN_MODELS
from .profile import _ensure_builtins_registered, available_profiles


def ask(prompt: str, default: str = "", choices: list[str] | None = None) -> str:
    """Prompt user for input. Shows default in brackets. Validates against choices if given."""
    display_default = f" [{default}]" if default else ""
    if choices:
        choices_str = "/".join(choices)
        full_prompt = f"{prompt} ({choices_str}){display_default}: "
    else:
        full_prompt = f"{prompt}{display_default}: "

    while True:
        value = input(full_prompt).strip()
        if not value:
            return default
        if choices is None or value in choices:
            return value
        print(f"Invalid choice {value!r}. Must be one of: {', '.join(choices)}")


def run_wizard(output_path: Path, use_defaults: bool = False) -> int:
    """Run the interactive config wizard. Returns 0 on success, 1 on failure."""
    try:
        return _run_wizard_impl(output_path, use_defaults)
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        return 1


def _collect_inputs(use_defaults: bool) -> dict[str, str]:
    """Gather all wizard inputs, either from defaults or interactively."""
    default_work_dir = os.getcwd()

    if use_defaults:
        return {
            "profile": "local",
            "task_model": "sonnet",
            "budget": "10.0",
            "work_dir": default_work_dir,
            "projects": "",
            "slack_enabled": "n",
            "slack_webhook_url": "",
            "teams_enabled": "n",
            "teams_webhook_url": "",
            "dashboard_port": "8081",
        }

    _ensure_builtins_registered()
    profiles = available_profiles()
    models = sorted(KNOWN_MODELS)

    result: dict[str, str] = {}
    result["profile"] = ask("Which profile?", default="local", choices=profiles)
    result["task_model"] = ask(
        "Which model for tasks?", default="sonnet", choices=models
    )

    while True:
        result["budget"] = ask("Budget per task (USD)?", default="10.0")
        try:
            if float(result["budget"]) > 0:
                break
        except ValueError:
            pass
        print("Budget must be a positive number.")

    result["work_dir"] = ask("Default working directory?", default=default_work_dir)

    if result["profile"] == "github":
        result["projects"] = ask(
            "GitHub repos to poll (owner/repo, comma-separated):", default=""
        )
        result["detection_tag"] = ask(
            "Issue label for agent-eligible tasks?", default="golem"
        )
    else:
        result["projects"] = ask(
            "Project identifiers (comma-separated, or empty):", default=""
        )
        result["detection_tag"] = ""
    result["slack_enabled"] = ask(
        "Enable Slack notifications?", default="n", choices=["y", "n"]
    )
    result["slack_webhook_url"] = ""
    if result["slack_enabled"] == "y":
        result["slack_webhook_url"] = ask("Slack webhook URL:", default="")

    result["teams_enabled"] = ask(
        "Enable Teams notifications?", default="n", choices=["y", "n"]
    )
    result["teams_webhook_url"] = ""
    if result["teams_enabled"] == "y":
        result["teams_webhook_url"] = ask("Teams webhook URL:", default="")

    while True:
        result["dashboard_port"] = ask("Dashboard port?", default="8081")
        try:
            if 1 <= int(result["dashboard_port"]) <= 65535:
                break
        except ValueError:
            pass
        print("Port must be an integer between 1 and 65535.")

    return result


def _build_config(inputs: dict[str, str]) -> dict[str, Any]:
    """Build the config dict from collected inputs."""
    projects: list[str] = (
        [p.strip() for p in inputs["projects"].split(",") if p.strip()]
        if inputs["projects"].strip()
        else []
    )
    work_dirs = {proj: inputs["work_dir"] for proj in projects}

    slack_webhooks: dict[str, str] = {}
    if inputs["slack_enabled"] == "y" and inputs["slack_webhook_url"]:
        slack_webhooks["default"] = inputs["slack_webhook_url"]

    teams_webhooks: dict[str, str] = {}
    if inputs["teams_enabled"] == "y" and inputs["teams_webhook_url"]:
        teams_webhooks["default"] = inputs["teams_webhook_url"]

    flow_config: dict[str, Any] = {
        "enabled": True,
        "profile": inputs["profile"],
        "projects": projects,
        "task_model": inputs["task_model"],
        "budget_per_task_usd": float(inputs["budget"]),
        "default_work_dir": inputs["work_dir"],
        "work_dirs": work_dirs,
        # Heartbeat — self-directed work when idle (opt-in)
        "heartbeat_enabled": False,
        "heartbeat_interval_seconds": 300,
        "heartbeat_idle_threshold_seconds": 900,
        "heartbeat_daily_budget_usd": 1.0,
        "heartbeat_max_inflight": 1,
        "heartbeat_candidate_limit": 5,
        "heartbeat_dedup_ttl_days": 30,
    }
    if inputs.get("detection_tag"):
        flow_config["detection_tag"] = inputs["detection_tag"]

    return {
        "flows": {
            "golem": flow_config,
        },
        "claude": {
            "model": inputs["task_model"],
        },
        "dashboard": {
            "port": int(inputs["dashboard_port"]),
        },
        "slack": {
            "enabled": inputs["slack_enabled"] == "y",
            **({"webhooks": slack_webhooks} if slack_webhooks else {}),
        },
        "teams": {
            "enabled": inputs["teams_enabled"] == "y",
            **({"webhooks": teams_webhooks} if teams_webhooks else {}),
        },
    }


def _run_wizard_impl(output_path: Path, use_defaults: bool) -> int:
    if output_path.exists() and not use_defaults:
        answer = ask(
            "Config file already exists. Overwrite?", default="n", choices=["y", "n"]
        )
        if answer != "y":
            print("Aborted.")
            return 1

    inputs = _collect_inputs(use_defaults)
    config_dict = _build_config(inputs)

    output_path.write_text(
        yaml.dump(config_dict, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    projects = config_dict["flows"]["golem"]["projects"]
    print(f"\nConfig written to {output_path}")
    print(f"  profile:  {inputs['profile']}")
    print(f"  model:    {inputs['task_model']}")
    print(f"  budget:   ${inputs['budget']}")
    print(f"  work_dir: {inputs['work_dir']}")
    if projects:
        print(f"  projects: {', '.join(projects)}")
    print(f"  port:     {inputs['dashboard_port']}")

    if inputs["profile"] == "github":
        print("\nGitHub profile requires the gh CLI: run 'gh auth login' if needed.")

    _setup_git_hooks()

    return 0


def _setup_git_hooks() -> None:
    """Configure git to use tracked .githooks/ directory."""
    repo_root = Path(__file__).resolve().parent.parent
    if not (repo_root / ".githooks").is_dir():
        return
    try:
        cur = subprocess.run(
            ["git", "config", "core.hooksPath"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            check=False,
        ).stdout.strip()
        if cur == ".githooks":
            return
        subprocess.run(
            ["git", "config", "core.hooksPath", ".githooks"],
            cwd=str(repo_root),
            check=True,
        )
        print("\nGit hooks configured (core.hooksPath = .githooks)")
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        print("\nNote: run 'make setup' to enable git hooks")
