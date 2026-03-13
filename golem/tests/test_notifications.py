# pylint: disable=too-few-public-methods
"""Tests for golem.notifications — Teams card builders."""

import pytest

from golem.notifications import (
    _fmt_duration,
    build_health_alert_card,
    build_task_activity_card,
    build_task_completed_card,
    build_task_escalation_card,
    build_task_failure_card,
    build_task_started_card,
)


class TestFmtDuration:
    def test_seconds_only(self):
        assert _fmt_duration(45) == "45s"

    def test_minutes_and_seconds(self):
        assert _fmt_duration(150) == "2m 30s"

    def test_zero(self):
        assert _fmt_duration(0) == "0s"


class TestBuildTaskStartedCard:
    def test_structure(self):
        card = build_task_started_card(123, "Fix the bug")
        assert card["type"] == "AdaptiveCard"
        body_text = str(card["body"])
        assert "123" in body_text


class TestBuildTaskCompletedCard:
    def test_basic(self):
        card = build_task_completed_card(
            123,
            "Fix the bug",
            total_cost_usd=1.50,
            duration_s=120,
            steps=5,
        )
        body_text = str(card["body"])
        assert "123" in body_text

    def test_with_verdict_and_commit(self):
        card = build_task_completed_card(
            42,
            "Refactor",
            total_cost_usd=2.0,
            duration_s=300,
            steps=10,
            verdict="PASS",
            confidence=0.95,
            commit_sha="abc123",
            retry_count=2,
            fix_iteration=3,
        )
        body_str = str(card)
        assert "PASS" in body_str
        assert "abc123" in body_str
        assert "Full retries" in body_str
        assert "Fix iterations" in body_str

    def test_with_concerns(self):
        card = build_task_completed_card(
            1,
            "Task",
            total_cost_usd=0.5,
            concerns=["issue 1", "issue 2"],
        )
        body_str = str(card)
        assert "issue 1" in body_str


class TestBuildTaskActivityCard:
    def test_structure(self):
        card = build_task_activity_card(
            99,
            "Working on it",
            "Analyzing code",
            60.0,
            3,
        )
        body_str = str(card)
        assert "In Progress" in body_str
        assert "99" in body_str


class TestBuildTaskFailureCard:
    def test_structure(self):
        card = build_task_failure_card(
            88,
            "Broken task",
            "TimeoutError",
            cost_usd=0.75,
            duration_s=1800,
        )
        body_str = str(card)
        assert "Failed" in body_str
        assert "TimeoutError" in body_str

    def test_with_verdict(self):
        card = build_task_failure_card(
            88,
            "Broken task",
            "TimeoutError",
            cost_usd=0.75,
            duration_s=1800,
            verdict="FAIL",
        )
        body_str = str(card)
        assert "FAIL" in body_str


class TestBuildTaskEscalationCard:
    def test_structure(self):
        card = build_task_escalation_card(
            77,
            "Needs review",
            "PARTIAL",
            "Not quite right",
            concerns=["Missing test"],
            cost_usd=1.0,
            duration_s=600,
        )
        body_str = str(card)
        assert "Needs Review" in body_str
        assert "PARTIAL" in body_str
        assert "Missing test" in body_str


class TestBuildHealthAlertCard:
    @pytest.mark.parametrize(
        "alert_type, expected_label",
        [
            ("consecutive_failures", "Consecutive Failures"),
            ("high_error_rate", "High Error Rate"),
            ("queue_depth", "Queue Backlog"),
            ("stale_daemon", "Daemon Idle"),
            ("unknown_type", "Unknown Type"),
        ],
    )
    def test_known_and_unknown_labels(self, alert_type, expected_label):
        card = build_health_alert_card(alert_type, "Something went wrong")
        body_str = str(card)
        assert expected_label in body_str

    def test_structure_no_details(self):
        card = build_health_alert_card("queue_depth", "Queue is too deep")
        assert card["type"] == "AdaptiveCard"
        body_str = str(card)
        assert "Health Alert" in body_str
        assert "Queue is too deep" in body_str

    def test_details_with_value_and_threshold(self):
        card = build_health_alert_card(
            "high_error_rate",
            "Error rate exceeded",
            details={"value": 0.42, "threshold": 0.10},
        )
        body_str = str(card)
        assert "0.42" in body_str
        assert "0.1" in body_str

    def test_details_with_none_value_omitted(self):
        card = build_health_alert_card(
            "consecutive_failures",
            "Too many failures",
            details={"value": None, "threshold": 5},
        )
        body_str = str(card)
        assert "Current" not in body_str
        assert "5" in body_str

    def test_details_with_none_threshold_omitted(self):
        card = build_health_alert_card(
            "consecutive_failures",
            "Too many failures",
            details={"value": 10, "threshold": None},
        )
        body_str = str(card)
        assert "10" in body_str
        assert "Threshold" not in body_str

    def test_details_both_none_no_fact_set(self):
        card = build_health_alert_card(
            "stale_daemon",
            "Daemon is idle",
            details={"value": None, "threshold": None},
        )
        body_str = str(card)
        assert "Current" not in body_str
        assert "Threshold" not in body_str

    def test_no_details_no_fact_set(self):
        card = build_health_alert_card("stale_daemon", "Daemon is idle", details=None)
        # body should have only header and text block (2 items)
        assert len(card["body"]) == 2
