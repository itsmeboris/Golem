"""Core flow abstractions — base classes and trace helpers.

Extracted from ``flows.base`` so that both ``flows/`` and ``agents/`` can
import them without a cross-package dependency.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, TypeVar

from .config import DATA_DIR, Config, FlowConfig
from .live_state import LiveState
from .triggers.base import TriggerEvent

_FC = TypeVar("_FC", bound=FlowConfig)

logger = logging.getLogger("Tools.AgentAutomation.Flows")

TRACES_DIR = DATA_DIR / "traces"


def _write_prompt(flow_name: str, event_id: str, prompt: str) -> str:
    safe_id = event_id.replace("/", "_")
    trace_dir = TRACES_DIR / flow_name
    trace_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = trace_dir / f"{safe_id}.prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")
    path = str(prompt_file.relative_to(DATA_DIR.parent))
    logger.info("Prompt saved: %s", prompt_file)
    return path


def _write_trace(flow_name: str, event_id: str, events: list[dict]) -> str:
    trace_dir = TRACES_DIR / flow_name
    trace_dir.mkdir(parents=True, exist_ok=True)
    safe_id = event_id.replace("/", "_").replace("\\", "_")
    trace_file = trace_dir / f"{safe_id}.jsonl"
    with open(trace_file, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, default=str) + "\n")
    return str(trace_file.relative_to(DATA_DIR.parent))


@dataclass
class FlowResult:
    """Outcome of a flow execution (success/error, data, actions taken)."""

    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    actions_taken: list[str] = field(default_factory=list)


class PollableFlow(ABC):
    """Mixin for flows that support polling for new items."""

    @abstractmethod
    def poll_new_items(self) -> list[dict[str, Any]]:
        """Return new items to process."""

    @abstractmethod
    def generate_event_id(self, item_data: dict[str, Any]) -> str:
        """Generate a unique event ID for an item."""

    def on_item_success(self, item_id: Any) -> None:
        """Called after successful processing of an item."""


class WebhookableFlow(ABC):
    """Mixin for flows that can receive and parse webhook payloads."""

    @abstractmethod
    def parse_webhook_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Parse a raw webhook payload into normalized event data."""

    @abstractmethod
    def generate_webhook_event_id(self, event_data: dict[str, Any]) -> str:
        """Generate a unique event ID from parsed webhook data."""


class BaseFlow(ABC):
    """Minimal contract for all flows: a name and a handle method."""

    logger = logging.getLogger("Tools.AgentAutomation.Flows")

    def __init__(
        self,
        config: Config,
        flow_config: FlowConfig | None = None,
        *,
        live_state: "LiveState | None" = None,
    ):
        self.config = config
        self.flow_config = flow_config or config.get_flow_config(self.name)
        self.live_state = live_state or LiveState.get()

    def typed_config(self, cls: type[_FC]) -> _FC:
        """Return the flow config narrowed to *cls*, or a default instance."""
        if isinstance(self.flow_config, cls):
            return self.flow_config
        return cls()

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique flow identifier (e.g. 'redmine', 'jenkins')."""

    @property
    def mcp_servers(self) -> list[str]:
        """MCP servers needed by this flow (default: none)."""
        return []

    @abstractmethod
    async def handle(self, event: TriggerEvent) -> FlowResult:
        """Process *event* and return a FlowResult."""

    def after_run(self, event: TriggerEvent, result: FlowResult) -> None:
        """Called by the dispatcher after handle() completes. Override for post-processing."""
