# golem/types.py
"""Shared TypedDict contracts for cross-module data structures.

Every module that produces or consumes these dict shapes should import
from here.  This prevents key-mismatch bugs where a producer writes
one key name and a consumer reads another.

See: docs/plans/2026-03-09-quality-assurance-pipeline-v2-design.md
"""

from typing import Any, NotRequired, TypedDict


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


class ActiveTaskDict(TypedDict):
    """A currently-active task in the live state snapshot.

    Producers: live_state.py snapshot()
    Consumers: dashboard.py _format_active_task(), _format_live_section()
    """

    event_id: str
    flow: str
    model: str
    phase: str
    elapsed_s: float


class CompletedTaskDict(TypedDict):
    """A recently-completed task in the live state snapshot.

    Producers: live_state.py snapshot()
    Consumers: dashboard.py _format_live_section()
    """

    event_id: str
    flow: str
    success: bool
    duration_s: float
    cost_usd: float
    finished_ago_s: float


class LiveSnapshotDict(TypedDict):
    """Full live state snapshot.

    Producers: live_state.py snapshot()
    Consumers: dashboard.py _format_live_section(), health.py
    """

    uptime_s: float
    active_tasks: list[ActiveTaskDict]
    active_count: int
    queue_depth: int
    queued_event_ids: list[str]
    models_active: dict[str, int]
    recently_completed: list[CompletedTaskDict]


class RunRecordDict(TypedDict):
    """A single run-log entry serialized to/from JSONL.

    Producers: run_log.py record_run()
    Consumers: dashboard.py _aggregate_stats(), _format_recent_runs()
    """

    event_id: str
    flow: str
    task_id: str
    source: str
    started_at: str
    finished_at: str
    duration_s: float
    success: bool
    error: str | None
    model: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    actions_taken: list[str]
    verdict: str
    trace_file: str
    queue_wait_ms: int


class AlertDict(TypedDict):
    """A health alert produced by the monitoring system.

    Producers: health.py _compute_alerts()
    Consumers: health.py _maybe_notify(), snapshot()
    """

    type: str
    message: str
    value: float
    threshold: float


class StreamEventDict(TypedDict, total=False):
    """A raw stream-JSON event from the Claude CLI.

    Producers: Claude CLI (external)
    Consumers: event_tracker.py handle_event(), dashboard.py _parse_trace(),
               stream_printer.py handle()

    All keys are optional (total=False) because different event types
    carry different subsets of keys.
    """

    type: str
    subtype: str
    session_id: str
    tool_call: dict[str, Any]
    message: dict[str, Any]
    cost_usd: float
    duration_ms: int
    model: str


class ConfigSnapshotDict(TypedDict):
    """Dashboard-safe snapshot of the golem configuration.

    Producers: dashboard.py config_to_snapshot()
    Consumers: dashboard API /api/config
    """

    model: str
    max_concurrent: int
    budget: float
    timeout: int
    flows: dict[str, bool]
    flow_models: dict[str, str]


class ValidationResultDict(TypedDict):
    """Structured output from the validation agent.

    Producers: validation.py _parse_validation_output()
    Consumers: orchestrator.py _apply_verdict(), committer.py
    """

    verdict: str
    confidence: float
    summary: str
    concerns: list[str]
    files_to_fix: list[str]
    test_failures: list[str]
    task_type: str


class VerificationResultDict(TypedDict):
    """Structured output from the deterministic verifier.

    Producers: verifier.py run_verification()
    Consumers: orchestrator.py, validation.py (injected into reviewer prompt)
    """

    passed: bool
    black_ok: bool
    black_output: str
    pylint_ok: bool
    pylint_output: str
    pytest_ok: bool
    pytest_output: str
    test_count: int
    failures: list[str]
    coverage_pct: float
    duration_s: float
