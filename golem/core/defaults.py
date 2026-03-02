"""Single source of truth for service URLs, HTTP timeouts, and shared utilities.

All URLs default to empty and should be configured via environment variables
or config.yaml for your deployment.
"""

import os
from datetime import datetime, timezone

__all__ = ["_now_iso", "_fmt_duration"]

REDMINE_URL = os.environ.get("REDMINE_URL", "")
REDMINE_ISSUES_URL = f"{REDMINE_URL}/issues" if REDMINE_URL else ""

HTTP_TIMEOUT = 30
POST_TIMEOUT = 15


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _fmt_duration(seconds: float) -> str:
    """Format seconds as ``Xm Ys``."""
    m, s = divmod(int(seconds), 60)
    if m:
        return f"{m}m {s}s"
    return f"{s}s"
