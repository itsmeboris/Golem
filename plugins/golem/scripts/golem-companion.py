#!/usr/bin/env python3
"""Golem companion script — single entry point for the Claude Code plugin.

Usage: python3 golem-companion.py <subcommand> [args] [--json]

Subcommands:
  setup          Check golem CLI, start daemon, attach repo, collect signals
  setup --finalize  Derive verify.yaml from golem.md, update .gitignore
  run            Wrap golem run with session job tracking
  status         Wrap golem status with session job enrichment
  query          Query Golem control API for task results
  config         Wrap golem config get/set/list
  cancel         Wrap golem cancel
  session-start  Hook: daemon/repo health check
  session-end    Hook: flush session stats
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

# Add lib/ to path
sys.path.insert(0, str(Path(__file__).parent / "lib"))

from daemon import (  # pylint: disable=import-error
    attach_repo,
    ensure_running,
    is_daemon_running,
    is_golem_installed,
    is_repo_attached,
)
from setup_flow import (  # pylint: disable=import-error
    collect_repo_signals,
    finalize_setup,
    verify_commands,
)
from state import (  # pylint: disable=import-error
    flush_stats_to_global,
    get_session_jobs,
    get_session_stats,
    record_delegation,
    update_job_status,
)


def _output(data, use_json: bool = False) -> None:
    """Print output — JSON or human-readable."""
    if use_json:
        print(json.dumps(data, indent=2))
    else:
        for key, value in data.items():
            print(f"  {key}: {value}")


# --- Subcommand handlers ---


def cmd_setup(args) -> int:
    """Setup: check golem, start daemon, attach repo, collect signals."""
    if args.finalize:
        result = finalize_setup(args.cwd or os.getcwd())
        _output(result, args.json)
        return 0 if result.get("ok") else 1

    if args.verify:
        result = verify_commands(args.cwd or os.getcwd())
        _output(result, args.json)
        return 0 if result.get("ok") else 1

    cwd = args.cwd or os.getcwd()

    if not is_golem_installed():
        _output({"ready": False, "error": "golem not found in PATH"}, args.json)
        return 1

    daemon_result = ensure_running()
    attach_result = attach_repo(cwd)
    signals = collect_repo_signals(cwd)

    # Check if bootstrap actually succeeded
    daemon_ok = daemon_result.get("already_running") or daemon_result.get("started")
    attach_ok = attach_result.get("attached", False)
    ready = bool(daemon_ok and attach_ok)

    result = {
        "ready": ready,
        "daemon": daemon_result,
        "attach": attach_result,
        "signals": signals,
    }
    _output(result, args.json)
    return 0 if ready else 1


def cmd_run(args) -> int:
    """Run: delegate a task to golem. Supports --wait (poll) and --background."""
    prompt = " ".join(args.task) if args.task else ""
    if not prompt:
        _output({"error": "No task description provided"}, args.json)
        return 1

    cwd = args.cwd or os.getcwd()

    # Build golem run command — golem run is synchronous, submits and returns task_id
    cmd = ["golem", "run", "--prompt", prompt, "--cwd", cwd]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
            text=True,
        )
        task_id = _extract_task_id(result.stdout)

        if task_id and result.returncode == 0:
            mode = "wait" if args.wait else "background"
            record_delegation(task_id, prompt, mode)

        if not task_id or result.returncode != 0:
            _output(
                {
                    "ok": False,
                    "output": result.stdout.strip(),
                    "error": result.stderr.strip(),
                },
                args.json,
            )
            return result.returncode or 1

        # --wait: poll golem status until task completes
        if args.wait:
            import time

            for _ in range(360):  # 30 min max (5s intervals)
                time.sleep(5)
                status = subprocess.run(
                    ["golem", "status", "--task", str(task_id)],
                    capture_output=True,
                    timeout=10,
                    text=True,
                )
                output_text = status.stdout.strip()
                if "COMPLETED" in output_text or "FAILED" in output_text:
                    update_job_status(
                        task_id, "completed" if "COMPLETED" in output_text else "failed"
                    )
                    _output(
                        {
                            "ok": "COMPLETED" in output_text,
                            "task_id": task_id,
                            "output": output_text,
                        },
                        args.json,
                    )
                    return 0 if "COMPLETED" in output_text else 1

            _output(
                {"ok": False, "task_id": task_id, "error": "timed out waiting"},
                args.json,
            )
            return 1

        # --background (default): return immediately
        _output(
            {
                "ok": True,
                "task_id": task_id,
                "mode": "background",
                "output": result.stdout.strip(),
            },
            args.json,
        )
        return 0

    except FileNotFoundError:
        _output({"ok": False, "error": "golem not found in PATH"}, args.json)
        return 1
    except subprocess.TimeoutExpired:
        _output({"ok": False, "error": "golem run timed out"}, args.json)
        return 1


def cmd_status(args) -> int:
    """Status: wrap golem status with session enrichment. Supports --watch."""
    import time

    iterations = 1
    interval = 0
    if args.watch is not None:
        iterations = 120  # max 10 min at default 5s interval
        interval = args.watch or 5

    for i in range(iterations):
        cmd = ["golem", "status"]
        if args.task_id:
            cmd.extend(["--task", str(args.task_id)])
        if args.hours:
            cmd.extend(["--hours", str(args.hours)])

        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10, text=True)
            session_jobs = get_session_jobs()

            output = {
                "golem_status": result.stdout.strip(),
                "session_jobs": session_jobs,
                "session_stats": get_session_stats(),
            }
            _output(output, args.json)

            if interval and i < iterations - 1:
                time.sleep(interval)
            else:
                return result.returncode

        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            _output({"error": str(exc)}, args.json)
            return 1

    return 0


def cmd_query(args) -> int:
    """Query: fetch task results from Golem's control API."""
    from pathlib import Path

    if not args.task_id:
        _output({"error": "task-id required"}, args.json)
        return 1

    # Read API config
    config_path = Path.home() / ".golem" / "config.yaml"
    api_key = ""
    port = 8081

    if config_path.exists():
        try:
            import yaml

            config = yaml.safe_load(config_path.read_text()) or {}
            dashboard = config.get("dashboard", {})
            api_key = dashboard.get("api_key", "")
            port = dashboard.get("port", 8081)
        except Exception:
            pass

    url = f"http://localhost:{port}/api/sessions/{args.task_id}"
    req = urllib.request.Request(url)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())

        session = data.get("session", {})

        if args.raw:
            # Raw mode: session dict + raw trace file contents per spec
            trace_rel = session.get("trace_file", "")
            if trace_rel:
                data_root = Path.home() / ".golem" / "data"
                trace_path = _resolve_trace_path(trace_rel, data_root)
                if trace_path:
                    try:
                        data["raw_trace"] = trace_path.read_text().strip().splitlines()
                    except OSError:
                        data["raw_trace"] = []
                else:
                    data["raw_trace"] = []
            _output(data, args.json)
        else:
            # Build summary per spec: verdict, files changed, verification, phase durations
            commit_sha = session.get("commit_sha", "")
            files_changed = []
            if commit_sha:
                try:
                    diff_result = subprocess.run(
                        ["git", "diff", "--name-only", f"{commit_sha}~1", commit_sha],
                        capture_output=True,
                        timeout=10,
                        text=True,
                    )
                    if diff_result.returncode == 0:
                        files_changed = [
                            f for f in diff_result.stdout.strip().splitlines() if f
                        ]
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    pass

            # Read trace file for phase durations if available.
            # Golem stores trace paths relative to data dir — resolve via ~/.golem/data/
            # Phase durations are derived from Milestone timestamps, not raw events.
            phase_durations = {}
            trace_rel = session.get("trace_file", "")
            if trace_rel:
                data_root = Path.home() / ".golem" / "data"
                trace_path = _resolve_trace_path(trace_rel, data_root)
                if trace_path:
                    try:
                        milestones = []
                        with open(trace_path) as f:
                            for line in f:
                                entry = json.loads(line)
                                milestones.append(entry)
                        for ms in milestones:
                            if ms.get("duration_ms") and ms.get("summary"):
                                phase_durations[ms["summary"]] = ms["duration_ms"]
                    except (OSError, json.JSONDecodeError):
                        pass

            # Synthesize verdict from state + verification
            state = session.get("state", "unknown")
            verification = session.get("verification_result", {})
            if state == "COMPLETED" and verification.get("passed"):
                verdict = "PASSED"
            elif state == "COMPLETED" and not verification.get("passed"):
                verdict = "FAILED"
            elif state in ("RUNNING", "DETECTED"):
                verdict = "RUNNING"
            else:
                verdict = state

            summary = {
                "task_id": args.task_id,
                "verdict": verdict,
                "state": state,
                "verification": verification,
                "commit_sha": commit_sha,
                "files_changed": files_changed,
                "phase_durations": phase_durations,
            }
            _output(summary, args.json)
        return 0

    except urllib.error.HTTPError as exc:
        _output({"error": f"API error: {exc.code} {exc.reason}"}, args.json)
        return 1
    except urllib.error.URLError as exc:
        _output({"error": f"Cannot reach daemon: {exc.reason}"}, args.json)
        return 1
    except Exception as exc:
        _output({"error": str(exc)}, args.json)
        return 1


def cmd_config(args) -> int:
    """Config: wrap golem config."""
    cmd = ["golem", "config"] + args.config_args

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=10, text=True)
        output = result.stdout.strip()
        error = result.stderr.strip()
        if result.returncode != 0:
            _output({"output": output, "error": error or "command failed"}, args.json)
        else:
            _output({"output": output}, args.json)
        return result.returncode
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _output({"error": str(exc)}, args.json)
        return 1


def cmd_cancel(args) -> int:
    """Cancel: wrap golem cancel."""
    cmd = ["golem", "cancel", str(args.task_id)]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=10, text=True)
        if result.returncode == 0:
            update_job_status(args.task_id, "cancelled")
        _output(
            {
                "ok": result.returncode == 0,
                "output": result.stdout.strip(),
            },
            args.json,
        )
        return result.returncode
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _output({"error": str(exc)}, args.json)
        return 1


def cmd_session_start(args) -> int:
    """SessionStart hook: check daemon and repo status."""
    if not is_golem_installed():
        return 0  # Silent exit — golem not installed

    if is_daemon_running():
        if is_repo_attached():
            print(
                "[Golem] Daemon active. Repo attached.\n"
                "IMPORTANT: For complex tasks (multi-file changes, refactors, features "
                "needing tests), use /golem:run to delegate to Golem's autonomous pipeline "
                "instead of handling inline. Golem runs UNDERSTAND → PLAN → BUILD → REVIEW → "
                "VERIFY with full test/lint verification.\n"
                "Use /golem:status to check running tasks."
            )
        else:
            print(
                "[Golem] Daemon active but repo not attached. Use /golem:setup to bootstrap."
            )
    else:
        print("[Golem] Daemon not running. Use /golem:setup to bootstrap.")

    return 0


def cmd_session_end(args) -> int:
    """SessionEnd hook: flush session stats."""
    try:
        flush_stats_to_global()
    except Exception:
        pass  # Never block session teardown
    return 0


# --- Helpers ---


def _resolve_trace_path(trace_ref: str, data_root: Path) -> Path | None:
    """Resolve a trace file path, validating it stays within data_root.

    Returns None if the path would escape the allowed directory.
    """
    if Path(trace_ref).is_absolute():
        resolved = Path(trace_ref).resolve()
    else:
        resolved = (data_root / trace_ref).resolve()

    # Containment check: resolved path must be within data_root
    try:
        resolved.relative_to(data_root.resolve())
    except ValueError:
        return None

    if not resolved.is_file():
        return None

    return resolved


def _extract_task_id(output: str) -> int | None:
    """Extract task ID from golem run output.

    Handles formats:
      - "Submitted task #123"
      - "task_id: 123"
      - "task 123"
      - bare "123" on a line
    """
    import re

    # "Submitted task #123" — the actual golem CLI format
    match = re.search(r"task\s*#(\d+)", output, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # "task_id: 123" or "task id: 123" or "task: 123"
    match = re.search(r"task[_\s]?(?:id)?[:\s]+(\d+)", output, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # Try bare number on a line
    for line in output.strip().splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


# --- CLI ---


def main() -> int:
    # --json is a common flag; use a parent parser so each subcommand accepts it
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument("--json", action="store_true", help="Output JSON")

    parser = argparse.ArgumentParser(
        description="Golem companion script for Claude Code plugin",
    )

    sub = parser.add_subparsers(dest="command")

    # setup
    setup_p = sub.add_parser("setup", parents=[json_parent])
    setup_p.add_argument("--finalize", action="store_true")
    setup_p.add_argument("--verify", action="store_true")
    setup_p.add_argument("--regenerate", action="store_true")
    setup_p.add_argument("--update", action="store_true")
    setup_p.add_argument("--skip-verify", action="store_true")
    setup_p.add_argument("--cwd", default=None)
    setup_p.set_defaults(func=cmd_setup)

    # run
    run_p = sub.add_parser("run", parents=[json_parent])
    run_p.add_argument("task", nargs="*")
    run_mode = run_p.add_mutually_exclusive_group()
    run_mode.add_argument("--background", action="store_true")
    run_mode.add_argument("--wait", action="store_true")
    run_p.add_argument("--cwd", default=None)
    run_p.set_defaults(func=cmd_run)

    # status
    status_p = sub.add_parser("status", parents=[json_parent])
    status_p.add_argument("task_id", nargs="?", type=int, default=None)
    status_p.add_argument("--hours", type=int, default=24)
    status_p.add_argument("--watch", nargs="?", type=int, const=5, default=None)
    status_p.set_defaults(func=cmd_status)

    # query
    query_p = sub.add_parser("query", parents=[json_parent])
    query_p.add_argument("task_id", type=int)
    query_p.add_argument("--raw", action="store_true")
    query_p.set_defaults(func=cmd_query)

    # config
    config_p = sub.add_parser("config", parents=[json_parent])
    config_p.add_argument("config_args", nargs="*")
    config_p.set_defaults(func=cmd_config)

    # cancel
    cancel_p = sub.add_parser("cancel", parents=[json_parent])
    cancel_p.add_argument("task_id", type=int)
    cancel_p.set_defaults(func=cmd_cancel)

    # session-start
    ss_p = sub.add_parser("session-start", parents=[json_parent])
    ss_p.set_defaults(func=cmd_session_start)

    # session-end
    se_p = sub.add_parser("session-end", parents=[json_parent])
    se_p.set_defaults(func=cmd_session_end)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
