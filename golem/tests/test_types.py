# golem/tests/test_types.py
"""Tests for shared TypedDict contracts in golem/types.py."""

from golem.types import (
    ActiveTaskDict,
    AlertDict,
    CompletedTaskDict,
    ConfigSnapshotDict,
    HeartbeatCandidateDict,
    HeartbeatSnapshotDict,
    LiveSnapshotDict,
    MilestoneDict,
    RunRecordDict,
    StreamEventDict,
    TrackerExportDict,
    ValidationResultDict,
    VerificationResultDict,
)


class TestMilestoneDict:
    def test_milestone_dict_has_required_keys(self):
        """MilestoneDict must define the exact keys the event_tracker produces."""
        entry: MilestoneDict = {
            "kind": "tool_call",
            "tool_name": "Read",
            "summary": "reading file",
            "timestamp": 1741510800.0,
            "is_error": False,
        }
        assert entry["kind"] == "tool_call"
        assert entry["tool_name"] == "Read"
        assert entry["summary"] == "reading file"
        assert entry["timestamp"] == 1741510800.0
        assert entry["is_error"] is False

    def test_milestone_dict_optional_full_text(self):
        """full_text is optional — dict is valid without it."""
        entry: MilestoneDict = {
            "kind": "text",
            "tool_name": "",
            "summary": "truncated",
            "timestamp": 1741510800.0,
            "is_error": False,
        }
        assert "full_text" not in entry
        # __optional_keys__ / __required_keys__ are CPython TypedDict internals,
        # stable since 3.9 and the only way to introspect NotRequired at runtime.
        opt = MilestoneDict.__optional_keys__  # pylint: disable=no-member
        req = MilestoneDict.__required_keys__  # pylint: disable=no-member
        assert "full_text" in opt
        assert "full_text" not in req

    def test_milestone_dict_with_full_text(self):
        entry: MilestoneDict = {
            "kind": "text",
            "tool_name": "",
            "summary": "truncated",
            "full_text": "the complete untruncated text",
            "timestamp": 1741510800.0,
            "is_error": False,
        }
        assert entry["full_text"] == "the complete untruncated text"


class TestTrackerExportDict:
    def test_tracker_export_dict_has_required_keys(self):
        entry: TrackerExportDict = {
            "session_id": 123,
            "tools_called": ["Read", "Edit"],
            "mcp_tools_called": [],
            "errors": [],
            "last_activity": "reading file",
            "last_text": "",
            "cost_usd": 1.23,
            "milestone_count": 5,
            "finished": True,
            "event_log": [],
        }
        assert entry["session_id"] == 123
        assert entry["finished"] is True


class TestActiveTaskDict:
    def test_required_keys(self):
        entry: ActiveTaskDict = {
            "event_id": "12345",
            "flow": "golem",
            "model": "opus",
            "phase": "building",
            "elapsed_s": 30.5,
        }
        assert entry["phase"] == "building"


class TestCompletedTaskDict:
    def test_required_keys(self):
        entry: CompletedTaskDict = {
            "event_id": "12345",
            "flow": "golem",
            "success": True,
            "duration_s": 120.0,
            "cost_usd": 0.45,
            "finished_ago_s": 60.0,
        }
        assert entry["success"] is True


class TestLiveSnapshotDict:
    def test_required_keys(self):
        entry: LiveSnapshotDict = {
            "uptime_s": 3600.0,
            "active_tasks": [],
            "active_count": 0,
            "queue_depth": 0,
            "queued_event_ids": [],
            "models_active": {},
            "recently_completed": [],
        }
        assert entry["uptime_s"] == 3600.0


class TestRunRecordDict:
    def test_required_keys(self):
        entry: RunRecordDict = {
            "event_id": "12345",
            "flow": "golem",
            "task_id": "999",
            "source": "redmine",
            "started_at": "2026-03-09T10:00:00",
            "finished_at": "2026-03-09T10:05:00",
            "duration_s": 300.0,
            "success": True,
            "error": None,
            "model": "opus",
            "cost_usd": 1.23,
            "input_tokens": 1000,
            "output_tokens": 500,
            "actions_taken": ["Read", "Edit"],
            "verdict": "PASS",
            "trace_file": "/tmp/trace.jsonl",
            "queue_wait_ms": 100,
        }
        assert entry["success"] is True


class TestAlertDict:
    def test_required_keys(self):
        entry: AlertDict = {
            "type": "consecutive_failures",
            "message": "3 failures in a row",
            "value": 3.0,
            "threshold": 3.0,
        }
        assert entry["type"] == "consecutive_failures"


class TestStreamEventDict:
    def test_minimal(self):
        """StreamEventDict uses total=False -- all keys are optional."""
        entry: StreamEventDict = {}
        assert isinstance(entry, dict)

    def test_all_keys_optional(self):
        """Every key in StreamEventDict must be optional (total=False)."""
        # __optional_keys__ / __required_keys__ are CPython TypedDict internals,
        # stable since 3.9 and the only way to introspect total=False at runtime.
        req = StreamEventDict.__required_keys__  # pylint: disable=no-member
        assert len(req) == 0, f"Expected no required keys, got: {req}"

    def test_with_common_keys(self):
        entry: StreamEventDict = {
            "type": "assistant",
            "subtype": "text",
            "cost_usd": 0.05,
            "duration_ms": 1200,
        }
        assert entry["type"] == "assistant"


class TestConfigSnapshotDict:
    def test_required_keys(self):
        entry: ConfigSnapshotDict = {
            "model": "opus",
            "max_concurrent": 2,
            "budget": 5.0,
            "timeout": 300,
            "flows": {"golem": True},
            "flow_models": {"golem": "opus"},
        }
        assert entry["model"] == "opus"


class TestValidationResultDict:
    def test_required_keys(self):
        entry: ValidationResultDict = {
            "verdict": "PASS",
            "confidence": 0.92,
            "summary": "All good",
            "concerns": [],
            "files_to_fix": [],
            "test_failures": [],
            "task_type": "code_change",
        }
        assert entry["verdict"] == "PASS"


class TestVerificationResultDict:
    def test_required_keys(self):
        entry: VerificationResultDict = {
            "passed": True,
            "black_ok": True,
            "black_output": "",
            "pylint_ok": True,
            "pylint_output": "",
            "pytest_ok": True,
            "pytest_output": "",
            "test_count": 189,
            "failures": [],
            "coverage_pct": 100.0,
            "duration_s": 3.5,
        }
        assert entry["passed"] is True
        assert entry["test_count"] == 189


class TestHeartbeatCandidateDict:
    def test_required_keys(self):
        candidate: HeartbeatCandidateDict = {
            "id": "github:42",
            "subject": "Fix login bug",
            "body": "Steps to reproduce...",
            "automatable": True,
            "confidence": 0.85,
            "complexity": "small",
            "reason": "Clear bug fix",
            "tier": 1,
        }
        assert candidate["id"] == "github:42"
        assert candidate["confidence"] == 0.85


class TestHeartbeatSnapshotDict:
    def test_required_keys(self):
        snap: HeartbeatSnapshotDict = {
            "enabled": True,
            "state": "idle",
            "last_scan_at": "2026-03-15T10:30:00Z",
            "last_scan_tier": 1,
            "daily_spend_usd": 0.03,
            "daily_budget_usd": 1.0,
            "inflight_task_ids": [],
            "candidate_count": 3,
            "dedup_entry_count": 12,
        }
        assert snap["enabled"] is True
        assert snap["state"] == "idle"
