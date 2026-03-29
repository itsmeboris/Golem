"""Startup dependency validation for the Golem daemon."""

import logging
import shutil

logger = logging.getLogger("golem.startup")

_REQUIRED_TOOLS: list[str] = ["git"]
_OPTIONAL_TOOLS: list[str] = ["claude"]


def validate_dependencies() -> list[str]:
    """Check for required tools in PATH.

    Raises RuntimeError if a required tool is missing.
    Returns a list of warning strings for missing optional tools.
    """
    warnings: list[str] = []
    for tool in _REQUIRED_TOOLS:
        if not shutil.which(tool):
            raise RuntimeError(
                "%s not found in PATH — required for Golem operation" % tool
            )
    for tool in _OPTIONAL_TOOLS:
        if not shutil.which(tool):
            msg = "%s not found in PATH — some features may not work" % tool
            warnings.append(msg)
            logger.warning("%s not found in PATH — some features may not work", tool)
    return warnings
