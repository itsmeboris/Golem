"""Parse JSONL trace events into structured timeline for dashboard display.

Pure module — no file I/O, no external dependencies beyond stdlib.
Input: list of parsed JSONL event dicts.
Output: ParsedTrace dict (see design spec for full shape).
"""

from __future__ import annotations

import re
from typing import Any

PHASE_NAMES = ("UNDERSTAND", "PLAN", "BUILD", "REVIEW", "VERIFY")
PHASE_MARKER_RE = re.compile(r"## Phase:\s*(UNDERSTAND|PLAN|BUILD|REVIEW|VERIFY)")


def _extract_text_blocks(event: dict[str, Any]) -> list[str]:
    """Extract all text strings from an assistant event's content blocks."""
    if event.get("type") != "assistant":
        return []
    content = event.get("message", {}).get("content", [])
    return [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    ]


def _extract_tool_uses(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool_use blocks from an assistant event."""
    if event.get("type") != "assistant":
        return []
    content = event.get("message", {}).get("content", [])
    return [
        block
        for block in content
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]


def _detect_phases(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan events for phase markers. Return list of phase dicts with boundaries."""
    phases: list[dict[str, Any]] = []
    for idx, event in enumerate(events):
        for text in _extract_text_blocks(event):
            for match in PHASE_MARKER_RE.finditer(text):
                name = match.group(1)
                # Close previous phase
                if phases:
                    phases[-1]["end_event"] = idx
                phases.append(
                    {
                        "name": name,
                        "start_event": idx,
                        "end_event": idx,  # updated when next phase starts or at end
                        "orchestrator_text": "",
                        "orchestrator_tools": [],
                        "subagents": [],
                        "fix_cycles": [],  # Populated by _detect_fix_cycles() in Chunk 2
                        "tokens": 0,
                        "duration_ms": 0,
                    }
                )
    # Close last phase at end of events
    if phases:
        phases[-1]["end_event"] = len(events) - 1
    return phases


def _build_tool_result_map(events: list[dict[str, Any]]) -> dict[str, str]:
    """Build tool_use_id -> result_content map from all user events."""
    results: dict[str, str] = {}
    for event in events:
        if event.get("type") != "user":
            continue
        for block in event.get("message", {}).get("content", []):
            if not isinstance(block, dict):
                continue
            tuid = block.get("tool_use_id")
            if tuid:
                content = block.get("content", "")
                if isinstance(content, list):
                    # Multi-block content — join text parts
                    content = "\n".join(
                        part.get("text", str(part))
                        for part in content
                        if isinstance(part, dict)
                    )
                results[tuid] = str(content)[:2000]  # preview limit
    return results


def _summarize_tool_description(name: str, tool_input: dict[str, Any]) -> str:
    """Build a short description from tool name + input."""
    if name == "Read":
        return tool_input.get("file_path", "")
    if name in ("Write", "Edit"):
        return tool_input.get("file_path", "")
    if name in ("Glob", "Grep"):
        return tool_input.get("pattern", "")
    if name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:120]
    return str(tool_input)[:120]


def _infer_status(output: str) -> str:
    """Infer subagent status from output text.

    Scans the first 1000 chars — reviewers typically lead with their verdict
    (APPROVED or NEEDS_FIXES) in the first line or two.
    """
    upper = output[:1000].upper()
    if "NEEDS_FIXES" in upper:
        return "NEEDS_FIXES"
    if "APPROVED" in upper:
        return "APPROVED"
    return "completed"


def _build_tool_timeline(progress_events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build tool timeline from task_progress events for a subagent."""
    timeline: list[dict[str, Any]] = []
    prev_ms = 0
    for ev in progress_events:
        usage = ev.get("usage", {})
        cumulative_ms = usage.get("duration_ms", 0)
        cumulative_tools = usage.get("tool_uses", 0)
        timeline.append(
            {
                "tool": ev.get("last_tool_name", ""),
                "description": ev.get("description", ""),
                "cumulative_ms": cumulative_ms,
                "delta_ms": cumulative_ms - prev_ms,
                "cumulative_tools": cumulative_tools,
            }
        )
        prev_ms = cumulative_ms
    return timeline


def _populate_phases(
    phases: list[dict[str, Any]],
    events: list[dict[str, Any]],
    tool_result_map: dict[str, str],
) -> None:
    """Populate orchestrator_text, orchestrator_tools, and subagents for each phase."""
    # Build lifecycle lookup maps
    # tool_use_id -> task_id (from task_started events)
    tool_use_to_task: dict[str, str] = {}
    # task_id -> list of task_progress events
    task_progress_map: dict[str, list[dict[str, Any]]] = {}
    # task_id -> task_notification event
    task_notification_map: dict[str, dict[str, Any]] = {}
    # tool_use_id -> task_notification event (for lookup by tool_use_id)
    tool_use_to_notification: dict[str, dict[str, Any]] = {}

    for event in events:
        if event.get("type") != "system":
            continue
        subtype = event.get("subtype")
        if subtype == "task_started":
            tuid = event.get("tool_use_id")
            tid = event.get("task_id")
            if tuid and tid:
                tool_use_to_task[tuid] = tid
        elif subtype == "task_progress":
            tid = event.get("task_id")
            if tid:
                task_progress_map.setdefault(tid, []).append(event)
        elif subtype == "task_notification":
            tid = event.get("task_id")
            tuid = event.get("tool_use_id")
            if tid:
                task_notification_map[tid] = event
            if tuid:
                tool_use_to_notification[tuid] = event

    for phase in phases:
        start = phase["start_event"]
        end = phase["end_event"]
        text_parts: list[str] = []
        orchestrator_tools: list[dict[str, Any]] = []
        subagents: list[dict[str, Any]] = []

        for idx in range(start, end + 1):
            event = events[idx]

            # Collect orchestrator text blocks
            for text in _extract_text_blocks(event):
                text_parts.append(text)

            # Collect tool_use blocks
            for tool_use in _extract_tool_uses(event):
                tool_name = tool_use.get("name", "")
                tool_input = tool_use.get("input", {})
                tuid = tool_use.get("id", "")

                if tool_name == "Agent":
                    # Subagent dispatch
                    task_id = tool_use_to_task.get(tuid, "")
                    notification = task_notification_map.get(
                        task_id
                    ) or tool_use_to_notification.get(tuid)
                    usage = notification.get("usage", {}) if notification else {}
                    progress_events = task_progress_map.get(task_id, [])
                    output = tool_result_map.get(tuid, "")
                    model_val = tool_input.get("model")
                    model = model_val if model_val else "unknown"
                    subagent = {
                        "description": tool_input.get("description", ""),
                        "role": tool_input.get("subagent_type", ""),
                        "model": model,
                        "task_id": task_id,
                        "tool_use_id": tuid,
                        "status": _infer_status(output),
                        "prompt": tool_input.get("prompt", ""),
                        "output": output,
                        "tokens": usage.get("total_tokens", 0),
                        "tool_count": usage.get("tool_uses", 0),
                        "duration_ms": usage.get("duration_ms", 0),
                        "tool_timeline": _build_tool_timeline(progress_events),
                    }
                    subagents.append(subagent)
                else:
                    # Orchestrator tool call
                    description = _summarize_tool_description(tool_name, tool_input)
                    result_preview = tool_result_map.get(tuid, "")
                    orchestrator_tools.append(
                        {
                            "tool": tool_name,
                            "description": description,
                            "event_idx": idx,
                            "result_preview": result_preview,
                        }
                    )

        phase["orchestrator_text"] = "\n".join(text_parts)
        phase["orchestrator_tools"] = orchestrator_tools
        phase["subagents"] = subagents


def parse_trace(events: list[dict[str, Any]], since_event: int = 0) -> dict[str, Any]:
    """Parse JSONL trace events into structured ParsedTrace dict.

    Args:
        events: Full list of parsed JSONL event dicts.
        since_event: Stored in the returned dict for the caller's use.
            Incremental parsing logic will be implemented in a future task (Chunk 2/3).
    """
    phases = _detect_phases(events)
    tool_result_map = _build_tool_result_map(events)
    _populate_phases(phases, events, tool_result_map)

    return {
        "phases": phases,
        "retry": None,
        "final_report": None,
        "result_meta": None,
        "since_event": since_event,
        "total_events": len(events),
        "totals": {
            "duration_ms": 0,
            "tokens": 0,
            "tool_calls": 0,
            "subagent_count": 0,
            "fix_cycles": 0,
        },
    }
