"""Standalone flow-control API router.

Provides ``/api/flow/start``, ``/api/flow/stop``, ``/api/flow/status``,
``/api/health``, and ``/api/submit`` endpoints as a FastAPI ``APIRouter``.
Can be mounted on the webhook app, a standalone control server, or the
dashboard app.

Runtime dependencies are injected once via :func:`wire_control_api`.
"""

# pylint: disable=invalid-name,global-statement

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import TaskNotCancelableError, TaskNotFoundError
from .triggers import FASTAPI_AVAILABLE

if TYPE_CHECKING:
    from typing import Any

    Dispatcher = Any
    PollingTrigger = Any

logger = logging.getLogger("golem.core.control_api")


class _RateLimiter:
    """Simple in-memory sliding-window rate limiter."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self._max = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        """Return True if request is allowed, False if rate limited."""
        now = time.monotonic()
        window_start = now - self._window
        # Prune old entries
        self._requests[key] = [t for t in self._requests[key] if t > window_start]
        if len(self._requests[key]) >= self._max:
            return False
        self._requests[key].append(now)
        return True


_submit_limiter = _RateLimiter(max_requests=10, window_seconds=60)

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
_api_key: str = ""
_golem_flow: "Any | None" = None
_start_time: float = time.time()
_config_path: str = "config.yaml"
_reload_event: "asyncio.Event | None" = None
_self_update_manager: "Any | None" = None


def wire_control_api(
    polling_trigger: "PollingTrigger | None" = None,
    dispatcher: "Dispatcher | None" = None,
    admin_token: str = "",
    api_key: str = "",
    golem_flow: "Any | None" = None,
    config_path: str = "config.yaml",
    reload_event: "asyncio.Event | None" = None,
    self_update_manager: "Any | None" = None,
) -> None:
    """Inject runtime dependencies.  Called once at daemon startup."""
    global _polling_trigger, _dispatcher, _admin_token, _api_key, _golem_flow, _start_time
    global _config_path, _reload_event, _self_update_manager
    _polling_trigger = polling_trigger
    _dispatcher = dispatcher
    _admin_token = admin_token
    _api_key = api_key
    _golem_flow = golem_flow
    _start_time = time.time()
    _config_path = config_path
    _reload_event = reload_event
    _self_update_manager = self_update_manager


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
    if not token or not hmac.compare_digest(token, _admin_token):
        raise HTTPException(status_code=401, detail="Invalid admin token")


def _require_api_key(request: "Request"):
    if not _api_key:
        return
    token = ""
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    if not token:
        token = request.query_params.get("token", "")
    if not token or not hmac.compare_digest(token, _api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _require_admin_or_open(request: "Request"):
    """Like _require_admin but allows open access when no token is configured."""
    if not _admin_token:
        return  # open access
    _require_admin(request)


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
        """Health check — includes metrics when GolemFlow is wired."""
        result = {
            "ok": True,
            "pid": os.getpid(),
            "uptime_seconds": round(time.time() - _start_time, 1),
        }
        if _golem_flow is not None:
            result["health"] = _golem_flow.health.snapshot()
        return result

    @health_router.post("/submit")
    async def submit_task(request: Request):
        """Submit a prompt task to the running daemon.

        Accepts JSON::

            {"prompt": "...", "subject": "...", "work_dir": "...", "mcp": true}

        or::

            {"file": "/path/to/prompt.md"}

        Returns ``{"ok": true, "task_id": ..., "status": "submitted"}``.
        """
        _require_api_key(request)
        client_ip = request.client.host if request.client else "unknown"
        if not _submit_limiter.check(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
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
            p = Path(file_path).resolve()

            # Build allowed base directories: CWD and attached repo paths.
            # work_dir is user-supplied and MUST NOT be trusted as an
            # allowed base (attacker could set work_dir: "/" to bypass).
            allowed_bases = [Path.cwd().resolve()]
            try:
                from golem.repo_registry import RepoRegistry  # noqa: PLC0415

                for entry in RepoRegistry().list_repos():
                    repo_path = entry.get("path", "")
                    if repo_path:
                        allowed_bases.append(Path(repo_path).resolve())
            except Exception:  # pylint: disable=broad-except
                logger.warning("Could not load repo registry for path validation")

            if not any(p == base or base in p.parents for base in allowed_bases):
                raise HTTPException(
                    status_code=403,
                    detail="File path outside allowed directories",
                )

            # Atomic open with O_NOFOLLOW to prevent TOCTOU symlink swap.
            # This also handles the "file not found" case since os.open raises
            # OSError (ENOENT) when the path does not exist.
            try:
                fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
            except OSError:
                raise HTTPException(status_code=403, detail="File not accessible")
            try:
                with os.fdopen(fd, "r", encoding="utf-8") as f:
                    prompt = f.read()
            except Exception:
                # os.fdopen takes ownership of fd on success; if it raised
                # before taking ownership the fd may still be open, but that
                # is an exceptional path — let the OS reclaim it on request end.
                raise HTTPException(status_code=400, detail="Could not read file")

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
            logger.exception("submit_task failed")
            raise HTTPException(
                status_code=500,
                detail="Internal server error",
            ) from None
        return {"ok": True, **result}

    @health_router.post("/sessions/clear-failed")
    async def clear_failed_sessions(request: Request):
        """Remove all FAILED sessions from state.

        Returns ``{"ok": true, "cleared": [...]}``.
        """
        _require_api_key(request)
        if _golem_flow is None:
            raise HTTPException(
                status_code=503,
                detail="Daemon not ready — GolemFlow not wired",
            )
        cleared = _golem_flow.clear_failed_sessions()
        return {"ok": True, "cleared": cleared}

    @health_router.post("/cancel/{task_id}")
    async def cancel_task(task_id: int, request: Request):
        """Cancel a running task by ID.

        Returns ``{"ok": true, "task_id": ..., "status": "cancelled"}``.
        """
        _require_api_key(request)
        client_ip = request.client.host if request.client else "unknown"
        if not _submit_limiter.check(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        if _golem_flow is None:
            raise HTTPException(
                status_code=503,
                detail="Daemon not ready — GolemFlow not wired",
            )
        try:
            result = _golem_flow.cancel_session(task_id)
        except TaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except TaskNotCancelableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        _require_api_key(request)
        client_ip = request.client.host if request.client else "unknown"
        if not _submit_limiter.check(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
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

        known_keys: set[str] = set()
        for i, task in enumerate(tasks):
            prompt = task.get("prompt") if isinstance(task, dict) else None
            if not isinstance(prompt, str) or not prompt.strip():
                raise HTTPException(
                    status_code=400,
                    detail=f"Task at index {i} is missing a non-empty 'prompt' string",
                )
            task_key = task.get("key", "")
            if task_key:
                known_keys.add(task_key)
            for dep in task.get("depends_on", []):
                if isinstance(dep, str):
                    if dep not in known_keys:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"Task at index {i} has unknown depends_on key {dep!r}: "
                                f"must reference a 'key' declared by a preceding task"
                            ),
                        )
                elif not isinstance(dep, int) or dep < 0 or dep >= i:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Task at index {i} has invalid depends_on value {dep!r}: "
                            f"must be an int in range [0, {i}) or a string key"
                        ),
                    )

        group_id = payload.get("group_id", "")
        try:
            result = _golem_flow.submit_batch(tasks, group_id=group_id)
        except Exception:
            logger.exception("submit_batch failed")
            raise HTTPException(
                status_code=500,
                detail="Internal server error",
            ) from None
        return {"ok": True, **result}

    @health_router.get("/sessions/{task_id}")
    async def get_session(task_id: int, request: Request):
        _require_api_key(request)
        if _golem_flow is None:
            raise HTTPException(
                status_code=503,
                detail="Daemon not ready — GolemFlow not wired",
            )
        session = _golem_flow.get_session(task_id)
        if session is None:
            raise HTTPException(
                status_code=404,
                detail=f"No session found for task_id={task_id}",
            )
        return {"ok": True, "session": session.to_dict()}

    @health_router.get("/batch/{group_id}")
    async def get_batch(group_id: str, request: Request):
        """Return batch status for a given group_id."""
        _require_api_key(request)
        if _golem_flow is None:
            raise HTTPException(
                status_code=503,
                detail="Daemon not ready — GolemFlow not wired",
            )
        batch = _golem_flow.get_batch(group_id)
        if batch is None:
            raise HTTPException(
                status_code=404,
                detail=f"No batch found for group_id={group_id!r}",
            )
        return {"ok": True, "batch": batch}

    @health_router.get("/batches")
    async def list_batches(request: Request):
        """Return all tracked batches."""
        _require_api_key(request)
        if _golem_flow is None:
            raise HTTPException(
                status_code=503,
                detail="Daemon not ready — GolemFlow not wired",
            )
        return {"ok": True, "batches": _golem_flow.list_batches()}

    @health_router.get("/self-update")
    async def self_update_status(request: Request):
        """Return self-update manager status snapshot."""
        _require_api_key(request)
        if _self_update_manager is None:
            return {"enabled": False}
        return _self_update_manager.snapshot()

    @health_router.get("/config")
    async def get_config(request: Request):
        """Return current config values grouped by category."""
        _require_admin_or_open(request)
        from golem.config_editor import get_config_by_category  # noqa: PLC0415
        from golem.core.config import load_config  # noqa: PLC0415

        config = load_config(_config_path)
        categories = get_config_by_category(config)
        result = {}
        for cat, fields in categories.items():
            result[cat] = []
            for fi in fields:
                value = "***" if fi.meta.sensitive else fi.value
                result[cat].append(
                    {
                        "key": fi.key,
                        "value": value,
                        "meta": {
                            "category": fi.meta.category,
                            "field_type": fi.meta.field_type,
                            "description": fi.meta.description,
                            "choices": fi.meta.choices,
                            "min_val": fi.meta.min_val,
                            "max_val": fi.meta.max_val,
                            "sensitive": fi.meta.sensitive,
                        },
                    }
                )
        return result

    @health_router.post("/config/update")
    async def update_config_endpoint(request: Request):
        """Validate and apply config updates, optionally triggering a reload."""
        _require_admin_or_open(request)
        from golem.config_editor import update_config  # noqa: PLC0415

        body = await request.json()
        errors = update_config(Path(_config_path), body)
        if errors:
            return {"success": False, "errors": errors}
        if _reload_event is not None:

            async def _deferred_reload():
                await asyncio.sleep(0.5)
                _reload_event.set()

            asyncio.create_task(_deferred_reload())
        return {"success": True, "errors": []}

else:  # pragma: no cover
    # Provide a no-op router when FastAPI is not installed.
    control_router = None  # type: ignore[assignment]
    health_router = None  # type: ignore[assignment]
