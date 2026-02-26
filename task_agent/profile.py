"""Task-agent profile — bundles pluggable backends into a named configuration.

A ``TaskAgentProfile`` groups the five backend interfaces (TaskSource,
StateBackend, Notifier, ToolProvider, PromptProvider) into a single object
that the orchestrator, supervisor, and flow consume.

Profiles are registered via ``register_profile`` and instantiated at runtime
by ``build_profile`` using the ``profile`` key in ``TaskAgentFlowConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .interfaces import (
    Notifier,
    PromptProvider,
    StateBackend,
    TaskSource,
    ToolProvider,
)


@dataclass
class TaskAgentProfile:
    """A complete set of backends for the task-agent system."""

    name: str
    task_source: TaskSource
    state_backend: StateBackend
    notifier: Notifier
    tool_provider: ToolProvider
    prompt_provider: PromptProvider


# -- Profile registry --------------------------------------------------------

ProfileFactory = Callable[[Any], TaskAgentProfile]

_PROFILE_FACTORIES: dict[str, ProfileFactory] = {}


def register_profile(name: str, factory: ProfileFactory) -> None:
    """Register a profile factory function.

    *factory* receives the full ``Config`` object and returns a
    ``TaskAgentProfile``.
    """
    _PROFILE_FACTORIES[name] = factory


def _ensure_builtins_registered() -> None:
    """Lazily import built-in profiles on first use."""
    if not _PROFILE_FACTORIES:
        import task_agent.backends.profiles  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel


def build_profile(name: str, config: Any) -> TaskAgentProfile:
    """Build a profile by *name* using *config*.

    Raises ``ValueError`` if the profile name is not registered.
    """
    _ensure_builtins_registered()
    factory = _PROFILE_FACTORIES.get(name)
    if factory is None:
        available = sorted(_PROFILE_FACTORIES) or ["(none)"]
        raise ValueError(
            f"Unknown task-agent profile: {name!r}. "
            f"Available: {', '.join(available)}"
        )
    return factory(config)


def available_profiles() -> list[str]:
    """Return the names of all registered profiles."""
    return sorted(_PROFILE_FACTORIES)
