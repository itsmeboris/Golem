"""Golem profile — bundles pluggable backends into a named configuration.

A ``GolemProfile`` groups the five backend interfaces (TaskSource,
StateBackend, Notifier, ToolProvider, PromptProvider) into a single object
that the orchestrator, supervisor, and flow consume.

Profiles are registered via ``register_profile`` and instantiated at runtime
by ``build_profile`` using the ``profile`` key in ``GolemFlowConfig``.
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
class GolemProfile:
    """A complete set of backends for the golem system."""

    name: str
    task_source: TaskSource
    state_backend: StateBackend
    notifier: Notifier
    tool_provider: ToolProvider
    prompt_provider: PromptProvider


# -- Profile registry --------------------------------------------------------

ProfileFactory = Callable[[Any], GolemProfile]

_PROFILE_FACTORIES: dict[str, ProfileFactory] = {}


def register_profile(name: str, factory: ProfileFactory) -> None:
    """Register a profile factory function.

    *factory* receives the full ``Config`` object and returns a
    ``GolemProfile``.
    """
    _PROFILE_FACTORIES[name] = factory


def _ensure_builtins_registered() -> None:
    """Lazily import built-in profiles on first use."""
    if not _PROFILE_FACTORIES:
        import golem.backends.profiles  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel


def build_profile(name: str, config: Any) -> GolemProfile:
    """Build a profile by *name* using *config*.

    Raises ``ValueError`` if the profile name is not registered.
    """
    _ensure_builtins_registered()
    factory = _PROFILE_FACTORIES.get(name)
    if factory is None:
        available = sorted(_PROFILE_FACTORIES) or ["(none)"]
        raise ValueError(
            f"Unknown golem profile: {name!r}. " f"Available: {', '.join(available)}"
        )
    return factory(config)


def available_profiles() -> list[str]:
    """Return the names of all registered profiles."""
    return sorted(_PROFILE_FACTORIES)
