"""Base classes for all trigger types."""

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TriggerEvent:
    """An event to be dispatched to a flow (carries flow name, ID, and payload)."""

    flow_name: str
    event_id: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    source: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (timestamp → ISO string)."""
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TriggerEvent":
        """Deserialize from a dict produced by ``to_dict``."""
        data = dict(data)
        ts_raw = data.pop("timestamp", None)
        if isinstance(ts_raw, str):
            ts = datetime.fromisoformat(ts_raw)
        elif isinstance(ts_raw, datetime):
            ts = ts_raw
        else:
            ts = datetime.now()
        return cls(
            flow_name=data.pop("flow_name"),
            event_id=data.pop("event_id"),
            data=data.pop("data", {}),
            timestamp=ts,
            source=data.pop("source", "unknown"),
        )


class Trigger(ABC):
    """Abstract base for all trigger types (CLI, polling, webhook)."""

    @abstractmethod
    async def start(self) -> None:
        """Begin listening for / producing events."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the trigger."""
