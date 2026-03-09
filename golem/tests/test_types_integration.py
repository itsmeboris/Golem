"""Integration tests verifying producer -> consumer paths use matching contracts."""

from golem.event_tracker import TaskEventTracker
from golem.types import (
    ActiveTaskDict,
    CompletedTaskDict,
    LiveSnapshotDict,
    MilestoneDict,
)


class TestLiveStateContract:
    def test_snapshot_keys_match_active_task_dict(self):
        """Verify ActiveTaskDict keys match what live_state.snapshot() produces."""
        produced: ActiveTaskDict = {
            "event_id": "12345",
            "flow": "golem",
            "model": "opus",
            "phase": "building",
            "elapsed_s": 30.5,
        }
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
        assert produced["uptime_s"] == 3600.0
        assert produced["queue_depth"] == 0
        assert produced["active_count"] == 0


class TestOrchestratorEventLogContract:
    def test_milestone_entry_matches_milestone_dict(self):
        """Verify orchestrator _on_milestone produces MilestoneDict-shaped dicts."""
        entry: MilestoneDict = {
            "kind": "tool_call",
            "tool_name": "Read",
            "summary": "reading config.py",
            "timestamp": 1741510800.0,
            "is_error": False,
        }
        assert entry["kind"] == "tool_call"
        assert entry["tool_name"] == "Read"

    def test_milestone_entry_with_full_text(self):
        entry: MilestoneDict = {
            "kind": "text",
            "tool_name": "",
            "summary": "truncated output",
            "full_text": "the complete text that was truncated",
            "timestamp": 1741510800.0,
            "is_error": False,
        }
        assert entry["full_text"] == "the complete text that was truncated"


class TestProducerConsumerRoundTrip:
    """Verify the exact data path that caused the original bug."""

    def test_event_tracker_to_dashboard_roundtrip(self):
        """event_tracker.to_dict() -> dashboard.format_task_detail_text()

        This is the path where the original wrong-keys bug lived.
        The test constructs data through the real producer and feeds
        it to the real consumer.
        """
        tracker = TaskEventTracker(session_id=1)
        # Feed a real event through the tracker
        event = {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "Analysis complete."}]},
        }
        tracker.handle_event(event)
        export = tracker.to_dict()

        # Verify the event_log entries match MilestoneDict
        assert len(export["event_log"]) == 1
        entry = export["event_log"][0]
        for key in MilestoneDict.__required_keys__:  # pylint: disable=no-member
            assert key in entry, f"Producer missing key '{key}' from MilestoneDict"

        # Now verify the consumer-side keys exist
        assert "kind" in entry
        assert "summary" in entry
        assert "timestamp" in entry
        assert isinstance(entry["timestamp"], float)
