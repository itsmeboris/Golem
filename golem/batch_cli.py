"""Batch CLI subcommands: submit, status, list.

Extracted from cli.py to keep module size manageable.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .core.run_log import format_duration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_COLORS: dict[str, str] = {
    "completed": "32",
    "done": "32",
    "failed": "31",
    "error": "31",
    "running": "33",
    "in_progress": "33",
    "detected": "33",
    "planning": "33",
}

_VERDICT_PASS = {"pass", "passed", "success"}
_VERDICT_FAIL = {"fail", "failed"}


def _color(text: str, code: str, *, enabled: bool) -> str:
    return f"\033[{code}m{text}\033[0m" if enabled else text


def _color_status(text: str, status: str, *, enabled: bool) -> str:
    code = _STATUS_COLORS.get(status)
    return _color(text, code, enabled=enabled) if code else text


def _color_verdict(text: str, verdict: str, *, enabled: bool) -> str:
    vl = verdict.lower()
    if vl in _VERDICT_PASS:
        return _color(text, "32", enabled=enabled)
    if vl in _VERDICT_FAIL:
        return _color(text, "31", enabled=enabled)
    return text


def batch_api_get(port: int, path: str, api_key: str = "") -> dict | None:
    """GET a batch API endpoint. Returns parsed JSON or None on error."""
    url = f"http://127.0.0.1:{port}{path}"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        print(f"Error {exc.code}: {body}", file=sys.stderr)
        return None
    except urllib.error.URLError as exc:
        print(f"Cannot reach daemon: {exc.reason}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# _format_batch_status
# ---------------------------------------------------------------------------


def _print_task_row(
    tid: Any,
    task_result: dict,
    *,
    use_color: bool,
) -> None:
    """Print one row of the per-task breakdown table."""
    state = task_result.get("state", "unknown")
    tv = task_result.get("validation_verdict", "")
    cost = task_result.get("total_cost_usd", 0.0)
    dur = task_result.get("duration_seconds", 0.0)

    state_str = _color_status(f"{state:<14}", state, enabled=use_color)
    verdict_str = (
        _color_verdict(f"{tv:<12}", tv, enabled=use_color) if tv else f"{'-':<12}"
    )
    dur_str = format_duration(dur) if dur else "-"
    cost_str = f"${cost:.2f}" if cost else "-"

    print(
        f"  {str(tid):<12} {state_str} {verdict_str} " f"{cost_str:>8}  {dur_str:>10}"
    )


def format_batch_status(batch: dict) -> None:  # pylint: disable=too-many-branches
    """Print a richly formatted batch status report."""
    group_id = batch.get("group_id", "?")
    status = batch.get("status", "?")
    task_ids = batch.get("task_ids", [])
    task_results = batch.get("task_results", {})
    verdict = batch.get("validation_verdict", "")
    use_color = sys.stdout.isatty()

    print(f"\n{'=' * 60}")
    print(f"  Batch: {group_id}")
    print(f"{'=' * 60}")

    status_display = _color_status(status.upper(), status, enabled=use_color)
    print(f"  Status: {status_display}")

    created = batch.get("created_at", "")
    if created:
        print(f"  Created: {created}")
    completed_at = batch.get("completed_at", "")
    if completed_at:
        print(f"  Completed: {completed_at}")
    print(f"  Tasks: {len(task_ids)}")

    if task_results:
        print(f"\n  {'─' * 56}")
        print(
            f"  {'Task ID':<12} {'State':<14} {'Verdict':<12} "
            f"{'Cost':>8}  {'Duration':>10}"
        )
        print(f"  {'─' * 56}")
        for tid in task_ids:
            _print_task_row(tid, task_results.get(str(tid), {}), use_color=use_color)
        print(f"  {'─' * 56}")

    total_cost = batch.get("total_cost_usd", 0.0)
    total_duration = batch.get("total_duration_s", 0.0)
    print(f"\n  Total cost:     ${total_cost:.2f}")
    print(f"  Total duration: {format_duration(total_duration)}")
    if verdict:
        v_display = _color_verdict(verdict, verdict, enabled=use_color)
        print(f"  Overall verdict: {v_display}")
    print()


# ---------------------------------------------------------------------------
# _parse_batch_file
# ---------------------------------------------------------------------------


def _read_batch_file(file_path: str) -> tuple[str, str] | int:
    """Read and validate a batch file exists and is non-empty.

    Returns ``(raw_content, suffix)`` or an int exit code on error.
    """
    p = Path(file_path)
    if not p.is_file():
        print(f"Error: file not found: {file_path}", file=sys.stderr)
        return 1

    raw = p.read_text(encoding="utf-8")
    if not raw.strip():
        print(f"Error: file is empty: {file_path}", file=sys.stderr)
        return 1

    return raw, p.suffix.lower()


def _parse_batch_file(file_path: str) -> dict | int:
    """Parse a JSON/YAML batch file. Returns the payload dict or an int exit code."""
    try:
        import yaml  # pylint: disable=import-outside-toplevel
    except ImportError:
        yaml = None  # type: ignore[assignment]

    read_result = _read_batch_file(file_path)
    if isinstance(read_result, int):
        return read_result
    raw, suffix = read_result

    yaml_exc_types: tuple = (json.JSONDecodeError, ValueError)
    if yaml is not None:
        yaml_exc_types = (json.JSONDecodeError, ValueError, yaml.YAMLError)

    try:
        payload = _decode_content(raw, suffix, yaml)
    except yaml_exc_types as exc:
        print(f"Error: failed to parse {file_path}: {exc}", file=sys.stderr)
        return 1

    if not isinstance(payload, dict):
        print(
            "Error: file must contain a JSON/YAML object with a 'tasks' array",
            file=sys.stderr,
        )
        return 1

    tasks = payload.get("tasks")
    if not tasks or not isinstance(tasks, list):
        print("Error: 'tasks' array is required in the batch file", file=sys.stderr)
        return 1

    return payload


def _decode_content(raw: str, suffix: str, yaml: Any) -> Any:
    """Decode raw file content as JSON or YAML based on suffix."""
    if suffix in (".yaml", ".yml"):
        if yaml is None:
            print(
                "Error: PyYAML is required for .yaml files (pip install pyyaml)",
                file=sys.stderr,
            )
            raise ValueError("PyYAML not installed")
        return yaml.safe_load(raw)
    if suffix == ".json":
        return json.loads(raw)
    # Unknown suffix: try JSON first, fall back to YAML
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        if yaml is None:
            raise  # noqa: TRY201
        return yaml.safe_load(raw)


def _print_submit_result(result: dict, input_tasks: list) -> None:
    """Print the formatted submit response."""
    group_id = result.get("group_id", "?")
    submitted_tasks = result.get("tasks", [])

    print(f"\n{'=' * 60}")
    print(f"  Batch submitted: {group_id}")
    print(f"{'=' * 60}")
    print(f"  Tasks: {len(submitted_tasks)}")
    print()

    for i, st in enumerate(submitted_tasks):
        task_id = st.get("task_id", "?")
        status = st.get("status", "?")
        orig = input_tasks[i] if i < len(input_tasks) else {}
        subject = orig.get("subject", "")
        key = orig.get("key", "")
        deps = orig.get("depends_on", [])

        parts = [f"  #{task_id}"]
        if key:
            parts.append(f"[key={key}]")
        parts.append(f"({status})")
        if subject:
            parts.append(f"- {subject}")
        if deps:
            deps_str = ", ".join(str(d) for d in deps)
            parts.append(f"  depends_on: [{deps_str}]")
        print(" ".join(parts))

    print(f"\n  Track with: golem batch status {group_id}")


# ---------------------------------------------------------------------------
# cmd_batch_submit
# ---------------------------------------------------------------------------


def _post_batch(port: int, payload: dict, api_key: str, timeout: int) -> dict | None:
    """POST a batch payload to the daemon. Returns parsed JSON or None on error."""
    url = f"http://127.0.0.1:{port}/api/submit/batch"
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


def cmd_batch_submit(args: argparse.Namespace, config: Any) -> int:
    """Submit a batch of tasks from a JSON or YAML file."""
    from .cli import _ensure_daemon  # pylint: disable=import-outside-toplevel
    from .core.config import DashboardConfig  # pylint: disable=import-outside-toplevel

    parsed = _parse_batch_file(args.file)
    if isinstance(parsed, int):
        return parsed
    payload = parsed

    port = config.dashboard.port if config.dashboard else DashboardConfig.port
    daemon_cfg = config.daemon
    _ensure_daemon(args, config, port, daemon_cfg=daemon_cfg)

    api_key = config.dashboard.api_key if config.dashboard else ""
    result = _post_batch(port, payload, api_key, daemon_cfg.http_submit_timeout)
    if result is None:
        return 1

    _print_submit_result(result, payload["tasks"])
    return 0


# ---------------------------------------------------------------------------
# cmd_batch (top-level dispatcher)
# ---------------------------------------------------------------------------


def cmd_batch(args: argparse.Namespace) -> int:
    """Query batch status from the daemon API."""
    from .core.config import (  # pylint: disable=import-outside-toplevel
        DashboardConfig,
        load_config,
    )

    config = load_config(getattr(args, "config", None))
    port = config.dashboard.port if config.dashboard else DashboardConfig.port
    api_key = config.dashboard.api_key if config.dashboard else ""
    batch_cmd = getattr(args, "batch_command", None)

    if batch_cmd == "submit":
        return cmd_batch_submit(args, config)

    if batch_cmd == "status":
        data = batch_api_get(port, f"/api/batch/{args.group_id}", api_key=api_key)
        if data is None:
            return 1
        format_batch_status(data.get("batch", {}))
        return 0

    if batch_cmd == "list":
        return _cmd_batch_list(port, api_key)

    print("Usage: golem batch {submit,status,list}", file=sys.stderr)
    return 1


def _cmd_batch_list(port: int, api_key: str) -> int:
    data = batch_api_get(port, "/api/batches", api_key=api_key)
    if data is None:
        return 1
    batches = data.get("batches", [])
    if not batches:
        print("No batches found.")
        return 0
    for b in batches:
        gid = b.get("group_id", "?")
        status = b.get("status", "?")
        count = len(b.get("task_ids", []))
        print(f"  {gid}  status={status}  tasks={count}")
    return 0
