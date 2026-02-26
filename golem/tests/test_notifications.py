# pylint: disable=too-few-public-methods
"""Tests for golem.notifications — Teams card builders."""

from golem.notifications import (
    _fmt_duration,
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
        )
        body_str = str(card)
        assert "PASS" in body_str
        assert "abc123" in body_str
        assert "Retries" in body_str

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
