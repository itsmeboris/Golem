"""Event-sourced tracking for golem sessions.

Processes stream-json events from Claude CLI into typed ``Milestone`` objects.
Inspired by OpenHands' immutable event log pattern — every event is recorded,
and structured state is derived from the log.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .core.run_log import format_duration

logger = logging.getLogger("golem.event_tracker")


@dataclass
class Milestone:
    """A single structured event extracted from the Claude CLI stream."""

    kind: str  # "tool_call" | "tool_result" | "error" | "result" | "text"
    tool_name: str = ""
    summary: str = ""
    timestamp: float = 0.0
    is_error: bool = False


@dataclass
class TrackerState:
    """Aggregate state derived from the event log."""

    tools_called: list[str] = field(default_factory=list)
    mcp_tools_called: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    last_activity: str = ""
    last_text: str = ""
    cost_usd: float = 0.0
    milestone_count: int = 0
    event_log: list[Milestone] = field(default_factory=list)
    finished: bool = False
    session_id: str = ""


# Callback signature: (milestone, tracker_state) -> None
MilestoneCallback = Callable[[Milestone, TrackerState], None]


def _short_path(path: str) -> str:
    """Shorten an absolute file path for display (last 3 components)."""
    parts = path.replace("\\", "/").rstrip("/").split("/")
    if len(parts) > 3:
        return ".../" + "/".join(parts[-3:])
    return path


def _summarize_tool_input(name: str, tool_input: dict) -> str:
    """Create a human-readable one-line summary from a tool_use block."""
    summary = _TOOL_SUMMARIZERS.get(name, _summarize_default)(name, tool_input)
    return summary or f"Called {name}"


def _summarize_bash(_name: str, inp: dict) -> str:
    desc = inp.get("description", "")
    if desc:
        return f"Bash: {desc}"
    cmd = inp.get("command", "")
    if cmd:
        return f"Bash: {cmd.replace(chr(10), ' ').strip()}"
    return ""


def _summarize_file_tool(name: str, inp: dict) -> str:
    path = inp.get("file_path", "")
    return f"{name} {_short_path(path)}" if path else ""


def _summarize_grep(_name: str, inp: dict) -> str:
    pattern = inp.get("pattern", "")
    path = inp.get("path", "")
    if pattern and path:
        return f"Grep '{pattern}' in {_short_path(path)}"
    if pattern:
        return f"Grep '{pattern}'"
    return ""


def _summarize_keyed(key: str) -> Callable[[str, dict], str]:
    """Return a summarizer that formats ``Name: value`` from *key*."""

    def _fn(name: str, inp: dict) -> str:
        val = inp.get(key, "")
        return f"{name}: {val}" if val else ""

    return _fn


def _summarize_agent(_name: str, inp: dict) -> str:
    desc = inp.get("description", "")
    prompt = inp.get("prompt", "")
    agent_type = inp.get("subagent_type", "")
    parts = []
    if agent_type:
        parts.append(f"[{agent_type}]")
    if desc:
        parts.append(desc)
    elif prompt:
        parts.append(prompt.replace("\n", " ").strip())
    return "Agent: " + " ".join(parts) if parts else ""


def _summarize_skill(_name: str, inp: dict) -> str:
    skill = inp.get("skill", "")
    args = inp.get("args", "")
    if skill and args:
        return f"Skill: {skill} {args}"
    return f"Skill: {skill}" if skill else ""


def _summarize_todo_write(_name: str, inp: dict) -> str:
    todos = inp.get("todos", [])
    if isinstance(todos, list) and todos:
        return f"TodoWrite: {len(todos)} items"
    return "TodoWrite"


def _summarize_task_create(_name: str, inp: dict) -> str:
    desc = inp.get("description", "")
    if desc:
        short = desc.replace("\n", " ").strip()[:80]
        return f"TaskCreate: {short}"
    return ""


def _summarize_task_update(_name: str, inp: dict) -> str:
    task_id = inp.get("task_id", "")
    status = inp.get("status", "")
    if task_id and status:
        return f"TaskUpdate: #{task_id} → {status}"
    if task_id:
        return f"TaskUpdate: #{task_id}"
    return ""


def _summarize_default(name: str, _inp: dict) -> str:
    if name.startswith("mcp__"):
        parts = name.split("__")
        return f"MCP: {parts[-1]}" if len(parts) >= 3 else f"MCP: {name}"
    return ""


_TOOL_SUMMARIZERS: dict[str, Callable[[str, dict], str]] = {
    "Bash": _summarize_bash,
    "Read": _summarize_file_tool,
    "Edit": _summarize_file_tool,
    "Write": _summarize_file_tool,
    "Glob": _summarize_keyed("pattern"),
    "Grep": _summarize_grep,
    "Task": _summarize_keyed("description"),
    "ToolSearch": _summarize_keyed("query"),
    "Agent": _summarize_agent,
    "Skill": _summarize_skill,
    "TodoWrite": _summarize_todo_write,
    "TaskCreate": _summarize_task_create,
    "TaskUpdate": _summarize_task_update,
}


class TaskEventTracker:
    """Per-session event processor.

    Receives raw stream-json dicts (same format as ``_StreamPrinter`` handles),
    extracts structured ``Milestone`` objects, and maintains aggregate
    ``TrackerState``.
    """

    def __init__(
        self,
        session_id: int,
        on_milestone: MilestoneCallback | None = None,
    ):
        self.session_id = session_id
        self._on_milestone = on_milestone
        self.state = TrackerState()
        self._seen_tools: set[str] = set()

    def handle_event(self, event: dict) -> Milestone | None:
        """Process a single stream-json event, return a Milestone if one was produced."""
        etype = event.get("type", "")

        # Capture session_id from the init event
        if (
            etype == "system"
            and event.get("subtype") == "init"
            and not self.state.session_id
        ):
            self.state.session_id = event.get("session_id", "")

        milestone: Milestone | None = None

        if etype == "tool_call":
            milestone = self._handle_tool_call(event)
        elif etype == "assistant":
            milestone = self._handle_assistant(event)
        elif etype == "tool_result":
            milestone = self._handle_tool_result(event)
        elif etype == "result":
            milestone = self._handle_result(event)

        if milestone is not None:
            self.state.event_log.append(milestone)
            self.state.milestone_count += 1
            self.state.last_activity = milestone.summary or milestone.kind

            if self._on_milestone:
                self._on_milestone(milestone, self.state)

        return milestone

    def _handle_tool_call(self, event: dict) -> Milestone | None:
        subtype = event.get("subtype", "")
        call = event.get("tool_call", {})
        mcp = call.get("mcpToolCall", {})

        if subtype == "started":
            tool_name = mcp.get("args", {}).get("toolName", "")
            if not tool_name:
                tool_name = call.get("name", "")
            if tool_name:
                self._record_tool(tool_name, mcp=bool(mcp.get("args")))
                summary = _summarize_tool_input(tool_name, {})
                return Milestone(
                    kind="tool_call",
                    tool_name=tool_name,
                    summary=summary,
                    timestamp=time.time(),
                )

        elif subtype == "completed":
            result = mcp.get("result", {})
            rejected = result.get("rejected", {})
            if rejected:
                reason = rejected.get("reason", "unknown")
                error_msg = f"MCP rejected: {reason}"
                self.state.errors.append(error_msg)
                return Milestone(
                    kind="error",
                    summary=error_msg,
                    timestamp=time.time(),
                    is_error=True,
                )

        return None

    def _handle_assistant(self, event: dict) -> Milestone | None:
        blocks = self._find_content_blocks(event)
        text_parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "tool_use":
                name = block.get("name", "?")
                tool_input = block.get("input", {})
                summary = _summarize_tool_input(name, tool_input)
                self._record_tool(name, mcp=name.startswith("mcp__"))
                return Milestone(
                    kind="tool_call",
                    tool_name=name,
                    summary=summary,
                    timestamp=time.time(),
                )
            if btype == "tool_result":
                return self._handle_tool_result(block)
            if btype == "text":
                text = block.get("text", "")
                cleaned = " ".join(text.split()).strip()
                if cleaned:
                    text_parts.append(cleaned)
                    self.state.last_text = cleaned

        # Emit text milestones so the dashboard shows agent messages.
        if text_parts:
            combined = " ".join(text_parts)
            return Milestone(
                kind="text",
                summary=combined,
                timestamp=time.time(),
            )
        return None

    def _handle_tool_result(self, block: dict) -> Milestone | None:
        is_error = block.get("is_error", False)
        content = block.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        text = str(content).replace("\n", " ").strip()
        snippet = text or ""

        if is_error:
            self.state.errors.append(snippet or "(empty error)")
            return Milestone(
                kind="error",
                summary=snippet or "(empty error)",
                timestamp=time.time(),
                is_error=True,
            )

        if snippet:
            return Milestone(
                kind="result",
                summary=snippet,
                timestamp=time.time(),
            )
        return None

    def _handle_result(self, event: dict) -> Milestone:
        cost = event.get("cost_usd", 0) or event.get("total_cost_usd", 0)
        self.state.cost_usd = float(cost)
        self.state.finished = True
        duration_s = (event.get("duration_ms", 0) or 0) / 1000
        return Milestone(
            kind="result",
            summary=f"Finished (${cost:.2f}, {format_duration(duration_s)})",
            timestamp=time.time(),
        )

    def _record_tool(self, name: str, *, mcp: bool = False) -> None:
        if name in self._seen_tools:
            return
        self._seen_tools.add(name)
        if mcp or name.startswith("mcp__"):
            self.state.mcp_tools_called.append(name)
        else:
            self.state.tools_called.append(name)

    @staticmethod
    def _find_content_blocks(event: dict) -> list:
        for path in [
            lambda e: e.get("message", {}).get("content", []),
            lambda e: e.get("content", []),
            lambda e: e.get("content_block", []),
        ]:
            blocks = path(event)
            if isinstance(blocks, list) and blocks:
                return blocks
            if isinstance(blocks, dict):
                return [blocks]
        return []

    def to_dict(self) -> dict:
        """Serialize tracker state for persistence."""
        return {
            "session_id": self.session_id,
            "tools_called": self.state.tools_called,
            "mcp_tools_called": self.state.mcp_tools_called,
            "errors": self.state.errors,
            "last_activity": self.state.last_activity,
            "last_text": self.state.last_text,
            "cost_usd": self.state.cost_usd,
            "milestone_count": self.state.milestone_count,
            "finished": self.state.finished,
            "event_log": [
                {
                    "kind": m.kind,
                    "tool_name": m.tool_name,
                    "summary": m.summary,
                    "timestamp": m.timestamp,
                    "is_error": m.is_error,
                }
                for m in self.state.event_log
            ],
        }
