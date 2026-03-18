"""Minimal trigger support for the agent."""

# Provide FASTAPI_AVAILABLE without pulling in heavy webhook dependencies.
try:
    import fastapi  # noqa: F401

    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
