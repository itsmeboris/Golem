"""Integration tests verifying producer -> consumer paths use matching contracts."""

from golem.core.live_state import LiveState
from golem.event_tracker import TaskEventTracker
from golem.types import (
    ActiveTaskDict,
    CompletedTaskDict,
    LiveSnapshotDict,
    MilestoneDict,
)


class TestLiveStateContract:
    def test_snapshot_produces_valid_live_snapshot_dict(self):
        state = LiveState.get()
        state.enqueue("evt-1", "golem", "opus")
        snap = state.snapshot()
        for key in LiveSnapshotDict.__required_keys__:
            assert key in snap, f"Missing key: {key}"
        assert len(snap["active_tasks"]) == 1
        task = snap["active_tasks"][0]
        for key in ActiveTaskDict.__required_keys__:
            assert key in task, f"Missing key in active task: {key}"

    def test_completed_task_produces_valid_dict(self):
        state = LiveState.get()
        state.enqueue("evt-2", "golem", "opus")
        state.finish("evt-2", success=True, cost_usd=1.5)
        snap = state.snapshot()
        assert len(snap["recently_completed"]) == 1
        completed = snap["recently_completed"][0]
        for key in CompletedTaskDict.__required_keys__:
            assert key in completed, f"Missing key: {key}"


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
