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
