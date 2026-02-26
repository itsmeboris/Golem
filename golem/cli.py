#!/usr/bin/env python3
"""CLI entry point for Golem.

Subcommands:
    golem run -p "fix the bug"          # run from inline prompt (simplest)
    golem run -p "refactor" --bg        # prompt in background
    golem run 4895049                   # execute a task by tracker ID
    golem run 4895049 --dry             # preview without executing
    golem poll                          # scan for [AGENT] issues
    golem poll --run                    # scan + execute all found
    golem daemon --foreground           # tick-loop daemon
    golem stop                          # stop daemon
    golem status                        # show run stats
    golem dashboard                     # standalone dashboard
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .core.config import DATA_DIR, load_config
from .core.daemon_utils import (
    daemonize,
    read_pid,
    remove_pid,
    setup_daemon_tee,
    update_latest_symlink,
    write_pid,
)
from .core.run_log import format_duration
from .core.stream_printer import StreamPrinter as _StreamPrinter
from .core.triggers import FASTAPI_AVAILABLE
from .orchestrator import (
    TaskOrchestrator,
    TaskSession,
    TaskSessionState,
    load_sessions,
    save_sessions,
)
from .profile import build_profile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("golem.cli")

DEFAULT_DAEMON_LOG_DIR = DATA_DIR / "logs"
DEFAULT_PID_FILE = DATA_DIR / "daemon.pid"
DEFAULT_DASHBOARD_PID_FILE = DATA_DIR / "dashboard.pid"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_profile(config):
    """Build a GolemProfile from config.  Raises on failure."""
    tc = config.get_flow_config("golem")
    name = tc.profile if tc else "redmine"
    return build_profile(name, config)


def _now_iso():
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _save_cli_session(session):
    """Merge a CLI-created session into the on-disk sessions file."""
    sessions = load_sessions()
    sessions[session.parent_issue_id] = session
    save_sessions(sessions)


_SEP = "·" * 40


def _print_cli_summary(session):
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
            for c in session.validation_concerns[:5]:
                print(f"    - {c}")
    if session.commit_sha:
        print(f"  Commit: {session.commit_sha}")
    if session.execution_mode == "supervisor" and session.subtask_results:
        print(f"  Subtasks: {len(session.subtask_results)}")
    if session.errors:
        print(f"  Errors: {len(session.errors)}")
        for err in session.errors[:3]:
            print(f"    - {err[:120]}")


def _print_run_header(parent_id, subject, profile, tc, cwd_override):
    """Print task info banner at the start of a CLI run."""
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
    budget = tc.budget_per_task_usd if tc else 10.0
    timeout = tc.task_timeout_seconds if tc else 1800

    print(f"  Profile: {profile.name}")
    print(f"  Model: {model}")
    print(f"  MCP servers: {mcp_servers}")
    print(f"  Budget: {'unlimited' if not budget else f'${budget}'}")
    print(f"  Timeout: {timeout}s")
    print(f"  CWD: {cwd_override or (tc.default_work_dir if tc else '') or '(auto)'}")


def _make_event_handler(tracker, printer, session=None, start_time=None):
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
            pass

    return handler


def run_issue(  # pylint: disable=too-many-arguments,too-many-locals
    parent_id,
    config,
    dry=False,
    subject_override="",
    profile_override=None,
    cwd_override="",
    mcp_override=None,
):
    """Run a task through the orchestrator pipeline.

    Both Redmine tasks (``run <issue_id>``) and prompt-mode tasks
    (``run -p "..."``) share the same execution path.  The *profile*
    determines how tasks are read, how state is updated, and which
    notifications are sent.

    Parameters
    ----------
    profile_override
        If given, use this profile instead of building one from config.
    cwd_override
        If given, override the working directory.
    mcp_override
        ``True`` enables keyword-scoped MCP; ``False`` disables all MCP;
        ``None`` (default) uses whatever the profile provides.
    """
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
    subject = subject_override or profile.task_source.get_task_subject(parent_id)

    _print_run_header(parent_id, subject, profile, tc, cwd_override)
    budget = tc.budget_per_task_usd if tc else 10.0

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
            pass

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


def poll_for_agent_issues(config):
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


def print_results(results):
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


def _manage_golem_tick(config, tasks):
    """Start the golem tick loop via GolemFlow."""
    from .flow import GolemFlow

    tc = config.get_flow_config("golem")
    if not tc or not tc.enabled:
        return None
    flow = GolemFlow(config)
    if hasattr(flow, "start_tick_loop"):
        tasks.append(flow.start_tick_loop())
        print("Golem tick loop started")
    return flow


async def _start_dashboard_server(
    port: int,
    config_snapshot: dict | None = None,
    live_state_file: "Path | None" = None,
) -> asyncio.Task:
    import socket

    import uvicorn
    from fastapi import FastAPI

    from .core.control_api import control_router
    from .core.dashboard import mount_dashboard

    app = FastAPI(title="Golem Dashboard")
    mount_dashboard(
        app, config_snapshot=config_snapshot, live_state_file=live_state_file
    )
    if control_router is not None:
        app.include_router(control_router)
    uvi_config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(uvi_config)
    task = asyncio.create_task(server.serve())
    hostname = socket.getfqdn()
    print(f"Dashboard at http://{hostname}:{port}/dashboard")
    return task


async def run_daemon(args, config) -> int:
    """Start the golem daemon with tick loop and dashboard."""
    tasks: list[asyncio.Task] = []
    shutdown_event = asyncio.Event()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_event.set)

    # Enable LiveState persistence so the dashboard sees live activity.
    from .core.live_state import LiveState

    live = LiveState.get()
    live.enable_persistence(DATA_DIR / "live_state.json")

    flow = _manage_golem_tick(config, tasks)
    if flow is None:
        print("Golem is not enabled in config", file=sys.stderr)
        return 1

    # Start dashboard
    from .core.dashboard import config_to_snapshot

    snap = config_to_snapshot(config)
    port = getattr(args, "port", None) or config.dashboard.port
    dash_task = await _start_dashboard_server(port, config_snapshot=snap)
    tasks.append(dash_task)

    print("Agent daemon running. Press Ctrl+C to stop.")

    try:
        await shutdown_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down agent daemon...")
        if flow and hasattr(flow, "stop_tick_loop"):
            flow.stop_tick_loop()
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
    if prompt_text:
        return _cmd_run_prompt(args, config, prompt_text)

    if args.parent_id is None:
        print("Error: provide a task ID or use --prompt", file=sys.stderr)
        return 1

    ok = run_issue(
        args.parent_id,
        config,
        dry=args.dry,
        subject_override=getattr(args, "subject", ""),
        mcp_override=getattr(args, "mcp", None),
    )
    if args.dry:
        return 0
    return 0 if ok else 1


def _cmd_run_prompt(args, config, prompt_text):
    """Run a task from an inline prompt through the orchestrator pipeline.

    Creates a synthetic local task and routes through ``run_issue()`` so the
    prompt run gets the same validation, retry, commit, session tracking, and
    dashboard visibility as a Redmine-sourced task.  The orchestrator handles
    worktree creation, commit, and merge-back automatically.
    """
    run_bg = getattr(args, "bg", False)
    task_id = int(time.time())

    # Background mode: fork to a log file and return immediately
    if run_bg:
        return _run_prompt_bg(args, prompt_text, task_id)

    # Build a synthetic local profile with the prompt as task description.
    # Default to no MCP in prompt mode — the user may not have MCP servers.
    # Use --mcp to explicitly enable.
    mcp_flag = getattr(args, "mcp", None)
    mcp_enabled = mcp_flag if mcp_flag is not None else False
    profile = _build_prompt_profile(task_id, prompt_text, mcp_enabled=mcp_enabled)
    subject = f"[AGENT] {prompt_text[:80]}"

    ok = run_issue(
        task_id,
        config,
        dry=getattr(args, "dry", False),
        subject_override=subject,
        profile_override=profile,
    )

    if getattr(args, "dry", False):
        return 0
    return 0 if ok else 1


def _build_prompt_profile(task_id, prompt_text, mcp_enabled=False):
    """Create a local profile with a synthetic task file for prompt mode."""
    from .backends.local import (
        LocalFileTaskSource,
        LogNotifier,
        NullStateBackend,
        NullToolProvider,
    )
    from .backends.mcp_tools import KeywordToolProvider
    from .profile import GolemProfile
    from .prompts import FilePromptProvider

    # Write a synthetic task file so the local task source can serve it
    tasks_dir = DATA_DIR / "prompt_tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    task_file = tasks_dir / f"{task_id}.json"
    task_file.write_text(
        json.dumps(
            {
                "id": str(task_id),
                "subject": f"[AGENT] {prompt_text[:80]}",
                "description": prompt_text,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    prompts_dir = Path(__file__).resolve().parent / "prompts"
    return GolemProfile(
        name="prompt",
        task_source=LocalFileTaskSource(tasks_dir),
        state_backend=NullStateBackend(),
        notifier=LogNotifier(),
        tool_provider=KeywordToolProvider() if mcp_enabled else NullToolProvider(),
        prompt_provider=FilePromptProvider(prompts_dir),
    )


def _run_prompt_bg(args, prompt_text, task_id):
    """Fork prompt execution to a background process with log file."""
    import subprocess as _sp

    log_dir = DATA_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"prompt_{task_id}.log"

    # Re-invoke ourselves without --bg to avoid infinite recursion
    cmd = [
        sys.executable,
        "-m",
        "golem",
    ]
    if getattr(args, "config", None):
        cmd += ["-c", str(args.config)]
    cmd += ["run", "-p", prompt_text]
    if getattr(args, "worktree", False):
        cmd.append("--worktree")
    if getattr(args, "mcp", None) is True:
        cmd.append("--mcp")
    elif getattr(args, "mcp", None) is False:
        cmd.append("--no-mcp")
    # Don't pass --bg again

    with open(log_file, "w", encoding="utf-8") as lf:
        proc = _sp.Popen(  # pylint: disable=consider-using-with
            cmd,
            stdout=lf,
            stderr=_sp.STDOUT,
            start_new_session=True,
        )

    print(f"  Background PID: {proc.pid}")
    print(f"  Log: {log_file}")
    print(f"  Task ID: {task_id}")
    print(f"\n  Follow output: tail -f {log_file}")
    return 0


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
    config = load_config(getattr(args, "config", None))

    log_dir = Path(getattr(args, "log_dir", None) or DEFAULT_DAEMON_LOG_DIR)
    pid_file = Path(getattr(args, "pid_file", None) or DEFAULT_PID_FILE)

    existing_pid = read_pid(pid_file)
    if existing_pid is not None:
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
        except OSError:
            pass
        if _wait_for_exit(pid, 3):
            remove_pid(pid_file)
            print(f"{target} killed.")
            return 0

    remove_pid(pid_file)
    print(f"{target} (PID {pid}) did not exit.", file=sys.stderr)
    return 1


def cmd_status(args) -> int:
    """Handler for the 'status' subcommand — show recent run history."""
    from .core.dashboard import format_status_text

    since = getattr(args, "hours", 24)
    print(format_status_text(since_hours=since, flow="golem"))
    return 0


def cmd_dashboard(args) -> int:
    """Handler for the 'dashboard' subcommand — launch standalone web UI."""
    if not FASTAPI_AVAILABLE:
        print("Error: FastAPI not installed.", file=sys.stderr)
        return 1

    import socket

    import uvicorn
    from fastapi import FastAPI

    from .core.control_api import control_router
    from .core.dashboard import config_to_snapshot, mount_dashboard
    from .core.live_state import DEFAULT_LIVE_STATE_FILE

    config_path = getattr(args, "config", None)
    try:
        cfg = load_config(config_path)
        snap = config_to_snapshot(cfg)
        default_port = cfg.dashboard.port
    except Exception:  # pylint: disable=broad-except
        snap = None
        default_port = 8082

    port = getattr(args, "port", None) or default_port
    app = FastAPI(title="Golem Dashboard")
    mount_dashboard(app, config_snapshot=snap, live_state_file=DEFAULT_LIVE_STATE_FILE)
    if control_router is not None:
        app.include_router(control_router)
    hostname = socket.getfqdn()
    print(f"Dashboard running at http://{hostname}:{port}/dashboard")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    return 0


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def main() -> int:
    """CLI entry point for Golem."""
    parser = argparse.ArgumentParser(
        description="Golem",
        prog="golem",
    )
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

    # run
    run_p = sub.add_parser("run", help="Execute a task via single agent")
    run_p.add_argument(
        "parent_id", nargs="?", type=int, default=None, help="Task/issue ID"
    )
    run_p.add_argument(
        "--prompt",
        "-p",
        default="",
        help="Run from inline prompt (no external services)",
    )
    run_p.add_argument(
        "--worktree",
        "-w",
        action="store_true",
        help="Run in an isolated git worktree (for prompt mode)",
    )
    run_p.add_argument(
        "--bg",
        action="store_true",
        help="Run in background with output to log file (for prompt mode)",
    )
    run_p.add_argument("--dry", action="store_true", help="Preview without executing")
    run_p.add_argument("--subject", default="", help="Override issue subject")
    run_mcp = run_p.add_mutually_exclusive_group()
    run_mcp.add_argument(
        "--mcp",
        dest="mcp",
        action="store_const",
        const=True,
        default=None,
        help="Enable MCP servers (keyword-scoped from task subject)",
    )
    run_mcp.add_argument(
        "--no-mcp",
        dest="mcp",
        action="store_const",
        const=False,
        help="Disable all MCP servers",
    )
    run_p.set_defaults(func=cmd_run)

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
        "--foreground",
        action="store_true",
        help="Stay attached to terminal",
    )
    daemon_p.add_argument("--log-dir", type=Path, help="Directory for logs")
    daemon_p.add_argument("--pid-file", type=Path, help="PID file path")
    daemon_p.add_argument("--port", type=int, help="Dashboard port")
    daemon_p.set_defaults(func=cmd_daemon)

    # stop
    stop_p = sub.add_parser("stop", help="Stop agent daemon")
    stop_p.add_argument(
        "--dashboard",
        action="store_true",
        help="Stop dashboard instead",
    )
    stop_p.add_argument("--pid-file", type=Path)
    stop_p.add_argument("--force", action="store_true")
    stop_p.set_defaults(func=cmd_stop)

    # status
    status_p = sub.add_parser("status", help="Show run stats")
    status_p.add_argument("--hours", type=int, default=24)
    status_p.set_defaults(func=cmd_status)

    # dashboard
    dash_p = sub.add_parser("dashboard", help="Launch standalone dashboard")
    dash_p.add_argument("--port", type=int)
    dash_p.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
