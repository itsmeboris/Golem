"""Built-in profile factories — registered on import.

Importing this module registers the ``redmine``, ``local``, and ``github`` profiles.
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


def _build_local_profile(config: Any) -> GolemProfile:
    """Build a local profile backed by file-based submissions."""
    from ..core.config import DATA_DIR
    from ..prompts import FilePromptProvider

    from .local import LocalFileTaskSource, NullStateBackend, NullToolProvider
    from .mcp_tools import KeywordToolProvider

    task_config = config.get_flow_config("golem")
    prompts_dir = task_config.prompts_dir if task_config else ""
    mcp_enabled = task_config.mcp_enabled if task_config else False

    submissions_dir = DATA_DIR / "submissions"
    submissions_dir.mkdir(parents=True, exist_ok=True)

    return GolemProfile(
        name="local",
        task_source=LocalFileTaskSource(submissions_dir),
        state_backend=NullStateBackend(),
        notifier=_build_notifier(config),
        tool_provider=KeywordToolProvider() if mcp_enabled else NullToolProvider(),
        prompt_provider=FilePromptProvider(prompts_dir or None),
    )


def _build_github_profile(config: Any) -> GolemProfile:
    """Build a GitHub Issues profile using the ``gh`` CLI."""
    from ..prompts import FilePromptProvider

    from .github import GitHubStateBackend, GitHubTaskSource
    from .local import NullToolProvider
    from .mcp_tools import KeywordToolProvider

    task_config = config.get_flow_config("golem")
    prompts_dir = task_config.prompts_dir if task_config else ""
    mcp_enabled = task_config.mcp_enabled if task_config else False

    return GolemProfile(
        name="github",
        task_source=GitHubTaskSource(),
        state_backend=GitHubStateBackend(),
        notifier=_build_notifier(config),
        tool_provider=KeywordToolProvider() if mcp_enabled else NullToolProvider(),
        prompt_provider=FilePromptProvider(prompts_dir or None),
    )


# -- Register on import -----------------------------------------------------
register_profile("redmine", _build_redmine_profile)
register_profile("local", _build_local_profile)
register_profile("github", _build_github_profile)
