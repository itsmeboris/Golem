"""MCP tool provider backend for the golem profile system.

Wraps the existing keyword-based MCP server scoping logic in ``mcp_scope.py``
behind the ``ToolProvider`` protocol.
"""

import logging
from typing import Any

from ..lint.mcp_schema import validate_tool_schema
from ..mcp_scope import _BASE_SERVERS, _KEYWORD_SERVERS, determine_mcp_scope

logger = logging.getLogger("golem.backends.mcp_tools")


class KeywordToolProvider:
    """Keyword-driven MCP server selection.

    Delegates to the existing ``determine_mcp_scope`` logic by default.
    Accepts optional overrides for base servers and keyword mappings.

    Parameters
    ----------
    base_servers:
        Always-included servers.  ``None`` uses the global default.
    keyword_servers:
        Subject-keyword → extra servers mapping.  ``None`` uses the global
        default.
    role_servers:
        Optional role → allowed-server-list mapping.  When a role is provided
        in ``servers_for_subject`` and that role is found here, results are
        intersected with the allowed list.  A role absent from this dict means
        no filtering is applied.
    max_servers:
        When > 0, the result list is truncated to this length after filtering.
        0 means no limit.
    """

    def __init__(
        self,
        base_servers: list[str] | None = None,
        keyword_servers: dict[str, list[str]] | None = None,
        role_servers: dict[str, list[str]] | None = None,
        max_servers: int = 0,
    ):
        self._base = base_servers if base_servers is not None else list(_BASE_SERVERS)
        self._keywords = (
            keyword_servers if keyword_servers is not None else dict(_KEYWORD_SERVERS)
        )
        self._use_defaults = base_servers is None and keyword_servers is None
        self._role_servers = role_servers
        self._max_servers = max_servers

    def base_servers(self) -> list[str]:
        """Return the base set of MCP servers (always included)."""
        return list(self._base)

    def servers_for_subject(self, subject: str, *, role: str = "") -> list[str]:
        """Return MCP servers for *subject*, adding keyword-matched extras.

        When *role* is non-empty and present in ``role_servers``, the result
        is filtered to only servers in that role's allowed list.

        When ``max_servers > 0`` and the result exceeds that count, a warning
        is logged and the list is truncated.
        """
        if self._use_defaults:
            servers_list = determine_mcp_scope(subject)
        else:
            servers = set(self._base)
            lower = subject.lower()
            for keyword, extra in self._keywords.items():
                if keyword in lower:
                    servers.update(extra)
            servers_list = sorted(servers)

        # Role-based filtering
        if role and self._role_servers is not None and role in self._role_servers:
            allowed = set(self._role_servers[role])
            servers_list = [s for s in servers_list if s in allowed]

        # Max servers enforcement
        if self._max_servers > 0 and len(servers_list) > self._max_servers:
            logger.warning(
                "MCP server count %d exceeds limit %d, truncating to first %d",
                len(servers_list),
                self._max_servers,
                self._max_servers,
            )
            servers_list = servers_list[: self._max_servers]

        return servers_list

    def validate_tools(
        self, tools: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Validate MCP tool definitions, returning valid tools and warnings.

        Invalid tools are rejected with logged warnings.  Each violation
        produces a warning message; the list of warning strings is also
        returned so callers can surface them through their own channels.

        Parameters
        ----------
        tools:
            Raw MCP tool definition dicts received from an MCP server.

        Returns
        -------
        tuple[list[dict], list[str]]
            ``(valid_tools, warnings)`` where ``valid_tools`` contains only
            tools that passed schema validation and ``warnings`` is a list of
            human-readable rejection messages.
        """
        valid: list[dict[str, Any]] = []
        warnings: list[str] = []
        for tool in tools:
            violations = validate_tool_schema(tool)
            if violations:
                name = (
                    tool.get("name", "<unnamed>")
                    if isinstance(tool, dict)
                    else "<unnamed>"
                )
                msg = "Rejected MCP tool %s: %s" % (name, "; ".join(violations))
                logger.warning(msg)
                warnings.append(msg)
            else:
                valid.append(tool)
        return valid, warnings

    @staticmethod
    def filter_tool_definitions(
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Filter a list of MCP tool definitions to only those that are valid.

        Static convenience wrapper that consumers can call without constructing
        a full provider instance.  Useful for filtering MCP protocol tool lists
        received from an MCP server before passing them to the agent.

        Parameters
        ----------
        tools:
            Raw MCP tool definition dicts to validate.

        Returns
        -------
        list[dict]
            Only the tools that pass schema validation.
        """
        valid: list[dict[str, Any]] = []
        for tool in tools:
            if not validate_tool_schema(tool):
                valid.append(tool)
        return valid
