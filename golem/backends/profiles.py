"""Built-in profile factories — registered on import.

Importing this module registers the ``redmine`` profile.
"""

from __future__ import annotations

from typing import Any

from ..profile import GolemProfile, register_profile


def _build_redmine_profile(config: Any) -> GolemProfile:
    """Build the Redmine + Teams + MCP profile (current default)."""
    from ..core.teams import TeamsClient

    from ..prompts import FilePromptProvider
    from .local import LogNotifier, NullToolProvider
    from .mcp_tools import KeywordToolProvider
    from .redmine import RedmineStateBackend, RedmineTaskSource
    from .teams_notifier import TeamsNotifier

    task_config = config.get_flow_config("golem")
    prompts_dir = task_config.prompts_dir if task_config else ""
    mcp_enabled = task_config.mcp_enabled if task_config else True

    # Notifications: Teams if enabled, otherwise log-only
    notifier: Any
    if config.teams.enabled:
        client = TeamsClient(webhooks=config.teams.webhooks)
        notifier = TeamsNotifier(client)
    else:
        notifier = LogNotifier()

    return GolemProfile(
        name="redmine",
        task_source=RedmineTaskSource(),
        state_backend=RedmineStateBackend(),
        notifier=notifier,
        tool_provider=KeywordToolProvider() if mcp_enabled else NullToolProvider(),
        prompt_provider=FilePromptProvider(prompts_dir or None),
    )


# -- Register on import -----------------------------------------------------
register_profile("redmine", _build_redmine_profile)
