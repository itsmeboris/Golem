"""Cost analytics over run-log data and task sessions.

Computes cost-specific aggregate statistics: cost over time, cost by verdict,
cost per retry bucket, and budget utilization from task sessions.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _extract_retry_count(run: dict) -> int:
    """Parse actions_taken list to find 'retries:N' and return N as int.

    Returns 0 if not found or actions_taken is missing.
    """
    for action in run.get("actions_taken", []):
        if isinstance(action, str) and action.startswith("retries:"):
            try:
                return int(action.split(":", 1)[1])
            except (ValueError, IndexError) as exc:
                logger.debug("Failed to parse retry count from %r: %s", action, exc)
    return 0


def _retry_bucket(retry_count: int) -> str:
    """Map a retry count to its display bucket key."""
    if retry_count >= 3:
        return "3+"
    return str(retry_count)


def compute_cost_analytics(
    runs: list[dict],
    sessions: dict[int, Any] | None = None,
) -> dict:
    """Compute cost analytics from run records and optional task sessions.

    Parameters
    ----------
    runs
        List of run-log dicts (from ``read_runs``).
    sessions
        Optional dict mapping task_id (int) to TaskSession objects.

    Returns
    -------
    dict
        Analytics dict with keys: cost_over_time, cost_by_verdict,
        cost_per_retry, budget_utilization, summary.
    """
    # ------------------------------------------------------------------ #
    # Summary stats
    # ------------------------------------------------------------------ #
    costs = [r.get("cost_usd", 0.0) for r in runs]
    total_cost = sum(costs)
    total_runs = len(runs)
    avg_cost_per_run = total_cost / total_runs if total_runs else 0.0
    max_cost_run = max(costs) if costs else 0.0
    min_cost_run = min(costs) if costs else 0.0

    summary = {
        "total_cost": round(total_cost, 4),
        "total_runs": total_runs,
        "avg_cost_per_run": round(avg_cost_per_run, 4),
        "max_cost_run": round(max_cost_run, 4),
        "min_cost_run": round(min_cost_run, 4),
    }

    # ------------------------------------------------------------------ #
    # Cost over time — daily buckets
    # ------------------------------------------------------------------ #
    daily: dict[str, dict] = {}
    for run in runs:
        started_at = run.get("started_at", "")
        if not started_at:
            continue
        date_str = started_at[:10]  # "YYYY-MM-DD"
        cost = run.get("cost_usd", 0.0)
        if date_str not in daily:
            daily[date_str] = {"date": date_str, "total_cost": 0.0, "run_count": 0}
        daily[date_str]["total_cost"] += cost
        daily[date_str]["run_count"] += 1

    cost_over_time = []
    for date_str in sorted(daily):
        entry = daily[date_str]
        count = entry["run_count"]
        total = entry["total_cost"]
        cost_over_time.append(
            {
                "date": date_str,
                "total_cost": round(total, 4),
                "run_count": count,
                "avg_cost": round(total / count, 4) if count else 0.0,
            }
        )

    # ------------------------------------------------------------------ #
    # Cost by verdict
    # ------------------------------------------------------------------ #
    verdict_buckets: dict[str, dict] = {}
    for run in runs:
        verdict = (run.get("verdict", "") or "").upper()
        cost = run.get("cost_usd", 0.0)
        if verdict not in verdict_buckets:
            verdict_buckets[verdict] = {"count": 0, "total_cost": 0.0}
        verdict_buckets[verdict]["count"] += 1
        verdict_buckets[verdict]["total_cost"] += cost

    cost_by_verdict = {}
    for verdict, data in verdict_buckets.items():
        count = data["count"]
        total = data["total_cost"]
        cost_by_verdict[verdict] = {
            "count": count,
            "total_cost": round(total, 4),
            "avg_cost": round(total / count, 4) if count else 0.0,
        }

    # ------------------------------------------------------------------ #
    # Cost per retry bucket
    # ------------------------------------------------------------------ #
    retry_buckets: dict[str, dict] = {}
    for run in runs:
        retry_count = _extract_retry_count(run)
        bucket = _retry_bucket(retry_count)
        cost = run.get("cost_usd", 0.0)
        if bucket not in retry_buckets:
            retry_buckets[bucket] = {"count": 0, "total_cost": 0.0}
        retry_buckets[bucket]["count"] += 1
        retry_buckets[bucket]["total_cost"] += cost

    cost_per_retry = {}
    for bucket, data in retry_buckets.items():
        count = data["count"]
        total = data["total_cost"]
        cost_per_retry[bucket] = {
            "count": count,
            "total_cost": round(total, 4),
            "avg_cost": round(total / count, 4) if count else 0.0,
        }

    # ------------------------------------------------------------------ #
    # Budget utilization from sessions
    # ------------------------------------------------------------------ #
    budget_utilization = _compute_budget_utilization(sessions)

    return {
        "cost_over_time": cost_over_time,
        "cost_by_verdict": cost_by_verdict,
        "cost_per_retry": cost_per_retry,
        "budget_utilization": budget_utilization,
        "summary": summary,
    }


def _compute_budget_utilization(
    sessions: dict[int, Any] | None,
) -> dict | None:
    """Compute budget utilization from terminal-state task sessions.

    Only COMPLETED and FAILED sessions are included.
    Returns None if sessions is empty/None or no terminal sessions exist.
    """
    if not sessions:
        return None

    from golem.orchestrator import TaskSessionState

    terminal_states = {TaskSessionState.COMPLETED, TaskSessionState.FAILED}

    tasks = []
    for session in sessions.values():
        if session.state not in terminal_states:
            continue
        budget = session.budget_usd
        spent = session.total_cost_usd
        util_pct = round(spent / budget * 100.0, 2) if budget else 0.0
        tasks.append(
            {
                "task_id": session.parent_issue_id,
                "budget": round(budget, 4),
                "spent": round(spent, 4),
                "utilization_pct": util_pct,
            }
        )

    if not tasks:
        return None

    total_budget = round(sum(t["budget"] for t in tasks), 4)
    total_spent = round(sum(t["spent"] for t in tasks), 4)
    utilization_pct = (
        round(total_spent / total_budget * 100.0, 2) if total_budget else 0.0
    )
    over_budget_count = sum(1 for t in tasks if t["spent"] > t["budget"])

    return {
        "total_budget": total_budget,
        "total_spent": total_spent,
        "utilization_pct": utilization_pct,
        "over_budget_count": over_budget_count,
        "tasks": tasks,
    }


def format_cost_summary_text(analytics: dict) -> str:
    """Format cost analytics as a human-readable multi-line string.

    Parameters
    ----------
    analytics
        Output from ``compute_cost_analytics``.

    Returns
    -------
    str
        Formatted plain text suitable for CLI output.
    """
    lines = []

    # ------------------------------------------------------------------ #
    # SUMMARY section
    # ------------------------------------------------------------------ #
    s = analytics["summary"]
    lines.append("=== SUMMARY ===")
    lines.append("Total runs:          %d" % s["total_runs"])
    lines.append("Total cost:          $%.2f" % s["total_cost"])
    if s["total_runs"] > 0:
        lines.append("Avg cost per run:    $%.2f" % s["avg_cost_per_run"])
        lines.append("Max cost (single):   $%.2f" % s["max_cost_run"])
        lines.append("Min cost (single):   $%.2f" % s["min_cost_run"])
    lines.append("")

    # ------------------------------------------------------------------ #
    # COST BY VERDICT section
    # ------------------------------------------------------------------ #
    lines.append("=== COST BY VERDICT ===")
    cost_by_verdict = analytics["cost_by_verdict"]
    if cost_by_verdict:
        for verdict in sorted(cost_by_verdict):
            entry = cost_by_verdict[verdict]
            label = verdict if verdict else "(unknown)"
            lines.append(
                "%-10s  count=%-4d  total=$%-8.2f  avg=$%.2f"
                % (label, entry["count"], entry["total_cost"], entry["avg_cost"])
            )
    else:
        lines.append("  (no data)")
    lines.append("")

    # ------------------------------------------------------------------ #
    # COST BY RETRY section
    # ------------------------------------------------------------------ #
    lines.append("=== COST BY RETRY ===")
    cost_per_retry = analytics["cost_per_retry"]
    if cost_per_retry:
        for bucket in sorted(cost_per_retry, key=lambda k: (k == "3+", k)):
            entry = cost_per_retry[bucket]
            lines.append(
                "retries=%-4s  count=%-4d  total=$%-8.2f  avg=$%.2f"
                % (bucket, entry["count"], entry["total_cost"], entry["avg_cost"])
            )
    else:
        lines.append("  (no data)")
    lines.append("")

    # ------------------------------------------------------------------ #
    # BUDGET UTILIZATION section (only if available)
    # ------------------------------------------------------------------ #
    bu = analytics.get("budget_utilization")
    if bu is not None:
        lines.append("=== BUDGET UTILIZATION ===")
        lines.append("Total budget:        $%.2f" % bu["total_budget"])
        lines.append("Total spent:         $%.2f" % bu["total_spent"])
        lines.append("Utilization:         %.1f%%" % bu["utilization_pct"])
        lines.append("Over-budget tasks:   %d" % bu["over_budget_count"])
        if bu["tasks"]:
            lines.append("  Tasks:")
            for task in bu["tasks"]:
                lines.append(
                    "    task=%-6d  budget=$%-7.2f  spent=$%-7.2f  util=%.1f%%"
                    % (
                        task["task_id"],
                        task["budget"],
                        task["spent"],
                        task["utilization_pct"],
                    )
                )
        lines.append("")

    return "\n".join(lines)
