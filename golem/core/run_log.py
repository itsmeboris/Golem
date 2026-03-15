"""Unified run log — one JSONL line per completed flow execution."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from golem.types import RunRecordDict
from golem.utils import format_duration

from .config import DATA_DIR

logger: logging.Logger = logging.getLogger("golem.core.run_log")

DEFAULT_RUN_LOG: Path = DATA_DIR / "runs" / "runs.jsonl"

__all__ = ["RunRecord", "record_run", "read_runs", "purge_flow"]


@dataclass
class RunRecord:
    """Schema for a single run-log entry."""

    event_id: str
    flow: str
    task_id: str
    source: str = "unknown"
    started_at: str = ""
    finished_at: str = ""
    duration_s: float = 0.0
    success: bool = False
    error: str | None = None
    model: str = ""
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    actions_taken: list[str] = field(default_factory=list)
    verdict: str = ""
    trace_file: str = ""
    queue_wait_ms: int = 0
    prompt_hash: str = ""


def record_run(record: RunRecord, log_file: Path | None = None) -> None:
    """Serialize *record* to JSON and append one line to *log_file*."""
    if log_file is None:
        log_file = DEFAULT_RUN_LOG
    log_file.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(record), default=str)
    with open(log_file, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_runs(
    log_file: Path | None = None,
    flow: str | None = None,
    limit: int = 100,
    since: datetime | None = None,
) -> list[RunRecordDict]:
    """Read run records from *log_file*, applying optional filters.

    Returns the most recent *limit* matching entries (newest first).
    """
    if log_file is None:
        log_file = DEFAULT_RUN_LOG
    if not log_file.exists():
        return []

    records: list[RunRecordDict] = []
    with open(log_file, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if flow and entry.get("flow") != flow:
                continue

            if since and entry.get("started_at"):
                try:
                    started = datetime.fromisoformat(entry["started_at"])
                    if started < since:
                        continue
                except (ValueError, TypeError):
                    pass

            records.append(entry)

    records.reverse()
    return records[:limit]


def purge_flow(flow_name: str, log_file: Path | None = None) -> int:
    """Remove all run-log entries for *flow_name*.  Returns count removed."""
    if log_file is None:
        log_file = DEFAULT_RUN_LOG
    if not log_file.exists():
        return 0

    kept: list[str] = []
    removed = 0
    with open(log_file, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                entry = json.loads(stripped)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if entry.get("flow") == flow_name:
                removed += 1
            else:
                kept.append(line)

    if removed:
        log_file.write_text("".join(kept))
    return removed
