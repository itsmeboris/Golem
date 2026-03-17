# pylint: disable=too-few-public-methods,redefined-outer-name
"""Tests for golem.health — HealthMonitor and HealthNotifier protocol."""

from unittest.mock import MagicMock, patch

import pytest

from golem.core.config import HealthConfig
from golem.core.live_state import LiveState
from golem.health import (
    ALERT_CONSECUTIVE_FAILURES,
    ALERT_DISK_USAGE,
    ALERT_HIGH_ERROR_RATE,
    ALERT_LABELS,
    ALERT_MERGE_QUEUE_BLOCKED,
    ALERT_QUEUE_DEPTH,
    ALERT_STALE_DAEMON,
    STATUS_DEGRADED,
    STATUS_HEALTHY,
    STATUS_UNHEALTHY,
    HealthMonitor,
    HealthNotifier,
    _compute_status,
)


@pytest.fixture(autouse=True)
def _reset_live_state():
    LiveState.reset()
    yield
    LiveState.reset()


@pytest.fixture
def config():
    return HealthConfig(
        enabled=True,
        check_interval_seconds=60,
        consecutive_failure_threshold=3,
        error_rate_threshold=0.5,
        error_rate_window_seconds=900,
        error_rate_min_tasks=4,
        queue_depth_threshold=5,
        stale_seconds=3600,
        alert_cooldown_seconds=900,
        disk_usage_threshold_gb=0,
    )


@pytest.fixture
def monitor(config):
    return HealthMonitor(config)


class TestHealthNotifierProtocol:
    def test_notifier_protocol_structural_subtyping(self):
        class MyNotifier:
            def notify_health_alert(self, alert_type, message, *, details=None):
                pass

        assert isinstance(MyNotifier(), HealthNotifier)

    def test_missing_method_fails_protocol_check(self):
        class BadNotifier:
            pass

        assert not isinstance(BadNotifier(), HealthNotifier)


class TestRecordTaskResult:
    def test_success_resets_consecutive_failures(self, monitor):
        monitor.record_task_result(False)
        monitor.record_task_result(False)
        monitor.record_task_result(True)
        assert monitor._consecutive_failures == 0

    def test_failure_increments_consecutive(self, monitor):
        monitor.record_task_result(False)
        monitor.record_task_result(False)
        assert monitor._consecutive_failures == 2

    def test_total_tasks_incremented(self, monitor):
        monitor.record_task_result(True)
        monitor.record_task_result(False)
        assert monitor._total_tasks == 2

    def test_total_failures_incremented(self, monitor):
        monitor.record_task_result(True)
        monitor.record_task_result(False)
        assert monitor._total_failures == 1

    def test_updates_last_activity(self, monitor):
        before = monitor._last_activity
        monitor.record_task_result(True)
        assert monitor._last_activity >= before

    def test_sliding_window_trim(self, monitor):
        # Inject an old entry (2 hours ago) directly
        old_ts = monitor._last_activity - 7200
        monitor._task_results.append((old_ts, True))
        monitor.record_task_result(True)
        # All results should be within the last hour
        cutoff = monitor._last_activity - 3600
        assert all(t > cutoff for t, _ in monitor._task_results)


class TestRecordPollError:
    def test_increments_poll_errors(self, monitor):
        monitor.record_poll_error()
        monitor.record_poll_error()
        assert monitor._poll_errors == 2

    def test_updates_last_activity(self, monitor):
        before = monitor._last_activity
        monitor.record_poll_error()
        assert monitor._last_activity >= before


class TestRecordPollSuccess:
    def test_updates_last_activity(self, monitor):
        before = monitor._last_activity
        monitor.record_poll_success()
        assert monitor._last_activity >= before


class TestCheckDisabled:
    def test_returns_empty_when_disabled(self, config):
        config.enabled = False
        mon = HealthMonitor(config)
        # Force conditions that would normally trigger alerts
        mon._consecutive_failures = 10
        assert not mon.check()


class TestCheckConsecutiveFailures:
    def test_triggers_when_at_threshold(self, monitor):
        monitor._consecutive_failures = 3
        alerts = monitor.check()
        types = [a["type"] for a in alerts]
        assert ALERT_CONSECUTIVE_FAILURES in types

    def test_no_alert_below_threshold(self, monitor):
        monitor._consecutive_failures = 2
        alerts = monitor.check()
        types = [a["type"] for a in alerts]
        assert ALERT_CONSECUTIVE_FAILURES not in types

    def test_alert_includes_value_and_threshold(self, monitor):
        monitor._consecutive_failures = 5
        alerts = monitor.check()
        alert = next(a for a in alerts if a["type"] == ALERT_CONSECUTIVE_FAILURES)
        assert alert["value"] == 5
        assert alert["threshold"] == 3


class TestCheckHighErrorRate:
    def test_triggers_at_threshold(self, config):
        # error_rate_min_tasks=4, threshold=0.5
        config.error_rate_min_tasks = 4
        mon = HealthMonitor(config)
        for _ in range(2):
            mon.record_task_result(True)
        for _ in range(2):
            mon.record_task_result(False)
        alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_HIGH_ERROR_RATE in types

    def test_no_alert_below_min_tasks(self, config):
        # Only 3 tasks, min_tasks=4 → not enough data
        config.error_rate_min_tasks = 4
        mon = HealthMonitor(config)
        mon.record_task_result(False)
        mon.record_task_result(False)
        mon.record_task_result(False)
        alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_HIGH_ERROR_RATE not in types

    def test_no_alert_below_rate_threshold(self, config):
        # 1 failure out of 4 = 25% < 50%
        config.error_rate_min_tasks = 4
        mon = HealthMonitor(config)
        mon.record_task_result(True)
        mon.record_task_result(True)
        mon.record_task_result(True)
        mon.record_task_result(False)
        alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_HIGH_ERROR_RATE not in types

    def test_alert_includes_rate_value(self, config):
        config.error_rate_min_tasks = 4
        mon = HealthMonitor(config)
        for _ in range(4):
            mon.record_task_result(False)
        alerts = mon.check()
        alert = next((a for a in alerts if a["type"] == ALERT_HIGH_ERROR_RATE), None)
        assert alert is not None
        assert alert["value"] == 1.0


class TestCheckQueueDepth:
    def test_triggers_when_queue_exceeds_threshold(self, config):
        config.queue_depth_threshold = 2
        mon = HealthMonitor(config)
        ls = LiveState.get()
        ls.enqueue("e1", "golem", "sonnet")
        ls.mark_queued("e1")
        ls.enqueue("e2", "golem", "sonnet")
        ls.mark_queued("e2")
        alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_QUEUE_DEPTH in types

    def test_no_alert_below_threshold(self, config):
        config.queue_depth_threshold = 10
        mon = HealthMonitor(config)
        ls = LiveState.get()
        ls.enqueue("e1", "golem", "sonnet")
        ls.mark_queued("e1")
        alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_QUEUE_DEPTH not in types

    def test_live_state_exception_is_swallowed(self, config):
        mon = HealthMonitor(config)
        with patch("golem.health.LiveState.get", side_effect=RuntimeError("boom")):
            alerts = mon.check()
        # Should not raise, queue_depth alert simply absent
        types = [a["type"] for a in alerts]
        assert ALERT_QUEUE_DEPTH not in types


class TestCheckStaleDaemon:
    def test_triggers_when_idle_exceeds_threshold(self, config):
        config.stale_seconds = 10
        mon = HealthMonitor(config)
        mon._last_activity = mon._last_activity - 20
        alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_STALE_DAEMON in types

    def test_no_alert_when_recently_active(self, config):
        config.stale_seconds = 3600
        mon = HealthMonitor(config)
        mon.record_poll_success()
        alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_STALE_DAEMON not in types

    def test_alert_includes_idle_seconds(self, config):
        config.stale_seconds = 5
        mon = HealthMonitor(config)
        mon._last_activity = mon._last_activity - 100
        alerts = mon.check()
        alert = next(a for a in alerts if a["type"] == ALERT_STALE_DAEMON)
        assert alert["value"] >= 100


class TestNotificationCooldown:
    def test_notification_sent_on_first_alert(self, config):
        notifier = MagicMock()
        mon = HealthMonitor(config, notifier=notifier)
        mon._consecutive_failures = 5
        mon.check()
        notifier.notify_health_alert.assert_called_once()

    def test_notification_not_resent_within_cooldown(self, config):
        config.alert_cooldown_seconds = 900
        notifier = MagicMock()
        mon = HealthMonitor(config, notifier=notifier)
        mon._consecutive_failures = 5
        mon.check()
        mon.check()
        assert notifier.notify_health_alert.call_count == 1

    def test_notification_resent_after_cooldown(self, config):
        config.alert_cooldown_seconds = 1
        notifier = MagicMock()
        mon = HealthMonitor(config, notifier=notifier)
        mon._consecutive_failures = 5
        mon.check()
        # Expire the cooldown by backdating the last_alert time
        mon._last_alert[ALERT_CONSECUTIVE_FAILURES] = (
            mon._last_alert[ALERT_CONSECUTIVE_FAILURES] - 2
        )
        mon.check()
        assert notifier.notify_health_alert.call_count == 2

    def test_notifier_exception_is_swallowed(self, config):
        notifier = MagicMock()
        notifier.notify_health_alert.side_effect = RuntimeError("send failed")
        mon = HealthMonitor(config, notifier=notifier)
        mon._consecutive_failures = 5
        # Should not raise
        mon.check()

    def test_no_notifier_no_error(self, monitor):
        monitor._consecutive_failures = 5
        # Should work fine without a notifier
        alerts = monitor.check()
        assert any(a["type"] == ALERT_CONSECUTIVE_FAILURES for a in alerts)

    def test_notification_passes_correct_args(self, config):
        notifier = MagicMock()
        mon = HealthMonitor(config, notifier=notifier)
        mon._consecutive_failures = 5
        mon.check()
        call_kwargs = notifier.notify_health_alert.call_args
        assert call_kwargs.kwargs["details"]["value"] == 5
        assert call_kwargs.kwargs["details"]["threshold"] == 3


class TestSnapshot:
    def test_returns_dict_with_expected_keys(self, monitor):
        snap = monitor.snapshot()
        for key in (
            "status",
            "healthy",
            "consecutive_failures",
            "error_rate",
            "error_rate_window",
            "total_tasks",
            "total_failures",
            "poll_errors",
            "idle_seconds",
            "heartbeat_age_seconds",
            "active_alerts",
        ):
            assert key in snap

    def test_healthy_true_when_no_alerts(self, monitor):
        snap = monitor.snapshot()
        assert snap["healthy"] is True
        assert snap["status"] == STATUS_HEALTHY

    def test_unhealthy_when_severe_alerts(self, monitor):
        monitor._consecutive_failures = 5
        snap = monitor.snapshot()
        assert snap["healthy"] is False
        assert snap["status"] == STATUS_UNHEALTHY

    def test_active_alerts_lists_types(self, monitor):
        monitor._consecutive_failures = 5
        snap = monitor.snapshot()
        assert ALERT_CONSECUTIVE_FAILURES in snap["active_alerts"]

    def test_error_rate_computed_correctly(self, monitor):
        monitor.record_task_result(True)
        monitor.record_task_result(False)
        snap = monitor.snapshot()
        assert snap["error_rate"] == 0.5

    def test_poll_errors_in_snapshot(self, monitor):
        monitor.record_poll_error()
        monitor.record_poll_error()
        snap = monitor.snapshot()
        assert snap["poll_errors"] == 2

    def test_snapshot_does_not_send_notifications(self, config):
        notifier = MagicMock()
        mon = HealthMonitor(config, notifier=notifier)
        mon._consecutive_failures = 5
        mon.snapshot()
        notifier.notify_health_alert.assert_not_called()

    def test_disabled_monitor_is_healthy(self, config):
        config.enabled = False
        mon = HealthMonitor(config)
        mon._consecutive_failures = 100
        snap = mon.snapshot()
        assert snap["healthy"] is True
        assert snap["status"] == STATUS_HEALTHY
        assert snap["active_alerts"] == []

    def test_error_rate_window_shows_actual_count(self, monitor):
        monitor.record_task_result(True)
        snap = monitor.snapshot()
        assert snap["error_rate_window"] == 1

    def test_idle_seconds_is_non_negative(self, monitor):
        snap = monitor.snapshot()
        assert snap["idle_seconds"] >= 0

    def test_heartbeat_age_in_snapshot(self, monitor):
        monitor.record_heartbeat()
        snap = monitor.snapshot()
        assert snap["heartbeat_age_seconds"] >= 0
        assert snap["heartbeat_age_seconds"] < 5


class TestComputeStatus:
    def test_no_alerts_is_healthy(self):
        assert _compute_status([]) == STATUS_HEALTHY

    def test_queue_depth_only_is_degraded(self):
        alerts = [{"type": ALERT_QUEUE_DEPTH}]
        assert _compute_status(alerts) == STATUS_DEGRADED

    def test_high_error_rate_only_is_degraded(self):
        alerts = [{"type": ALERT_HIGH_ERROR_RATE}]
        assert _compute_status(alerts) == STATUS_DEGRADED

    def test_consecutive_failures_is_unhealthy(self):
        alerts = [{"type": ALERT_CONSECUTIVE_FAILURES}]
        assert _compute_status(alerts) == STATUS_UNHEALTHY

    def test_stale_daemon_is_unhealthy(self):
        alerts = [{"type": ALERT_STALE_DAEMON}]
        assert _compute_status(alerts) == STATUS_UNHEALTHY

    def test_disk_usage_is_unhealthy(self):
        alerts = [{"type": ALERT_DISK_USAGE}]
        assert _compute_status(alerts) == STATUS_UNHEALTHY

    def test_mixed_severe_and_mild_is_unhealthy(self):
        alerts = [{"type": ALERT_QUEUE_DEPTH}, {"type": ALERT_STALE_DAEMON}]
        assert _compute_status(alerts) == STATUS_UNHEALTHY


class TestHeartbeat:
    def test_record_heartbeat_updates_timestamp(self, monitor):
        before = monitor._last_heartbeat
        monitor.record_heartbeat()
        assert monitor._last_heartbeat >= before

    def test_check_interval_property(self, config):
        config.check_interval_seconds = 42
        mon = HealthMonitor(config)
        assert mon.check_interval == 42


class TestDiskUsageAlert:
    def test_triggers_when_over_threshold(self, config):
        config.disk_usage_threshold_gb = 1.0
        mon = HealthMonitor(config)
        mock_usage = MagicMock()
        mock_usage.total = 100 * 1024**3  # 100 GB
        mock_usage.free = 10 * 1024**3  # 10 GB free → 90 GB used
        with patch("golem.health.shutil.disk_usage", return_value=mock_usage):
            alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_DISK_USAGE in types

    def test_no_alert_when_under_threshold(self, config):
        config.disk_usage_threshold_gb = 200.0
        mon = HealthMonitor(config)
        mock_usage = MagicMock()
        mock_usage.total = 100 * 1024**3
        mock_usage.free = 10 * 1024**3
        with patch("golem.health.shutil.disk_usage", return_value=mock_usage):
            alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_DISK_USAGE not in types

    def test_no_alert_when_threshold_zero(self, config):
        config.disk_usage_threshold_gb = 0
        mon = HealthMonitor(config)
        alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_DISK_USAGE not in types

    def test_disk_usage_exception_swallowed(self, config):
        config.disk_usage_threshold_gb = 1.0
        mon = HealthMonitor(config)
        with patch("golem.health.shutil.disk_usage", side_effect=OSError("nope")):
            alerts = mon.check()
        types = [a["type"] for a in alerts]
        assert ALERT_DISK_USAGE not in types


class TestCheckMergeQueueBlocked:
    def test_alert_when_deferred_exceeds_threshold(self, monitor):
        """Fire alert when deferred merge count >= threshold."""
        monitor._merge_deferred_count_fn = lambda: 6
        monitor._config.merge_deferred_threshold = 5
        alerts = monitor._compute_alerts()
        types = [a["type"] for a in alerts]
        assert ALERT_MERGE_QUEUE_BLOCKED in types
        alert = next(a for a in alerts if a["type"] == ALERT_MERGE_QUEUE_BLOCKED)
        assert alert["value"] == 6
        assert alert["threshold"] == 5

    def test_no_alert_when_below_threshold(self, monitor):
        """No alert when deferred count is below threshold."""
        monitor._merge_deferred_count_fn = lambda: 2
        monitor._config.merge_deferred_threshold = 5
        alerts = monitor._compute_alerts()
        types = [a["type"] for a in alerts]
        assert ALERT_MERGE_QUEUE_BLOCKED not in types

    def test_no_alert_when_fn_is_none(self, monitor):
        """No alert when no merge queue fn is configured."""
        monitor._merge_deferred_count_fn = None
        alerts = monitor._compute_alerts()
        types = [a["type"] for a in alerts]
        assert ALERT_MERGE_QUEUE_BLOCKED not in types

    def test_fn_exception_is_caught(self, monitor):
        """Exception in count fn is caught gracefully."""

        def _boom():
            raise RuntimeError("broken")

        monitor._merge_deferred_count_fn = _boom
        alerts = monitor._compute_alerts()
        types = [a["type"] for a in alerts]
        assert ALERT_MERGE_QUEUE_BLOCKED not in types

    def test_merge_queue_blocked_is_severe(self):
        """merge_queue_blocked should make status unhealthy."""
        alerts = [
            {
                "type": ALERT_MERGE_QUEUE_BLOCKED,
                "message": "x",
                "value": 6,
                "threshold": 5,
            }
        ]
        status = _compute_status(alerts)
        assert status == STATUS_UNHEALTHY

    def test_alert_at_exact_threshold(self, monitor):
        """Alert fires when count equals the threshold (>= not just >)."""
        monitor._merge_deferred_count_fn = lambda: 5
        monitor._config.merge_deferred_threshold = 5
        alerts = monitor._compute_alerts()
        types = [a["type"] for a in alerts]
        assert ALERT_MERGE_QUEUE_BLOCKED in types


class TestAlertLabels:
    def test_all_constants_have_labels(self):
        for const in (
            ALERT_CONSECUTIVE_FAILURES,
            ALERT_HIGH_ERROR_RATE,
            ALERT_QUEUE_DEPTH,
            ALERT_STALE_DAEMON,
            ALERT_DISK_USAGE,
            ALERT_MERGE_QUEUE_BLOCKED,
        ):
            assert const in ALERT_LABELS


class TestParseHealthConfig:
    def test_defaults(self):
        from golem.core.config import _parse_health_config

        cfg = _parse_health_config({})
        assert cfg.enabled is True
        assert cfg.check_interval_seconds == 60
        assert cfg.consecutive_failure_threshold == 3
        assert cfg.error_rate_threshold == 0.5
        assert cfg.error_rate_window_seconds == 900
        assert cfg.error_rate_min_tasks == 4
        assert cfg.queue_depth_threshold == 10
        assert cfg.stale_seconds == 3600
        assert cfg.alert_cooldown_seconds == 900
        assert cfg.disk_usage_threshold_gb == 0
        assert cfg.merge_deferred_threshold == 5

    def test_custom_values(self):
        from golem.core.config import _parse_health_config

        cfg = _parse_health_config(
            {
                "enabled": False,
                "check_interval_seconds": 30,
                "consecutive_failure_threshold": 5,
                "error_rate_threshold": 0.8,
                "error_rate_window_seconds": 1800,
                "error_rate_min_tasks": 10,
                "queue_depth_threshold": 20,
                "stale_seconds": 1800,
                "alert_cooldown_seconds": 300,
                "disk_usage_threshold_gb": 50.0,
            }
        )
        assert cfg.enabled is False
        assert cfg.check_interval_seconds == 30
        assert cfg.consecutive_failure_threshold == 5
        assert cfg.error_rate_threshold == 0.8
        assert cfg.error_rate_window_seconds == 1800
        assert cfg.error_rate_min_tasks == 10
        assert cfg.queue_depth_threshold == 20
        assert cfg.stale_seconds == 1800
        assert cfg.alert_cooldown_seconds == 300
        assert cfg.disk_usage_threshold_gb == 50.0


class TestConfigIntegration:
    def test_config_has_health_field(self):
        from golem.core.config import Config

        cfg = Config()
        assert hasattr(cfg, "health")
        assert isinstance(cfg.health, HealthConfig)

    def test_load_config_parses_health(self, tmp_path, monkeypatch):
        from golem.core.config import load_config

        config_content = """\
health:
  enabled: false
  consecutive_failure_threshold: 7
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content, encoding="utf-8")
        monkeypatch.setattr("golem.core.config.load_dotenv", lambda p: None)
        cfg = load_config(config_file)
        assert cfg.health.enabled is False
        assert cfg.health.consecutive_failure_threshold == 7
