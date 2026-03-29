"""Tests for prompt auto-tuning evaluator-optimizer loop."""

from golem.prompt_optimizer import (
    OptimizationSuggestion,
    PromptEvaluator,
    PromptOptimizer,
    PromptScore,
)


class TestPromptScore:
    def test_success_rate_zero_runs(self):
        """success_rate returns 0.0 when run_count is 0 (no ZeroDivisionError)."""
        score = PromptScore(prompt_hash="abc", template_name="t")
        assert score.success_rate == 0.0

    def test_avg_cost_zero_runs(self):
        """avg_cost_usd returns 0.0 when run_count is 0."""
        score = PromptScore(prompt_hash="abc", template_name="t")
        assert score.avg_cost_usd == 0.0

    def test_avg_duration_zero_runs(self):
        """avg_duration_s returns 0.0 when run_count is 0."""
        score = PromptScore(prompt_hash="abc", template_name="t")
        assert score.avg_duration_s == 0.0

    def test_success_rate_computed(self):
        """success_rate = success_count / run_count."""
        score = PromptScore(
            prompt_hash="abc",
            template_name="t",
            run_count=4,
            success_count=3,
        )
        assert score.success_rate == 0.75

    def test_avg_cost_computed(self):
        """avg_cost_usd = total_cost_usd / run_count."""
        score = PromptScore(
            prompt_hash="abc",
            template_name="t",
            run_count=2,
            total_cost_usd=6.0,
        )
        assert score.avg_cost_usd == 3.0

    def test_avg_duration_computed(self):
        """avg_duration_s = total_duration_s / run_count."""
        score = PromptScore(
            prompt_hash="abc",
            template_name="t",
            run_count=5,
            total_duration_s=100.0,
        )
        assert score.avg_duration_s == 20.0

    def test_full_success(self):
        """All successful runs yields success_rate of 1.0."""
        score = PromptScore(
            prompt_hash="abc",
            template_name="t",
            run_count=10,
            success_count=10,
        )
        assert score.success_rate == 1.0

    def test_zero_success(self):
        """Zero successes yields success_rate of 0.0."""
        score = PromptScore(
            prompt_hash="abc",
            template_name="t",
            run_count=5,
            success_count=0,
        )
        assert score.success_rate == 0.0


class TestPromptEvaluatorEvaluate:
    def test_empty_runs_returns_empty_dict(self, tmp_path):
        """evaluate() with no runs returns empty scores."""
        ev = PromptEvaluator(runs_dir=tmp_path)
        result = ev.evaluate([])
        assert result == {}

    def test_runs_without_prompt_hash_skipped(self, tmp_path):
        """Runs with missing or empty prompt_hash are excluded."""
        ev = PromptEvaluator(runs_dir=tmp_path)
        result = ev.evaluate(
            [
                {"success": True, "cost_usd": 1.0, "duration_s": 10},
                {"prompt_hash": "", "success": True, "cost_usd": 1.0, "duration_s": 10},
            ]
        )
        assert result == {}

    def test_groups_runs_by_prompt_hash(self, tmp_path):
        """Runs are grouped by prompt_hash and scored independently."""
        ev = PromptEvaluator(runs_dir=tmp_path)
        runs = [
            {
                "prompt_hash": "aaa",
                "template_name": "build",
                "success": True,
                "cost_usd": 2.0,
                "duration_s": 20.0,
            },
            {
                "prompt_hash": "bbb",
                "template_name": "review",
                "success": False,
                "cost_usd": 4.0,
                "duration_s": 40.0,
            },
        ]
        result = ev.evaluate(runs)
        assert set(result.keys()) == {"aaa", "bbb"}

    def test_accumulates_run_statistics(self, tmp_path):
        """Multiple runs for the same hash accumulate totals correctly."""
        ev = PromptEvaluator(runs_dir=tmp_path)
        runs = [
            {
                "prompt_hash": "abc",
                "template_name": "build",
                "success": True,
                "cost_usd": 2.0,
                "duration_s": 20.0,
            },
            {
                "prompt_hash": "abc",
                "template_name": "build",
                "success": False,
                "cost_usd": 4.0,
                "duration_s": 40.0,
            },
        ]
        result = ev.evaluate(runs)
        score = result["abc"]
        assert score.run_count == 2
        assert score.success_count == 1
        assert score.total_cost_usd == 6.0
        assert score.total_duration_s == 60.0

    def test_uses_template_name_from_first_run(self, tmp_path):
        """template_name is taken from the first run with that prompt_hash."""
        ev = PromptEvaluator(runs_dir=tmp_path)
        runs = [
            {
                "prompt_hash": "xyz",
                "template_name": "builder",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            },
        ]
        result = ev.evaluate(runs)
        assert result["xyz"].template_name == "builder"

    def test_missing_cost_and_duration_default_to_zero(self, tmp_path):
        """Runs without cost_usd/duration_s don't crash and default to 0."""
        ev = PromptEvaluator(runs_dir=tmp_path)
        runs = [{"prompt_hash": "xyz", "template_name": "build"}]
        result = ev.evaluate(runs)
        assert result["xyz"].total_cost_usd == 0.0
        assert result["xyz"].total_duration_s == 0.0

    def test_none_cost_and_duration_default_to_zero(self, tmp_path):
        """None values for cost/duration are treated as 0."""
        ev = PromptEvaluator(runs_dir=tmp_path)
        runs = [
            {
                "prompt_hash": "xyz",
                "template_name": "build",
                "cost_usd": None,
                "duration_s": None,
            }
        ]
        result = ev.evaluate(runs)
        assert result["xyz"].total_cost_usd == 0.0
        assert result["xyz"].total_duration_s == 0.0

    def test_evaluate_clears_previous_scores(self, tmp_path):
        """Calling evaluate() twice replaces old scores entirely."""
        ev = PromptEvaluator(runs_dir=tmp_path)
        ev.evaluate(
            [
                {
                    "prompt_hash": "old",
                    "template_name": "t",
                    "success": True,
                    "cost_usd": 1.0,
                    "duration_s": 10.0,
                }
            ]
        )
        result = ev.evaluate(
            [
                {
                    "prompt_hash": "new",
                    "template_name": "t",
                    "success": True,
                    "cost_usd": 1.0,
                    "duration_s": 10.0,
                }
            ]
        )
        assert "old" not in result
        assert "new" in result


class TestPromptEvaluatorGetUnderperforming:
    def _make_evaluator(self, tmp_path, runs):
        ev = PromptEvaluator(runs_dir=tmp_path)
        ev.evaluate(runs)
        return ev

    def test_returns_empty_when_no_scores(self, tmp_path):
        ev = PromptEvaluator(runs_dir=tmp_path)
        ev.evaluate([])
        assert ev.get_underperforming() == []

    def test_filters_by_min_runs(self, tmp_path):
        """Prompts with fewer than min_runs are excluded even if low success."""
        runs = [
            {
                "prompt_hash": "low",
                "template_name": "t",
                "success": False,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            },
            {
                "prompt_hash": "low",
                "template_name": "t",
                "success": False,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            },
        ]
        ev = self._make_evaluator(tmp_path, runs)
        # min_runs=3 but only 2 runs → excluded
        result = ev.get_underperforming(min_runs=3)
        assert result == []

    def test_includes_prompt_meeting_min_runs_and_below_threshold(self, tmp_path):
        """Prompt with enough runs and low success is returned."""
        runs = [
            {
                "prompt_hash": "bad",
                "template_name": "t",
                "success": False,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for _ in range(5)
        ]
        ev = self._make_evaluator(tmp_path, runs)
        result = ev.get_underperforming(min_runs=3, max_success_rate=0.5)
        assert len(result) == 1
        assert result[0].prompt_hash == "bad"

    def test_excludes_prompt_above_success_threshold(self, tmp_path):
        """High-success prompt is not flagged as underperforming."""
        runs = [
            {
                "prompt_hash": "good",
                "template_name": "t",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for _ in range(5)
        ]
        ev = self._make_evaluator(tmp_path, runs)
        result = ev.get_underperforming(min_runs=3, max_success_rate=0.5)
        assert result == []

    def test_boundary_success_rate_excluded(self, tmp_path):
        """Prompt at exactly max_success_rate is NOT flagged (strict less-than)."""
        runs = [
            {
                "prompt_hash": "edge",
                "template_name": "t",
                "success": i < 2,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for i in range(4)
        ]
        # 2/4 = 0.5 — exactly at threshold
        ev = self._make_evaluator(tmp_path, runs)
        result = ev.get_underperforming(min_runs=3, max_success_rate=0.5)
        assert result == []


class TestPromptEvaluatorGetBestVariant:
    def _make_evaluator(self, tmp_path, runs):
        ev = PromptEvaluator(runs_dir=tmp_path)
        ev.evaluate(runs)
        return ev

    def test_returns_none_when_no_scores(self, tmp_path):
        ev = PromptEvaluator(runs_dir=tmp_path)
        ev.evaluate([])
        assert ev.get_best_variant("build") is None

    def test_returns_none_for_unknown_template(self, tmp_path):
        runs = [
            {
                "prompt_hash": "abc",
                "template_name": "review",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            },
            {
                "prompt_hash": "abc",
                "template_name": "review",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            },
        ]
        ev = self._make_evaluator(tmp_path, runs)
        assert ev.get_best_variant("build") is None

    def test_requires_min_two_runs(self, tmp_path):
        """Variants with fewer than 2 runs are not eligible for best variant."""
        runs = [
            {
                "prompt_hash": "abc",
                "template_name": "build",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            },
        ]
        ev = self._make_evaluator(tmp_path, runs)
        assert ev.get_best_variant("build") is None

    def test_returns_best_by_success_rate(self, tmp_path):
        """Returns variant with highest success rate (given min 2 runs each)."""
        runs = [
            {
                "prompt_hash": "good",
                "template_name": "build",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for _ in range(4)
        ] + [
            {
                "prompt_hash": "poor",
                "template_name": "build",
                "success": False,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for _ in range(4)
        ]
        ev = self._make_evaluator(tmp_path, runs)
        best = ev.get_best_variant("build")
        assert best is not None
        assert best.prompt_hash == "good"

    def test_tiebreak_prefers_lower_cost(self, tmp_path):
        """When success rates tie, lower avg cost wins."""
        runs = [
            {
                "prompt_hash": "cheap",
                "template_name": "build",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for _ in range(2)
        ] + [
            {
                "prompt_hash": "expensive",
                "template_name": "build",
                "success": True,
                "cost_usd": 5.0,
                "duration_s": 10.0,
            }
            for _ in range(2)
        ]
        ev = self._make_evaluator(tmp_path, runs)
        best = ev.get_best_variant("build")
        assert best is not None
        assert best.prompt_hash == "cheap"


class TestPromptOptimizerSuggest:
    def _make_optimizer(self, tmp_path, runs):
        ev = PromptEvaluator(runs_dir=tmp_path)
        ev.evaluate(runs)
        return PromptOptimizer(ev)

    def test_no_underperforming_returns_empty(self, tmp_path):
        """When all prompts perform well, no suggestions are generated."""
        runs = [
            {
                "prompt_hash": "good",
                "template_name": "build",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for _ in range(5)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        assert opt.suggest(min_runs=3, max_success_rate=0.5) == []

    def test_suggests_for_low_success_prompt(self, tmp_path):
        """A prompt below the success threshold gets a section suggestion."""
        runs = [
            {
                "prompt_hash": "bad",
                "template_name": "review",
                "success": False,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for _ in range(5)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.template_name == "review"
        assert s.suggestion_type == "section"
        assert s.current_score.prompt_hash == "bad"

    def test_suggests_better_variant_when_exists(self, tmp_path):
        """When a better variant exists for the same template, 'variant' type is used."""
        # Two variants of "build": "great" (100% success, 4 runs) vs "bad" (0%, 4 runs)
        runs = [
            {
                "prompt_hash": "great",
                "template_name": "build",
                "success": True,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for _ in range(4)
        ] + [
            {
                "prompt_hash": "bad",
                "template_name": "build",
                "success": False,
                "cost_usd": 1.0,
                "duration_s": 10.0,
            }
            for _ in range(4)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        variant_suggestions = [s for s in suggestions if s.suggestion_type == "variant"]
        assert len(variant_suggestions) == 1
        s = variant_suggestions[0]
        assert "great" in s.details
        assert s.template_name == "build"

    def test_suggests_parameter_for_high_cost_prompt(self, tmp_path):
        """A prompt with high avg cost and low success gets a 'parameter' suggestion."""
        runs = [
            {
                "prompt_hash": "pricey",
                "template_name": "generate",
                "success": False,
                "cost_usd": 3.0,
                "duration_s": 60.0,
            }
            for _ in range(4)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.suggestion_type == "parameter"
        assert s.template_name == "generate"

    def test_suggestion_contains_reason_and_details(self, tmp_path):
        """Every suggestion has non-empty reason and details."""
        runs = [
            {
                "prompt_hash": "x",
                "template_name": "scout",
                "success": False,
                "cost_usd": 0.5,
                "duration_s": 5.0,
            }
            for _ in range(3)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        assert len(suggestions) == 1
        s = suggestions[0]
        assert s.reason
        assert s.details

    def test_suggest_returns_optimization_suggestion_instances(self, tmp_path):
        """suggest() returns OptimizationSuggestion dataclass instances."""
        runs = [
            {
                "prompt_hash": "x",
                "template_name": "scout",
                "success": False,
                "cost_usd": 0.5,
                "duration_s": 5.0,
            }
            for _ in range(3)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        assert all(isinstance(s, OptimizationSuggestion) for s in suggestions)


class TestPromptOptimizerFormatReport:
    def _make_optimizer(self, tmp_path, runs):
        ev = PromptEvaluator(runs_dir=tmp_path)
        ev.evaluate(runs)
        return PromptOptimizer(ev)

    def test_empty_suggestions_returns_all_performing_well_message(self, tmp_path):
        """No suggestions produces the 'all prompts performing well' message."""
        opt = self._make_optimizer(tmp_path, [])
        report = opt.format_report([])
        assert "No optimization suggestions" in report
        assert "all prompts performing well" in report.lower()

    def test_report_contains_template_name(self, tmp_path):
        """Format report includes the template name as a heading."""
        runs = [
            {
                "prompt_hash": "low",
                "template_name": "my_template",
                "success": False,
                "cost_usd": 0.5,
                "duration_s": 5.0,
            }
            for _ in range(3)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        report = opt.format_report(suggestions)
        assert "my_template" in report

    def test_report_contains_suggestion_type(self, tmp_path):
        """Format report includes the suggestion type."""
        runs = [
            {
                "prompt_hash": "low",
                "template_name": "t",
                "success": False,
                "cost_usd": 0.5,
                "duration_s": 5.0,
            }
            for _ in range(3)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        report = opt.format_report(suggestions)
        assert "section" in report

    def test_report_contains_stats(self, tmp_path):
        """Format report includes run count and success rate."""
        runs = [
            {
                "prompt_hash": "low",
                "template_name": "t",
                "success": False,
                "cost_usd": 0.5,
                "duration_s": 5.0,
            }
            for _ in range(3)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        report = opt.format_report(suggestions)
        assert "3" in report  # run count
        assert "0%" in report  # success rate

    def test_report_has_header(self, tmp_path):
        """Report begins with a Markdown heading."""
        runs = [
            {
                "prompt_hash": "low",
                "template_name": "t",
                "success": False,
                "cost_usd": 0.5,
                "duration_s": 5.0,
            }
            for _ in range(3)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        report = opt.format_report(suggestions)
        assert report.startswith("# Prompt Optimization Report")

    def test_report_numbers_suggestions(self, tmp_path):
        """Multiple suggestions are numbered sequentially."""
        runs = [
            {
                "prompt_hash": "a1",
                "template_name": "alpha",
                "success": False,
                "cost_usd": 0.5,
                "duration_s": 5.0,
            }
            for _ in range(3)
        ] + [
            {
                "prompt_hash": "b1",
                "template_name": "beta",
                "success": False,
                "cost_usd": 0.5,
                "duration_s": 5.0,
            }
            for _ in range(3)
        ]
        opt = self._make_optimizer(tmp_path, runs)
        suggestions = opt.suggest(min_runs=3, max_success_rate=0.5)
        report = opt.format_report(suggestions)
        assert "## 1." in report
        assert "## 2." in report
