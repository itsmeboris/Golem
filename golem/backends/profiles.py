"""Built-in profile factories — registered on import.

Importing this module registers the ``redmine`` profile.
"""

from __future__ import annotations

from typing import Any

from ..interfaces import Notifier
from ..profile import GolemProfile, register_profile


def _build_notifier(config: Any) -> Notifier:
    """Pick a notifier based on config: Slack > Teams > log."""
    from ..core.slack import SlackClient
    from ..core.teams import TeamsClient

    from .local import LogNotifier
    from .slack_notifier import SlackNotifier
    from .teams_notifier import TeamsNotifier

    if config.slack.enabled:
        client = SlackClient(webhooks=config.slack.webhooks)
        return SlackNotifier(client)

    if config.teams.enabled:
        client = TeamsClient(webhooks=config.teams.webhooks)
        return TeamsNotifier(client)

    return LogNotifier()


def _build_redmine_profile(config: Any) -> GolemProfile:
    """Build the Redmine + notification + MCP profile (current default)."""
    from ..prompts import FilePromptProvider
    from .local import NullToolProvider
    from .mcp_tools import KeywordToolProvider
    from .redmine import RedmineStateBackend, RedmineTaskSource

    task_config = config.get_flow_config("golem")
    prompts_dir = task_config.prompts_dir if task_config else ""
    mcp_enabled = task_config.mcp_enabled if task_config else True

    return GolemProfile(
        name="redmine",
        task_source=RedmineTaskSource(),
        state_backend=RedmineStateBackend(),
        notifier=_build_notifier(config),
        tool_provider=KeywordToolProvider() if mcp_enabled else NullToolProvider(),
        prompt_provider=FilePromptProvider(prompts_dir or None),
    )


# -- Register on import -----------------------------------------------------
register_profile("redmine", _build_redmine_profile)
