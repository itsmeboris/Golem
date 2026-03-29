"""Runtime MCP tool validation.

Validates MCP tool definitions at load time to prevent schema poisoning
attacks where a malicious MCP server advertises tools with invalid schemas
that could confuse the agent.
"""

import logging
from typing import Any

from .lint.mcp_schema import validate_tool_schema

logger = logging.getLogger("golem.mcp_validator")


def validate_and_filter_tools(
    tools: list[dict[str, Any]],
    *,
    server_name: str = "",
) -> list[dict[str, Any]]:
    """Validate MCP tool definitions, rejecting invalid ones.

    Returns only the tools that pass schema validation.
    Logs a warning for each rejected tool (including the specific violations)
    and a summary warning when any tools are rejected.

    Parameters
    ----------
    tools:
        List of raw MCP tool definition dicts to validate.
    server_name:
        Optional name of the MCP server supplying these tools.  Used in log
        messages to identify the source of invalid tools.
    """
    valid: list[dict[str, Any]] = []
    for tool in tools:
        violations = validate_tool_schema(tool)
        if violations:
            if isinstance(tool, dict):
                name = tool.get("name", "<unnamed>")
            else:
                name = "<unnamed>"
            logger.warning(
                "Rejected MCP tool %s from %s: %s",
                name,
                server_name or "unknown",
                "; ".join(violations),
            )
        else:
            valid.append(tool)

    rejected = len(tools) - len(valid)
    if rejected:
        logger.warning(
            "MCP schema validation: %d of %d tool(s) rejected from %s",
            rejected,
            len(tools),
            server_name or "unknown",
        )

    return valid
