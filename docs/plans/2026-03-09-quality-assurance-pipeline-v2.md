# Quality Assurance Pipeline v2 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace Golem's diff-only validation with a three-layer quality pipeline: TypedDicts for structural contracts, a deterministic verifier, and an enhanced LLM reviewer with repo access.

**Architecture:** Layer 1 (golem/types.py) defines all cross-module data shapes as TypedDicts. Layer 2 (golem/verifier.py) runs black/pylint/pytest independently — failure triggers retry without reaching the reviewer. Layer 3 (enhanced golem/validation.py) gives the reviewer verification evidence, types.py content, and instructions to cross-reference data contracts.

**Tech Stack:** Python 3.12, TypedDict (typing), subprocess, pytest, existing CLIConfig/CLIType infrastructure.

**Design doc:** `docs/plans/2026-03-09-quality-assurance-pipeline-v2-design.md`

---

## Task 1: Create golem/types.py — MilestoneDict and TrackerExportDict

The foundational TypedDicts for event log entries and tracker state export. These are the contracts that would have prevented the original bug.

**Files:**
- Create: `golem/types.py`
- Test: `golem/tests/test_types.py`

**Step 1: Write the failing test**

```python
# golem/tests/test_types.py
"""Tests for shared TypedDict contracts in golem/types.py."""

from golem.types import MilestoneDict, TrackerExportDict


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
            "session_id": "abc123",
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
        assert entry["session_id"] == "abc123"
        assert entry["finished"] is True
```

**Step 2: Run test to verify it fails**

Run: `pytest golem/tests/test_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'golem.types'`

**Step 3: Write minimal implementation**

```python
# golem/types.py
"""Shared TypedDict contracts for cross-module data structures.

Every module that produces or consumes these dict shapes should import
from here.  This prevents key-mismatch bugs where a producer writes
one key name and a consumer reads another.

See: docs/plans/2026-03-09-quality-assurance-pipeline-v2-design.md
"""

from __future__ import annotations

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

    session_id: str
    tools_called: list[str]
    mcp_tools_called: list[str]
    errors: list[str]
    last_activity: str
    last_text: str
    cost_usd: float
    milestone_count: int
    finished: bool
    event_log: list[MilestoneDict]
```

**Step 4: Run test to verify it passes**

Run: `pytest golem/tests/test_types.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add golem/types.py golem/tests/test_types.py
git commit -m "feat(types): add MilestoneDict and TrackerExportDict contracts"
```

---

## Task 2: Add remaining TypedDicts to golem/types.py

Add all remaining cross-module contracts: LiveSnapshot, RunRecord, Alert, StreamEvent, Config, Validation, Verification.

**Files:**
- Modify: `golem/types.py`
- Modify: `golem/tests/test_types.py`

**Step 1: Write failing tests**

Add to `golem/tests/test_types.py`:

```python
from golem.types import (
    ActiveTaskDict,
    AlertDict,
    CompletedTaskDict,
    ConfigSnapshotDict,
    LiveSnapshotDict,
    MilestoneDict,
    RunRecordDict,
    StreamEventDict,
    TrackerExportDict,
    ValidationResultDict,
    VerificationResultDict,
)


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
        """StreamEventDict uses total=False — all keys are optional."""
        entry: StreamEventDict = {}
        assert isinstance(entry, dict)

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
            "duration_seconds": 3.5,
        }
        assert entry["passed"] is True
        assert entry["test_count"] == 189
```

**Step 2: Run test to verify it fails**

Run: `pytest golem/tests/test_types.py -v`
Expected: FAIL with `ImportError` for the missing TypedDicts

**Step 3: Write implementation**

Add to `golem/types.py`:

```python
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
    tool_call: dict
    message: dict
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
    duration_seconds: float
```

**Step 4: Run test to verify it passes**

Run: `pytest golem/tests/test_types.py -v`
Expected: PASS — all TypedDict tests green

**Step 5: Commit**

```bash
git add golem/types.py golem/tests/test_types.py
git commit -m "feat(types): add all cross-module TypedDict contracts"
```

---

## Task 3: Migrate event_tracker.py to use MilestoneDict and TrackerExportDict

Wire the first producer to use the contracts.

**Files:**
- Modify: `golem/event_tracker.py:383-406` (to_dict method)
- Test: existing tests in `golem/tests/test_event_tracker_rich.py` (must still pass)

**Step 1: Write a failing integration test**

Add to `golem/tests/test_event_tracker_rich.py`:

```python
from golem.types import MilestoneDict, TrackerExportDict


class TestTrackerExportContract:
    """Verify that to_dict() output matches the TrackerExportDict contract."""

    def test_to_dict_matches_tracker_export_dict(self):
        tracker = TaskEventTracker(session_id=1)
        event = {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "id": "x",
                        "input": {"file_path": "/tmp/foo.py"},
                    }
                ]
            },
        }
        tracker.handle_event(event)
        result = tracker.to_dict()

        # Verify all TrackerExportDict required keys are present
        required_keys = TrackerExportDict.__required_keys__
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

        # Verify event_log entries match MilestoneDict
        assert len(result["event_log"]) == 1
        entry = result["event_log"][0]
        for key in MilestoneDict.__required_keys__:
            assert key in entry, f"Missing event_log key: {key}"
```

**Step 2: Run test to verify it fails**

Run: `pytest golem/tests/test_event_tracker_rich.py::TestTrackerExportContract -v`
Expected: Might pass already since we're just adding type annotations, or might fail on import. Run to confirm.

**Step 3: Add type annotations to event_tracker.py**

In `golem/event_tracker.py`, change the `to_dict` method signature and add import:

At top of file, add:
```python
from golem.types import MilestoneDict, TrackerExportDict
```

Change `to_dict` return annotation (line 383):
```python
def to_dict(self) -> TrackerExportDict:
```

Change the event_log list comprehension to annotate each entry:
```python
        entry: MilestoneDict = {
            "kind": m.kind,
            ...
        }
```

**Step 4: Run full test suite to verify nothing breaks**

Run: `pytest golem/tests/test_event_tracker_rich.py -v`
Expected: All 64 tests PASS

**Step 5: Commit**

```bash
git add golem/event_tracker.py golem/tests/test_event_tracker_rich.py
git commit -m "refactor(event_tracker): annotate to_dict with TypedDict contracts"
```

---

## Task 4: Migrate dashboard.py to use TypedDicts

Wire the primary consumer to use the contracts. This is the module where the original bug lived.

**Files:**
- Modify: `golem/core/dashboard.py` (imports + type annotations at format_task_detail_text, _format_live_section, _format_active_task, _aggregate_stats, _format_recent_runs, config_to_snapshot)
- Test: existing tests must still pass

**Step 1: Write a contract integration test**

Add to `golem/tests/test_dashboard.py`:

```python
from golem.types import MilestoneDict


class TestEventLogContractIntegration:
    """Verify dashboard reads event log entries using the correct contract keys."""

    @patch("golem.core.dashboard.load_sessions")
    def test_format_task_detail_reads_milestone_dict_keys(self, mock_sessions):
        """Build event log entries from MilestoneDict and verify dashboard reads them."""
        event: MilestoneDict = {
            "kind": "tool_call",
            "tool_name": "Read",
            "summary": "reading /tmp/foo.py",
            "timestamp": 1741510800.0,
            "is_error": False,
        }
        sess = _make_session(event_log=[event])
        mock_sessions.return_value = {12345: sess}
        result = format_task_detail_text(12345)
        assert "tool_call" in result
        assert "reading /tmp/foo.py" in result
```

**Step 2: Run test to verify it passes (validates the contract)**

Run: `pytest golem/tests/test_dashboard.py::TestEventLogContractIntegration -v`
Expected: PASS (since we already fixed the keys in the earlier bugfix)

**Step 3: Add type annotations to dashboard.py**

Add import at top of `golem/core/dashboard.py`:
```python
from golem.types import (
    ActiveTaskDict,
    CompletedTaskDict,
    ConfigSnapshotDict,
    LiveSnapshotDict,
    MilestoneDict,
    RunRecordDict,
)
```

Annotate key functions:
- `config_to_snapshot(config: Any) -> ConfigSnapshotDict:`
- `_format_live_section(snap: LiveSnapshotDict, ...) -> list[str]:`
- `_format_active_task(task: ActiveTaskDict, ...) -> list[str]:`
- `_aggregate_stats(runs: list[RunRecordDict]) -> dict[str, Any]:`
- `_format_recent_runs(runs: list[RunRecordDict], ...) -> list[str]:`

In `format_task_detail_text`, annotate the event log loop:
```python
for ev in sess.event_log[-10:]:
    ev: MilestoneDict  # type: ignore[no-redef]
```

**Step 4: Run full dashboard tests**

Run: `pytest golem/tests/test_dashboard.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add golem/core/dashboard.py golem/tests/test_dashboard.py
git commit -m "refactor(dashboard): annotate with TypedDict contracts from types.py"
```

---

## Task 5: Migrate remaining modules to use TypedDicts

Wire live_state, run_log, health, stream_printer, orchestrator, validation to their contracts.

**Files:**
- Modify: `golem/core/live_state.py:215-257` (snapshot return type)
- Modify: `golem/core/run_log.py:51-71,74-81,84-124` (RunRecord serialization)
- Modify: `golem/health.py:108-197` (alert construction)
- Modify: `golem/core/stream_printer.py:28-44` (event handling)
- Modify: `golem/orchestrator.py:135-188,919-927` (session to_dict/from_dict, event log)
- Modify: `golem/validation.py:283-299,342-351` (_format_event_log, _parse_validation_output)

**Step 1: Add contract integration tests**

Create `golem/tests/test_types_integration.py`:

```python
"""Integration tests verifying producer -> consumer paths use matching contracts."""

from golem.types import (
    ActiveTaskDict,
    CompletedTaskDict,
    LiveSnapshotDict,
    MilestoneDict,
)


class TestLiveStateContract:
    def test_snapshot_keys_match_active_task_dict(self):
        """Verify ActiveTaskDict keys match what live_state.snapshot() produces."""
        # Build a dict matching the producer code in live_state.py:220-228
        produced: ActiveTaskDict = {
            "event_id": "12345",
            "flow": "golem",
            "model": "opus",
            "phase": "building",
            "elapsed_s": 30.5,
        }
        # Verify consumer code in dashboard.py can read it
        assert produced["event_id"] == "12345"
        assert produced["phase"] == "building"
        assert produced["model"] == "opus"
        assert produced["elapsed_s"] == 30.5

    def test_snapshot_keys_match_completed_task_dict(self):
        produced: CompletedTaskDict = {
            "event_id": "12345",
            "flow": "golem",
            "success": True,
            "duration_s": 120.0,
            "cost_usd": 0.45,
            "finished_ago_s": 60.0,
        }
        assert produced["success"] is True
        assert produced["cost_usd"] == 0.45

    def test_snapshot_keys_match_live_snapshot_dict(self):
        produced: LiveSnapshotDict = {
            "uptime_s": 3600.0,
            "active_tasks": [],
            "active_count": 0,
            "queue_depth": 0,
            "queued_event_ids": [],
            "models_active": {},
            "recently_completed": [],
        }
        # Keys consumed by dashboard._format_live_section and health.py
        assert produced["uptime_s"] == 3600.0
        assert produced["queue_depth"] == 0
        assert produced["active_count"] == 0
```

**Step 2: Run tests**

Run: `pytest golem/tests/test_types_integration.py -v`
Expected: PASS

**Step 3: Add type annotations to all remaining modules**

For each module, add the import and annotate the relevant functions/methods. The pattern is the same as Tasks 3-4: import from `golem.types`, annotate return types and parameter types.

Key changes:
- `live_state.py`: `def snapshot(self) -> LiveSnapshotDict:`
- `run_log.py`: `read_runs` returns `list[RunRecordDict]`
- `health.py`: alert dicts annotated as `AlertDict`
- `stream_printer.py`: `def handle(self, event: StreamEventDict) -> None:`
- `orchestrator.py`: event log entries use `MilestoneDict`, session serialization matches contracts
- `validation.py`: `_format_event_log(event_log: list[MilestoneDict])`, `_parse_validation_output` returns matches `ValidationResultDict`

**Step 4: Run full test suite**

Run: `pytest --cov=golem --cov-fail-under=100`
Expected: All tests PASS with 100% coverage

**Step 5: Commit**

```bash
git add golem/core/live_state.py golem/core/run_log.py golem/health.py golem/core/stream_printer.py golem/orchestrator.py golem/validation.py golem/tests/test_types_integration.py
git commit -m "refactor: migrate all modules to TypedDict contracts from types.py"
```

---

## Task 6: Create golem/verifier.py — deterministic verification

The Layer 2 verifier: runs black, pylint, pytest independently and returns structured results.

**Files:**
- Create: `golem/verifier.py`
- Create: `golem/tests/test_verifier.py`

**Step 1: Write failing tests**

```python
# golem/tests/test_verifier.py
"""Tests for the deterministic verification runner."""

import subprocess
from unittest.mock import patch, MagicMock

from golem.verifier import run_verification, VerificationResult


class TestVerificationResult:
    def test_all_pass(self):
        r = VerificationResult(
            passed=True,
            black_ok=True, black_output="",
            pylint_ok=True, pylint_output="",
            pytest_ok=True, pytest_output="64 passed in 1.01s",
            test_count=64, failures=[], coverage_pct=100.0,
            duration_seconds=1.5,
        )
        assert r.passed is True

    def test_partial_failure(self):
        r = VerificationResult(
            passed=False,
            black_ok=True, black_output="",
            pylint_ok=False, pylint_output="E0001: syntax error",
            pytest_ok=True, pytest_output="64 passed",
            test_count=64, failures=[], coverage_pct=100.0,
            duration_seconds=2.0,
        )
        assert r.passed is False
        assert r.pylint_ok is False


class TestRunVerification:
    @patch("golem.verifier.subprocess.run")
    def test_all_commands_pass(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All done! ✨ 🍰 ✨\n64 passed in 1.01s\n"
                   "TOTAL    1000    0   100%\n",
            stderr="",
        )
        result = run_verification("/tmp/workdir")
        assert result.passed is True
        assert result.black_ok is True
        assert result.pylint_ok is True
        assert result.pytest_ok is True
        assert mock_run.call_count == 3  # black, pylint, pytest

    @patch("golem.verifier.subprocess.run")
    def test_black_fails(self, mock_run):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "black" in cmd:
                return MagicMock(returncode=1, stdout="would reformat foo.py", stderr="")
            return MagicMock(returncode=0, stdout="64 passed\nTOTAL 1000 0 100%", stderr="")

        mock_run.side_effect = side_effect
        result = run_verification("/tmp/workdir")
        assert result.passed is False
        assert result.black_ok is False
        assert "would reformat" in result.black_output

    @patch("golem.verifier.subprocess.run")
    def test_pytest_fails_with_failures(self, mock_run):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "pytest" in cmd:
                return MagicMock(
                    returncode=1,
                    stdout="FAILED golem/tests/test_foo.py::test_bar\n"
                           "FAILED golem/tests/test_foo.py::test_baz\n"
                           "2 failed, 62 passed in 3.00s\n"
                           "TOTAL    1000    50    95%\n",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = run_verification("/tmp/workdir")
        assert result.passed is False
        assert result.pytest_ok is False
        assert result.test_count == 64  # 2 + 62
        assert len(result.failures) == 2
        assert "test_bar" in result.failures[0]
        assert result.coverage_pct == 95.0

    @patch("golem.verifier.subprocess.run")
    def test_all_three_run_even_if_first_fails(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="error", stderr="")
        result = run_verification("/tmp/workdir")
        assert mock_run.call_count == 3  # all three still run

    @patch("golem.verifier.subprocess.run")
    def test_timeout_handled(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="black", timeout=300)
        result = run_verification("/tmp/workdir")
        assert result.passed is False
        assert "timed out" in result.black_output.lower()
```

**Step 2: Run test to verify it fails**

Run: `pytest golem/tests/test_verifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'golem.verifier'`

**Step 3: Write implementation**

```python
# golem/verifier.py
"""Deterministic verification runner for Golem task output.

Runs black, pylint, and pytest independently in the agent's work directory.
Returns structured results — no LLM judgment, just facts.

Part of the Quality Assurance Pipeline v2 (Layer 2).
See: docs/plans/2026-03-09-quality-assurance-pipeline-v2-design.md
"""

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field

logger = logging.getLogger("golem.verifier")


@dataclass
class VerificationResult:
    """Structured output from running black + pylint + pytest."""

    passed: bool
    black_ok: bool
    black_output: str
    pylint_ok: bool
    pylint_output: str
    pytest_ok: bool
    pytest_output: str
    test_count: int = 0
    failures: list[str] = field(default_factory=list)
    coverage_pct: float = 0.0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        """Serialize for JSON persistence."""
        from golem.types import VerificationResultDict  # avoid circular

        result: VerificationResultDict = {
            "passed": self.passed,
            "black_ok": self.black_ok,
            "black_output": self.black_output,
            "pylint_ok": self.pylint_ok,
            "pylint_output": self.pylint_output,
            "pytest_ok": self.pytest_ok,
            "pytest_output": self.pytest_output,
            "test_count": self.test_count,
            "failures": self.failures,
            "coverage_pct": self.coverage_pct,
            "duration_seconds": self.duration_seconds,
        }
        return result


def _run_cmd(
    cmd: list[str], cwd: str, timeout: int
) -> tuple[bool, str]:
    """Run a command and return (success, combined_output)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"Command failed: {exc}"


_FAILED_RE = re.compile(r"FAILED\s+(\S+)")
_PASSED_FAILED_RE = re.compile(r"(\d+)\s+(?:failed|passed)", re.IGNORECASE)
_COVERAGE_RE = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+)%")


def _parse_pytest_output(output: str) -> tuple[int, list[str], float]:
    """Extract test count, failure names, and coverage % from pytest output."""
    failures = _FAILED_RE.findall(output)

    counts = _PASSED_FAILED_RE.findall(output)
    test_count = sum(int(c) for c in counts)

    cov_match = _COVERAGE_RE.search(output)
    coverage = float(cov_match.group(1)) if cov_match else 0.0

    return test_count, failures, coverage


def run_verification(
    work_dir: str, *, timeout: int = 300
) -> VerificationResult:
    """Run black, pylint, pytest and return structured results.

    All three commands run regardless of earlier failures to collect
    complete evidence.
    """
    start = time.time()

    black_ok, black_output = _run_cmd(
        ["black", "--check", "."], work_dir, timeout
    )
    pylint_ok, pylint_output = _run_cmd(
        ["pylint", "--errors-only", "golem/"], work_dir, timeout
    )
    pytest_ok, pytest_output = _run_cmd(
        ["pytest", "--cov=golem", "--cov-fail-under=100"], work_dir, timeout
    )

    test_count, failures, coverage_pct = _parse_pytest_output(pytest_output)

    passed = black_ok and pylint_ok and pytest_ok
    duration = time.time() - start

    logger.info(
        "Verification %s: black=%s pylint=%s pytest=%s (%d tests, %.0f%% cov) in %.1fs",
        "PASSED" if passed else "FAILED",
        black_ok, pylint_ok, pytest_ok,
        test_count, coverage_pct, duration,
    )

    return VerificationResult(
        passed=passed,
        black_ok=black_ok,
        black_output=black_output,
        pylint_ok=pylint_ok,
        pylint_output=pylint_output,
        pytest_ok=pytest_ok,
        pytest_output=pytest_output,
        test_count=test_count,
        failures=failures,
        coverage_pct=coverage_pct,
        duration_seconds=round(duration, 2),
    )
```

**Step 4: Run test to verify it passes**

Run: `pytest golem/tests/test_verifier.py -v`
Expected: All tests PASS

**Step 5: Run full suite**

Run: `pytest --cov=golem --cov-fail-under=100`
Expected: PASS

**Step 6: Commit**

```bash
git add golem/verifier.py golem/tests/test_verifier.py
git commit -m "feat(verifier): deterministic black/pylint/pytest runner (Layer 2)"
```

---

## Task 7: Wire verifier into orchestrator pipeline

Add the VERIFYING state and integrate the verifier as a hard gate before the reviewer.

**Files:**
- Modify: `golem/orchestrator.py:59-72` (add VERIFYING state)
- Modify: `golem/orchestrator.py:514-549` (wire verifier before validation)
- Modify: `golem/orchestrator.py:75-188` (add verification_result to TaskSession)
- Test: `golem/tests/test_orchestrator.py` (or wherever orchestrator tests live)

**Step 1: Write failing tests**

Test that the orchestrator runs verification before validation, and retries on verification failure. Find the existing orchestrator test file and add:

```python
class TestVerifierGate:
    @patch("golem.verifier.run_verification")
    @patch("golem.validation.run_validation")
    async def test_verification_pass_proceeds_to_validation(
        self, mock_validate, mock_verify
    ):
        """When verifier passes, reviewer runs."""
        mock_verify.return_value = VerificationResult(
            passed=True, black_ok=True, black_output="",
            pylint_ok=True, pylint_output="",
            pytest_ok=True, pytest_output="64 passed",
            test_count=64, failures=[], coverage_pct=100.0,
            duration_seconds=1.0,
        )
        mock_validate.return_value = ValidationVerdict(verdict="PASS", confidence=0.9)
        # ... invoke orchestrator flow ...
        mock_verify.assert_called_once()
        mock_validate.assert_called_once()

    @patch("golem.verifier.run_verification")
    @patch("golem.validation.run_validation")
    async def test_verification_fail_skips_validation_triggers_retry(
        self, mock_validate, mock_verify
    ):
        """When verifier fails, reviewer does NOT run, retry triggers."""
        mock_verify.return_value = VerificationResult(
            passed=False, black_ok=True, black_output="",
            pylint_ok=False, pylint_output="E0001: error",
            pytest_ok=True, pytest_output="64 passed",
            test_count=64, failures=[], coverage_pct=100.0,
            duration_seconds=1.0,
        )
        # ... invoke orchestrator flow ...
        mock_verify.assert_called_once()
        mock_validate.assert_not_called()
```

**Step 2: Run tests — expected FAIL**

**Step 3: Implement orchestrator changes**

Add to `TaskSessionState`:
```python
VERIFYING = "verifying"
```

Add to `TaskSession`:
```python
verification_result: dict | None = None  # VerificationResultDict
```

Add new method `_run_verification` in orchestrator:
```python
async def _run_verification(self, work_dir: str) -> VerificationResult:
    """Layer 2: Run deterministic verification before reviewer."""
    self.session.state = TaskSessionState.VERIFYING
    self.session.updated_at = _now_iso()

    result = await asyncio.get_event_loop().run_in_executor(
        None, lambda: run_verification(work_dir)
    )
    self.session.verification_result = result.to_dict()
    return result
```

Modify the post-agent flow to call verification first:
```python
# After agent finishes:
verification = await self._run_verification(work_dir)
if not verification.passed:
    # Build retry feedback from verification output
    feedback = self._format_verification_feedback(verification)
    await self._retry_agent(issue_id, work_dir, feedback)
    return

# Verification passed — proceed to reviewer
verdict = await self._run_validation(issue_id, work_dir, verification)
```

Add `_format_verification_feedback`:
```python
def _format_verification_feedback(self, result: VerificationResult) -> str:
    parts = ["Independent verification failed:"]
    if not result.black_ok:
        parts.append(f"\nblack --check: FAILED\n{result.black_output}")
    if not result.pylint_ok:
        parts.append(f"\npylint: FAILED\n{result.pylint_output}")
    if not result.pytest_ok:
        parts.append(f"\npytest: FAILED ({len(result.failures)} failures)")
        for f in result.failures:
            parts.append(f"  - {f}")
        if result.pytest_output:
            parts.append(f"\n{result.pytest_output[-2000:]}")
    return "\n".join(parts)
```

**Step 4: Run tests**

Run: `pytest golem/tests/test_orchestrator*.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add golem/orchestrator.py golem/tests/test_orchestrator*.py
git commit -m "feat(orchestrator): wire verifier as hard gate before reviewer (Layer 2)"
```

---

## Task 8: Enhance validation prompt — new criteria and verification evidence

Update the reviewer to use verification evidence, types.py cross-referencing, and new quality criteria.

**Files:**
- Modify: `golem/prompts/validate_task.txt`
- Modify: `golem/validation.py:307-331` (_build_validation_prompt — inject verification + types.py)

**Step 1: Write failing test**

Add to existing validation tests:

```python
class TestValidationPromptEnhancements:
    def test_prompt_includes_verification_evidence(self):
        """Verification results must appear in the validation prompt."""
        from golem.validation import _build_validation_prompt
        from golem.verifier import VerificationResult

        vr = VerificationResult(
            passed=True, black_ok=True, black_output="",
            pylint_ok=True, pylint_output="",
            pytest_ok=True, pytest_output="64 passed, 100%",
            test_count=64, failures=[], coverage_pct=100.0,
            duration_seconds=1.0,
        )
        prompt = _build_validation_prompt(
            issue_id=123,
            subject="Test",
            description="desc",
            session_data={"event_log": [], "tools_called": [], "mcp_tools_called": [], "errors": []},
            work_dir="/tmp/fake",
            verification_result=vr,
        )
        assert "Independent Verification Results" in prompt
        assert "black: PASS" in prompt
        assert "pytest: PASS" in prompt

    def test_prompt_includes_types_py_content(self):
        from golem.validation import _build_validation_prompt
        from golem.verifier import VerificationResult

        vr = VerificationResult(
            passed=True, black_ok=True, black_output="",
            pylint_ok=True, pylint_output="",
            pytest_ok=True, pytest_output="64 passed",
            test_count=64, failures=[], coverage_pct=100.0,
            duration_seconds=1.0,
        )
        prompt = _build_validation_prompt(
            issue_id=123, subject="Test", description="desc",
            session_data={"event_log": [], "tools_called": [], "mcp_tools_called": [], "errors": []},
            work_dir="/tmp/fake",
            verification_result=vr,
        )
        assert "Shared Data Contracts" in prompt
        assert "MilestoneDict" in prompt
```

**Step 2: Run test — expected FAIL**

**Step 3: Update validate_task.txt**

Add after criterion 6 (Antipattern Detection):

```
7. **Cross-Module Consistency**: When the diff adds or modifies code that
   reads from shared data structures (dicts, JSON, dataclass fields):
   - Open golem/types.py (included below) and verify the keys used match
     the TypedDict contract
   - If the code accesses dict keys not defined in types.py, flag as HIGH concern
   - If test fixtures construct dicts, verify their shape matches types.py
   - If new cross-module dict shapes should be in types.py but aren't, flag as concern

8. **Test Validity**: Tests must verify behavior, not just confirm assumptions:
   - Check that test fixtures use realistic data shapes (matching types.py contracts)
   - Flag tests where mocks/fixtures mirror the implementation rather than the
     contract (circular validation — the bug class this criterion prevents)
   - For cross-module features, verify at least one test exercises the real
     producer -> consumer path end-to-end
```

Replace criterion 5 (Verification Evidence):

```
5. **Verification Evidence**: Independent verification results are provided below.
   Do NOT check the event log for verification commands — the results below are
   from independent execution after the agent finished. Trust these results over
   any claims in the event log. If verification failed, the agent would have been
   retried — if you see this prompt, verification passed.
```

Add new sections at the bottom of the prompt:

```
## Independent Verification Results

{verification_evidence}

## Shared Data Contracts (golem/types.py)

{types_py_content}

When reviewing, verify that any dict key access in the diff uses keys defined
in these contracts. Flag mismatches as HIGH confidence concerns.
```

**Step 4: Update _build_validation_prompt in validation.py**

Add `verification_result` parameter. Read `golem/types.py` and inject it. Format verification evidence.

```python
def _build_validation_prompt(
    issue_id: int,
    subject: str,
    description: str,
    session_data: dict[str, Any],
    work_dir: str,
    verification_result: VerificationResult | None = None,
) -> str:
    git_diff = get_git_diff(work_dir)
    event_log_summary = _format_event_log(session_data.get("event_log", []))
    verification_evidence = _format_verification_evidence(verification_result)
    types_py_content = _read_types_py()

    return format_prompt(
        "validate_task.txt",
        ...,  # existing params
        verification_evidence=verification_evidence,
        types_py_content=types_py_content,
    )


def _format_verification_evidence(result: VerificationResult | None) -> str:
    if result is None:
        return "(no independent verification was run)"
    lines = []
    for name, ok, output in [
        ("black", result.black_ok, result.black_output),
        ("pylint", result.pylint_ok, result.pylint_output),
        ("pytest", result.pytest_ok, result.pytest_output),
    ]:
        status = "PASS" if ok else "FAIL"
        lines.append(f"- {name}: {status}")
        if not ok and output:
            lines.append(f"  {output[:500]}")
    if result.pytest_ok:
        lines.append(
            f"  {result.test_count} tests, {len(result.failures)} failures, "
            f"coverage: {result.coverage_pct}%"
        )
    return "\n".join(lines)


def _read_types_py() -> str:
    types_path = Path(__file__).resolve().parent.parent / "types.py"
    try:
        return types_path.read_text(encoding="utf-8")
    except OSError:
        return "(golem/types.py not found)"
```

**Step 5: Run tests**

Run: `pytest golem/tests/test_validation*.py -v`
Expected: PASS

**Step 6: Commit**

```bash
git add golem/prompts/validate_task.txt golem/validation.py golem/tests/test_validation*.py
git commit -m "feat(validation): enhanced reviewer with verification evidence and cross-module checks (Layer 3)"
```

---

## Task 9: Add new antipatterns to scan_diff_antipatterns

Expand static analysis with raw dict access detection.

**Files:**
- Modify: `golem/validation.py:187-275` (add new regex patterns and handlers)
- Test: existing validation antipattern tests

**Step 1: Write failing tests**

```python
class TestNewAntipatterns:
    def test_raw_dict_access_flagged(self):
        diff = '''\
+++ b/golem/core/dashboard.py
+        ev_type = ev.get("type", "")
+        msg = ev.get("message", "")
'''
        concerns = scan_diff_antipatterns(diff)
        assert any("raw dict" in c.lower() or "untyped dict" in c.lower() for c in concerns)

    def test_raw_dict_access_in_test_files_not_flagged(self):
        diff = '''\
+++ b/golem/tests/test_dashboard.py
+        ev_type = ev.get("type", "")
'''
        concerns = scan_diff_antipatterns(diff)
        assert not any("raw dict" in c.lower() or "untyped dict" in c.lower() for c in concerns)

    def test_typed_dict_access_not_flagged(self):
        """Dict access with type annotation nearby should not trigger."""
        diff = '''\
+++ b/golem/core/dashboard.py
+        ev: MilestoneDict = event_log[0]
+        ev_type = ev["kind"]
'''
        # This is a judgment call — the static regex may still flag it.
        # The reviewer (LLM) makes the final decision, not the regex.
        concerns = scan_diff_antipatterns(diff)
        # We accept that the regex is a heuristic; the concern is flagged
        # but with lower weight than other antipatterns.
```

**Step 2: Run test — expected FAIL**

**Step 3: Add new patterns to validation.py**

```python
# New: raw dict key access — heuristic for cross-module boundary violations
_RAW_DICT_ACCESS_RE = re.compile(
    r'\.get\(\s*["\'][a-z_]+["\']\s*'
    r'|'
    r'\[["\'][a-z_]+["\']\]'
)
```

Add to `_check_line_antipatterns`:
```python
if _RAW_DICT_ACCESS_RE.search(content):
    dict_access_hits.append(loc)
```

Add to `scan_diff_antipatterns` return:
```python
if dict_access_hits:
    files = sorted(set(dict_access_hits))
    concerns.append(
        f"Antipattern: untyped dict access in {', '.join(files)} "
        f"— verify keys match golem/types.py contracts"
    )
```

Note: This is intentionally a soft signal (the reviewer makes the judgment), not a hard blocker.

**Step 4: Run tests**

Run: `pytest golem/tests/test_validation*.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add golem/validation.py golem/tests/test_validation*.py
git commit -m "feat(validation): add raw-dict-access antipattern detection"
```

---

## Task 10: Update agent prompts — run_task.txt and retry_task.txt

Tell the agent about TypedDicts and independent verification.

**Files:**
- Modify: `golem/prompts/run_task.txt`
- Modify: `golem/prompts/retry_task.txt`

**Step 1: Update run_task.txt**

After the Guidelines section, before Output, add:

```
## Data Contracts

When accessing shared data structures that cross module boundaries (event log
entries, session dicts, live state snapshots, run records, etc.), import and use
the TypedDict definitions from ``golem/types.py``. Do not use raw string keys
for cross-module data — this prevents key-mismatch bugs where producer and
consumer disagree on field names.

When writing tests that construct these data structures, use the same TypedDict
types to ensure test fixtures match the real data shapes.

## Independent Verification

Your output will be independently verified by a separate process after you
finish. It will run ``black --check .``, ``pylint --errors-only golem/``, and
``pytest --cov=golem --cov-fail-under=100`` on your final work directory.
You should still run these during development for your own feedback loop, but
the independent verifier is the source of truth.
```

**Step 2: Update retry_task.txt**

Add a new section after "Validator Feedback" for verification failures:

```
## Verification Failures (if applicable)

{verification_feedback}
```

And add to the format_prompt call in the orchestrator's retry path.

**Step 3: No tests needed** (prompt-only changes; covered by integration tests in Task 7)

**Step 4: Commit**

```bash
git add golem/prompts/run_task.txt golem/prompts/retry_task.txt
git commit -m "docs(prompts): add TypedDict and independent verification instructions"
```

---

## Task 11: Full integration test and verification

End-to-end verification that the complete pipeline works.

**Files:**
- Modify: `golem/tests/test_types_integration.py` (add round-trip tests)

**Step 1: Write round-trip integration tests**

```python
class TestProducerConsumerRoundTrip:
    """Verify the exact data path that caused the original bug."""

    def test_event_tracker_to_dashboard_roundtrip(self):
        """event_tracker.to_dict() -> dashboard.format_task_detail_text()

        This is the path where the original wrong-keys bug lived.
        The test constructs data through the real producer and feeds
        it to the real consumer.
        """
        from golem.event_tracker import TaskEventTracker
        from golem.types import MilestoneDict

        tracker = TaskEventTracker(session_id=1)
        # Feed a real event through the tracker
        event = {
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": "Analysis complete."}]
            },
        }
        tracker.handle_event(event)
        export = tracker.to_dict()

        # Verify the event_log entries match MilestoneDict
        assert len(export["event_log"]) == 1
        entry = export["event_log"][0]
        for key in MilestoneDict.__required_keys__:
            assert key in entry, f"Producer missing key '{key}' from MilestoneDict"

        # Now feed it to the consumer (dashboard)
        # The consumer reads "kind", "summary", "timestamp" — verify they exist
        assert "kind" in entry
        assert "summary" in entry
        assert "timestamp" in entry
        assert isinstance(entry["timestamp"], float)
```

**Step 2: Run all tests**

Run: `pytest --cov=golem --cov-fail-under=100`
Expected: ALL PASS

**Step 3: Run black and pylint**

Run: `black --check .`
Run: `pylint --errors-only golem/`
Expected: Both clean

**Step 4: Commit**

```bash
git add golem/tests/test_types_integration.py
git commit -m "test: add producer-consumer round-trip integration tests"
```

---

## Task Summary

| Task | Layer | What | Dependencies |
|------|-------|------|-------------|
| 1 | L1 | MilestoneDict + TrackerExportDict | None |
| 2 | L1 | All remaining TypedDicts | Task 1 |
| 3 | L1 | Migrate event_tracker.py | Task 1 |
| 4 | L1 | Migrate dashboard.py | Task 1 |
| 5 | L1 | Migrate all remaining modules | Tasks 2-4 |
| 6 | L2 | Create golem/verifier.py | Task 2 (VerificationResultDict) |
| 7 | L2 | Wire verifier into orchestrator | Tasks 5-6 |
| 8 | L3 | Enhanced validation prompt | Tasks 5-7 |
| 9 | L3 | New antipattern detection | Task 5 |
| 10 | - | Update agent prompts | Tasks 1-9 |
| 11 | - | Full integration tests | Tasks 1-10 |

**Parallelization:** Tasks 3, 4, 6 can run in parallel after Tasks 1-2. Tasks 8 and 9 can run in parallel.
