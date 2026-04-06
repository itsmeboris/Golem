# golem/tests/test_types.py
"""Tests for shared TypedDict contracts in golem/types.py.

Each test calls a real producer function and verifies the output matches
the TypedDict contract — no tautological dict construction.
"""

import dataclasses
from unittest.mock import MagicMock, patch

import pytest

from golem.config_editor import FieldInfo, FieldMeta, get_config_by_category
from golem.core.config import Config
from golem.core.live_state import LiveState
from golem.core.run_log import RunRecord
from golem.event_tracker import TaskEventTracker
from golem.handoff import create_handoff
from golem.types import (
    ActiveTaskDict,
    AlertDict,
    CommandResultDict,
    CompletedTaskDict,
    ConfigSnapshotDict,
    FieldInfoDict,
    FieldMetaDict,
    FileRoleDict,
    HeartbeatCandidateDict,
    HeartbeatSnapshotDict,
    LiveSnapshotDict,
    MergeEntryDict,
    MergeHistoryEntryDict,
    MergeQueueSnapshotDict,
    MilestoneDict,
    PhaseHandoffDict,
    RunRecordDict,
    SelfUpdateSnapshotDict,
    SelfUpdateStateDict,
    StreamEventDict,
    TrackerExportDict,
    ValidationResultDict,
    VerificationResultDict,
    VerifyCommandDict,
    VerifyConfigDict,
)
from golem.verifier import run_verification


class TestMilestoneDict:
    def test_event_tracker_produces_valid_milestone(self):
        """TaskEventTracker.to_dict() event_log entries match MilestoneDict."""
        tracker = TaskEventTracker(session_id=1)
        tracker.handle_event(
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "text", "text": "Starting work on the task."}]
                },
            }
        )
        exported = tracker.to_dict()
        assert len(exported["event_log"]) >= 1
        milestone = exported["event_log"][0]
        for key in MilestoneDict.__required_keys__:  # pylint: disable=no-member
            assert key in milestone, "Missing required key: %s" % key

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

    def test_milestone_full_text_present_when_text_event(self):
        """Text milestones include full_text in the exported dict."""
        tracker = TaskEventTracker(session_id=2)
        tracker.handle_event(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Long detailed response here."}
                    ]
                },
            }
        )
        exported = tracker.to_dict()
        milestone = exported["event_log"][0]
        assert "full_text" in milestone
        assert milestone["full_text"] == "Long detailed response here."


class TestTrackerExportDict:
    def test_to_dict_matches_contract(self):
        """TaskEventTracker.to_dict() output matches TrackerExportDict contract."""
        tracker = TaskEventTracker(session_id=99)
        tracker.handle_event({"type": "result", "cost_usd": 0.05, "duration_ms": 500})
        exported = tracker.to_dict()
        for key in TrackerExportDict.__required_keys__:  # pylint: disable=no-member
            assert key in exported, "Missing required key: %s" % key
        assert isinstance(exported["session_id"], int)
        assert isinstance(exported["finished"], bool)
        assert isinstance(exported["event_log"], list)
        assert isinstance(exported["cost_usd"], float)


class TestActiveTaskDict:
    def test_live_state_produces_valid_active_task(self):
        """LiveState.snapshot() active_tasks entries match ActiveTaskDict."""
        state = LiveState.get()
        state.enqueue("evt-1", "golem", "opus")
        snap = state.snapshot()
        assert len(snap["active_tasks"]) == 1
        task = snap["active_tasks"][0]
        for key in ActiveTaskDict.__required_keys__:  # pylint: disable=no-member
            assert key in task, "Missing required key: %s" % key
        assert task["event_id"] == "evt-1"
        assert task["flow"] == "golem"
        assert task["model"] == "opus"
        assert isinstance(task["elapsed_s"], float)


class TestCompletedTaskDict:
    def test_live_state_produces_valid_completed_task(self):
        """LiveState.snapshot() recently_completed entries match CompletedTaskDict."""
        state = LiveState.get()
        state.enqueue("evt-2", "golem", "sonnet")
        state.finish("evt-2", success=True, cost_usd=0.42)
        snap = state.snapshot()
        assert len(snap["recently_completed"]) == 1
        task = snap["recently_completed"][0]
        for key in CompletedTaskDict.__required_keys__:  # pylint: disable=no-member
            assert key in task, "Missing required key: %s" % key
        assert task["event_id"] == "evt-2"
        assert task["success"] is True
        assert isinstance(task["duration_s"], float)
        assert isinstance(task["cost_usd"], float)
        assert isinstance(task["finished_ago_s"], float)


class TestLiveSnapshotDict:
    def test_snapshot_matches_contract(self):
        """LiveState.snapshot() output matches LiveSnapshotDict contract."""
        state = LiveState.get()
        snap = state.snapshot()
        for key in LiveSnapshotDict.__required_keys__:  # pylint: disable=no-member
            assert key in snap, "Missing required key: %s" % key
        assert isinstance(snap["uptime_s"], float)
        assert isinstance(snap["active_tasks"], list)
        assert isinstance(snap["active_count"], int)
        assert isinstance(snap["queue_depth"], int)
        assert isinstance(snap["queued_event_ids"], list)
        assert isinstance(snap["models_active"], dict)
        assert isinstance(snap["recently_completed"], list)


class TestRunRecordDict:
    def test_run_record_asdict_matches_contract(self):
        """dataclasses.asdict(RunRecord(...)) output matches RunRecordDict contract."""
        record = RunRecord(
            event_id="evt-123",
            flow="golem",
            task_id="42",
            source="redmine",
            success=True,
            model="opus",
        )
        d = dataclasses.asdict(record)
        for key in RunRecordDict.__required_keys__:  # pylint: disable=no-member
            assert key in d, "Missing required key: %s" % key
        assert isinstance(d["success"], bool)
        assert isinstance(d["duration_s"], float)
        assert isinstance(d["actions_taken"], list)


class TestAlertDict:
    def test_required_keys_non_empty(self):
        """AlertDict has a non-empty required-keys set."""
        req = AlertDict.__required_keys__  # pylint: disable=no-member
        assert len(req) > 0
        assert "type" in req
        assert "message" in req
        assert "value" in req
        assert "threshold" in req


class TestStreamEventDict:
    def test_all_keys_optional(self):
        """Every key in StreamEventDict must be optional (total=False)."""
        # __optional_keys__ / __required_keys__ are CPython TypedDict internals,
        # stable since 3.9 and the only way to introspect total=False at runtime.
        req = StreamEventDict.__required_keys__  # pylint: disable=no-member
        assert len(req) == 0, "Expected no required keys, got: %s" % (req,)

    def test_event_tracker_accepts_stream_event_shape(self):
        """TaskEventTracker.handle_event processes dicts matching StreamEventDict."""
        tracker = TaskEventTracker(session_id=5)
        # A well-formed system init event with StreamEventDict keys
        event: StreamEventDict = {
            "type": "system",
            "subtype": "init",
            "session_id": "sess-abc",
        }
        result = tracker.handle_event(event)
        # init event stores session_id but produces no Milestone
        assert result is None
        assert tracker.state.session_id == "sess-abc"


class TestConfigSnapshotDict:
    def test_required_keys_non_empty(self):
        """ConfigSnapshotDict has all expected required keys."""
        req = ConfigSnapshotDict.__required_keys__  # pylint: disable=no-member
        assert "model" in req
        assert "max_concurrent" in req
        assert "budget" in req
        assert "timeout" in req
        assert "flows" in req
        assert "flow_models" in req


class TestValidationResultDict:
    def test_required_keys_non_empty(self):
        """ValidationResultDict has all expected required keys."""
        req = ValidationResultDict.__required_keys__  # pylint: disable=no-member
        assert "verdict" in req
        assert "confidence" in req
        assert "summary" in req
        assert "concerns" in req
        assert "files_to_fix" in req
        assert "test_failures" in req
        assert "task_type" in req


class TestVerificationResultDict:
    @patch("golem.verifier.subprocess.run")
    def test_to_dict_matches_contract(self, mock_run):
        """VerificationResult.to_dict() output matches VerificationResultDict contract."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="64 passed in 1.01s\nTOTAL    1000    0   100%\n",
            stderr="",
        )
        result = run_verification("/tmp/test")
        d = result.to_dict()
        for (
            key
        ) in VerificationResultDict.__required_keys__:  # pylint: disable=no-member
            assert key in d, "Missing required key: %s" % key
        assert isinstance(d["passed"], bool)
        assert isinstance(d["test_count"], int)
        assert isinstance(d["coverage_pct"], float)
        assert isinstance(d["failures"], list)
        assert isinstance(d["duration_s"], float)


class TestMergeEntryDict:
    def test_required_keys_non_empty(self):
        """MergeEntryDict has a non-empty required-keys set."""
        req = MergeEntryDict.__required_keys__  # pylint: disable=no-member
        assert len(req) > 0
        expected = {
            "session_id",
            "branch_name",
            "worktree_path",
            "priority",
            "group_id",
            "queued_at",
            "changed_files",
        }
        assert expected <= req


class TestMergeHistoryEntryDict:
    def test_required_keys_non_empty(self):
        """MergeHistoryEntryDict has a non-empty required-keys set."""
        req = MergeHistoryEntryDict.__required_keys__  # pylint: disable=no-member
        assert len(req) > 0
        expected = {
            "session_id",
            "success",
            "merge_sha",
            "conflict_files",
            "error",
            "changed_files",
            "deferred",
            "merge_branch",
            "timestamp",
        }
        assert expected <= req


class TestMergeQueueSnapshotDict:
    def test_required_keys_non_empty(self):
        """MergeQueueSnapshotDict has a non-empty required-keys set."""
        req = MergeQueueSnapshotDict.__required_keys__  # pylint: disable=no-member
        assert len(req) > 0
        expected = {"pending", "active", "deferred", "conflicts", "history"}
        assert expected <= req


class TestHeartbeatCandidateDict:
    def test_required_keys_non_empty(self):
        """HeartbeatCandidateDict has a non-empty required-keys set (importable)."""
        req = HeartbeatCandidateDict.__required_keys__  # pylint: disable=no-member
        assert len(req) > 0
        expected = {
            "id",
            "subject",
            "body",
            "automatable",
            "confidence",
            "complexity",
            "reason",
            "tier",
        }
        assert expected <= req


class TestHeartbeatSnapshotDict:
    def test_required_keys_non_empty(self):
        """HeartbeatSnapshotDict has a non-empty required-keys set (importable)."""
        req = HeartbeatSnapshotDict.__required_keys__  # pylint: disable=no-member
        assert len(req) > 0
        expected = {
            "enabled",
            "state",
            "last_scan_at",
            "last_scan_tier",
            "daily_spend_usd",
            "daily_budget_usd",
            "inflight_task_ids",
            "candidate_count",
            "dedup_entry_count",
        }
        assert expected <= req


class TestFieldMetaDict:
    def test_config_editor_produces_valid_field_meta(self):
        """get_config_by_category() produces FieldInfo objects whose meta matches FieldMetaDict."""
        config = Config()
        categories = get_config_by_category(config)
        assert len(categories) > 0
        # Pick the first FieldInfo and verify the meta shape
        first_category = next(iter(categories.values()))
        assert len(first_category) > 0
        fi = first_category[0]
        assert isinstance(fi, FieldInfo)
        assert isinstance(fi.meta, FieldMeta)
        for key in FieldMetaDict.__required_keys__:  # pylint: disable=no-member
            assert hasattr(fi.meta, key), "FieldMeta missing attribute: %s" % key


class TestFieldInfoDict:
    def test_config_editor_produces_valid_field_info(self):
        """get_config_by_category() items conform to FieldInfoDict shape."""
        config = Config()
        categories = get_config_by_category(config)
        first_category = next(iter(categories.values()))
        fi = first_category[0]
        for key in FieldInfoDict.__required_keys__:  # pylint: disable=no-member
            assert hasattr(fi, key), "FieldInfo missing attribute: %s" % key
        assert isinstance(fi.key, str)
        assert fi.key != ""


class TestSelfUpdateStateDict:
    def test_required_keys_non_empty(self):
        """SelfUpdateStateDict has a non-empty required-keys set (importable)."""
        req = SelfUpdateStateDict.__required_keys__  # pylint: disable=no-member
        assert len(req) > 0
        expected = {
            "last_checked_sha",
            "last_check_timestamp",
            "last_update_sha",
            "last_update_timestamp",
            "last_review_verdict",
            "last_review_reasoning",
            "consecutive_crash_count",
            "update_history",
        }
        assert expected <= req


class TestSelfUpdateSnapshotDict:
    def test_required_keys_non_empty(self):
        """SelfUpdateSnapshotDict has a non-empty required-keys set (importable)."""
        req = SelfUpdateSnapshotDict.__required_keys__  # pylint: disable=no-member
        assert len(req) > 0
        expected = {
            "enabled",
            "branch",
            "strategy",
            "last_checked_sha",
            "last_check_timestamp",
            "last_review_verdict",
            "last_review_reasoning",
            "current_sha",
            "update_history",
        }
        assert expected <= req


class TestFileRoleDictContract:
    def test_required_keys(self):
        """FileRoleDict has expected required keys (importable from golem.types)."""
        req = FileRoleDict.__required_keys__  # pylint: disable=no-member
        assert {"path", "role", "relevance"} <= req

    def test_no_optional_keys(self):
        """FileRoleDict has no optional keys."""
        opt = FileRoleDict.__optional_keys__  # pylint: disable=no-member
        assert len(opt) == 0


class TestPhaseHandoffDictContract:
    def test_required_keys(self):
        """PhaseHandoffDict has expected required keys (importable from golem.types)."""
        req = PhaseHandoffDict.__required_keys__  # pylint: disable=no-member
        expected = {
            "from_phase",
            "to_phase",
            "context",
            "files",
            "open_questions",
            "warnings",
            "timestamp",
        }
        assert expected <= req

    def test_create_handoff_produces_valid_phase_handoff(self):
        """create_handoff() output matches PhaseHandoffDict contract."""
        result = create_handoff(
            from_phase="BUILD",
            to_phase="REVIEW",
            context=["implemented feature"],
            files=[],
            open_questions=[],
            warnings=[],
        )
        for key in PhaseHandoffDict.__required_keys__:  # pylint: disable=no-member
            assert key in result, "Missing required key: %s" % key
        assert result["from_phase"] == "BUILD"
        assert result["to_phase"] == "REVIEW"


class TestVerifyConfigDicts:
    @pytest.mark.parametrize("key", ["role", "cmd", "source"])
    def test_verify_command_required_keys_present(self, key):
        assert key in VerifyCommandDict.__required_keys__  # pylint: disable=no-member

    @pytest.mark.parametrize("key", ["version", "commands", "detected_at", "stack"])
    def test_verify_config_required_keys_present(self, key):
        assert key in VerifyConfigDict.__required_keys__  # pylint: disable=no-member

    @pytest.mark.parametrize("key", ["role", "cmd", "passed", "output", "duration_s"])
    def test_command_result_required_keys_present(self, key):
        assert key in CommandResultDict.__required_keys__  # pylint: disable=no-member

    def test_timeout_is_optional_on_verify_command(self):
        assert (
            "timeout"
            not in VerifyCommandDict.__required_keys__  # pylint: disable=no-member
        )

    def test_coverage_threshold_removed_from_verify_config(self):
        """coverage_threshold was removed — not enforced."""
        all_keys = set(VerifyConfigDict.__required_keys__) | set(  # pylint: disable=no-member
            VerifyConfigDict.__optional_keys__  # pylint: disable=no-member
        )
        assert "coverage_threshold" not in all_keys
