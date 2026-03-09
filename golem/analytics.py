"""Quality metrics analytics over run-log data.

Computes aggregate statistics: pass/partial/fail rates, average cost,
retry effectiveness, common failure categories, and trends over time.
"""

from collections import Counter
from typing import Any


def compute_analytics(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate quality metrics from run records.

    Parameters
    ----------
    runs
        List of run-log dicts (from ``read_runs``).

    Returns
    -------
    dict
        Analytics summary with keys: total_tasks, pass_rate, partial_rate,
        fail_rate, avg_cost_usd, avg_duration_s, retry_effectiveness,
        top_failure_reasons.
    """
    if not runs:
        return {
            "total_tasks": 0,
            "pass_rate": 0.0,
            "partial_rate": 0.0,
            "fail_rate": 0.0,
            "avg_cost_usd": 0.0,
            "avg_duration_s": 0.0,
            "retry_effectiveness": 0.0,
            "top_failure_reasons": [],
        }

    total = len(runs)
    verdicts = Counter(r.get("verdict", "").upper() for r in runs)
    pass_count = verdicts.get("PASS", 0)
    partial_count = verdicts.get("PARTIAL", 0)
    fail_count = verdicts.get("FAIL", 0)

    total_cost = sum(r.get("cost_usd", 0) for r in runs)
    total_duration = sum(r.get("duration_s", 0) for r in runs)

    # Retry effectiveness: of tasks that were retried, how many eventually passed?
    retried = [r for r in runs if _was_retried(r)]
    retried_passed = [r for r in retried if r.get("verdict", "").upper() == "PASS"]
    retry_eff = len(retried_passed) / len(retried) if retried else 0.0

    # Top failure reasons
    errors = [r.get("error", "") for r in runs if r.get("error")]
    error_counts = Counter(errors).most_common(10)

    return {
        "total_tasks": total,
        "pass_rate": pass_count / total,
        "partial_rate": partial_count / total,
        "fail_rate": fail_count / total,
        "avg_cost_usd": total_cost / total,
        "avg_duration_s": total_duration / total,
        "retry_effectiveness": retry_eff,
        "top_failure_reasons": error_counts,
    }


def _was_retried(run: dict) -> bool:
    """Check if a run record indicates retries were attempted."""
    for action in run.get("actions_taken", []):
        if action.startswith("retries:") and action != "retries:0":
            return True
    return False
