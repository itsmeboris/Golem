"""MCP tool provider backend for the golem profile system.

Wraps the existing keyword-based MCP server scoping logic in ``mcp_scope.py``
behind the ``ToolProvider`` protocol.
"""

from ..mcp_scope import _BASE_SERVERS, _KEYWORD_SERVERS, determine_mcp_scope


class KeywordToolProvider:
    """Keyword-driven MCP server selection.

    Delegates to the existing ``determine_mcp_scope`` logic by default.
    Accepts optional overrides for base servers and keyword mappings.
    """

    def __init__(
        self,
        base_servers: list[str] | None = None,
        keyword_servers: dict[str, list[str]] | None = None,
    ):
        self._base = base_servers if base_servers is not None else list(_BASE_SERVERS)
        self._keywords = (
            keyword_servers if keyword_servers is not None else dict(_KEYWORD_SERVERS)
        )
        self._use_defaults = base_servers is None and keyword_servers is None

    def base_servers(self) -> list[str]:
        """Return the base set of MCP servers (always included)."""
        return list(self._base)

    def servers_for_subject(self, subject: str) -> list[str]:
        """Return MCP servers for *subject*, adding keyword-matched extras."""
        if self._use_defaults:
            return determine_mcp_scope(subject)

        servers = set(self._base)
        lower = subject.lower()
        for keyword, extra in self._keywords.items():
            if keyword in lower:
                servers.update(extra)
        return sorted(servers)
