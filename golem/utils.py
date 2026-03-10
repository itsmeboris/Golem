"""Shared utility functions for golem."""

from __future__ import annotations


def format_duration(seconds: float) -> str:
    """Format a duration in seconds into a human-readable string."""
    if seconds <= 0:
        return "0s"
    if seconds < 1:
        return "< 1s"
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    if total < 3600:
        m, s = divmod(total, 60)
        return f"{m}m {s}s"
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    return f"{h}h {m}m {s}s"
