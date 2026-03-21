"""Tests for cost analytics module."""

import pytest

from golem.cost_analytics import (
    _extract_retry_count,
    compute_cost_analytics,
)


def _make_run(
    verdict="PASS",
    cost_usd=1.0,
    started_at="2024-01-15T10:00:00",
    actions_taken=None,
):
    return {
        "verdict": verdict,
        "cost_usd": cost_usd,
        "started_at": started_at,
        "actions_taken": actions_taken or [],
    }


def _make_session(task_id=1, budget=10.0, spent=5.0, state="completed"):
    from golem.orchestrator import TaskSession, TaskSessionState

    session = TaskSession(parent_issue_id=task_id)
    session.budget_usd = budget
    session.total_cost_usd = spent
    session.state = TaskSessionState(state)
    return session


class TestExtractRetryCount:
    @pytest.mark.parametrize(
        "actions_taken,expected",
        [
            ([], 0),
            (["retries:0"], 0),
            (["retries:1"], 1),
            (["retries:2"], 2),
            (["retries:3"], 3),
            (["retries:5"], 5),
            (["verdict:PASS", "retries:2"], 2),
            (["retries:1", "verdict:FAIL"], 1),
            (["verdict:PASS"], 0),
            (None, 0),
        ],
    )
    def test_extracts_retry_count(self, actions_taken, expected):
        run = {"actions_taken": actions_taken or []}
        assert _extract_retry_count(run) == expected

    def test_missing_actions_taken_defaults_to_zero(self):
        assert _extract_retry_count({}) == 0

    def test_malformed_retries_value_defaults_to_zero(self):
        """A 'retries:' entry with a non-integer value is silently ignored."""
        run = {"actions_taken": ["retries:notanumber"]}
        assert _extract_retry_count(run) == 0

    def test_malformed_retries_value_logs_debug(self, caplog):
        """A malformed 'retries:' entry triggers a debug log message."""
        import logging

        run = {"actions_taken": ["retries:notanumber"]}
        with caplog.at_level(logging.DEBUG, logger="golem.cost_analytics"):
            result = _extract_retry_count(run)

        assert result == 0
        assert any(
            "Failed to parse retry count" in r.message and r.levelno == logging.DEBUG
            for r in caplog.records
        )


class TestComputeCostAnalyticsEmpty:
    def test_empty_runs_returns_defaults(self):
        result = compute_cost_analytics([])
        assert result["cost_over_time"] == []
        assert result["cost_by_verdict"] == {}
        assert result["cost_per_retry"] == {}
        assert result["budget_utilization"] is None
        assert result["summary"]["total_cost"] == 0.0
        assert result["summary"]["total_runs"] == 0
        assert result["summary"]["avg_cost_per_run"] == 0.0
        assert result["summary"]["max_cost_run"] == 0.0
        assert result["summary"]["min_cost_run"] == 0.0

    def test_none_sessions_returns_no_budget_utilization(self):
        result = compute_cost_analytics([], sessions=None)
        assert result["budget_utilization"] is None

    def test_empty_sessions_returns_no_budget_utilization(self):
        result = compute_cost_analytics([], sessions={})
        assert result["budget_utilization"] is None


class TestComputeCostOverTime:
    def test_single_run_single_date(self):
        runs = [_make_run(cost_usd=5.0, started_at="2024-01-15T10:00:00")]
        result = compute_cost_analytics(runs)
        assert len(result["cost_over_time"]) == 1
        entry = result["cost_over_time"][0]
        assert entry["date"] == "2024-01-15"
        assert entry["total_cost"] == 5.0
        assert entry["run_count"] == 1
        assert entry["avg_cost"] == 5.0

    def test_multiple_runs_same_date_grouped(self):
        runs = [
            _make_run(cost_usd=3.0, started_at="2024-01-15T08:00:00"),
            _make_run(cost_usd=7.0, started_at="2024-01-15T14:00:00"),
        ]
        result = compute_cost_analytics(runs)
        assert len(result["cost_over_time"]) == 1
        entry = result["cost_over_time"][0]
        assert entry["total_cost"] == 10.0
        assert entry["run_count"] == 2
        assert entry["avg_cost"] == 5.0

    def test_multiple_dates_sorted_chronologically(self):
        runs = [
            _make_run(cost_usd=2.0, started_at="2024-01-17T10:00:00"),
            _make_run(cost_usd=4.0, started_at="2024-01-15T10:00:00"),
            _make_run(cost_usd=6.0, started_at="2024-01-16T10:00:00"),
        ]
        result = compute_cost_analytics(runs)
        dates = [e["date"] for e in result["cost_over_time"]]
        assert dates == ["2024-01-15", "2024-01-16", "2024-01-17"]

    def test_run_with_missing_started_at_skipped(self):
        runs = [
            {"verdict": "PASS", "cost_usd": 5.0, "actions_taken": []},
            _make_run(cost_usd=3.0, started_at="2024-01-15T10:00:00"),
        ]
        result = compute_cost_analytics(runs)
        assert len(result["cost_over_time"]) == 1
        assert result["cost_over_time"][0]["total_cost"] == 3.0

    def test_run_with_empty_started_at_skipped(self):
        runs = [
            _make_run(cost_usd=5.0, started_at=""),
            _make_run(cost_usd=3.0, started_at="2024-01-15T10:00:00"),
        ]
        result = compute_cost_analytics(runs)
        assert len(result["cost_over_time"]) == 1


class TestComputeCostByVerdict:
    @pytest.mark.parametrize(
        "verdict,cost",
        [
            ("PASS", 5.0),
            ("PARTIAL", 8.0),
            ("FAIL", 10.0),
        ],
    )
    def test_single_verdict(self, verdict, cost):
        runs = [_make_run(verdict=verdict, cost_usd=cost)]
        result = compute_cost_analytics(runs)
        assert verdict in result["cost_by_verdict"]
        entry = result["cost_by_verdict"][verdict]
        assert entry["count"] == 1
        assert entry["total_cost"] == cost
        assert entry["avg_cost"] == cost

    def test_multiple_verdicts(self):
        runs = [
            _make_run(verdict="PASS", cost_usd=4.0),
            _make_run(verdict="PASS", cost_usd=6.0),
            _make_run(verdict="FAIL", cost_usd=10.0),
        ]
        result = compute_cost_analytics(runs)
        assert result["cost_by_verdict"]["PASS"]["count"] == 2
        assert result["cost_by_verdict"]["PASS"]["total_cost"] == 10.0
        assert result["cost_by_verdict"]["PASS"]["avg_cost"] == 5.0
        assert result["cost_by_verdict"]["FAIL"]["count"] == 1
        assert result["cost_by_verdict"]["FAIL"]["total_cost"] == 10.0

    def test_verdict_normalized_to_uppercase(self):
        runs = [_make_run(verdict="pass", cost_usd=5.0)]
        result = compute_cost_analytics(runs)
        assert "PASS" in result["cost_by_verdict"]

    def test_missing_verdict_grouped_as_unknown(self):
        runs = [
            {"cost_usd": 5.0, "actions_taken": [], "started_at": "2024-01-15T10:00:00"}
        ]
        result = compute_cost_analytics(runs)
        # Missing verdict should be grouped under empty string or some key
        assert len(result["cost_by_verdict"]) == 1


class TestComputeCostPerRetry:
    @pytest.mark.parametrize(
        "retries,expected_bucket",
        [
            (0, "0"),
            (1, "1"),
            (2, "2"),
            (3, "3+"),
            (4, "3+"),
            (10, "3+"),
        ],
    )
    def test_retry_bucketing(self, retries, expected_bucket):
        actions = [f"retries:{retries}"]
        runs = [_make_run(cost_usd=5.0, actions_taken=actions)]
        result = compute_cost_analytics(runs)
        assert expected_bucket in result["cost_per_retry"]
        assert result["cost_per_retry"][expected_bucket]["count"] == 1

    def test_multiple_runs_same_retry_bucket(self):
        runs = [
            _make_run(cost_usd=3.0, actions_taken=["retries:1"]),
            _make_run(cost_usd=7.0, actions_taken=["retries:1"]),
        ]
        result = compute_cost_analytics(runs)
        assert result["cost_per_retry"]["1"]["count"] == 2
        assert result["cost_per_retry"]["1"]["total_cost"] == 10.0
        assert result["cost_per_retry"]["1"]["avg_cost"] == 5.0

    def test_no_retry_action_defaults_to_zero_bucket(self):
        runs = [_make_run(cost_usd=5.0, actions_taken=[])]
        result = compute_cost_analytics(runs)
        assert "0" in result["cost_per_retry"]
        assert result["cost_per_retry"]["0"]["count"] == 1


class TestBudgetUtilization:
    def test_single_session_under_budget(self):
        session = _make_session(task_id=42, budget=10.0, spent=5.0, state="completed")
        result = compute_cost_analytics([], sessions={42: session})
        bu = result["budget_utilization"]
        assert bu is not None
        assert bu["total_budget"] == 10.0
        assert bu["total_spent"] == 5.0
        assert bu["utilization_pct"] == pytest.approx(50.0)
        assert bu["over_budget_count"] == 0
        assert len(bu["tasks"]) == 1
        task_entry = bu["tasks"][0]
        assert task_entry["task_id"] == 42
        assert task_entry["budget"] == 10.0
        assert task_entry["spent"] == 5.0
        assert task_entry["utilization_pct"] == pytest.approx(50.0)

    def test_single_session_over_budget(self):
        session = _make_session(task_id=1, budget=5.0, spent=8.0, state="failed")
        result = compute_cost_analytics([], sessions={1: session})
        bu = result["budget_utilization"]
        assert bu["over_budget_count"] == 1
        assert bu["utilization_pct"] == pytest.approx(160.0)

    def test_non_terminal_sessions_excluded(self):
        """Sessions not in COMPLETED or FAILED state are excluded."""
        sessions = {}
        for i, state in enumerate(
            [
                "detected",
                "running",
                "verifying",
                "validating",
                "retrying",
                "human_review",
            ]
        ):
            s = _make_session(task_id=i, budget=10.0, spent=5.0, state=state)
            sessions[i] = s

        result = compute_cost_analytics([], sessions=sessions)
        assert result["budget_utilization"] is None

    def test_mixed_terminal_and_non_terminal_sessions(self):
        sessions = {
            1: _make_session(task_id=1, budget=10.0, spent=4.0, state="completed"),
            2: _make_session(task_id=2, budget=10.0, spent=3.0, state="running"),
        }
        result = compute_cost_analytics([], sessions=sessions)
        bu = result["budget_utilization"]
        assert bu is not None
        assert len(bu["tasks"]) == 1
        assert bu["tasks"][0]["task_id"] == 1

    def test_multiple_terminal_sessions(self):
        sessions = {
            1: _make_session(task_id=1, budget=10.0, spent=5.0, state="completed"),
            2: _make_session(task_id=2, budget=10.0, spent=12.0, state="failed"),
        }
        result = compute_cost_analytics([], sessions=sessions)
        bu = result["budget_utilization"]
        assert bu["total_budget"] == 20.0
        assert bu["total_spent"] == 17.0
        assert bu["over_budget_count"] == 1
        assert len(bu["tasks"]) == 2

    def test_zero_budget_session_utilization(self):
        """Session with zero budget handles division safely."""
        session = _make_session(task_id=1, budget=0.0, spent=0.0, state="completed")
        result = compute_cost_analytics([], sessions={1: session})
        bu = result["budget_utilization"]
        assert bu["utilization_pct"] == 0.0
        assert bu["tasks"][0]["utilization_pct"] == 0.0


class TestSummary:
    def test_summary_single_run(self):
        runs = [_make_run(cost_usd=5.0)]
        result = compute_cost_analytics(runs)
        s = result["summary"]
        assert s["total_cost"] == 5.0
        assert s["total_runs"] == 1
        assert s["avg_cost_per_run"] == 5.0
        assert s["max_cost_run"] == 5.0
        assert s["min_cost_run"] == 5.0

    def test_summary_multiple_runs(self):
        runs = [
            _make_run(cost_usd=2.0),
            _make_run(cost_usd=8.0),
            _make_run(cost_usd=5.0),
        ]
        result = compute_cost_analytics(runs)
        s = result["summary"]
        assert s["total_cost"] == 15.0
        assert s["total_runs"] == 3
        assert s["avg_cost_per_run"] == 5.0
        assert s["max_cost_run"] == 8.0
        assert s["min_cost_run"] == 2.0

    def test_summary_missing_cost_defaults_to_zero(self):
        runs = [{"verdict": "PASS", "actions_taken": []}]
        result = compute_cost_analytics(runs)
        s = result["summary"]
        assert s["total_cost"] == 0.0
        assert s["total_runs"] == 1
