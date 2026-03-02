"""Minimal trigger support — only base types needed by the agent."""

from .base import TriggerEvent  # noqa: F401

# Provide FASTAPI_AVAILABLE without pulling in heavy webhook dependencies.
try:
    import fastapi  # noqa: F401

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
