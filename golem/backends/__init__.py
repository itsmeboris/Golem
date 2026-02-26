"""Pluggable backend implementations for the golem profile system.

Submodules:
    redmine         — RedmineTaskSource, RedmineStateBackend
    teams_notifier  — TeamsNotifier (wraps existing card builders)
    mcp_tools       — KeywordToolProvider (wraps existing mcp_scope.py)
    local           — LocalFileTaskSource, NullStateBackend, LogNotifier, NullToolProvider
"""
