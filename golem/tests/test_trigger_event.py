# pylint: disable=too-few-public-methods
"""Tests for golem.core.triggers.base — TriggerEvent serde."""

from datetime import datetime

from golem.core.triggers.base import TriggerEvent


class TestTriggerEventToDict:
    def test_roundtrip(self):
        ts = datetime(2025, 6, 1, 12, 0, 0)
        ev = TriggerEvent(
            flow_name="myflow",
            event_id="e-1",
            data={"k": "v"},
            timestamp=ts,
            source="test",
        )
        d = ev.to_dict()
        assert d["flow_name"] == "myflow"
        assert d["event_id"] == "e-1"
        assert d["timestamp"] == ts.isoformat()
        assert d["source"] == "test"

    def test_from_dict_iso_string(self):
        ts = datetime(2025, 6, 1, 12, 0, 0)
        d = {
            "flow_name": "f",
            "event_id": "e",
            "data": {"x": 1},
            "timestamp": ts.isoformat(),
            "source": "s",
        }
        ev = TriggerEvent.from_dict(d)
        assert ev.flow_name == "f"
        assert ev.timestamp == ts

    def test_from_dict_datetime_object(self):
        ts = datetime(2025, 6, 1)
        d = {
            "flow_name": "f",
            "event_id": "e",
            "timestamp": ts,
        }
        ev = TriggerEvent.from_dict(d)
        assert ev.timestamp == ts
        assert ev.source == "unknown"

    def test_from_dict_missing_timestamp(self):
        d = {"flow_name": "f", "event_id": "e"}
        ev = TriggerEvent.from_dict(d)
        assert isinstance(ev.timestamp, datetime)

    def test_roundtrip_full(self):
        ev = TriggerEvent(flow_name="a", event_id="b", data={"z": 9})
        restored = TriggerEvent.from_dict(ev.to_dict())
        assert restored.flow_name == ev.flow_name
        assert restored.event_id == ev.event_id
        assert restored.data == ev.data
