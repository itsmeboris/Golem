# golem/types.py
"""Shared TypedDict contracts for cross-module data structures.

Every module that produces or consumes these dict shapes should import
from here.  This prevents key-mismatch bugs where a producer writes
one key name and a consumer reads another.

See: docs/plans/2026-03-09-quality-assurance-pipeline-v2-design.md
"""

from typing import NotRequired, TypedDict


class MilestoneDict(TypedDict):
    """A single event-log entry produced by TaskEventTracker.

    Producers: event_tracker.py to_dict(), orchestrator.py _on_milestone()
    Consumers: dashboard.py format_task_detail_text(), validation.py _format_event_log()
    """

    kind: str  # "tool_call" | "tool_result" | "error" | "result" | "text"
    tool_name: str
    summary: str
    timestamp: float
    is_error: bool
    full_text: NotRequired[str]


class TrackerExportDict(TypedDict):
    """Serialized tracker state from TaskEventTracker.to_dict().

    Producers: event_tracker.py to_dict()
    Consumers: orchestrator.py _populate_session_from_tracker()
    """

    session_id: int  # TaskEventTracker.session_id (issue_id), not TrackerState.session_id
    tools_called: list[str]
    mcp_tools_called: list[str]
    errors: list[str]
    last_activity: str
    last_text: str
    cost_usd: float
    milestone_count: int
    finished: bool
    event_log: list[MilestoneDict]
