"""Dynamic MCP server scoping based on subtask keywords.

Determines which MCP servers a task-agent session needs by scanning the issue
subject for domain keywords (e.g. "jenkins", "confluence").  This keeps agent
invocations lean — only the relevant servers are loaded.

Key exports:
- ``determine_mcp_scope`` — returns the list of MCP server names for a given
  subtask subject.

Customize ``_KEYWORD_SERVERS`` and ``_BASE_SERVERS`` for your environment.
"""

import logging

logger = logging.getLogger("Tools.AgentAutomation.Flows.TaskAgent.MCPScope")

# Keyword → additional MCP servers to include.
# Customize this mapping for the MCP servers available in your deployment.
_KEYWORD_SERVERS: dict[str, list[str]] = {
    "jenkins": ["jenkins"],
    "build": ["jenkins"],
    "ci": ["jenkins"],
    "gerrit": ["gerrit"],
    "review": ["gerrit"],
    "confluence": ["confluence"],
    "wiki": ["confluence"],
    "document": ["confluence"],
    "redmine": ["redmine"],
    "issue": ["redmine"],
    "ticket": ["redmine"],
}

# Always included (empty list if no base servers needed)
_BASE_SERVERS: list[str] = ["redmine"]


def determine_mcp_scope(subtask_subject: str) -> list[str]:
    """Return the list of MCP servers relevant for *subtask_subject*.

    Servers in ``_BASE_SERVERS`` are always included.  Additional servers
    are added when keywords in the subject match ``_KEYWORD_SERVERS``.
    """
    servers = set(_BASE_SERVERS)
    lower = subtask_subject.lower()

    for keyword, extra_servers in _KEYWORD_SERVERS.items():
        if keyword in lower:
            servers.update(extra_servers)

    result = sorted(servers)
    logger.debug("MCP scope for '%s': %s", subtask_subject[:60], result)
    return result
