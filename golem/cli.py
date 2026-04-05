#!/usr/bin/env python3
"""CLI entry point for Golem — run, poll, daemon, status, dashboard."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import time
import urllib.error
import urllib.request

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .core.config import (
    DATA_DIR,
    GOLEM_HOME,
    DaemonConfig,
    DashboardConfig,
    load_config,
)
from .core.defaults import _now_iso
from .core.daemon_utils import (
    daemonize,
    read_pid,
    remove_pid,
    setup_daemon_tee,
    update_latest_symlink,
    write_pid,
)
from .utils import format_duration
from .core.stream_printer import StreamPrinter as _StreamPrinter
from .core.control_api import wire_control_api
from .core.triggers import FASTAPI_AVAILABLE
from .startup import validate_dependencies
from .orchestrator import (
    TaskOrchestrator,
    TaskSession,
    TaskSessionState,
    load_sessions,
    save_sessions,
)
from .profile import build_profile
from .repo_registry import RepoRegistry
from .log_context import setup_logging

if TYPE_CHECKING:
    from collections.abc import Callable

    from .core.config import Config, GolemFlowConfig
    from .event_tracker import TaskEventTracker
    from .flow import GolemFlow
    from .profile import GolemProfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
setup_logging()

logger = logging.getLogger("golem.cli")

DEFAULT_DAEMON_LOG_DIR = DATA_DIR / "logs"
DEFAULT_PID_FILE = DATA_DIR / "daemon.pid"
DEFAULT_DASHBOARD_PID_FILE = DATA_DIR / "dashboard.pid"

# Plugin detection — path to Claude Code's installed-plugins registry
_PLUGINS_FILE = Path.home() / ".claude" / "plugins" / "installed_plugins.json"

# Recommended plugins for Golem development workflows
_RECOMMENDED_PLUGINS: list[tuple[str, str, str]] = [
    ("superpowers", "planning, TDD, debugging workflows", "RECOMMENDED"),
    ("code-review", "PR review automation", "RECOMMENDED"),
    ("code-simplifier", "code clarity and reuse", "OPTIONAL"),
]


def _get_profile(config: Config) -> GolemProfile:
    """Build a GolemProfile from config.  Raises on failure."""
    tc = config.get_flow_config("golem")
    name = tc.profile if tc else "redmine"
    return build_profile(name, config)


def _save_cli_session(session: TaskSession) -> None:
    """Merge a CLI-created session into the on-disk sessions file."""
    sessions = load_sessions()
    sessions[session.parent_issue_id] = session
    save_sessions(sessions)


_SEP = "·" * 40


def _print_cli_summary(session: TaskSession) -> None:
    """Print final execution summary to stdout for CLI users."""
    sep = _SEP
    print(f"\n  {sep} result {sep}\n")
    print(f"  State: {session.state.value}")
    print(f"  Cost: ${session.total_cost_usd:.2f}")
    print(f"  Duration: {format_duration(session.duration_seconds)}")
    print(f"  Milestones: {session.milestone_count}")
    tools_str = ", ".join(session.tools_called) or "none"
    mcp_str = ", ".join(session.mcp_tools_called) or "none"
    print(f"  Tools: {tools_str}")
    print(f"  MCP Tools: {mcp_str}")
    if session.validation_verdict:
        print(
            f"  Verdict: {session.validation_verdict} "
            f"(confidence: {session.validation_confidence:.0%})"
        )
        print(f"  Summary: {session.validation_summary}")
        if session.validation_concerns:
            print("  Concerns:")
            for c in session.validation_concerns:
                print(f"    - {c}")
    if session.commit_sha:
        print(f"  Commit: {session.commit_sha}")
    if session.execution_mode == "subagent" and session.supervisor_phase:
        print(f"  Mode: subagent ({session.supervisor_phase})")
    if session.errors:
        print(f"  Errors: {len(session.errors)}")
        for err in session.errors:
            print(f"    - {err}")


def _print_run_header(
    parent_id: int,
    subject: str,
    profile: GolemProfile,
    tc: GolemFlowConfig | None,
    cwd_override: str,
    daemon_cfg: DaemonConfig | None = None,
) -> None:
    """Print task info banner at the start of a CLI run."""
    if daemon_cfg is None:
        daemon_cfg = DaemonConfig()
    print(f"\n{'='*60}")
    print(f"  GOLEM: #{parent_id}")
    print(f"{'='*60}")

    children = profile.task_source.get_child_tasks(parent_id)
    if children:
        print(f"  Found {len(children)} child issue(s):")
        for ch in children:
            status = ch.get("status", {}).get("name", "?")
            print(f"    #{ch['id']}: [{status}] {ch.get('subject', '?')}")
    else:
        print("  No child issues — agent will handle task holistically")
    print()

    mcp_servers = profile.tool_provider.servers_for_subject(subject)
    model = tc.task_model if tc else "sonnet"
    budget = tc.budget_per_task_usd if tc else daemon_cfg.fallback_budget_usd
    timeout = (
        tc.task_timeout_seconds if tc else daemon_cfg.fallback_task_timeout_seconds
    )

    print(f"  Profile: {profile.name}")
    print(f"  Model: {model}")
    print(f"  MCP servers: {mcp_servers}")
    print(f"  Budget: {'unlimited' if not budget else f'${budget}'}")
    print(f"  Timeout: {timeout}s")
    print(f"  CWD: {cwd_override or (tc.default_work_dir if tc else '') or '(auto)'}")


def _make_event_handler(
    tracker: TaskEventTracker,
    printer: _StreamPrinter,
    session: TaskSession | None = None,
    start_time: float | None = None,
) -> Callable[[dict[str, Any]], None]:
    """Build a stream-event handler that drives live session updates.

    Returns a callable ``handler(event)`` that:

    1. Forwards every raw event to *printer* for console output.
    2. Feeds the event to *tracker* for milestone extraction.
    3. On each milestone, copies tracker state into *session* (if given)
       and persists via ``_save_cli_session``.
    """

    def handler(event):
        printer.handle(event)
        milestone = tracker.handle_event(event)
        if milestone is None or session is None:
            return
        st = tracker.state
        session.milestone_count = st.milestone_count
        session.tools_called = list(st.tools_called)
        session.mcp_tools_called = list(st.mcp_tools_called)
        session.last_activity = st.last_activity
        session.event_log = [
            {
                "kind": m.kind,
                "tool_name": m.tool_name,
                "summary": m.summary,
                "timestamp": m.timestamp,
                "is_error": m.is_error,
            }
            for m in st.event_log
        ]
        if start_time:
            session.duration_seconds = time.time() - start_time
        try:
            _save_cli_session(session)
        except Exception:  # pylint: disable=broad-except
            logger.debug("Failed to save CLI session", exc_info=True)

    return handler


def run_issue(  # pylint: disable=too-many-arguments,too-many-locals
    parent_id: int,
    config: Config,
    dry: bool = False,
    subject_override: str = "",
    profile_override: GolemProfile | None = None,
    cwd_override: str = "",
    mcp_override: bool | None = None,
) -> bool:
    """Run a task through the orchestrator pipeline."""
    profile = profile_override or _get_profile(config)

    # Apply CLI --mcp / --no-mcp override
    if mcp_override is not None:
        if mcp_override:
            from .backends.mcp_tools import KeywordToolProvider

            profile.tool_provider = KeywordToolProvider()
        else:
            from .backends.local import NullToolProvider

            profile.tool_provider = NullToolProvider()
    tc = config.get_flow_config("golem")
    daemon_cfg = config.daemon
    subject = subject_override or profile.task_source.get_task_subject(parent_id)

    _print_run_header(parent_id, subject, profile, tc, cwd_override, daemon_cfg)
    budget = tc.budget_per_task_usd if tc else daemon_cfg.fallback_budget_usd

    if dry:
        print("\n  [DRY RUN] — would execute. Remove --dry to run.")
        return True

    # Always use worktrees for CLI runs to prevent file loss
    if tc:
        tc.use_worktrees = True

    # Create session
    now = _now_iso()
    session = TaskSession(
        parent_issue_id=parent_id,
        parent_subject=subject,
        state=TaskSessionState.DETECTED,
        created_at=now,
        updated_at=now,
        grace_deadline=now,  # no grace period for CLI
        budget_usd=budget,
        execution_mode="prompt" if profile.name == "prompt" else "",
    )
    _save_cli_session(session)

    # Build streaming callbacks for console output
    printer = _StreamPrinter(sys.stderr)

    def on_event(event):
        printer.handle(event)

    def on_progress(sess, milestone):
        kind_tag = "ERROR" if milestone.is_error else milestone.kind.upper()
        print(
            f"  [MILESTONE:{kind_tag}] {milestone.summary}",
            file=sys.stderr,
        )
        try:
            _save_cli_session(sess)
        except Exception:  # pylint: disable=broad-except
            logger.debug("Failed to save CLI session", exc_info=True)

    print(f"\n  {_SEP} agent output {_SEP}\n")
    profile.notifier.notify_started(parent_id, subject)

    orchestrator = TaskOrchestrator(
        session=session,
        config=config,
        task_config=tc,
        on_progress=on_progress,
        save_callback=lambda: _save_cli_session(session),
        profile=profile,
        event_callback=on_event,
        work_dir_override=cwd_override or None,
    )

    asyncio.run(orchestrator.run_once())

    ok = session.state == TaskSessionState.COMPLETED
    _print_cli_summary(session)
    return ok


def poll_for_agent_issues(config: Config) -> list[dict[str, Any]]:
    """Scan configured projects for issues tagged with the detection tag."""
    tc = config.get_flow_config("golem")
    projects = tc.projects if tc else []
    tag = tc.detection_tag if tc else "[AGENT]"
    profile = _get_profile(config)

    print(f"\n{'='*60}")
    print(f"  Polling {len(projects)} project(s) for '{tag}' issues")
    print(f"  Profile: {profile.name}")
    print(f"  Projects: {', '.join(projects)}")
    print(f"{'='*60}\n")

    issues = profile.task_source.poll_tasks(projects, detection_tag=tag)
    if not issues:
        print("  No [AGENT] issues found.")
        return []

    print(f"  Found {len(issues)} [AGENT] issue(s):\n")
    for issue in issues:
        children = profile.task_source.get_child_tasks(issue["id"])
        child_info = f"{len(children)} subtasks" if children else "no subtasks"
        print(f"    #{issue['id']}: {issue.get('subject', '?')} ({child_info})")
    print()
    return issues


def print_results(results: list[tuple[int, bool]]) -> None:
    """Print a summary table of task execution outcomes."""
    if not results:
        return
    print(f"\n{'='*60}")
    print("  RESULTS")
    print(f"{'='*60}")
    for issue_id, ok in results:
        print(f"    #{issue_id}: {'OK' if ok else 'FAIL'}")
    print()


# ---------------------------------------------------------------------------
# Daemon infrastructure
# ---------------------------------------------------------------------------


async def _handle_reload(
    reload_event: asyncio.Event,
    *,
    flow: Any = None,
    self_update_manager: Any = None,
    drain_timeout: int = 300,
) -> None:
    """Wait for reload_event, drain tasks, apply pending update, os.execv.

    The self_update_manager (if set) is called after drain to run
    ``git merge --ff-only`` or ``git reset --hard`` before restarting.
    This ensures the new code is on disk when os.execv re-imports modules.
    """
    await reload_event.wait()
    reload_event.clear()

    logger.info("Reload requested — draining active tasks (timeout=%ds)", drain_timeout)

    if flow:
        flow.stop_tick_loop()

        # Wait for active (non-terminal) sessions to finish.
        from .orchestrator import TaskSessionState

        _terminal = {
            TaskSessionState.COMPLETED,
            TaskSessionState.FAILED,
            TaskSessionState.HUMAN_REVIEW,
        }
        deadline = asyncio.get_running_loop().time() + drain_timeout
        while any(s.state not in _terminal for s in flow._sessions.values()):
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                active = sum(
                    1 for s in flow._sessions.values() if s.state not in _terminal
                )
                logger.warning(
                    "Drain timeout reached with %d active sessions — proceeding",
                    active,
                )
                break
            await asyncio.sleep(min(1.0, remaining))

    # Apply pending git update (if any) AFTER drain, BEFORE os.execv.
    if self_update_manager:
        await self_update_manager.apply_update()

    logger.info("Restarting daemon via os.execv")
    # PID file is kept — cmd_daemon recognises its own PID after execv.
    # Use --foreground so the re-exec'd process doesn't fork again.
    argv = list(sys.argv)
    if "--foreground" not in argv:
        argv.append("--foreground")
    try:
        os.execv(sys.executable, [sys.executable] + argv)
    except OSError:
        logger.exception("os.execv failed — resuming with current code")
        if flow:
            flow.start_tick_loop()


def _manage_golem_tick(
    config: Config,
    tasks: list[asyncio.Task[Any]],
    reload_event: asyncio.Event | None = None,
) -> GolemFlow | None:
    """Start the golem tick loop via GolemFlow."""
    from .flow import GolemFlow

    tc = config.get_flow_config("golem")
    if not tc or not tc.enabled:
        return None
    flow = GolemFlow(config, reload_event=reload_event)
    if hasattr(flow, "start_tick_loop"):
        tasks.append(flow.start_tick_loop())
        print("Golem tick loop started")
    return flow


async def _start_dashboard_server(
    port: int,
    config_snapshot: dict | None = None,
    live_state_file: "Path | None" = None,
    merge_queue: "Any | None" = None,
    heartbeat: "Any | None" = None,
) -> tuple[asyncio.Task, Any]:
    import socket

    import uvicorn
    from fastapi import FastAPI

    from .core.control_api import control_router, health_router
    from .core.dashboard import mount_dashboard

    app = FastAPI(title="Golem Dashboard")
    mount_dashboard(
        app,
        _config_snapshot=config_snapshot,
        live_state_file=live_state_file,
        merge_queue=merge_queue,
        heartbeat=heartbeat,
    )
    if control_router is not None:
        app.include_router(control_router)
    if health_router is not None:
        app.include_router(health_router)
    uvi_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(uvi_config)

    async def _serve_gracefully() -> None:
        try:
            await server.serve()
        except asyncio.CancelledError:
            server.should_exit = True

    task = asyncio.create_task(_serve_gracefully())
    hostname = socket.getfqdn()
    print(f"Dashboard at http://{hostname}:{port}/dashboard")
    return task, server


async def run_daemon(args, config) -> int:
    """Start the golem daemon with tick loop and dashboard."""
    setup_logging(json_mode=config.golem.json_logging)
    tasks: list[asyncio.Task] = []
    shutdown_event = asyncio.Event()
    reload_event = asyncio.Event()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)
    loop.add_signal_handler(signal.SIGHUP, reload_event.set)

    # Enable LiveState persistence so the dashboard sees live activity.
    from .core.live_state import LiveState

    live = LiveState.get()
    live.enable_persistence(DATA_DIR / "live_state.json")

    flow = _manage_golem_tick(config, tasks, reload_event=reload_event)
    if flow is None:
        print("Golem is not enabled in config", file=sys.stderr)
        return 1

    self_update = flow._self_update if flow else None
    config_path = str(getattr(args, "config", "config.yaml"))

    wire_control_api(
        golem_flow=flow,
        api_key=config.dashboard.api_key,
        config_path=config_path,
        reload_event=reload_event,
        self_update_manager=self_update,
    )

    reload_task = asyncio.create_task(
        _handle_reload(
            reload_event,
            flow=flow,
            self_update_manager=self_update,
            drain_timeout=config.daemon.drain_timeout_seconds,
        )
    )

    # Start dashboard
    from .core.dashboard import config_to_snapshot

    snap = config_to_snapshot(config)
    port = getattr(args, "port", None) or config.dashboard.port
    dash_task, dash_server = await _start_dashboard_server(
        port,
        config_snapshot=snap,
        merge_queue=flow._merge_queue if flow else None,
        heartbeat=flow._heartbeat if flow else None,
    )
    tasks.append(dash_task)

    print("Agent daemon running. Press Ctrl+C to stop.")

    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down agent daemon...")
        # Signal dashboard to stop accepting new requests before cancelling.
        from .core import dashboard as _dash_mod

        _dash_mod._shutting_down = True
        dash_server.should_exit = True
        reload_task.cancel()
        if flow is not None:
            await flow.graceful_stop()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Agent daemon stopped")

    return 0


# ---------------------------------------------------------------------------
# CLI subcommand handlers
# ---------------------------------------------------------------------------


def cmd_run(args) -> int:
    """Handler for the 'run' subcommand — execute a single task."""
    config = load_config(getattr(args, "config", None))

    prompt_text = getattr(args, "prompt", "")
    file_path = getattr(args, "file", "")

    if prompt_text:
        return _cmd_run_prompt(args, config, prompt_text)

    if file_path:
        return _cmd_run_file(args, config, file_path)

    if args.parent_id is None:
        print("Error: provide a task ID, --prompt, or --file", file=sys.stderr)
        return 1

    ok = run_issue(
        args.parent_id,
        config,
        dry=args.dry,
        subject_override=getattr(args, "subject", ""),
        cwd_override=getattr(args, "cwd", ""),
        mcp_override=getattr(args, "mcp", None),
    )
    if args.dry:
        return 0
    return 0 if ok else 1


def _cmd_run_prompt(args: argparse.Namespace, config: Config, prompt_text: str) -> int:
    """Submit a prompt to the daemon for background execution."""
    port = config.dashboard.port if config.dashboard else DashboardConfig.port
    daemon_cfg = config.daemon
    _ensure_daemon(args, config, port, daemon_cfg=daemon_cfg)

    subject = getattr(args, "subject", "") or ""
    work_dir = getattr(args, "cwd", "") or os.getcwd()
    result = _submit_to_daemon(
        prompt=prompt_text,
        subject=subject,
        port=port,
        work_dir=work_dir,
        timeout=daemon_cfg.http_submit_timeout,
        api_key=config.dashboard.api_key,
    )

    if not result:
        return 1

    task_id = result.get("task_id", "?")
    print(f"\n  Submitted task #{task_id}")
    print("  Track with: golem status")
    return 0


def _cmd_run_file(args: argparse.Namespace, config: Config, file_path: str) -> int:
    """Read a prompt from *file_path* and submit it to the daemon."""
    p = Path(file_path)
    if not p.is_file():
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        return 1
    prompt_text = p.read_text(encoding="utf-8").strip()
    if not prompt_text:
        print(f"Error: file is empty: {file_path}", file=sys.stderr)
        return 1
    return _cmd_run_prompt(args, config, prompt_text)


def _daemon_health(port: int, timeout: int = 3) -> bool:
    """Probe the daemon health endpoint.  Returns True if healthy."""
    try:
        url = f"http://127.0.0.1:{port}/api/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _pid_from_health(port: int, timeout: int = 3) -> int | None:
    """Query /api/health for the daemon PID.  Returns None on failure."""
    try:
        url = f"http://127.0.0.1:{port}/api/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return int(data["pid"])
    except (urllib.error.URLError, OSError, KeyError, ValueError, TypeError):
        return None


def _ensure_daemon(args, _config, port: int, daemon_cfg=None) -> None:
    """Make sure the daemon is running; start it in background if not."""
    if daemon_cfg is None:
        daemon_cfg = DaemonConfig()
    hc_timeout = daemon_cfg.health_check_timeout
    if _daemon_health(port, timeout=hc_timeout):
        return
    pid = read_pid(DEFAULT_PID_FILE)
    if pid is not None:
        try:
            os.kill(pid, 0)
        except OSError:
            remove_pid(DEFAULT_PID_FILE)
    if not _daemon_health(port, timeout=hc_timeout):
        print("  Starting daemon in background...")
        import subprocess as _sp

        cmd = [sys.executable, "-m", "golem"]
        cfg_path = getattr(args, "config", None)
        if cfg_path:
            cmd += ["-c", str(cfg_path)]
        cmd += ["daemon"]
        log_dir = DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        bg_log = log_dir / f"agent_{stamp}.log"
        with open(bg_log, "w", encoding="utf-8") as lf:
            _sp.Popen(  # pylint: disable=consider-using-with
                cmd,
                stdout=lf,
                stderr=_sp.STDOUT,
                start_new_session=True,
            )
        for _ in range(daemon_cfg.startup_max_iterations):
            time.sleep(daemon_cfg.startup_poll_seconds)
            if _daemon_health(port, timeout=hc_timeout):
                print(f"  Daemon started (log: {bg_log})")
                return
        print(
            "  Warning: daemon may not be ready yet. "
            "Check logs or run 'golem daemon --foreground'.",
            file=sys.stderr,
        )


def _submit_to_daemon(
    prompt: str,
    port: int,
    subject: str = "",
    file_path: str = "",
    work_dir: str = "",
    timeout: int = 10,
    api_key: str = "",
) -> dict | None:
    """POST a task to the daemon's /api/submit endpoint."""
    payload: dict[str, Any] = {}
    if file_path:
        payload["file"] = file_path
    else:
        payload["prompt"] = prompt
    if subject:
        payload["subject"] = subject
    if work_dir:
        payload["work_dir"] = work_dir
    url = f"http://127.0.0.1:{port}/api/submit"
    data = json.dumps(payload).encode("utf-8")
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"  Submit failed ({exc.code}): {body}", file=sys.stderr)
        return None
    except (urllib.error.URLError, OSError) as exc:
        print(f"  Submit failed: {exc}", file=sys.stderr)
        return None


def cmd_poll(args) -> int:
    """Handler for the 'poll' subcommand — scan for agent issues."""
    config = load_config(getattr(args, "config", None))
    issues = poll_for_agent_issues(config)
    if not issues:
        return 0
    if args.dry:
        for issue in issues:
            run_issue(issue["id"], config, dry=True)
        return 0
    if not args.run:
        return 0
    all_results = []
    for issue in issues:
        ok = run_issue(issue["id"], config)
        all_results.append((issue["id"], ok))
    print_results(all_results)
    return 0 if all(ok for _, ok in all_results) else 1


def cmd_daemon(args) -> int:
    """Handler for the 'daemon' subcommand — start tick loop and dashboard."""
    try:
        validate_dependencies()
    except RuntimeError as exc:
        print("Startup error: %s" % exc, file=sys.stderr)
        return 1
    config = load_config(getattr(args, "config", None))

    if config.golem.otel_enabled:
        from golem.tracing import init_tracing

        init_tracing(
            otlp_endpoint=config.golem.otel_endpoint,
            console_export=config.golem.otel_console_export,
        )

    log_dir = Path(getattr(args, "log_dir", None) or DEFAULT_DAEMON_LOG_DIR)
    pid_file = Path(getattr(args, "pid_file", None) or DEFAULT_PID_FILE)

    existing_pid = read_pid(pid_file)
    if existing_pid is not None and existing_pid != os.getpid():
        try:
            os.kill(existing_pid, 0)
            print(
                f"Agent daemon already running (PID {existing_pid}). "
                f"Use 'stop' to terminate it first.",
                file=sys.stderr,
            )
            return 1
        except OSError:
            remove_pid(pid_file)

    if getattr(args, "foreground", False):
        log_path, cleanup_tee = setup_daemon_tee(log_dir)
        write_pid(pid_file)
        logger.info("Agent daemon log: %s", log_path)
        try:
            return asyncio.run(run_daemon(args, config))
        finally:
            remove_pid(pid_file)
            cleanup_tee()
    else:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        bg_log = log_dir / f"agent_{stamp}.log"
        print(f"Starting agent daemon in background (log: {bg_log})")
        update_latest_symlink(log_dir, bg_log)
        daemonize(bg_log)
        write_pid(pid_file)
        logger.info("Agent daemon started in background, PID %d", os.getpid())
        try:
            return asyncio.run(run_daemon(args, config))
        finally:
            remove_pid(pid_file)


def _wait_for_exit(pid: int, seconds: int) -> bool:
    for _ in range(seconds):
        time.sleep(1)
        try:
            os.kill(pid, 0)
        except OSError:
            return True
    return False


def cmd_stop(args) -> int:
    """Handler for the 'stop' subcommand — stop a running daemon or dashboard."""
    is_dashboard = getattr(args, "dashboard", False)
    default_pid = DEFAULT_DASHBOARD_PID_FILE if is_dashboard else DEFAULT_PID_FILE
    target = "Dashboard" if is_dashboard else "Agent daemon"

    pid_file = Path(getattr(args, "pid_file", None) or default_pid)
    force = getattr(args, "force", False)

    pid = read_pid(pid_file)
    if pid is None and not is_dashboard:
        config = load_config(getattr(args, "config", None))
        port = getattr(args, "port", None) or config.dashboard.port
        pid = _pid_from_health(port)
        if pid is not None:
            print(f"PID file missing — recovered PID {pid} from health endpoint.")
    if pid is None:
        print(f"No {target.lower()} PID file found. Is it running?", file=sys.stderr)
        return 1

    try:
        os.kill(pid, 0)
    except OSError:
        print(f"{target} (PID {pid}) is not running. Cleaning up.")
        remove_pid(pid_file)
        return 0

    sig = signal.SIGKILL if force else signal.SIGTERM
    print(
        f"Sending {'SIGKILL' if force else 'SIGTERM'} to {target.lower()} (PID {pid})..."
    )
    try:
        os.kill(pid, sig)
    except OSError as exc:
        print(f"Failed to send signal: {exc}", file=sys.stderr)
        return 1

    grace = 5 if force else 15
    if _wait_for_exit(pid, grace):
        remove_pid(pid_file)
        print(f"{target} stopped.")
        return 0

    if not force:
        print(f"{target} did not exit within {grace}s, sending SIGKILL...")
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError as exc:
            logger.debug("SIGKILL failed for PID %d: %s", pid, exc)
        if _wait_for_exit(pid, 3):
            remove_pid(pid_file)
            print(f"{target} killed.")
            return 0

    remove_pid(pid_file)
    print(f"{target} (PID {pid}) did not exit.", file=sys.stderr)
    return 1


def cmd_cancel(args) -> int:
    """Handler for the 'cancel' subcommand — cancel a running task."""
    config = load_config(getattr(args, "config", None))
    port = config.dashboard.port if config.dashboard else DashboardConfig.port
    task_id = args.task_id

    url = f"http://127.0.0.1:{port}/api/cancel/{task_id}"
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            json.loads(resp.read().decode("utf-8"))
            print(f"Task #{task_id} cancelled.")
            return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Cancel failed ({exc.code}): {body}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"Cannot reach daemon: {exc}", file=sys.stderr)
        return 1


def cmd_clear_failed(args) -> int:
    """Handler for the 'clear-failed' subcommand — remove all failed tasks."""
    config = load_config(getattr(args, "config", None))
    port = config.dashboard.port if config.dashboard else DashboardConfig.port

    url = f"http://127.0.0.1:{port}/api/sessions/clear-failed"
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            cleared = data.get("cleared", [])
            if cleared:
                print(f"Cleared {len(cleared)} failed tasks: {cleared}")
            else:
                print("No failed tasks to clear.")
            return 0
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Clear failed ({exc.code}): {body}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError) as exc:
        print(f"Cannot reach daemon: {exc}", file=sys.stderr)
        return 1


def cmd_status(args) -> int:
    """Handler for the 'status' subcommand — show recent run history."""
    from .core.dashboard import format_status_text, format_task_detail_text

    task_id = getattr(args, "task", None)
    if task_id is not None:
        print(format_task_detail_text(task_id))
        return 0

    since = getattr(args, "hours", 24)
    watch = getattr(args, "watch", None)

    if watch is None:
        print(format_status_text(since_hours=since, flow="golem"))
        return 0

    # Watch mode: clear screen and re-render in a loop
    interval = max(0.5, watch)
    try:
        while True:
            output = format_status_text(since_hours=since, flow="golem")
            print("\033[2J\033[H" + output, flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def cmd_dashboard(args) -> int:
    """Handler for the 'dashboard' subcommand — launch standalone web UI."""
    if not FASTAPI_AVAILABLE:
        print("Error: FastAPI not installed.", file=sys.stderr)
        return 1

    import socket

    import uvicorn
    from fastapi import FastAPI

    from .core.control_api import control_router, health_router, wire_control_api
    from .core.dashboard import mount_dashboard
    from .core.live_state import DEFAULT_LIVE_STATE_FILE

    config_path = getattr(args, "config", None)
    try:
        cfg = load_config(config_path)
        default_port = cfg.dashboard.port
        admin_token = cfg.dashboard.admin_token
    except Exception:  # pylint: disable=broad-except
        default_port = DashboardConfig.port
        admin_token = ""

    port = getattr(args, "port", None) or default_port
    app = FastAPI(title="Golem Dashboard")
    mount_dashboard(app, live_state_file=DEFAULT_LIVE_STATE_FILE)
    if control_router is not None:
        app.include_router(control_router)
    if health_router is not None:
        app.include_router(health_router)
    wire_control_api(
        admin_token=admin_token,
        config_path=config_path or "config.yaml",
    )
    hostname = socket.getfqdn()
    print(f"Dashboard running at http://{hostname}:{port}/dashboard")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    """Dispatch to batch subcommands (submit, status, list)."""
    from .batch_cli import (
        cmd_batch as _cmd_batch,
    )  # pylint: disable=import-outside-toplevel

    return _cmd_batch(args)


def cmd_config(args) -> int:
    """Handler for the 'config' subcommand."""
    from .config_editor import (  # pylint: disable=import-outside-toplevel
        get_config_by_category,
        signal_daemon_reload,
        update_config,
    )

    config_path = Path(args.config)
    action = getattr(args, "config_action", None)

    if action == "get":
        config = load_config(str(config_path))
        categories = get_config_by_category(config)
        for fields in categories.values():
            for fi in fields:
                if fi.key == args.field:
                    print(fi.value)
                    return 0
        print("Unknown field: %s" % args.field, file=sys.stderr)
        return 1

    if action == "set":
        errors = update_config(config_path, {args.field: args.value})
        if errors:
            for e in errors:
                print(e, file=sys.stderr)
            return 1
        pid_file = DATA_DIR / "daemon.pid"
        reloaded = signal_daemon_reload(pid_file)
        if reloaded:
            print("Config saved. Daemon reload triggered.")
        else:
            print("Config saved. No running daemon — changes apply on next start.")
        return 0

    if action == "list":
        config = load_config(str(config_path))
        categories = get_config_by_category(config)
        for _, fields in sorted(categories.items()):
            for fi in fields:
                display = "***" if fi.meta.sensitive else fi.value
                print("%s=%s" % (fi.key, display))
        return 0

    # Default: interactive mode
    return _config_interactive(config_path)


def _config_interactive(config_path: Path) -> int:
    """Launch the prompt_toolkit TUI config editor."""
    try:
        from .config_tui import (
            run_config_tui,
        )  # pylint: disable=import-outside-toplevel
    except ImportError:
        print(
            "Interactive config requires prompt_toolkit. "
            "Install with: pip install prompt_toolkit",
            file=sys.stderr,
        )
        return 1
    return run_config_tui(config_path)


def cmd_init(args) -> int:
    """Handler for the 'init' subcommand — generate starter config."""
    from .init_wizard import run_wizard  # pylint: disable=import-outside-toplevel

    default_output = str(GOLEM_HOME / "config.yaml")
    output = Path(getattr(args, "output", default_output))
    defaults = getattr(args, "defaults", False)
    return run_wizard(output, use_defaults=defaults)


def _check_plugins() -> list[str]:
    """Read installed plugin names from _PLUGINS_FILE.

    Returns a list of plugin name strings.  Returns [] if the file is absent
    or cannot be parsed.
    """
    if not _PLUGINS_FILE.exists():
        return []
    try:
        raw = _PLUGINS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        plugins = data.get("plugins", [])
        return [p.get("name", "") for p in plugins if isinstance(p, dict)]
    except (OSError, json.JSONDecodeError, AttributeError):
        logger.warning("Could not parse plugins file %s", _PLUGINS_FILE)
        return []


def cmd_setup(args) -> int:  # pylint: disable=unused-argument
    """Handler for the 'setup' subcommand — validate environment and recommend plugins."""
    ok_mark = "[ok]"
    fail_mark = "[x]"
    not_installed = "[--]"

    print("\nGolem Environment Setup")
    print("=======================")
    print()

    # --- Environment checks ---
    issues: list[str] = []
    print("Environment:")

    # git
    if shutil.which("git"):
        print(f"  {ok_mark} git found")
    else:
        print(f"  {fail_mark} git not found — required; install git")
        issues.append("git not in PATH")

    # claude CLI
    if shutil.which("claude"):
        print(f"  {ok_mark} claude CLI found")
    else:
        print(
            f"  {fail_mark} claude CLI not found — install from https://claude.ai/cli"
        )
        issues.append("claude CLI not in PATH")

    # Python version
    vi = sys.version_info
    if (vi[0], vi[1]) >= (3, 11):
        print(f"  {ok_mark} Python 3.11+")
    else:
        print(f"  {fail_mark} Python {vi[0]}.{vi[1]} — upgrade to Python 3.11+")
        issues.append("Python < 3.11")

    # ~/.golem/config.yaml
    config_path = GOLEM_HOME / "config.yaml"
    if config_path.exists():
        print(f"  {ok_mark} ~/.golem/config.yaml exists")
    else:
        print(f"  {fail_mark} ~/.golem/config.yaml missing")
        issues.append("~/.golem/config.yaml missing — run 'golem init'")

    print()

    # --- Plugin checks ---
    print("Recommended Claude Code Plugins:")
    installed = _check_plugins()

    for plugin_name, description, tier in _RECOMMENDED_PLUGINS:
        tier_label = f" ({tier.lower()})" if tier == "OPTIONAL" else ""
        if plugin_name in installed:
            print(f"  {ok_mark} {plugin_name} — {description}{tier_label}")
        else:
            print(f"  {not_installed} {plugin_name} — {description}{tier_label}")
            print(f"       Install: claude plugins install {plugin_name}")

    print()

    # --- Summary ---
    if issues:
        print("Actions needed:")
        for issue in issues:
            print(f"  - {issue}")
    else:
        print("Environment ready")

    if not config_path.exists():
        print()
        print("Run 'golem init' to configure interactively.")

    return 0


def cmd_attach(args) -> int:
    """Handler for the 'attach' subcommand — register a repo."""
    path = getattr(args, "path", None) or os.getcwd()
    path = str(Path(path).resolve())
    if not Path(path).is_dir():
        print(f"Error: directory does not exist: {path}", file=sys.stderr)
        return 1

    heartbeat = not getattr(args, "no_heartbeat", False)
    skip_detection = getattr(args, "no_detect", False)
    reg = RepoRegistry()
    reg.attach(path, heartbeat=heartbeat, run_detection=not skip_detection)
    hb_label = "heartbeat on" if heartbeat else "heartbeat off"
    print(f"  Attached: {path} ({hb_label})")

    if not skip_detection:
        from .verify_config import (
            load_verify_config,
        )  # pylint: disable=import-outside-toplevel

        cfg = load_verify_config(path)
        if cfg and cfg.commands:
            print(f"  Detected stack: {', '.join(cfg.stack)}")
            for cmd in cfg.commands:
                print(f"    [{cmd.role}] {' '.join(cmd.cmd)}")
        else:
            print(
                "  No verification commands detected"
                " (add .golem/verify.yaml manually)"
            )
    return 0


def cmd_detach(args) -> int:
    """Handler for the 'detach' subcommand — unregister a repo."""
    path = getattr(args, "path", None) or os.getcwd()
    path = str(Path(path).resolve())

    reg = RepoRegistry()
    if not reg.detach(path):
        print(f"Error: {path} is not attached", file=sys.stderr)
        return 1
    print(f"  Detached: {path}")
    return 0


def cmd_logs(args) -> int:
    """Handler for the 'logs' subcommand — view daemon log output."""
    log_dir = args.log_dir or DEFAULT_DAEMON_LOG_DIR

    # Prefer the daemon_latest.log symlink — it always points to the active log
    latest_link = log_dir / "daemon_latest.log"
    if latest_link.exists():
        log_file = latest_link.resolve()
    else:
        log_files = sorted(
            log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True
        )
        if not log_files:
            print("No log files found in %s" % log_dir)
            return 1
        log_file = log_files[0]
    lines = args.lines or 50

    if args.follow:
        try:
            with open(log_file, "r", encoding="utf-8") as f:
                f.seek(0, 2)
                file_size = f.tell()
                if file_size > 0:
                    read_size = min(file_size, lines * 200)
                    f.seek(max(0, file_size - read_size))
                    tail = f.read()
                    tail_lines = tail.splitlines()[-lines:]
                    for line in tail_lines:
                        print(line)

                print("--- following %s (Ctrl+C to stop) ---" % log_file.name)
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n--- stopped following ---")
            return 0
    else:
        try:
            content = log_file.read_text(encoding="utf-8")
            lines_list = content.splitlines()
            for line in lines_list[-lines:]:
                print(line)
        except OSError as exc:
            print("Error reading log file: %s" % exc)
            return 1

    return 0


def _add_run_subparser(sub: argparse._SubParsersAction) -> None:
    run_p = sub.add_parser("run", help="Execute a task via single agent")
    run_p.add_argument(
        "parent_id", nargs="?", type=int, default=None, help="Task/issue ID"
    )
    prompt_grp = run_p.add_mutually_exclusive_group()
    prompt_grp.add_argument(
        "--prompt",
        "-p",
        default="",
        help="Submit inline prompt to daemon for execution",
    )
    prompt_grp.add_argument(
        "--file",
        "-f",
        default="",
        help="Submit prompt from file to daemon for execution",
    )
    run_p.add_argument("--dry", action="store_true", help="Preview without executing")
    run_p.add_argument("--subject", default="", help="Override issue subject")
    run_p.add_argument(
        "--cwd",
        "-C",
        default="",
        help="Override working directory for this invocation",
    )
    mcp_grp = run_p.add_mutually_exclusive_group()
    mcp_grp.add_argument(
        "--mcp",
        dest="mcp",
        action="store_const",
        const=True,
        default=None,
        help="Enable MCP servers (keyword-scoped from task subject)",
    )
    mcp_grp.add_argument(
        "--no-mcp",
        dest="mcp",
        action="store_const",
        const=False,
        help="Disable all MCP servers",
    )
    run_p.set_defaults(func=cmd_run)


def _add_batch_subparser(sub: argparse._SubParsersAction) -> None:
    batch_p = sub.add_parser("batch", help="Submit and query batches")
    bsub = batch_p.add_subparsers(dest="batch_command")
    submit_p = bsub.add_parser("submit", help="Submit a batch from a JSON/YAML file")
    submit_p.add_argument(
        "file", help="Path to a JSON or YAML file describing the batch"
    )
    status_p = bsub.add_parser("status", help="Show batch status")
    status_p.add_argument("group_id", help="Batch group ID")
    bsub.add_parser("list", help="List all batches")
    batch_p.set_defaults(func=cmd_batch)


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Golem", prog="golem")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default="config.yaml",
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose logging"
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    _add_run_subparser(sub)

    # poll
    poll_p = sub.add_parser("poll", help="Scan for [AGENT] issues")
    poll_p.add_argument("--run", action="store_true", help="Also execute found tasks")
    poll_p.add_argument(
        "--dry",
        action="store_true",
        help="Show detected issues and config without executing",
    )
    poll_p.set_defaults(func=cmd_poll)

    # daemon
    daemon_p = sub.add_parser("daemon", help="Run golem daemon with tick loop")
    daemon_p.add_argument(
        "--foreground", action="store_true", help="Stay attached to terminal"
    )
    daemon_p.add_argument("--log-dir", type=Path, help="Directory for logs")
    daemon_p.add_argument("--pid-file", type=Path, help="PID file path")
    daemon_p.add_argument("--port", type=int, help="Dashboard port")
    daemon_p.set_defaults(func=cmd_daemon)

    # cancel
    cancel_p = sub.add_parser("cancel", help="Cancel a running task")
    cancel_p.add_argument("task_id", type=int, help="Task ID to cancel")
    cancel_p.set_defaults(func=cmd_cancel)

    # clear-failed
    cf_p = sub.add_parser("clear-failed", help="Remove all failed tasks")
    cf_p.set_defaults(func=cmd_clear_failed)

    # stop
    stop_p = sub.add_parser("stop", help="Stop agent daemon")
    stop_p.add_argument(
        "--dashboard", action="store_true", help="Stop dashboard instead"
    )
    stop_p.add_argument("--pid-file", type=Path)
    stop_p.add_argument("--force", action="store_true")
    stop_p.set_defaults(func=cmd_stop)

    # status
    status_p = sub.add_parser("status", help="Show run stats")
    status_p.add_argument("--hours", type=int, default=24)
    status_p.add_argument(
        "--watch",
        type=float,
        nargs="?",
        const=2.0,
        default=None,
        metavar="SECS",
        help="Auto-refresh every SECS seconds (default: 2)",
    )
    status_p.add_argument(
        "--task",
        type=int,
        default=None,
        metavar="ID",
        help="Show detail for a specific task ID",
    )
    status_p.set_defaults(func=cmd_status)

    # dashboard
    dash_p = sub.add_parser("dashboard", help="Launch standalone dashboard")
    dash_p.add_argument("--port", type=int)
    dash_p.set_defaults(func=cmd_dashboard)

    # logs
    logs_p = sub.add_parser("logs", help="View daemon log output")
    logs_p.add_argument(
        "-n",
        "--lines",
        type=int,
        default=50,
        help="Number of lines to show (default: 50)",
    )
    logs_p.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="Follow log output (like tail -f)",
    )
    logs_p.add_argument(
        "--log-dir",
        type=Path,
        help="Log directory (default: data/logs/)",
    )
    logs_p.set_defaults(func=cmd_logs)

    _add_batch_subparser(sub)

    # init
    init_p = sub.add_parser("init", help="Generate a starter config.yaml")
    init_p.add_argument(
        "-o",
        "--output",
        default=str(GOLEM_HOME / "config.yaml"),
        help="Output file path (default: ~/.golem/config.yaml)",
    )
    init_p.add_argument(
        "--defaults", action="store_true", help="Use defaults without prompting"
    )
    init_p.set_defaults(func=cmd_init)

    # setup
    setup_p = sub.add_parser("setup", help="Validate environment and recommend plugins")
    setup_p.set_defaults(func=cmd_setup)

    # attach
    attach_p = sub.add_parser("attach", help="Register a repo with the daemon")
    attach_p.add_argument(
        "path", nargs="?", default=None, help="Directory (default: cwd)"
    )
    attach_p.add_argument(
        "--no-heartbeat",
        action="store_true",
        help="Attach without heartbeat scanning",
    )
    attach_p.add_argument(
        "--no-detect",
        action="store_true",
        help="Skip automatic stack detection (do not write .golem/verify.yaml)",
    )
    attach_p.set_defaults(func=cmd_attach)

    # detach
    detach_p = sub.add_parser("detach", help="Unregister a repo from the daemon")
    detach_p.add_argument(
        "path", nargs="?", default=None, help="Directory (default: cwd)"
    )
    detach_p.set_defaults(func=cmd_detach)

    # config
    config_p = sub.add_parser("config", help="View and edit configuration")
    config_sub = config_p.add_subparsers(dest="config_action")
    get_p = config_sub.add_parser("get", help="Get a config value")
    get_p.add_argument("field", help="Dotted field path (e.g. golem.task_model)")
    set_p = config_sub.add_parser("set", help="Set a config value")
    set_p.add_argument("field", help="Dotted field path")
    set_p.add_argument("value", help="New value")
    config_sub.add_parser("list", help="List all config values")
    config_p.set_defaults(func=cmd_config)

    return parser


_DEFAULT_CONFIG_YAML = """\
# Golem configuration — auto-generated with defaults.
# Run 'golem init' to customize interactively.
flows:
  golem:
    enabled: true
    profile: local
    projects: []
    task_model: claude-sonnet-4-20250514
    budget_per_task_usd: 1.0
    default_work_dir: ""
    heartbeat_enabled: false
dashboard:
  port: 8081
"""


def _ensure_golem_home() -> None:
    """Create ~/.golem/ and a default config if they don't exist."""
    GOLEM_HOME.mkdir(parents=True, exist_ok=True)
    config_path = GOLEM_HOME / "config.yaml"
    if not config_path.exists():
        config_path.write_text(_DEFAULT_CONFIG_YAML, encoding="utf-8")
        logger.info("Created default config at %s", config_path)


def main() -> int:
    """CLI entry point for Golem."""
    _ensure_golem_home()
    parser = _build_parser()
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
