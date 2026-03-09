"""Interactive first-run config wizard for Golem."""

from __future__ import annotations

import os
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


def _run_wizard_impl(
    output_path: Path, use_defaults: bool
) -> int:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    if output_path.exists() and not use_defaults:
        answer = ask(
            "Config file already exists. Overwrite?", default="n", choices=["y", "n"]
        )
        if answer != "y":
            print("Aborted.")
            return 1

    _ensure_builtins_registered()
    profiles = available_profiles()
    models = sorted(KNOWN_MODELS)
    default_work_dir = os.getcwd()

    if use_defaults:
        profile = "local"
        task_model = "sonnet"
        budget_str = "10.0"
        work_dir = default_work_dir
        projects_str = ""
        slack_enabled = "n"
        slack_webhook_url = ""
        teams_enabled = "n"
        teams_webhook_url = ""
        dashboard_port_str = "8081"
    else:
        profile = ask("Which profile?", default="local", choices=profiles)
        task_model = ask("Which model for tasks?", default="sonnet", choices=models)

        while True:
            budget_str = ask("Budget per task (USD)?", default="10.0")
            try:
                budget_val = float(budget_str)
                if budget_val > 0:
                    break
            except ValueError:
                pass
            print("Budget must be a positive number.")

        work_dir = ask("Default working directory?", default=default_work_dir)
        projects_str = ask(
            "Project identifiers (comma-separated, or empty):", default=""
        )
        slack_enabled = ask(
            "Enable Slack notifications?", default="n", choices=["y", "n"]
        )
        slack_webhook_url = ""
        if slack_enabled == "y":
            slack_webhook_url = ask("Slack webhook URL:", default="")

        teams_enabled = ask(
            "Enable Teams notifications?", default="n", choices=["y", "n"]
        )
        teams_webhook_url = ""
        if teams_enabled == "y":
            teams_webhook_url = ask("Teams webhook URL:", default="")

        while True:
            dashboard_port_str = ask("Dashboard port?", default="8081")
            try:
                port_val = int(dashboard_port_str)
                if 1 <= port_val <= 65535:
                    break
            except ValueError:
                pass
            print("Port must be an integer between 1 and 65535.")

    budget = float(budget_str)
    dashboard_port = int(dashboard_port_str)

    projects: list[str] = (
        [p.strip() for p in projects_str.split(",") if p.strip()]
        if projects_str.strip()
        else []
    )

    work_dirs: dict[str, str] = {}
    for proj in projects:
        work_dirs[proj] = work_dir

    slack_webhooks: dict[str, str] = {}
    if not use_defaults and slack_enabled == "y" and slack_webhook_url:
        slack_webhooks["default"] = slack_webhook_url

    teams_webhooks: dict[str, str] = {}
    if not use_defaults and teams_enabled == "y" and teams_webhook_url:
        teams_webhooks["default"] = teams_webhook_url

    config_dict: dict[str, Any] = {
        "flows": {
            "golem": {
                "enabled": True,
                "profile": profile,
                "projects": projects,
                "task_model": task_model,
                "budget_per_task_usd": budget,
                "default_work_dir": work_dir,
                "work_dirs": work_dirs,
            }
        },
        "claude": {
            "model": task_model,
        },
        "dashboard": {
            "port": dashboard_port,
        },
        "slack": {
            "enabled": slack_enabled == "y",
            **({"webhooks": slack_webhooks} if slack_webhooks else {}),
        },
        "teams": {
            "enabled": teams_enabled == "y",
            **({"webhooks": teams_webhooks} if teams_webhooks else {}),
        },
    }

    output_path.write_text(
        yaml.dump(config_dict, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    print(f"\nConfig written to {output_path}")
    print(f"  profile:  {profile}")
    print(f"  model:    {task_model}")
    print(f"  budget:   ${budget}")
    print(f"  work_dir: {work_dir}")
    if projects:
        print(f"  projects: {', '.join(projects)}")
    print(f"  port:     {dashboard_port}")

    return 0
