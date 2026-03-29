"""HTML dashboard and API routes for run history and stats."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .control_api import _require_api_key
from .daemon_utils import read_pid
from .live_state import LiveState, read_live_snapshot
from .run_log import read_runs
from ..utils import format_duration
from ..event_tracker import _summarize_tool_input
from ..orchestrator import load_sessions
from ..trace_parser import parse_trace as _parse_trace_structured
from ..types import (
    ActiveTaskDict,
    ConfigSnapshotDict,
    LiveSnapshotDict,
    RunRecordDict,
)

logger = logging.getLogger("golem.core.dashboard")

_shutting_down = False


async def _safe_to_thread(func, *args, **kwargs):
    """Run *func* in a thread, returning None if the executor is shut down."""
    try:
        return await asyncio.to_thread(func, *args, **kwargs)
    except RuntimeError:
        if _shutting_down:
            return None
        raise


try:
    from fastapi import Query, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import (
        HTMLResponse,
        JSONResponse,
        Response,
        StreamingResponse,
    )

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    CORSMiddleware = None  # type: ignore[assignment,misc]
    Query = None
    Request = None  # type: ignore[assignment,misc]
    HTMLResponse = None
    JSONResponse = None
    Response = None
    StreamingResponse = None


# ---------------------------------------------------------------------------
# Helpers for trace / prompt / report API endpoints
# ---------------------------------------------------------------------------

TRACES_DIR = DATA_DIR / "traces"
REPORTS_DIR = DATA_DIR / "reports"


def _extract_numeric_id(event_id: str) -> tuple[str, str]:
    """Extract (flow_name, numeric_id) from an event_id.

    Examples:
        "golem-123-20260215"  -> ("golem", "123")
    """
    if "golem" in event_id:
        nums = re.findall(r"\b(\d+)\b", event_id)
        return "golem", nums[0] if nums else ""
    return "", ""


def _is_within(path: Path, base: Path) -> bool:
    """Return True if resolved path is within the base directory."""
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _resolve_paths(event_id: str) -> dict[str, Path | None]:
    """Find prompt/trace/report file paths for a given event_id."""
    # Normalize bare numeric IDs (from sessions API) to golem-{id} format
    if event_id.isdigit():
        event_id = f"golem-{event_id}"
    flow, numeric_id = _extract_numeric_id(event_id)
    safe_id = event_id.replace("/", "_")

    trace_path: Path | None = None
    prompt_path: Path | None = None
    report_path: Path | None = None

    if flow:
        t = TRACES_DIR / flow / f"{safe_id}.jsonl"
        if t.exists() and _is_within(t, TRACES_DIR):
            trace_path = t
        p = TRACES_DIR / flow / f"{safe_id}.prompt.txt"
        if p.exists() and _is_within(p, TRACES_DIR):
            prompt_path = p

        if numeric_id:
            r = REPORTS_DIR / flow / f"{numeric_id}.md"
            if r.exists() and _is_within(r, REPORTS_DIR):
                report_path = r

    return {
        "trace": trace_path,
        "prompt": prompt_path,
        "report": report_path,
    }


# Cache for parsed traces (completed traces are immutable).
# FIFO-bounded: evict oldest inserted entry when cache exceeds _MAX_TRACE_CACHE.
_MAX_TRACE_CACHE = 100
_parsed_trace_cache: dict[str, dict[str, Any]] = {}


def _read_jsonl_events(path: Path) -> list[dict[str, Any]] | None:
    """Read a JSONL file, returning parsed events or None if file not found."""
    events: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.debug("Skipping malformed JSON line: %s", line)
                    continue
    except FileNotFoundError:
        return None
    return events


def _read_and_parse_trace(event_id: str, since_event: int = 0) -> dict[str, Any] | None:
    """Read JSONL trace file and return ParsedTrace dict, or None if not found.

    When ``since_event`` matches the current event count, returns the cached
    result (if available) since the trace hasn't changed.  Otherwise, a full
    re-parse is performed.
    """
    # Fast-path: return cached result on initial (non-incremental) request
    if since_event == 0 and event_id in _parsed_trace_cache:
        return _parsed_trace_cache[event_id]

    paths = _resolve_paths(event_id)
    trace_path = paths.get("trace")
    if not trace_path:
        return None

    events = _read_jsonl_events(trace_path)
    if events is None:
        return None

    # Return cached result when trace hasn't grown since caller's last poll
    cached = _parsed_trace_cache.get(event_id)
    if cached and since_event > 0 and len(events) == since_event:
        return cached

    try:
        result = _parse_trace_structured(events, since_event=since_event)
    except Exception:  # pylint: disable=broad-except
        logger.exception("Failed to parse trace %s", event_id)
        result = _parse_trace_structured([])

    # Check for retry trace
    retry_path = trace_path.with_name(trace_path.stem + "-retry.jsonl")
    retry_events = _read_jsonl_events(retry_path)
    if retry_events:
        retry_parsed = _parse_trace_structured(retry_events)
        result["retry"] = {
            "type": "warm_resume",
            "trace_file": str(retry_path),
            "phases": retry_parsed["phases"],
            "totals": retry_parsed["totals"],
        }

    # Check for fix iteration traces (fix1, fix2, ...)
    fix_traces: list[dict[str, Any]] = []
    for i in range(1, 20):  # practical upper bound
        fix_path = trace_path.with_name(f"{trace_path.stem}-fix{i}.jsonl")
        fix_events = _read_jsonl_events(fix_path)
        if not fix_events:
            break
        fix_parsed = _parse_trace_structured(fix_events)
        fix_traces.append(
            {
                "iteration": i,
                "trace_file": str(fix_path),
                "phases": fix_parsed["phases"],
                "totals": fix_parsed["totals"],
            }
        )
    if fix_traces:
        result["fix_iterations"] = fix_traces

    # Auto-cache if trace has a result event (task completed)
    if result.get("result_meta") is not None:
        if len(_parsed_trace_cache) >= _MAX_TRACE_CACHE:
            _parsed_trace_cache.pop(next(iter(_parsed_trace_cache)))
        _parsed_trace_cache[event_id] = result

    return result


def _parse_trace(trace_path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL trace file into structured sections.

    Returns a list of section dicts with keys: type, content, metadata.
    """
    sections: list[dict[str, Any]] = []
    with open(trace_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSON line: %s", line)
                continue

            ev_type = ev.get("type", "")
            if ev_type == "system" and ev.get("subtype") == "init":
                sections.append(
                    {
                        "type": "system_init",
                        "content": {
                            "model": ev.get("model", ""),
                            "tools": ev.get("tools", []),
                            "mcp_servers": ev.get("mcp_servers", []),
                            "cwd": ev.get("cwd", ""),
                        },
                    }
                )
            elif ev_type == "assistant":
                msg = ev.get("message", {})
                content_blocks = msg.get("content", [])
                thinking = ""
                response = ""
                for block in content_blocks:
                    if isinstance(block, dict):
                        if block.get("type") == "thinking":
                            thinking += block.get("thinking", "")
                        elif block.get("type") == "text":
                            response += block.get("text", "")
                if thinking:
                    sections.append({"type": "thinking", "content": thinking})
                if response:
                    sections.append({"type": "response", "content": response})
            elif ev_type == "result":
                sections.append(
                    {
                        "type": "result",
                        "content": {
                            "duration_ms": ev.get("duration_ms", 0),
                            "total_cost_usd": ev.get("total_cost_usd", 0),
                            "num_turns": ev.get("num_turns", 0),
                            "is_error": ev.get("is_error", False),
                            "usage": ev.get("usage", {}),
                        },
                    }
                )
    return sections


def _term_ev(etype: str, text: str, **kw: Any) -> dict:
    """Build a single terminal-renderable event dict."""
    return {
        "type": etype,
        "text": text,
        "tool_name": kw.get("tool_name", ""),
        "is_error": kw.get("is_error", False),
    }


def _extract_assistant_events(ev: dict, events: list, stats: dict) -> None:
    """Extract tool_call / text / thinking events from an assistant message."""
    for block in ev.get("message", {}).get("content", []):
        if not isinstance(block, dict):
            continue
        btype = block.get("type", "")
        if btype == "tool_use":
            name = block.get("name", "?")
            tool_input = block.get("input", {})
            summary = _summarize_tool_input(name, tool_input) or name
            events.append(_term_ev("tool_call", summary, tool_name=name))
            stats["tool_calls"] += 1
        elif btype == "text":
            text = block.get("text", "").strip()
            if text:
                events.append(_term_ev("text", text))
        elif btype == "thinking":
            text = block.get("thinking", "").strip()
            if text:
                events.append(_term_ev("thinking", text))


def _extract_user_events(ev: dict, events: list, stats: dict) -> None:
    """Extract tool_result events from a user (tool-result) message."""
    for block in ev.get("message", {}).get("content", []):
        if not isinstance(block, dict) or "tool_use_id" not in block:
            continue
        is_error = block.get("is_error", False)
        content = block.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content if isinstance(c, dict)
            )
        text = str(content).strip()[:50000]
        events.append(_term_ev("tool_result", text, is_error=is_error))
        if is_error:
            stats["errors"] += 1


def _parse_trace_terminal(trace_path: Path) -> tuple[list[dict], dict]:
    """Parse a JSONL trace into flat terminal-renderable events + summary stats.

    Unlike :func:`_parse_trace` which groups events into sections, this returns
    a flat chronological list suitable for rendering as a scrolling terminal.
    """
    events: list[dict] = []
    stats = {
        "total_events": 0,
        "tool_calls": 0,
        "errors": 0,
        "cost_usd": 0.0,
        "duration_ms": 0,
    }

    with open(trace_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                logger.debug("Skipping malformed JSON line: %s", line)
                continue

            ev_type = ev.get("type", "")

            if ev_type == "system" and ev.get("subtype") == "init":
                events.append(
                    _term_ev(
                        "system_init",
                        f"Model: {ev.get('model', '')}  CWD: {ev.get('cwd', '')}",
                    )
                )
            elif ev_type == "assistant":
                _extract_assistant_events(ev, events, stats)
            elif ev_type == "user":
                _extract_user_events(ev, events, stats)
            elif ev_type == "result":
                stats["cost_usd"] = ev.get("total_cost_usd", 0)
                stats["duration_ms"] = ev.get("duration_ms", 0)
                events.append(
                    _term_ev(
                        "result",
                        f"Done  ${stats['cost_usd']:.2f}  "
                        f"{stats['duration_ms'] / 1000:.0f}s",
                        is_error=ev.get("is_error", False),
                    )
                )

            stats["total_events"] += 1

    return events, stats


def _aggregate_stats(runs: list[RunRecordDict]) -> dict[str, Any]:
    total = len(runs)
    if total == 0:
        return {
            "total_runs": 0,
            "success_count": 0,
            "failure_count": 0,
            "success_rate": 0.0,
            "by_flow": {},
            "total_cost_usd": 0.0,
            "avg_duration_s": 0.0,
            "total_tokens": 0,
        }

    successes = sum(1 for r in runs if r.get("success"))
    failures = total - successes
    total_cost = sum(r.get("cost_usd", 0.0) for r in runs)
    durations = [r.get("duration_s", 0.0) for r in runs if r.get("duration_s")]
    total_tokens = sum(
        r.get("input_tokens", 0) + r.get("output_tokens", 0) for r in runs
    )

    flow_counter: Counter[str] = Counter()
    flow_success: Counter[str] = Counter()
    for r in runs:
        flow_name = r.get("flow", "unknown")
        flow_counter[flow_name] += 1
        if r.get("success"):
            flow_success[flow_name] += 1

    by_flow = {}
    for flow_name, count in flow_counter.items():
        ok = flow_success.get(flow_name, 0)
        by_flow[flow_name] = {
            "total": count,
            "success": ok,
            "failure": count - ok,
            "success_rate": round(ok / count * 100, 1) if count else 0.0,
        }

    return {
        "total_runs": total,
        "success_count": successes,
        "failure_count": failures,
        "success_rate": round(successes / total * 100, 1),
        "by_flow": by_flow,
        "total_cost_usd": round(total_cost, 4),
        "avg_duration_s": (
            round(sum(durations) / len(durations), 2) if durations else 0.0
        ),
        "total_tokens": total_tokens,
    }


def config_to_snapshot(config: Any) -> ConfigSnapshotDict | dict:
    """Extract a JSON-safe snapshot of Config for the dashboard.

    Accepts the Config dataclass or None.  Returns a plain dict with
    model, concurrency, budget, and golem flow status.
    """
    if config is None:
        return {}
    try:
        flows = {}
        flow_models = {}
        fc = getattr(config, "golem", None)
        if fc is not None:
            flows["golem"] = getattr(fc, "enabled", False)
            fm = getattr(fc, "model", "")
            if fm:
                flow_models["golem"] = fm
        return {
            "model": getattr(config.claude, "model", ""),
            "max_concurrent": getattr(config.claude, "max_concurrent", 0),
            "budget": getattr(config.claude, "max_budget_usd", 0),
            "timeout": getattr(config.claude, "timeout_seconds", 0),
            "flows": flows,
            "flow_models": flow_models,
        }
    except Exception:  # pylint: disable=broad-except
        logger.debug("config_to_snapshot failed", exc_info=True)
        return {}


_SESSIONS_FILE = DATA_DIR / "state" / "golem_sessions.json"
_MERGE_QUEUE_SENTINEL = DATA_DIR / "state" / ".merge_queue_updated"


_LOG_DIR = DATA_DIR / "logs"


def _read_sessions() -> dict:
    """Read golem sessions from disk (called in a thread)."""
    if not _SESSIONS_FILE.exists():
        return {"sessions": {}}
    return json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))


def _read_log_tail(lines: int = 200) -> dict:
    """Read the tail of the latest daemon log file."""
    latest = _LOG_DIR / "daemon_latest.log"
    if not latest.exists():
        return {"lines": [], "file": ""}
    try:
        target = latest.resolve()
        text = target.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return {"lines": tail, "file": target.name, "total_lines": len(all_lines)}
    except OSError:
        return {"lines": [], "file": ""}


async def _sse_event_stream():
    """Async generator that yields SSE-formatted events.

    - Polls every ~1 s for session-file and trace-file mtime changes.
    - Emits ``event: session_update`` when the sessions file changes.
    - Emits ``event: trace_update`` (with the file stem as ``event_id``) when
      any ``.jsonl`` file inside ``TRACES_DIR`` is created or modified.
    - Emits a heartbeat ``data: {"type": "heartbeat"}`` every 15 s when no
      other event has been sent.
    - Exits cleanly on ``GeneratorExit`` or ``asyncio.CancelledError``.
    """
    # Snapshot initial state so only *changes* trigger events.
    sessions_mtime: float | None = None
    if _SESSIONS_FILE.exists():
        try:
            sessions_mtime = _SESSIONS_FILE.stat().st_mtime
        except OSError:
            sessions_mtime = None

    merge_queue_mtime: float | None = None
    if _MERGE_QUEUE_SENTINEL.exists():
        try:
            merge_queue_mtime = _MERGE_QUEUE_SENTINEL.stat().st_mtime
        except OSError:
            merge_queue_mtime = None

    trace_mtimes: dict[str, float] = {}
    if TRACES_DIR.exists():
        for p in TRACES_DIR.rglob("*.jsonl"):
            try:
                trace_mtimes[str(p)] = p.stat().st_mtime
            except OSError as exc:
                logger.debug("Failed to stat trace file: %s", exc)

    heartbeat_counter = 0
    try:
        while True:
            await asyncio.sleep(1)
            heartbeat_counter += 1
            sent_event = False

            # --- session file ---
            new_sessions_mtime: float | None = None
            if _SESSIONS_FILE.exists():
                try:
                    new_sessions_mtime = _SESSIONS_FILE.stat().st_mtime
                except OSError:
                    new_sessions_mtime = None
            if new_sessions_mtime is not None and new_sessions_mtime != sessions_mtime:
                sessions_mtime = new_sessions_mtime
                yield 'event: session_update\ndata: {"type": "session_update"}\n\n'
                sent_event = True
                heartbeat_counter = 0

            # --- merge queue sentinel ---
            new_mq_mtime: float | None = None
            if _MERGE_QUEUE_SENTINEL.exists():
                try:
                    new_mq_mtime = _MERGE_QUEUE_SENTINEL.stat().st_mtime
                except OSError:
                    new_mq_mtime = None
            if new_mq_mtime is not None and new_mq_mtime != merge_queue_mtime:
                merge_queue_mtime = new_mq_mtime
                yield 'event: merge_queue_update\ndata: {"type": "merge_queue_update"}\n\n'
                sent_event = True
                heartbeat_counter = 0

            # --- trace files ---
            if TRACES_DIR.exists():
                for p in TRACES_DIR.rglob("*.jsonl"):
                    path_str = str(p)
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        logger.debug("Cannot stat file %s, skipping", path_str)
                        continue
                    if path_str not in trace_mtimes or trace_mtimes[path_str] != mtime:
                        trace_mtimes[path_str] = mtime
                        event_id = p.stem
                        payload = json.dumps(
                            {"type": "trace_update", "event_id": event_id}
                        )
                        yield f"event: trace_update\ndata: {payload}\n\n"
                        sent_event = True
                        heartbeat_counter = 0

            # --- heartbeat ---
            if not sent_event and heartbeat_counter >= 15:
                yield 'data: {"type": "heartbeat"}\n\n'
                heartbeat_counter = 0
    except (GeneratorExit, asyncio.CancelledError):
        return


def mount_dashboard(  # pylint: disable=too-many-locals,too-many-statements
    app: Any,
    _config_snapshot: dict | None = None,
    live_state_file: Path | None = None,
    merge_queue: Any = None,
    heartbeat: Any = None,
) -> None:
    """Register /dashboard and API routes on *app*.

    *_config_snapshot* is accepted for backwards compatibility but no longer
    used.  Config data is served by the control API router instead.

    *live_state_file*, when set, makes ``/api/live`` read from a JSON file
    on disk instead of the in-memory :class:`LiveState` singleton.  Use this
    when the dashboard runs in a **separate process** from the daemon.
    """
    if not FASTAPI_AVAILABLE:
        logger.warning("FastAPI not available — dashboard routes not mounted")
        return

    # Restrict cross-origin requests to localhost origins only (daemon is local).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],  # no static origins; all matching done via regex
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/live")
    async def api_live(request: Request) -> JSONResponse:
        _require_api_key(request)
        if live_state_file is not None:
            snap = await _safe_to_thread(read_live_snapshot, live_state_file)
            if snap is None:
                return JSONResponse(content={})
        else:
            snap = LiveState.get().snapshot()
        return JSONResponse(content=snap)

    @app.get("/api/sessions")
    async def api_sessions(request: Request) -> JSONResponse:
        """Return golem session state from the sessions file."""
        _require_api_key(request)
        try:
            data = await _safe_to_thread(_read_sessions)
            if data is None:
                return JSONResponse(content={"sessions": {}})
            return JSONResponse(content=data)
        except (json.JSONDecodeError, OSError):
            return JSONResponse(content={"sessions": {}})

    @app.get("/api/logs")
    async def api_logs(
        request: Request,
        lines: int = Query(200, ge=10, le=2000),
    ) -> JSONResponse:
        """Return the tail of the daemon log file."""
        _require_api_key(request)
        data = await _safe_to_thread(_read_log_tail, lines)
        if data is None:
            return JSONResponse(content={"lines": [], "file": "", "total_lines": 0})
        return JSONResponse(content=data)

    @app.get("/api/ping")
    async def api_ping() -> JSONResponse:
        return JSONResponse(content={"status": "ok", "timestamp": int(time.time())})

    @app.get("/api/events")
    async def api_events(request: Request) -> StreamingResponse:
        _require_api_key(request)
        return StreamingResponse(
            _sse_event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/analytics")
    async def api_analytics(request: Request) -> JSONResponse:
        _require_api_key(request)
        from ..analytics import compute_analytics  # noqa: PLC0415

        runs = await _safe_to_thread(read_runs, limit=1000)
        if runs is None:
            return JSONResponse(content={})
        return JSONResponse(content=compute_analytics(runs))

    @app.get("/api/analytics/by-prompt")
    async def api_analytics_by_prompt(request: Request) -> JSONResponse:
        _require_api_key(request)
        from ..analytics import compute_prompt_analytics  # noqa: PLC0415

        runs = await _safe_to_thread(read_runs, limit=10_000)
        if runs is None:
            return JSONResponse(content={})
        return JSONResponse(content=compute_prompt_analytics(runs))

    @app.get("/api/cost-analytics")
    async def api_cost_analytics(request: Request) -> JSONResponse:
        _require_api_key(request)
        from ..cost_analytics import compute_cost_analytics  # noqa: PLC0415

        runs = await _safe_to_thread(read_runs, limit=10_000)
        sessions = await _safe_to_thread(load_sessions)
        if runs is None or sessions is None:
            return JSONResponse(content={})
        return JSONResponse(content=compute_cost_analytics(runs, sessions))

    @app.get("/api/trace-parsed/{event_id:path}")
    async def api_trace_parsed(
        request: Request, event_id: str, since_event: int = 0
    ) -> JSONResponse:
        """Return parsed trace. Pass ?since_event=N for incremental updates."""
        _require_api_key(request)
        result = await _safe_to_thread(_read_and_parse_trace, event_id, since_event)
        if result is None:
            return JSONResponse({"error": "Trace not found"}, status_code=404)
        return JSONResponse(result)

    @app.get("/api/trace/{event_id:path}")
    async def api_trace(request: Request, event_id: str) -> JSONResponse:
        _require_api_key(request)
        paths = _resolve_paths(event_id)
        if not paths["trace"]:
            return JSONResponse(
                status_code=404,
                content={"error": "No trace file found", "event_id": event_id},
            )
        sections = await _safe_to_thread(_parse_trace, paths["trace"])
        if sections is None:
            return JSONResponse(content={})
        return JSONResponse(content={"event_id": event_id, "sections": sections})

    @app.get("/api/prompt/{event_id:path}")
    async def api_prompt(request: Request, event_id: str) -> JSONResponse:
        _require_api_key(request)
        paths = _resolve_paths(event_id)
        if not paths["prompt"]:
            return JSONResponse(
                status_code=404,
                content={"error": "No prompt file found", "event_id": event_id},
            )
        text = await _safe_to_thread(paths["prompt"].read_text, encoding="utf-8")
        if text is None:
            return JSONResponse(content={})
        return JSONResponse(
            content={
                "event_id": event_id,
                "prompt": text,
                "size_bytes": len(text.encode("utf-8")),
            }
        )

    @app.get("/api/report/{event_id:path}")
    async def api_report(request: Request, event_id: str) -> JSONResponse:
        _require_api_key(request)
        paths = _resolve_paths(event_id)
        if not paths["report"]:
            return JSONResponse(
                status_code=404,
                content={"error": "No report file found", "event_id": event_id},
            )
        text = await _safe_to_thread(paths["report"].read_text, encoding="utf-8")
        if text is None:
            return JSONResponse(content={})
        return JSONResponse(content={"event_id": event_id, "markdown": text})

    @app.get("/api/trace-terminal/{event_id:path}")
    async def api_trace_terminal(request: Request, event_id: str) -> JSONResponse:
        """Return parsed terminal-renderable trace events for a flow run."""
        _require_api_key(request)
        paths = _resolve_paths(event_id)
        if not paths["trace"]:
            return JSONResponse(
                status_code=404,
                content={"error": "No trace file found", "event_id": event_id},
            )
        result = await _safe_to_thread(_parse_trace_terminal, paths["trace"])
        if result is None:
            return JSONResponse(content={})
        events, stats = result
        return JSONResponse(
            content={"event_id": event_id, "events": events, "stats": stats}
        )

    @app.get("/dashboard/shared.css")
    async def shared_css() -> Response:
        return Response(
            content=_shared_css_cache.read(),
            media_type="text/css",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/shared.js")
    async def shared_js() -> Response:
        return Response(
            content=_shared_js_cache.read(),
            media_type="application/javascript",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/task.css")
    async def task_css() -> Response:
        return Response(
            content=_task_css_cache.read(),
            media_type="text/css",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/task_api.js")
    async def task_api_js() -> Response:
        return Response(
            content=_task_api_js_cache.read(),
            media_type="application/javascript",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/task_timeline.js")
    async def task_timeline_js() -> Response:
        return Response(
            content=_task_timeline_js_cache.read(),
            media_type="application/javascript",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/task_overview.js")
    async def task_overview_js() -> Response:
        return Response(
            content=_task_overview_js_cache.read(),
            media_type="application/javascript",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/heartbeat_widget.js")
    async def heartbeat_widget_js() -> Response:
        return Response(
            content=_heartbeat_widget_js_cache.read(),
            media_type="application/javascript",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/task_live.js")
    async def task_live_js() -> Response:
        return Response(
            content=_task_live_js_cache.read(),
            media_type="application/javascript",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/elk.js")
    async def elk_js() -> Response:
        return Response(
            content=_elk_js_cache.read(),
            media_type="application/javascript",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/merge_queue.js")
    async def merge_queue_js() -> Response:
        return Response(
            content=_merge_queue_js_cache.read(),
            media_type="application/javascript",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/merge_queue.css")
    async def merge_queue_css() -> Response:
        return Response(
            content=_merge_queue_css_cache.read(),
            media_type="text/css",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/config_tab.js")
    async def config_tab_js() -> Response:
        return Response(
            content=_config_tab_js_cache.read(),
            media_type="application/javascript",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard/config_tab.css")
    async def config_tab_css() -> Response:
        return Response(
            content=_config_tab_css_cache.read(),
            media_type="text/css",
            headers=_NO_CACHE_HEADERS,
        )

    @app.get("/dashboard")
    async def dashboard() -> HTMLResponse:
        html = _task_dashboard_cache.read()
        # Inject cache-busting version query params into asset URLs
        # so browsers fetch fresh JS/CSS after file changes.
        for cache, ext in [
            (_shared_css_cache, "shared.css"),
            (_task_css_cache, "task.css"),
            (_shared_js_cache, "shared.js"),
            (_task_api_js_cache, "task_api.js"),
            (_task_timeline_js_cache, "task_timeline.js"),
            (_task_overview_js_cache, "task_overview.js"),
            (_heartbeat_widget_js_cache, "heartbeat_widget.js"),
            (_task_live_js_cache, "task_live.js"),
            (_merge_queue_js_cache, "merge_queue.js"),
            (_merge_queue_css_cache, "merge_queue.css"),
            (_config_tab_js_cache, "config_tab.js"),
            (_config_tab_css_cache, "config_tab.css"),
        ]:
            # Trigger a read so .version reflects current mtime
            cache.read()
            html = html.replace(
                f"/dashboard/{ext}",
                f"/dashboard/{ext}?v={cache.version}",
            )
        return HTMLResponse(content=html, headers=_NO_CACHE_HEADERS)

    @app.get("/api/merge-queue")
    async def api_merge_queue(request: Request):
        _require_api_key(request)
        if merge_queue is None:
            return {
                "pending": [],
                "active": None,
                "deferred": [],
                "conflicts": [],
                "history": [],
            }
        return merge_queue.snapshot()

    @app.post("/api/merge-queue/retry/{session_id}")
    async def api_merge_queue_retry(request: Request, session_id: int):
        _require_api_key(request)
        if merge_queue is None:
            return JSONResponse(
                status_code=503,
                content={"error": "Merge queue not available in standalone mode"},
            )
        try:
            await merge_queue.retry(session_id)
            return {"ok": True, "session_id": session_id}
        except ValueError as exc:
            return JSONResponse(status_code=404, content={"error": str(exc)})

    @app.get("/api/heartbeat")
    async def api_heartbeat(request: Request):
        _require_api_key(request)
        if heartbeat is None:
            return {
                "enabled": False,
                "state": "disabled",
                "last_scan_at": "",
                "last_scan_tier": 0,
                "daily_spend_usd": 0.0,
                "daily_budget_usd": 0.0,
                "inflight_task_ids": [],
                "candidate_count": 0,
                "dedup_entry_count": 0,
                "next_tick_seconds": 0,
            }
        return heartbeat.snapshot()

    @app.post("/api/heartbeat/trigger")
    async def api_heartbeat_trigger(request: Request):
        _require_api_key(request)
        if heartbeat is None:
            return {"ok": False, "detail": "heartbeat disabled"}
        triggered = heartbeat.trigger()
        return {"ok": triggered}

    @app.get("/dashboard/admin")
    async def admin_page() -> HTMLResponse:
        return HTMLResponse(content=_admin_cache.read())


_NO_CACHE_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}


class _FileCache:
    """Cache file content, reload only when mtime changes."""

    def __init__(self, path: Path):
        self._path = path
        self._mtime: float = 0.0
        self._content: str = ""

    @property
    def version(self) -> str:
        """Return mtime as a short version string for cache-busting URLs."""
        return str(int(self._mtime))

    def read(self) -> str:
        """Return cached content, re-reading from disk if mtime changed."""
        try:
            mt = self._path.stat().st_mtime
        except OSError:
            return self._content
        if mt != self._mtime:
            self._content = self._path.read_text(encoding="utf-8")
            self._mtime = mt
        return self._content


_task_dashboard_cache = _FileCache(Path(__file__).parent / "task_dashboard.html")
_admin_cache = _FileCache(Path(__file__).parent / "admin.html")
_shared_css_cache = _FileCache(Path(__file__).parent / "dashboard_shared.css")
_shared_js_cache = _FileCache(Path(__file__).parent / "dashboard_shared.js")
_task_css_cache = _FileCache(Path(__file__).parent / "task_dashboard.css")
_task_api_js_cache = _FileCache(Path(__file__).parent / "task_api.js")
_task_timeline_js_cache = _FileCache(Path(__file__).parent / "task_timeline.js")
_task_overview_js_cache = _FileCache(Path(__file__).parent / "task_overview.js")
_task_live_js_cache = _FileCache(Path(__file__).parent / "task_live.js")
_heartbeat_widget_js_cache = _FileCache(Path(__file__).parent / "heartbeat_widget.js")
_elk_js_cache = _FileCache(Path(__file__).parent / "elk.min.js")
_merge_queue_js_cache = _FileCache(Path(__file__).parent / "task_merge_queue.js")
_merge_queue_css_cache = _FileCache(Path(__file__).parent / "task_merge_queue.css")
_config_tab_js_cache = _FileCache(Path(__file__).parent / "config_tab.js")
_config_tab_css_cache = _FileCache(Path(__file__).parent / "config_tab.css")


_PID_FILE = DATA_DIR / "daemon.pid"


def _check_daemon_status(
    pid_file: Path | None = None,
) -> tuple[str, bool]:
    """Check if the golem daemon is running. Returns (label, is_running)."""
    pid_file = pid_file or _PID_FILE
    pid = read_pid(pid_file)
    if pid is None:
        return "stopped", False
    try:
        os.kill(pid, 0)
        return f"running (PID {pid})", True
    except OSError:
        return "stopped (stale PID)", False


def _resolve_subject(eid: str, sessions: dict, max_len: int = 50) -> tuple[str, str]:
    """Extract numeric ID and resolve subject from sessions, with truncation."""
    _, num = _extract_numeric_id(eid)
    sess = sessions.get(int(num)) if num else None
    subject = getattr(sess, "parent_subject", "") or eid
    if len(subject) > max_len:
        subject = subject[: max_len - 3] + "..."
    return num or "?", subject


def _format_active_task(task: ActiveTaskDict, sessions: dict) -> list[str]:
    """Format a single active task as two display lines."""
    num, subject = _resolve_subject(task["event_id"], sessions, max_len=50)
    elapsed = format_duration(task.get("elapsed_s", 0))
    sess = sessions.get(int(num)) if num != "?" else None
    cost = getattr(sess, "total_cost_usd", 0.0)
    return [
        f"    #{num:>5s}  {subject}",
        f"           Phase: {task.get('phase', '?')}  Model: {task.get('model', '?')}"
        f"  Elapsed: {elapsed}  Cost: ${cost:.2f}",
    ]


def _format_live_section(
    snap: LiveSnapshotDict, sessions: dict[int, Any] | None = None
) -> list[str]:
    """Build the LIVE block for CLI status output."""
    sessions = sessions if sessions is not None else {}
    lines = ["", f"  Uptime:       {format_duration(snap.get('uptime_s', 0))}"]

    if snap["active_count"]:
        lines += ["", "  ACTIVE:"]
        for t in snap["active_tasks"]:
            lines += _format_active_task(t, sessions)
    else:
        lines.append("  Active:       No active tasks")

    lines.append(f"  Queue:        {snap['queue_depth']} waiting")

    recent = snap.get("recently_completed", [])
    if recent:
        lines += ["", "  RECENT:"]
        for c in recent[:5]:
            status = "OK" if c.get("success") else "FAIL"
            ago = format_duration(c.get("finished_ago_s", 0))
            num, subject = _resolve_subject(c.get("event_id", ""), sessions, max_len=30)
            lines.append(
                f"    [{status:4s}]  {ago:>8s} ago  "
                f"#{num:>5s}  {subject:<30s}  ${c.get('cost_usd', 0.0):.2f}"
                f"  {format_duration(c.get('duration_s', 0))}"
            )

    return lines


def format_status_text(since_hours: int = 24, flow: str | None = None) -> str:
    """Build a plain-text status summary for CLI output."""
    since = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    runs = read_runs(limit=10_000, since=since, flow=flow)
    stats = _aggregate_stats(runs)

    scope = f" — {flow}" if flow else ""
    lines = [
        f"=== Golem Status (last {since_hours}h{scope}) ===",
    ]

    daemon_label, _ = _check_daemon_status()
    lines.append(f"  Daemon:       {daemon_label}")

    snap = read_live_snapshot()
    sessions = load_sessions()
    lines += _format_live_section(snap, sessions=sessions)

    # Compact history summary
    lines += [
        "",
        "  HISTORY:",
        f"    Total: {stats['total_runs']}  "
        f"Success: {stats['success_rate']}%  "
        f"Avg: {format_duration(stats['avg_duration_s'])}  "
        f"Cost: ${stats['total_cost_usd']:.2f}",
    ]

    if stats["by_flow"]:
        lines.append("")
        for flow_name, data in stats["by_flow"].items():
            lines.append(
                f"    {flow_name:12s}  "
                f"{data['success']}/{data['total']} passed  "
                f"({data['success_rate']}%)"
            )

    lines += _format_recent_runs(runs[:10], sessions)

    return "\n".join(lines)


def format_task_detail_text(task_id: int) -> str:
    """Build a plain-text detailed view for a single task."""
    sessions = load_sessions()
    if task_id not in sessions:
        return f"Task #{task_id} not found."

    sess = sessions[task_id]

    lines = [f"=== Task #{task_id} ==="]
    lines.append(f"  Subject:      {sess.parent_subject}")
    lines.append(f"  State:        {sess.state.value}")
    lines.append(f"  Priority:     {sess.priority}")
    lines.append(f"  Created:      {sess.created_at}")
    lines.append(f"  Updated:      {sess.updated_at}")
    lines.append(f"  Duration:     {format_duration(sess.duration_seconds)}")
    lines.append(
        f"  Cost:         ${sess.total_cost_usd:.2f} / ${sess.budget_usd:.2f} budget"
    )

    lines += [
        "",
        "  EXECUTION:",
        f"    Mode:         {sess.execution_mode}",
        f"    Phase:        {sess.supervisor_phase}",
        f"    Fix iters:    {sess.fix_iteration}",
        f"    Full retries: {sess.retry_count}",
        f"    Worktree:     {sess.worktree_path}",
    ]

    if sess.validation_verdict:
        concerns = (
            ", ".join(sess.validation_concerns) if sess.validation_concerns else "none"
        )
        lines += [
            "",
            "  VALIDATION:",
            f"    Verdict:      {sess.validation_verdict}"
            f" (confidence: {sess.validation_confidence})",
            f"    Summary:      {sess.validation_summary}",
            f"    Concerns:     {concerns}",
        ]

    lines += [
        "",
        "  RESULT:",
        f"    Summary:      {sess.result_summary}",
    ]
    if sess.commit_sha:
        lines.append(f"    Commit:       {sess.commit_sha}")
    if sess.files_changed:
        lines.append(f"    Files:        {len(sess.files_changed)} changed")
        for f in sess.files_changed:
            lines.append(f"      - {f}")

    lines += ["", "  ERRORS:"]
    if sess.errors:
        for err in sess.errors:
            lines.append(f"    {err}")
    else:
        lines.append("    (none)")

    if sess.event_log:
        lines += ["", f"  EVENT LOG (last {min(10, len(sess.event_log))})"]
        for ev in sess.event_log[-10:]:
            raw_ts = ev.get("timestamp", 0)
            ts = datetime.fromtimestamp(raw_ts).strftime("%H:%M:%S") if raw_ts else ""
            ev_kind = ev.get("kind", "")
            msg = ev.get("summary", "")
            lines.append(f"    {ts}  {ev_kind:<10s}  {msg}")

    return "\n".join(lines)


def _format_recent_runs(
    runs: list[RunRecordDict], sessions: dict[int, Any]
) -> list[str]:
    """Format the recent runs section for CLI status output."""
    if not runs:
        return []
    lines = ["", "  Recent runs:"]
    for r in runs:
        status = "OK" if r.get("success") else "FAIL"
        started = r.get("started_at", "")[:16].replace("T", " ")
        num, subject = _resolve_subject(r.get("event_id", ""), sessions, max_len=30)
        lines.append(
            f"    [{status:4s}] {started}  {r.get('flow', '?'):6s}  "
            f"#{num:>5s}  {subject:<30s}  ${r.get('cost_usd', 0.0):.2f}"
            f"  {format_duration(r.get('duration_s', 0))}"
        )
    return lines
