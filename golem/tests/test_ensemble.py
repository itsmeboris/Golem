"""Tests for ensemble retry with parallel approaches."""

from golem.core.config import GolemFlowConfig
from golem.ensemble import EnsembleResult, pick_best_result


class TestEnsembleConfig:
    def test_ensemble_disabled_by_default(self):
        """ensemble_on_second_retry defaults to False."""
        cfg = GolemFlowConfig()
        assert cfg.ensemble_on_second_retry is False

    def test_ensemble_candidates_default(self):
        """ensemble_candidates defaults to 2."""
        cfg = GolemFlowConfig()
        assert cfg.ensemble_candidates == 2


class TestPickBestResult:
    def test_picks_passing_result(self):
        """When one candidate passes validation, pick it."""
        results = [
            EnsembleResult(
                verdict="FAIL",
                confidence=0.3,
                cost_usd=5.0,
                work_dir="/tmp/a",
                summary="Failed attempt",
            ),
            EnsembleResult(
                verdict="PASS",
                confidence=0.9,
                cost_usd=7.0,
                work_dir="/tmp/b",
                summary="Successful fix",
            ),
        ]
        best = pick_best_result(results)
        assert best.verdict == "PASS"
        assert best.work_dir == "/tmp/b"

    def test_picks_highest_confidence_partial(self):
        """When no PASS, pick highest confidence PARTIAL."""
        results = [
            EnsembleResult(
                verdict="PARTIAL",
                confidence=0.6,
                cost_usd=5.0,
                work_dir="/tmp/a",
                summary="Partial A",
            ),
            EnsembleResult(
                verdict="PARTIAL",
                confidence=0.8,
                cost_usd=6.0,
                work_dir="/tmp/b",
                summary="Partial B",
            ),
        ]
        best = pick_best_result(results)
        assert best.confidence == 0.8
        assert best.work_dir == "/tmp/b"

    def test_all_fail_returns_highest_confidence(self):
        """When all fail, return the one with highest confidence (least bad)."""
        results = [
            EnsembleResult(
                verdict="FAIL",
                confidence=0.2,
                cost_usd=5.0,
                work_dir="/tmp/a",
                summary="Fail A",
            ),
            EnsembleResult(
                verdict="FAIL",
                confidence=0.4,
                cost_usd=6.0,
                work_dir="/tmp/b",
                summary="Fail B",
            ),
        ]
        best = pick_best_result(results)
        assert best.confidence == 0.4

    def test_empty_results_returns_none(self):
        """Empty results list returns None."""
        assert pick_best_result([]) is None
