"""Standalone flow-control API router.

Provides ``/api/flow/start``, ``/api/flow/stop``, ``/api/flow/status``,
``/api/health``, and ``/api/submit`` endpoints as a FastAPI ``APIRouter``.
Can be mounted on the webhook app, a standalone control server, or the
dashboard app.

Runtime dependencies are injected once via :func:`wire_control_api`.
"""

# pylint: disable=invalid-name,global-statement

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from .triggers import FASTAPI_AVAILABLE

if TYPE_CHECKING:
    from typing import Any

    Dispatcher = Any
    PollingTrigger = Any

logger = logging.getLogger("golem.core.control_api")

if FASTAPI_AVAILABLE:
    from fastapi import APIRouter, HTTPException, Request
else:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment,misc]
    HTTPException = None  # type: ignore[assignment,misc]
    Request = None  # type: ignore[assignment,misc]

# Module-level state — set once at startup by wire_control_api().
_polling_trigger: "PollingTrigger | None" = None
_dispatcher: "Dispatcher | None" = None
_admin_token: str = ""
_golem_flow: "Any | None" = None
_start_time: float = time.time()


def wire_control_api(
    polling_trigger: "PollingTrigger | None" = None,
    dispatcher: "Dispatcher | None" = None,
    admin_token: str = "",
    golem_flow: "Any | None" = None,
) -> None:
    """Inject runtime dependencies.  Called once at daemon startup."""
    global _polling_trigger, _dispatcher, _admin_token, _golem_flow, _start_time
    _polling_trigger = polling_trigger
    _dispatcher = dispatcher
    _admin_token = admin_token
    _golem_flow = golem_flow
    _start_time = time.time()


def _maybe_start_tick(flow_name: str) -> None:
    """Start the tick loop for *flow_name* if it supports one."""
    if _dispatcher is None:
        return
    flow = _dispatcher.get_flow(flow_name)
    if flow and hasattr(flow, "start_tick_loop"):
        flow.start_tick_loop()
        logger.info("Tick loop started for %s via control API", flow_name)


def _maybe_stop_tick(flow_name: str) -> bool:
    """Stop the tick loop for *flow_name* if it supports one.  Returns True if stopped."""
    if _dispatcher is None:
        return False
    flow = _dispatcher.get_flow(flow_name)
    if flow and hasattr(flow, "stop_tick_loop"):
        flow.stop_tick_loop()
        logger.info("Tick loop stopped for %s via control API", flow_name)
        return True
    return False


def _require_polling():
    """Raise 503 if the polling trigger is not wired."""
    if _polling_trigger is None:
        raise HTTPException(
            status_code=503,
            detail="Control API not connected to a running daemon",
        )


def _require_admin(request: "Request"):
    """Raise 401/403 if the request lacks a valid admin token.

    Accepts ``Authorization: Bearer <token>`` header or ``?token=`` query param.
    """
    if not _admin_token:
        raise HTTPException(status_code=403, detail="Admin features not configured")
    token = ""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    if not token:
        token = request.query_params.get("token", "")
    if not token or token != _admin_token:
        raise HTTPException(status_code=401, detail="Invalid admin token")


if FASTAPI_AVAILABLE:
    control_router = APIRouter(prefix="/api/flow", tags=["flow-control"])

    @control_router.post("/stop")
    async def flow_stop(request: Request):
        """Stop one or more flows by name."""
        _require_admin(request)
        _require_polling()
        payload = await request.json()
        results = {}
        for name in payload.get("flows", []):
            stopped = await _polling_trigger.stop_flow(name)
            if _maybe_stop_tick(name):
                stopped = True
            results[name] = "stopped" if stopped else "not_running"
        return {"ok": True, "results": results}

    @control_router.post("/start")
    async def flow_start(request: Request):
        """Start one or more flows by name."""
        _require_admin(request)
        _require_polling()
        payload = await request.json()
        results = {}
        for name in payload.get("flows", []):
            started = await _polling_trigger.start_flow(name, force=True)
            if started:
                _maybe_start_tick(name)
            results[name] = "started" if started else "already_running_or_unavailable"
        return {"ok": True, "results": results}

    @control_router.get("/status")
    async def flow_status(request: Request):
        """Return the status of all configured flows."""
        if _polling_trigger is None:
            return {"ok": True, "flows": {}}
        only = request.query_params.get("flow")
        status = _polling_trigger.flow_status()
        if only:
            status = {k: v for k, v in status.items() if k == only}
        return {"ok": True, "flows": status}

    # -- Health + Submit endpoints (no admin token required) ----------------

    health_router = APIRouter(prefix="/api", tags=["daemon"])

    @health_router.get("/health")
    async def health_check():
        """Lightweight health check for CLI daemon-readiness probing."""
        return {
            "ok": True,
            "pid": os.getpid(),
            "uptime_seconds": round(time.time() - _start_time, 1),
        }

    @health_router.post("/submit")
    async def submit_task(request: Request):
        """Submit a prompt task to the running daemon.

        Accepts JSON::

            {"prompt": "...", "subject": "...", "work_dir": "...", "mcp": true}

        or::

            {"file": "/path/to/prompt.md"}

        Returns ``{"ok": true, "task_id": ..., "status": "submitted"}``.
        """
        if _golem_flow is None:
            raise HTTPException(
                status_code=503,
                detail="Daemon not ready — GolemFlow not wired",
            )

        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON: {exc}",
            ) from exc

        prompt = payload.get("prompt", "")
        file_path = payload.get("file", "")

        if file_path and not prompt:
            p = Path(file_path)
            if not p.is_file():
                raise HTTPException(
                    status_code=400,
                    detail=f"File not found: {file_path}",
                )
            prompt = p.read_text(encoding="utf-8")

        if not prompt:
            raise HTTPException(
                status_code=400,
                detail="Either 'prompt' or 'file' is required",
            )

        subject = payload.get("subject", "")
        work_dir = payload.get("work_dir", "")
        try:
            result = _golem_flow.submit_task(
                prompt=prompt,
                subject=subject,
                work_dir=work_dir,
            )
        except Exception:
            logger.error("submit_task failed:\n%s", traceback.format_exc())
            raise HTTPException(
                status_code=500,
                detail=f"Internal error: {traceback.format_exc()}",
            ) from None
        return {"ok": True, **result}

    @health_router.post("/submit/batch")
    async def submit_batch(request: Request):
        """Submit multiple tasks as a batch with optional dependencies.

        Accepts JSON::

            {
                "tasks": [
                    {"prompt": "...", "subject": "...", "depends_on": []},
                    {"prompt": "...", "depends_on": [0]}
                ],
                "group_id": "optional-group-name"
            }

        ``depends_on`` entries are zero-based indices into the ``tasks`` array.
        Returns ``{"ok": true, "group_id": ..., "tasks": [...]}``.
        """
        if _golem_flow is None:
            raise HTTPException(
                status_code=503,
                detail="Daemon not ready — GolemFlow not wired",
            )

        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid JSON: {exc}",
            ) from exc

        tasks = payload.get("tasks")
        if not tasks or not isinstance(tasks, list):
            raise HTTPException(
                status_code=400,
                detail="'tasks' array is required",
            )

        for i, task in enumerate(tasks):
            prompt = task.get("prompt") if isinstance(task, dict) else None
            if not isinstance(prompt, str) or not prompt.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"Task at index {i} is missing a non-empty 'prompt' string",
                )
            for dep in task.get("depends_on", []):
                if not isinstance(dep, int) or dep < 0 or dep >= i:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Task at index {i} has invalid depends_on value {dep!r}: "
                            f"must be an int in range [0, {i})"
                        ),
                    )

        group_id = payload.get("group_id", "")
        try:
            result = _golem_flow.submit_batch(tasks, group_id=group_id)
        except Exception:
            logger.error("submit_batch failed:\n%s", traceback.format_exc())
            raise HTTPException(
                status_code=500,
                detail=f"Internal error: {traceback.format_exc()}",
            ) from None
        return {"ok": True, **result}

else:  # pragma: no cover
    # Provide a no-op router when FastAPI is not installed.
    control_router = None  # type: ignore[assignment]
    health_router = None  # type: ignore[assignment]
