# pylint: disable=too-few-public-methods
"""Tests for golem.notifications — Teams card builders."""

import pytest

from golem.notifications import (
    _fmt_duration,
    build_health_alert_card,
    build_task_completed_card,
    build_task_escalation_card,
    build_task_failure_card,
    build_task_started_card,
)


def _get_facts(card):
    """Extract {title: value} dict from the first FactSet in card body."""
    for item in card["body"]:
        if item.get("type") == "FactSet":
            return {f["title"]: f["value"] for f in item["facts"]}
    return {}


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
        assert "123" in card["body"][0]["text"]  # header
        assert card["body"][1]["text"] == "Fix the bug"  # subject


class TestBuildTaskCompletedCard:
    def test_basic(self):
        card = build_task_completed_card(
            123,
            "Fix the bug",
            total_cost_usd=1.50,
            duration_s=120,
            steps=5,
        )
        assert "123" in card["body"][0]["text"]
        assert card["body"][1]["text"] == "Fix the bug"
        facts = _get_facts(card)
        assert facts["Cost"] == "$1.50"
        assert facts["Steps"] == "5"

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
        assert "42" in card["body"][0]["text"]
        assert card["body"][1]["text"] == "Refactor"
        facts = _get_facts(card)
        assert facts["Verdict"] == "PASS (95%)"
        assert facts["Commit"] == "abc123"
        assert facts["Full retries"] == "2"
        assert facts["Fix iterations"] == "3"

    def test_with_concerns(self):
        card = build_task_completed_card(
            1,
            "Task",
            total_cost_usd=0.5,
            concerns=["issue 1", "issue 2"],
        )
        concerns_block = next(
            item
            for item in card["body"]
            if item.get("type") == "TextBlock" and "Concerns" in item.get("text", "")
        )
        assert "issue 1" in concerns_block["text"]
        assert "issue 2" in concerns_block["text"]


class TestBuildTaskFailureCard:
    def test_structure(self):
        card = build_task_failure_card(
            88,
            "Broken task",
            "TimeoutError",
            cost_usd=0.75,
            duration_s=1800,
        )
        assert "Failed" in card["body"][0]["text"]
        assert "88" in card["body"][0]["text"]
        facts = _get_facts(card)
        assert facts["Error"] == "TimeoutError"
        assert facts["Cost"] == "$0.75"
        assert facts["Duration"] == "30m 0s"

    def test_with_verdict(self):
        card = build_task_failure_card(
            88,
            "Broken task",
            "TimeoutError",
            cost_usd=0.75,
            duration_s=1800,
            verdict="FAIL",
        )
        facts = _get_facts(card)
        assert facts["Verdict"] == "FAIL"


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
        assert "Needs Review" in card["body"][0]["text"]
        assert "77" in card["body"][0]["text"]
        facts = _get_facts(card)
        assert facts["Verdict"] == "PARTIAL"
        concerns_block = next(
            item
            for item in card["body"]
            if item.get("type") == "TextBlock" and "Concerns" in item.get("text", "")
        )
        assert "Missing test" in concerns_block["text"]


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
        assert expected_label in card["body"][0]["text"]

    def test_structure_no_details(self):
        card = build_health_alert_card("queue_depth", "Queue is too deep")
        assert card["type"] == "AdaptiveCard"
        assert "Health Alert" in card["body"][0]["text"]
        assert card["body"][1]["text"] == "Queue is too deep"

    def test_details_with_value_and_threshold(self):
        card = build_health_alert_card(
            "high_error_rate",
            "Error rate exceeded",
            details={"value": 0.42, "threshold": 0.10},
        )
        facts = _get_facts(card)
        assert facts["Current"] == "0.42"
        assert facts["Threshold"] == "0.1"

    def test_details_with_none_value_omitted(self):
        card = build_health_alert_card(
            "consecutive_failures",
            "Too many failures",
            details={"value": None, "threshold": 5},
        )
        facts = _get_facts(card)
        assert "Current" not in facts
        assert facts["Threshold"] == "5"

    def test_details_with_none_threshold_omitted(self):
        card = build_health_alert_card(
            "consecutive_failures",
            "Too many failures",
            details={"value": 10, "threshold": None},
        )
        facts = _get_facts(card)
        assert facts["Current"] == "10"
        assert "Threshold" not in facts

    def test_details_both_none_no_fact_set(self):
        card = build_health_alert_card(
            "stale_daemon",
            "Daemon is idle",
            details={"value": None, "threshold": None},
        )
        assert _get_facts(card) == {}

    def test_no_details_no_fact_set(self):
        card = build_health_alert_card("stale_daemon", "Daemon is idle", details=None)
        # body should have only header and text block (2 items)
        assert len(card["body"]) == 2
