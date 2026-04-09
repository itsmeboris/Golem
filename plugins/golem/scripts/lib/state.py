"""Session-local job tracking for the golem plugin.

State is keyed by CLAUDE_SESSION_ID (set by Claude Code for all hook/command
invocations in the same session) or falls back to a repo-scoped file.
"""

import json
import os
import time
from pathlib import Path

_STATE_DIR = Path(os.environ.get("GOLEM_PLUGIN_STATE_DIR", "/tmp/golem-plugin"))


def _state_file() -> Path:
    """Return the session state file path, keyed by session ID or repo."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    session_id = os.environ.get("CLAUDE_SESSION_ID", "")
    if session_id:
        return _STATE_DIR / f"session-{session_id}.json"
    # Fallback: key by resolved cwd (stable across invocations in same repo)
    # Use hashlib (deterministic) instead of hash() (randomized per-process)
    import hashlib
    cwd_hash = hashlib.sha256(str(Path.cwd().resolve()).encode()).hexdigest()[:12]
    return _STATE_DIR / f"repo-{cwd_hash}.json"


def _load_state() -> dict:
    """Load session state from disk."""
    path = _state_file()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"jobs": [], "stats": {"delegated": 0, "completed": 0, "failed": 0}}
    return {"jobs": [], "stats": {"delegated": 0, "completed": 0, "failed": 0}}


def _save_state(state: dict) -> None:
    """Save session state to disk."""
    path = _state_file()
    path.write_text(json.dumps(state, indent=2))


def record_delegation(task_id: int, prompt: str, mode: str) -> None:
    """Record a task delegation in session state."""
    state = _load_state()
    state["jobs"].append({
        "task_id": task_id,
        "prompt": prompt[:200],  # truncate for storage
        "mode": mode,
        "delegated_at": time.time(),
        "status": "running",
    })
    state["stats"]["delegated"] += 1
    _save_state(state)


def update_job_status(task_id: int, status: str) -> None:
    """Update a job's status in session state."""
    state = _load_state()
    for job in state["jobs"]:
        if job["task_id"] == task_id:
            job["status"] = status
            if status == "completed":
                state["stats"]["completed"] += 1
            elif status == "failed":
                state["stats"]["failed"] += 1
            break
    _save_state(state)


def get_session_jobs() -> list[dict]:
    """Return all jobs from this session."""
    return _load_state()["jobs"]


def get_session_stats() -> dict:
    """Return session delegation stats."""
    return _load_state()["stats"]


def flush_stats_to_global() -> None:
    """Append session stats to global plugin stats file."""
    stats = get_session_stats()
    if stats["delegated"] == 0:
        return  # nothing to record

    global_path = Path.home() / ".golem" / "data" / "plugin-stats.json"
    global_path.parent.mkdir(parents=True, exist_ok=True)

    existing = []
    if global_path.exists():
        try:
            existing = json.loads(global_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = []

    existing.append({
        "timestamp": time.time(),
        "pid": os.getpid(),
        **stats,
    })

    global_path.write_text(json.dumps(existing, indent=2))
