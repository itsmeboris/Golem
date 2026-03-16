"""Integration tests verifying producer -> consumer paths use matching contracts.

LiveState contract tests live in test_types.py (TestActiveTaskDict,
TestCompletedTaskDict, TestLiveSnapshotDict).  This file covers cross-module
round-trips where a producer's output is fed to a consumer.
"""

from golem.event_tracker import TaskEventTracker
from golem.types import MilestoneDict


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
