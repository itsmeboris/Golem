"""Parse JSONL trace events into structured timeline for dashboard display.

Pure module — no file I/O, no external dependencies beyond stdlib.
Input: list of parsed JSONL event dicts.
Output: ParsedTrace dict (see design spec for full shape).
"""

from __future__ import annotations

import json
import re
from typing import Any

PHASE_NAMES = ("UNDERSTAND", "PLAN", "BUILD", "REVIEW", "VERIFY")
PHASE_MARKER_RE = re.compile(r"## Phase:\s*(UNDERSTAND|PLAN|BUILD|REVIEW|VERIFY)")

_ISSUE_RE = re.compile(
    r"\[(\d+)%\]\s+"  # confidence
    r"([\w/.]+(?::\d+)?)"  # file:line
    r"\s*[—–-]\s*"  # separator
    r"(.+)"  # description
)

_JSON_BLOCK_RE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)

# Keywords used to infer phase from subagent description (Task 2.4)
_PHASE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("UNDERSTAND", ["scout", "explore", "understand", "research"]),
    ("PLAN", ["plan"]),
    ("BUILD", ["build", "implement", "write", "create"]),
    ("REVIEW", ["review", "compliance", "quality"]),
    ("VERIFY", ["verify", "test suite", "validation"]),
]


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


def _extract_thinking_blocks(event: dict[str, Any]) -> list[str]:
    """Extract thinking text from an assistant event's content blocks."""
    if event.get("type") != "assistant":
        return []
    content = event.get("message", {}).get("content", [])
    return [
        block.get("thinking", "")
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "thinking"
        and block.get("thinking")
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


def _make_phase(name: str, start: int, end: int) -> dict[str, Any]:
    """Create a phase dict with the standard structure."""
    return {
        "name": name,
        "start_event": start,
        "end_event": end,
        "orchestrator_text": [],
        "orchestrator_thinking": [],
        "orchestrator_tools": [],
        "subagents": [],
        "fix_cycles": [],  # Populated by _detect_fix_cycles()
        "tokens": 0,
        "duration_ms": 0,
    }


def _detect_phases(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan events for phase markers. Return list of phase dicts with boundaries."""
    phases: list[dict[str, Any]] = []
    for idx, event in enumerate(events):
        for text in _extract_text_blocks(event):
            for match in PHASE_MARKER_RE.finditer(text):
                name = match.group(1)
                # Close previous phase — boundary event belongs to the new phase
                if phases:
                    phases[-1]["end_event"] = max(phases[-1]["start_event"], idx - 1)
                phases.append(_make_phase(name, idx, idx))
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
                results[tuid] = str(content)[:50000]  # generous limit for issue parsing
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


def _parse_issues(output: str) -> list[dict[str, Any]]:
    """Parse issue lines from a reviewer output using _ISSUE_RE."""
    issues: list[dict[str, Any]] = []
    for match in _ISSUE_RE.finditer(output):
        confidence = int(match.group(1))
        file_ref = match.group(2)
        description = match.group(3).strip()
        issues.append(
            {
                "confidence": confidence,
                "file": file_ref,
                "text": description,
            }
        )
    return issues


def _detect_fix_cycles(subagents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detect fix cycles in a REVIEW phase's subagents list.

    A fix cycle is: reviewer(NEEDS_FIXES) -> builder(fix) -> reviewer(re-check).
    The re-check status is derived from the next non-builder subagent after
    the fix builder (if any).
    """
    fix_cycles: list[dict[str, Any]] = []
    iteration = 0
    i = 0
    while i < len(subagents):
        agent = subagents[i]
        if agent["status"] == "NEEDS_FIXES":
            iteration += 1
            issues = _parse_issues(agent["output"])
            fix_builder = None
            recheck_status = "pending"
            # Look for the next builder
            j = i + 1
            while j < len(subagents):
                if subagents[j]["role"] == "builder":
                    fix_builder = subagents[j]
                    j += 1
                    break
                j += 1
            # Look ahead for re-check reviewer (next non-builder after fix)
            if fix_builder:
                k = j
                while k < len(subagents):
                    if subagents[k]["role"] != "builder":
                        recheck_status = subagents[k]["status"]
                        break
                    k += 1
            fix_cycles.append(
                {
                    "iteration": iteration,
                    "reviewer": agent,
                    "issues": issues,
                    "fix_builder": fix_builder,
                    "recheck_status": recheck_status,
                }
            )
            i = j
        else:
            i += 1
    return fix_cycles


def _build_lifecycle_maps(
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build lookup maps from system lifecycle events.

    Returns a dict with keys: tool_use_to_task, task_progress_map,
    task_notification_map, tool_use_to_notification.
    """
    maps: dict[str, Any] = {
        "tool_use_to_task": {},
        "task_progress_map": {},
        "task_notification_map": {},
        "tool_use_to_notification": {},
    }
    for event in events:
        if event.get("type") != "system":
            continue
        subtype = event.get("subtype")
        if subtype == "task_started":
            tuid = event.get("tool_use_id")
            tid = event.get("task_id")
            if tuid and tid:
                maps["tool_use_to_task"][tuid] = tid
        elif subtype == "task_progress":
            tid = event.get("task_id")
            if tid:
                maps["task_progress_map"].setdefault(tid, []).append(event)
        elif subtype == "task_notification":
            tid = event.get("task_id")
            tuid = event.get("tool_use_id")
            if tid:
                maps["task_notification_map"][tid] = event
            if tuid:
                maps["tool_use_to_notification"][tuid] = event
    return maps


def _build_subagent_dict(
    tool_use: dict[str, Any],
    lifecycle: dict[str, Any],
    tool_result_map: dict[str, str],
) -> dict[str, Any]:
    """Construct a subagent dict from an Agent tool_use and lifecycle maps."""
    tool_input = tool_use.get("input", {})
    tuid = tool_use.get("id", "")
    task_id = lifecycle["tool_use_to_task"].get(tuid, "")
    notification = lifecycle["task_notification_map"].get(task_id) or lifecycle[
        "tool_use_to_notification"
    ].get(tuid)
    usage = notification.get("usage", {}) if notification else {}
    output = tool_result_map.get(tuid, "")
    model_val = tool_input.get("model")
    return {
        "description": tool_input.get("description", ""),
        "role": tool_input.get("subagent_type", ""),
        "model": model_val if model_val else "unknown",
        "task_id": task_id,
        "tool_use_id": tuid,
        "status": _infer_status(output),
        "prompt": tool_input.get("prompt", ""),
        "output": output,
        "tokens": usage.get("total_tokens", 0),
        "tool_count": usage.get("tool_uses", 0),
        "duration_ms": usage.get("duration_ms", 0),
        "tool_timeline": _build_tool_timeline(
            lifecycle["task_progress_map"].get(task_id, [])
        ),
    }


def _populate_phases(
    phases: list[dict[str, Any]],
    events: list[dict[str, Any]],
    tool_result_map: dict[str, str],
) -> None:
    """Populate orchestrator_text, orchestrator_tools, and subagents for each phase."""
    lifecycle = _build_lifecycle_maps(events)

    for phase in phases:
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        orch_tools: list[dict[str, Any]] = []
        subagents: list[dict[str, Any]] = []

        for idx in range(phase["start_event"], phase["end_event"] + 1):
            event = events[idx]
            text_parts.extend(_extract_text_blocks(event))
            thinking_parts.extend(_extract_thinking_blocks(event))

            for tool_use in _extract_tool_uses(event):
                if tool_use.get("name", "") == "Agent":
                    subagents.append(
                        _build_subagent_dict(tool_use, lifecycle, tool_result_map)
                    )
                else:
                    orch_tools.append(
                        {
                            "tool": tool_use.get("name", ""),
                            "description": _summarize_tool_description(
                                tool_use.get("name", ""), tool_use.get("input", {})
                            ),
                            "event_idx": idx,
                            "result_preview": tool_result_map.get(
                                tool_use.get("id", ""), ""
                            ),
                        }
                    )

        phase["orchestrator_text"] = text_parts
        phase["orchestrator_thinking"] = thinking_parts
        phase["orchestrator_tools"] = orch_tools
        phase["subagents"] = subagents
        phase["_assistant_turns"] = sum(
            1
            for idx in range(phase["start_event"], phase["end_event"] + 1)
            if events[idx].get("type") == "assistant"
        )

        # Detect fix cycles for REVIEW phases
        if phase["name"] == "REVIEW":
            phase["fix_cycles"] = _detect_fix_cycles(subagents)

        # Compute per-phase totals (subagent time only — orchestrator time added later)
        phase["duration_ms"] = sum(s["duration_ms"] for s in subagents)
        phase["tokens"] = sum(s["tokens"] for s in subagents)


def _infer_phase_from_description(description: str) -> str | None:
    """Infer phase name from an Agent description using keywords."""
    lower = description.lower()
    for phase_name, keywords in _PHASE_KEYWORDS:
        for kw in keywords:
            if kw in lower:
                return phase_name
    return None


def _infer_phases_from_subagents(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fallback: infer phases from Agent tool_use descriptions when no markers exist."""
    # Collect all Agent dispatches with their event indices and inferred phases
    agent_dispatches: list[tuple[int, str, dict[str, Any]]] = []
    for idx, event in enumerate(events):
        for tool_use in _extract_tool_uses(event):
            if tool_use.get("name") == "Agent":
                tool_input = tool_use.get("input", {})
                desc = tool_input.get("description", "") or tool_input.get("prompt", "")
                inferred = _infer_phase_from_description(desc)
                if inferred:
                    agent_dispatches.append((idx, inferred, tool_use))

    if not agent_dispatches:
        return []

    # Group consecutive agents of the same inferred phase
    phases: list[dict[str, Any]] = []
    current_phase_name = agent_dispatches[0][1]
    current_start = agent_dispatches[0][0]
    current_end = agent_dispatches[0][0]

    for ev_idx, phase_name, _ in agent_dispatches[1:]:
        if phase_name == current_phase_name:
            current_end = ev_idx
        else:
            phases.append(_make_phase(current_phase_name, current_start, current_end))
            current_phase_name = phase_name
            current_start = ev_idx
            current_end = ev_idx

    # Add last group
    phases.append(_make_phase(current_phase_name, current_start, current_end))

    return phases


def _extract_result_meta(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Extract result metadata from the result event."""
    for event in events:
        if event.get("type") == "result":
            return {
                "total_cost_usd": event.get("total_cost_usd", 0),
                "duration_ms": event.get("duration_ms", 0),
                "num_turns": event.get("num_turns", 0),
                "model_usage": event.get("modelUsage", {}),
            }
    return None


def _extract_final_report(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Extract final report from the result event's JSON block."""
    for event in events:
        if event.get("type") == "result":
            result_text = event.get("result", "")
            match = _JSON_BLOCK_RE.search(result_text)
            if not match:
                return None
            try:
                data = json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                return None
            if not isinstance(data, dict):
                return None
            return {
                "status": data.get("status"),
                "summary": data.get("summary"),
                "files_changed": data.get("files_changed", []),
                "test_results": data.get("test_results", {}),
                "specs_satisfied": data.get("specs_satisfied", {}),
                "concerns": data.get("concerns", []),
            }
    return None


def _estimate_phase_durations(
    phases: list[dict[str, Any]],
    result_meta: dict[str, Any] | None,
) -> None:
    """Estimate orchestrator phase durations when per-event timestamps are absent.

    Subagent durations are already known from task_notification events.
    The remaining time (total - subagent) is distributed across phases
    proportionally by assistant turn count.
    """
    if not result_meta:
        return
    total_ms = result_meta.get("duration_ms", 0)
    if total_ms <= 0:
        return

    subagent_ms = sum(p["duration_ms"] for p in phases)
    orchestrator_ms = max(0, total_ms - subagent_ms)

    total_turns = sum(p.get("_assistant_turns", 0) for p in phases)
    if total_turns <= 0:
        return

    for phase in phases:
        turns = phase.get("_assistant_turns", 0)
        phase["duration_ms"] += round(orchestrator_ms * turns / total_turns)


def _compute_totals(
    phases: list[dict[str, Any]],
    result_meta: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compute aggregate totals across all phases."""
    subagent_count = sum(len(p["subagents"]) for p in phases)
    total_tokens = sum(s["tokens"] for p in phases for s in p["subagents"])
    total_tool_calls = sum(s["tool_count"] for p in phases for s in p["subagents"])
    fix_cycles = sum(len(p["fix_cycles"]) for p in phases)
    duration_ms = result_meta["duration_ms"] if result_meta else 0
    return {
        "duration_ms": duration_ms,
        "tokens": total_tokens,
        "tool_calls": total_tool_calls,
        "subagent_count": subagent_count,
        "fix_cycles": fix_cycles,
    }


def parse_trace(events: list[dict[str, Any]], since_event: int = 0) -> dict[str, Any]:
    """Parse JSONL trace events into structured ParsedTrace dict.

    Args:
        events: Full list of parsed JSONL event dicts.
        since_event: Event index bookkeeping for callers. Stored in the
            returned dict but does not filter events — full trace is always
            parsed. Callers use this to detect unchanged traces.
    """
    phases = _detect_phases(events)
    if not phases:
        phases = _infer_phases_from_subagents(events)

    tool_result_map = _build_tool_result_map(events)
    _populate_phases(phases, events, tool_result_map)

    result_meta = _extract_result_meta(events)
    final_report = _extract_final_report(events)

    # Estimate orchestrator phase durations from total time minus subagent time,
    # distributed proportionally by assistant turn count.
    _estimate_phase_durations(phases, result_meta)

    totals = _compute_totals(phases, result_meta)

    return {
        "phases": phases,
        "retry": None,
        "final_report": final_report,
        "result_meta": result_meta,
        "since_event": since_event,
        "total_events": len(events),
        "totals": totals,
    }
