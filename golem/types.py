# golem/types.py
"""Shared TypedDict contracts for cross-module data structures.

Every module that produces or consumes these dict shapes should import
from here.  This prevents key-mismatch bugs where a producer writes
one key name and a consumer reads another.

See: docs/plans/2026-03-09-quality-assurance-pipeline-v2-design.md
"""

from typing import Any, NotRequired, TypedDict


class ToolPermissionDict(TypedDict):
    """A single permission declaration in an MCP tool schema.

    Producers: MCP server tool definitions (external)
    Consumers: golem/lint/mcp_schema.py validate_tool_schema()
    """

    resource: str  # "filesystem" | "network" | "ui" | "process"
    access: str  # "read" | "write" | "execute"


class McpInputSchemaDict(TypedDict):
    """Input schema for an MCP tool definition.

    Producers: MCP server tool definitions (external)
    Consumers: golem/lint/mcp_schema.py validate_tool_schema()
    """

    type: str  # must be "object"
    properties: dict[str, Any]


class McpToolDict(TypedDict):
    """An MCP tool definition to be validated.

    Producers: MCP server tool definitions (external)
    Consumers: golem/lint/mcp_schema.py validate_tool_schema()
    """

    name: str
    description: str
    inputSchema: McpInputSchemaDict
    permissions: NotRequired[list[ToolPermissionDict]]


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

    session_id: (
        int  # TaskEventTracker.session_id (issue_id), not TrackerState.session_id
    )
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
    prompt_hash: str


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
    coverage_delta: NotRequired[dict[str, Any]]
    mutation_result: NotRequired["MutationResultDict"]


class SurvivedMutantDict(TypedDict):
    """A single surviving mutant from mutation testing.

    Producers: verifier.py parse_mutmut_results()
    Consumers: orchestrator.py, dashboard
    """

    file: str
    line: int
    mutant_id: int


class MutationResultDict(TypedDict):
    """Structured output from mutation testing.

    Producers: verifier.py run_mutation_testing()
    Consumers: orchestrator.py
    """

    exit_code: int
    output: str
    passed: bool
    duration_s: float
    mutants_total: int
    killed: int
    survived: int
    timeout: int
    suspicious: int
    skipped: int
    survived_mutants: list[SurvivedMutantDict]


class MutmutSummaryDict(TypedDict):
    """Summary counts from a mutmut run.

    Producers: verifier.py parse_mutmut_summary()
    Consumers: verifier.py run_mutation_testing()
    """

    mutants_total: int
    killed: int
    survived: int
    timeout: int
    suspicious: int
    skipped: int


class CoverageFileDataDict(TypedDict):
    """Per-file coverage data from coverage.py JSON output.

    Producers: coverage.py JSON output (external)
    Consumers: verifier.py parse_coverage_delta()
    """

    executed_lines: list[int]
    missing_lines: list[int]


class CoverageDataDict(TypedDict):
    """Top-level coverage.py JSON output structure.

    Producers: coverage.py JSON output (external)
    Consumers: verifier.py parse_coverage_delta()
    """

    files: dict[str, CoverageFileDataDict]


class MergeEntryDict(TypedDict):
    """Serialized merge queue entry for dashboard API.

    Producers: merge_queue.py snapshot()
    Consumers: dashboard.py GET /api/merge-queue
    """

    session_id: int
    branch_name: str
    worktree_path: str
    priority: int
    group_id: str
    queued_at: str
    changed_files: list[str]


class MergeHistoryEntryDict(TypedDict):
    """Serialized merge result for dashboard API.

    Producers: merge_queue.py snapshot()
    Consumers: dashboard.py GET /api/merge-queue
    """

    session_id: int
    success: bool
    merge_sha: str
    conflict_files: list[str]
    error: str
    changed_files: list[str]
    deferred: bool
    merge_branch: str
    timestamp: str


class MergeQueueSnapshotDict(TypedDict):
    """Full merge queue state for dashboard API.

    Producers: merge_queue.py snapshot()
    Consumers: dashboard.py GET /api/merge-queue
    """

    pending: list[MergeEntryDict]
    active: MergeEntryDict | None
    deferred: list[MergeEntryDict]
    conflicts: list[MergeEntryDict]
    history: list[MergeHistoryEntryDict]


class HeartbeatCandidateDict(TypedDict):
    """A candidate task discovered by the heartbeat scanner.

    Producers: heartbeat.py _run_tier1(), _run_tier2()
    Consumers: heartbeat.py _submit_top_candidate(), dashboard API
    """

    id: str  # e.g. "github:42" or "improvement:coverage:heartbeat"
    subject: str
    body: str
    automatable: bool
    confidence: float  # 0.0-1.0
    complexity: str  # "small" | "medium" | "large"
    reason: str
    tier: int  # 1 or 2


class HeartbeatSnapshotDict(TypedDict):
    """Dashboard-safe snapshot of heartbeat state.

    Producers: heartbeat.py snapshot()
    Consumers: dashboard API /api/heartbeat
    """

    enabled: bool
    state: str  # "idle" | "scanning" | "submitted" | "paused" | "budget_exhausted"
    last_scan_at: str
    last_scan_tier: int
    daily_spend_usd: float
    daily_budget_usd: float
    inflight_task_ids: list[int]
    candidate_count: int
    dedup_entry_count: int
    next_tick_seconds: int


class FieldMetaDict(TypedDict):
    """Metadata for a single config field in the editor registry.

    Producers: config_editor.py FIELD_REGISTRY
    Consumers: dashboard API GET /api/config, CLI TUI
    """

    category: str
    field_type: str  # "choice" | "bool" | "int" | "float" | "str" | "list"
    description: str
    choices: list[str] | None
    min_val: float | None
    max_val: float | None
    sensitive: bool


class FieldInfoDict(TypedDict):
    """A config field's current value plus its metadata.

    Producers: config_editor.get_config_by_category()
    Consumers: dashboard API, CLI TUI
    """

    key: str  # dotted path, e.g. "golem.task_model"
    value: Any
    meta: FieldMetaDict


class SelfUpdateStateDict(TypedDict):
    """Persisted state for the self-update manager.

    Producers: self_update.py save_state()
    Consumers: self_update.py load_state(), dashboard API /api/self-update
    """

    last_checked_sha: str
    last_check_timestamp: str
    last_update_sha: str
    last_update_timestamp: str
    last_review_verdict: str
    last_review_reasoning: str
    pre_update_sha: str | None
    last_startup_timestamp: str | None
    consecutive_crash_count: int
    update_history: list[dict[str, Any]]


class SelfUpdateSnapshotDict(TypedDict):
    """Dashboard-safe snapshot of self-update state.

    Producers: self_update.py snapshot()
    Consumers: dashboard API /api/self-update
    """

    enabled: bool
    branch: str
    strategy: str
    last_checked_sha: str
    last_check_timestamp: str
    last_review_verdict: str
    last_review_reasoning: str
    current_sha: str
    update_history: list[dict[str, Any]]


class FileRoleDict(TypedDict):
    """A file identified during phase handoff with its role and relevance.

    Producers: handoff.py create_handoff()
    Consumers: handoff.py format_handoff_markdown(), orchestrator.py TaskSession
    """

    path: str
    role: str  # "modify" | "read" | "create"
    relevance: str


class PhaseHandoffDict(TypedDict):
    """Structured context passed between orchestration phases.

    Producers: handoff.py create_handoff()
    Consumers: handoff.py validate_handoff(), format_handoff_markdown(),
               orchestrator.py TaskSession.phase_handoffs
    """

    from_phase: str
    to_phase: str
    context: list[str]
    files: list[FileRoleDict]
    open_questions: list[str]
    warnings: list[str]
    timestamp: str
