"""Daemon health monitoring with threshold-based alerting.

Tracks task success/failure rates, queue depth, disk usage, and consecutive
failures.  Fires notifications via the configured Notifier when thresholds
are breached.
"""

from __future__ import annotations

import logging
import shutil
import time
from typing import Any, Callable, Protocol, runtime_checkable

from .core.config import HealthConfig
from .types import AlertDict
from .core.live_state import LiveState

logger = logging.getLogger("golem.health")

# -- Alert type constants (shared across health, notifications, notifiers) ----
ALERT_CONSECUTIVE_FAILURES = "consecutive_failures"
ALERT_HIGH_ERROR_RATE = "high_error_rate"
ALERT_QUEUE_DEPTH = "queue_depth"
ALERT_STALE_DAEMON = "stale_daemon"
ALERT_DISK_USAGE = "disk_usage"
ALERT_MERGE_QUEUE_BLOCKED = "merge_queue_blocked"

ALERT_LABELS: dict[str, str] = {
    ALERT_CONSECUTIVE_FAILURES: "Consecutive Failures",
    ALERT_HIGH_ERROR_RATE: "High Error Rate",
    ALERT_QUEUE_DEPTH: "Queue Backlog",
    ALERT_STALE_DAEMON: "Daemon Idle",
    ALERT_DISK_USAGE: "Disk Usage",
    ALERT_MERGE_QUEUE_BLOCKED: "Merge Queue Blocked",
}

# -- Health status tiers ------------------------------------------------------
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_UNHEALTHY = "unhealthy"


@runtime_checkable
class HealthNotifier(Protocol):
    """Minimal protocol for health alert delivery."""

    def notify_health_alert(
        self,
        alert_type: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None: ...  # pragma: no cover


class HealthMonitor:
    """Monitors daemon health metrics and fires alerts on threshold breaches."""

    def __init__(
        self,
        config: HealthConfig,
        notifier: HealthNotifier | None = None,
        merge_deferred_count_fn: Callable[[], int] | None = None,
    ):
        self._config = config
        self._notifier = notifier
        self._merge_deferred_count_fn = merge_deferred_count_fn
        self._task_results: list[tuple[float, bool]] = []  # (timestamp, success)
        self._consecutive_failures: int = 0
        self._last_heartbeat: float = time.time()
        self._last_activity: float = time.time()
        self._last_alert: dict[str, float] = {}  # alert_type -> last_fired_time
        self._poll_errors: int = 0
        self._total_tasks: int = 0
        self._total_failures: int = 0

    @property
    def check_interval(self) -> int:
        """Public access to the health check interval (seconds)."""
        return self._config.check_interval_seconds

    def record_heartbeat(self) -> None:
        """Record a tick-loop heartbeat (called every tick regardless of work)."""
        self._last_heartbeat = time.time()

    def record_task_result(self, success: bool) -> None:
        """Record a task completion (success or failure)."""
        now = time.time()
        self._task_results.append((now, success))
        self._last_activity = now
        self._total_tasks += 1

        if success:
            self._consecutive_failures = 0
        else:
            self._consecutive_failures += 1
            self._total_failures += 1

        cutoff = now - 3600
        self._task_results = [(t, s) for t, s in self._task_results if t > cutoff]

    def record_poll_error(self) -> None:
        """Record a polling error."""
        self._poll_errors += 1
        self._last_activity = time.time()

    def record_poll_success(self) -> None:
        """Record a successful poll cycle."""
        self._last_activity = time.time()

    def _compute_alerts(self) -> list[AlertDict]:
        """Compute all active alerts without side effects."""
        alerts: list[AlertDict] = []
        now = time.time()

        if self._consecutive_failures >= self._config.consecutive_failure_threshold:
            alerts.append(
                {
                    "type": ALERT_CONSECUTIVE_FAILURES,
                    "message": (
                        f"{self._consecutive_failures} consecutive task failures"
                    ),
                    "value": self._consecutive_failures,
                    "threshold": self._config.consecutive_failure_threshold,
                }
            )

        # Time-based error rate: failures within the last N seconds
        window_seconds = self._config.error_rate_window_seconds
        cutoff = now - window_seconds
        recent = [(t, s) for t, s in self._task_results if t > cutoff]
        if len(recent) >= self._config.error_rate_min_tasks:
            failures = sum(1 for _, s in recent if not s)
            rate = failures / len(recent)
            if rate >= self._config.error_rate_threshold:
                alerts.append(
                    {
                        "type": ALERT_HIGH_ERROR_RATE,
                        "message": (
                            f"Error rate {rate:.0%} over last "
                            f"{window_seconds // 60}min"
                        ),
                        "value": round(rate, 3),
                        "threshold": self._config.error_rate_threshold,
                    }
                )

        try:
            live = LiveState.get()
            snap = live.snapshot()
            queue_depth = snap.get("queue_depth", 0)
            if queue_depth >= self._config.queue_depth_threshold:
                alerts.append(
                    {
                        "type": ALERT_QUEUE_DEPTH,
                        "message": f"Queue depth {queue_depth} exceeds threshold",
                        "value": queue_depth,
                        "threshold": self._config.queue_depth_threshold,
                    }
                )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Failed to read LiveState for health check", exc_info=True)

        idle_seconds = now - self._last_activity
        if idle_seconds >= self._config.stale_seconds:
            alerts.append(
                {
                    "type": ALERT_STALE_DAEMON,
                    "message": (
                        f"No activity for {int(idle_seconds)}s "
                        f"(threshold: {self._config.stale_seconds}s)"
                    ),
                    "value": round(idle_seconds, 0),
                    "threshold": self._config.stale_seconds,
                }
            )

        # Disk usage check
        if self._config.disk_usage_threshold_gb > 0:
            try:
                from .core.config import DATA_DIR

                usage = shutil.disk_usage(DATA_DIR)
                used_gb = round((usage.total - usage.free) / (1024**3), 2)
                if used_gb >= self._config.disk_usage_threshold_gb:
                    alerts.append(
                        {
                            "type": ALERT_DISK_USAGE,
                            "message": (
                                f"Disk usage {used_gb}GB exceeds "
                                f"{self._config.disk_usage_threshold_gb}GB threshold"
                            ),
                            "value": used_gb,
                            "threshold": self._config.disk_usage_threshold_gb,
                        }
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Failed to check disk usage", exc_info=True)

        if self._merge_deferred_count_fn is not None:
            try:
                deferred_count = self._merge_deferred_count_fn()
                if deferred_count >= self._config.merge_deferred_threshold:
                    alerts.append(
                        {
                            "type": ALERT_MERGE_QUEUE_BLOCKED,
                            "message": (
                                f"{deferred_count} deferred merges exceed threshold"
                            ),
                            "value": deferred_count,
                            "threshold": self._config.merge_deferred_threshold,
                        }
                    )
            except Exception:  # pylint: disable=broad-exception-caught
                logger.debug("Failed to read merge queue status", exc_info=True)

        return alerts

    def check(self) -> list[AlertDict]:
        """Run all health checks and return any triggered alerts.

        Also sends notifications for new alerts (respecting cooldown).
        """
        if not self._config.enabled:
            return []

        now = time.time()
        alerts = self._compute_alerts()

        for alert in alerts:
            self._maybe_notify(alert, now)

        return alerts

    def _maybe_notify(self, alert: AlertDict, now: float) -> None:
        """Send a notification if cooldown has elapsed for this alert type."""
        if self._notifier is None:
            return
        alert_type = alert["type"]
        last = self._last_alert.get(alert_type, 0.0)
        if now - last < self._config.alert_cooldown_seconds:
            return
        self._last_alert[alert_type] = now
        try:
            self._notifier.notify_health_alert(
                alert_type=alert_type,
                message=alert["message"],
                details={
                    "value": alert.get("value"),
                    "threshold": alert.get("threshold"),
                },
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.warning("Failed to send health alert: %s", alert_type, exc_info=True)

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict of health metrics."""
        now = time.time()
        window_seconds = self._config.error_rate_window_seconds
        cutoff = now - window_seconds
        recent = [(t, s) for t, s in self._task_results if t > cutoff]
        error_rate = 0.0
        if recent:
            error_rate = sum(1 for _, s in recent if not s) / len(recent)

        if self._config.enabled:
            alerts = self._compute_alerts()
        else:
            alerts = []

        status = _compute_status(alerts)

        return {
            "status": status,
            "healthy": status == STATUS_HEALTHY,
            "consecutive_failures": self._consecutive_failures,
            "error_rate": round(error_rate, 3),
            "error_rate_window": len(recent),
            "total_tasks": self._total_tasks,
            "total_failures": self._total_failures,
            "poll_errors": self._poll_errors,
            "idle_seconds": round(now - self._last_activity, 1),
            "heartbeat_age_seconds": round(now - self._last_heartbeat, 1),
            "active_alerts": [a["type"] for a in alerts],
        }


def _compute_status(alerts: list[AlertDict]) -> str:
    """Derive three-tier status from active alerts.

    - healthy: no alerts
    - degraded: only queue_depth or high_error_rate alerts
    - unhealthy: consecutive_failures, stale_daemon, or disk_usage alerts
    """
    if not alerts:
        return STATUS_HEALTHY
    severe = {
        ALERT_CONSECUTIVE_FAILURES,
        ALERT_STALE_DAEMON,
        ALERT_DISK_USAGE,
        ALERT_MERGE_QUEUE_BLOCKED,
    }
    if any(a["type"] in severe for a in alerts):
        return STATUS_UNHEALTHY
    return STATUS_DEGRADED
