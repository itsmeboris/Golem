"""HTML dashboard and API routes for run history and stats."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .live_state import LiveState, read_live_snapshot
from .run_log import read_runs

logger = logging.getLogger("golem.core.dashboard")

try:
    from fastapi import Query
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    Query = None
    HTMLResponse = None
    JSONResponse = None
    Response = None


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


def _resolve_paths(event_id: str) -> dict[str, Path | None]:
    """Find prompt/trace/report file paths for a given event_id."""
    flow, numeric_id = _extract_numeric_id(event_id)
    safe_id = event_id.replace("/", "_")

    trace_path: Path | None = None
    prompt_path: Path | None = None
    report_path: Path | None = None

    if flow:
        t = TRACES_DIR / flow / f"{safe_id}.jsonl"
        if t.exists():
            trace_path = t
        p = TRACES_DIR / flow / f"{safe_id}.prompt.txt"
        if p.exists():
            prompt_path = p

        if numeric_id:
            r = REPORTS_DIR / flow / f"{numeric_id}.md"
            if r.exists():
                report_path = r

    return {
        "trace": trace_path,
        "prompt": prompt_path,
        "report": report_path,
    }


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
            events.append(_term_ev("tool_call", name, tool_name=name))
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


def _aggregate_stats(runs: list[dict]) -> dict[str, Any]:
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


def config_to_snapshot(config: Any) -> dict:
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


def mount_dashboard(  # pylint: disable=too-many-locals,too-many-statements
    app: Any,
    config_snapshot: dict | None = None,
    live_state_file: Path | None = None,
) -> None:
    """Register /dashboard and API routes on *app*.

    *config_snapshot* is an optional dict with system configuration info
    to display in the dashboard header (model, flows, etc.).

    *live_state_file*, when set, makes ``/api/live`` read from a JSON file
    on disk instead of the in-memory :class:`LiveState` singleton.  Use this
    when the dashboard runs in a **separate process** from the daemon.
    """
    if not FASTAPI_AVAILABLE:
        logger.warning("FastAPI not available — dashboard routes not mounted")
        return

    config_snapshot = config_snapshot or {}  # noqa: PLW0127

    @app.get("/api/live")
    async def api_live() -> JSONResponse:
        if live_state_file is not None:
            snap = await asyncio.to_thread(read_live_snapshot, live_state_file)
        else:
            snap = LiveState.get().snapshot()
        return JSONResponse(content=snap)

    @app.get("/api/sessions")
    async def api_sessions() -> JSONResponse:
        """Return golem session state from the sessions file."""
        try:
            data = await asyncio.to_thread(_read_sessions)
            return JSONResponse(content=data)
        except (json.JSONDecodeError, OSError):
            return JSONResponse(content={"sessions": {}})

    @app.get("/api/logs")
    async def api_logs(
        lines: int = Query(200, ge=10, le=2000),
    ) -> JSONResponse:
        """Return the tail of the daemon log file."""
        data = await asyncio.to_thread(_read_log_tail, lines)
        return JSONResponse(content=data)

    @app.get("/api/config")
    async def api_config() -> JSONResponse:
        return JSONResponse(content=config_snapshot)

    @app.get("/api/trace/{event_id:path}")
    async def api_trace(event_id: str) -> JSONResponse:
        paths = _resolve_paths(event_id)
        if not paths["trace"]:
            return JSONResponse(
                status_code=404,
                content={"error": "No trace file found", "event_id": event_id},
            )
        sections = await asyncio.to_thread(_parse_trace, paths["trace"])
        return JSONResponse(content={"event_id": event_id, "sections": sections})

    @app.get("/api/prompt/{event_id:path}")
    async def api_prompt(event_id: str) -> JSONResponse:
        paths = _resolve_paths(event_id)
        if not paths["prompt"]:
            return JSONResponse(
                status_code=404,
                content={"error": "No prompt file found", "event_id": event_id},
            )
        text = await asyncio.to_thread(paths["prompt"].read_text, encoding="utf-8")
        return JSONResponse(
            content={
                "event_id": event_id,
                "prompt": text,
                "size_bytes": len(text.encode("utf-8")),
            }
        )

    @app.get("/api/report/{event_id:path}")
    async def api_report(event_id: str) -> JSONResponse:
        paths = _resolve_paths(event_id)
        if not paths["report"]:
            return JSONResponse(
                status_code=404,
                content={"error": "No report file found", "event_id": event_id},
            )
        text = await asyncio.to_thread(paths["report"].read_text, encoding="utf-8")
        return JSONResponse(content={"event_id": event_id, "markdown": text})

    @app.get("/api/trace-terminal/{event_id:path}")
    async def api_trace_terminal(event_id: str) -> JSONResponse:
        """Return parsed terminal-renderable trace events for a flow run."""
        paths = _resolve_paths(event_id)
        if not paths["trace"]:
            return JSONResponse(
                status_code=404,
                content={"error": "No trace file found", "event_id": event_id},
            )
        events, stats = await asyncio.to_thread(_parse_trace_terminal, paths["trace"])
        return JSONResponse(
            content={"event_id": event_id, "events": events, "stats": stats}
        )

    @app.get("/dashboard/shared.css")
    async def shared_css() -> Response:
        return Response(content=_shared_css_cache.read(), media_type="text/css")

    @app.get("/dashboard/shared.js")
    async def shared_js() -> Response:
        return Response(
            content=_shared_js_cache.read(),
            media_type="application/javascript",
        )

    @app.get("/dashboard/task.css")
    async def task_css() -> Response:
        return Response(content=_task_css_cache.read(), media_type="text/css")

    @app.get("/dashboard/task.js")
    async def task_js() -> Response:
        return Response(
            content=_task_js_cache.read(),
            media_type="application/javascript",
        )

    @app.get("/dashboard/elk.js")
    async def elk_js() -> Response:
        return Response(
            content=_elk_js_cache.read(),
            media_type="application/javascript",
        )

    @app.get("/dashboard")
    async def dashboard() -> HTMLResponse:
        return HTMLResponse(content=_task_dashboard_cache.read())

    @app.get("/dashboard/admin")
    async def admin_page() -> HTMLResponse:
        return HTMLResponse(content=_admin_cache.read())


class _FileCache:
    """Cache file content, reload only when mtime changes."""

    def __init__(self, path: Path):
        self._path = path
        self._mtime: float = 0.0
        self._content: str = ""

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
_task_js_cache = _FileCache(Path(__file__).parent / "task_dashboard.js")
_elk_js_cache = _FileCache(Path(__file__).parent / "elk.min.js")


def _format_live_section(snap: dict) -> list[str]:
    """Build the LIVE block for the CLI status output."""
    if not snap["active_count"] and not snap["queue_depth"]:
        return []

    lines = [
        "",
        "  LIVE:",
        f"    Running now:  {snap['active_count']}",
        f"    In queue:     {snap['queue_depth']}",
    ]
    for t in snap["active_tasks"]:
        eid = t["event_id"]
        if len(eid) > 40:
            eid = eid[:37] + "..."
        lines.append(
            f"      {t['flow']:10s}  {t['phase']:10s}  "
            f"{t['elapsed_s']:>6.1f}s  {eid}"
        )
    if snap["models_active"]:
        models = ", ".join(f"{m} x{c}" for m, c in snap["models_active"].items())
        lines.append(f"    Models:       {models}")
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

    lines += _format_live_section(LiveState.get().snapshot())

    lines += [
        "",
        f"  Total runs:    {stats['total_runs']}",
        f"  Success rate:  {stats['success_rate']}%",
        f"  Failures:      {stats['failure_count']}",
        f"  Avg duration:  {stats['avg_duration_s']}s",
        f"  Total cost:    ${stats['total_cost_usd']:.4f}",
        f"  Tokens used:   {stats['total_tokens']:,}",
    ]

    if stats["by_flow"]:
        lines += ["", "  By flow:"]
        for flow_name, data in stats["by_flow"].items():
            lines.append(
                f"    {flow_name:12s}  "
                f"{data['success']}/{data['total']} passed  "
                f"({data['success_rate']}%)"
            )

    recent = runs[:10]
    if recent:
        lines += ["", "  Recent runs:"]
        for r in recent:
            status = "OK" if r.get("success") else "FAIL"
            t = r.get("started_at", "")[:19]
            flow = r.get("flow", "?")
            eid = r.get("event_id", "")
            if len(eid) > 50:
                eid = eid[:47] + "..."
            lines.append(f"    [{status:4s}] {t}  {flow:10s}  {eid}")

    return "\n".join(lines)
