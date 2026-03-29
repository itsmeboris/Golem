"""Prompt auto-tuning via outcome-based evaluation.

Tracks prompt template performance (success rate, cost, duration),
identifies underperforming prompts, and suggests optimizations.

Key exports:
- ``PromptEvaluator`` — evaluates prompt effectiveness from run history.
- ``PromptOptimizer`` — suggests improvements to underperforming prompts.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("golem.prompt_optimizer")


@dataclass
class PromptScore:
    """Aggregated performance score for a prompt variant."""

    prompt_hash: str
    template_name: str
    run_count: int = 0
    success_count: int = 0
    total_cost_usd: float = 0.0
    total_duration_s: float = 0.0

    @property
    def success_rate(self) -> float:
        """Fraction of runs that succeeded; 0.0 when run_count is zero."""
        return self.success_count / self.run_count if self.run_count else 0.0

    @property
    def avg_cost_usd(self) -> float:
        """Mean cost per run in USD; 0.0 when run_count is zero."""
        return self.total_cost_usd / self.run_count if self.run_count else 0.0

    @property
    def avg_duration_s(self) -> float:
        """Mean duration per run in seconds; 0.0 when run_count is zero."""
        return self.total_duration_s / self.run_count if self.run_count else 0.0


@dataclass
class OptimizationSuggestion:
    """A suggested change to a prompt template."""

    template_name: str
    reason: str
    current_score: PromptScore
    suggestion_type: str  # "variant" | "parameter" | "section"
    details: str


class PromptEvaluator:
    """Evaluates prompt effectiveness from run history."""

    def __init__(self, runs_dir: Path):
        self._runs_dir = runs_dir
        self._scores: dict[str, PromptScore] = {}

    def evaluate(self, runs: list[dict]) -> dict[str, PromptScore]:
        """Compute scores from a list of run records.

        Each run dict should have: prompt_hash, template_name, success,
        cost_usd, duration_s.  Runs with a missing or empty prompt_hash
        are silently skipped.
        """
        self._scores.clear()

        for run in runs:
            ph = run.get("prompt_hash", "")
            if not ph:
                continue

            if ph not in self._scores:
                self._scores[ph] = PromptScore(
                    prompt_hash=ph,
                    template_name=run.get("template_name", "unknown"),
                )

            score = self._scores[ph]
            score.run_count += 1
            if run.get("success"):
                score.success_count += 1
            score.total_cost_usd += run.get("cost_usd", 0.0) or 0.0
            score.total_duration_s += run.get("duration_s", 0.0) or 0.0

        return dict(self._scores)

    def get_underperforming(
        self,
        min_runs: int = 3,
        max_success_rate: float = 0.5,
    ) -> list[PromptScore]:
        """Return prompts with success rate strictly below *max_success_rate*.

        Only prompts with at least *min_runs* executions are eligible.
        """
        return [
            s
            for s in self._scores.values()
            if s.run_count >= min_runs and s.success_rate < max_success_rate
        ]

    def get_best_variant(self, template_name: str) -> PromptScore | None:
        """Return the highest-scoring variant for *template_name*.

        Requires at least 2 runs for a variant to be eligible.
        Ranks by (success_rate DESC, avg_cost_usd ASC).
        """
        variants = [
            s
            for s in self._scores.values()
            if s.template_name == template_name and s.run_count >= 2
        ]
        if not variants:
            return None
        return max(variants, key=lambda s: (s.success_rate, -s.avg_cost_usd))


class PromptOptimizer:
    """Suggests improvements to underperforming prompts."""

    def __init__(self, evaluator: PromptEvaluator):
        self._evaluator = evaluator

    def suggest(
        self,
        min_runs: int = 3,
        max_success_rate: float = 0.5,
    ) -> list[OptimizationSuggestion]:
        """Generate optimization suggestions for underperforming prompts."""
        underperforming = self._evaluator.get_underperforming(
            min_runs=min_runs,
            max_success_rate=max_success_rate,
        )

        suggestions: list[OptimizationSuggestion] = []

        for score in underperforming:
            best = self._evaluator.get_best_variant(score.template_name)

            if (
                best
                and best.prompt_hash != score.prompt_hash
                and best.success_rate > score.success_rate
            ):
                suggestions.append(
                    OptimizationSuggestion(
                        template_name=score.template_name,
                        reason="Better variant exists (%.0f%% vs %.0f%% success rate)"
                        % (
                            best.success_rate * 100,
                            score.success_rate * 100,
                        ),
                        current_score=score,
                        suggestion_type="variant",
                        details="Consider reverting to prompt hash %s"
                        % best.prompt_hash,
                    )
                )
            elif score.avg_cost_usd > 2.0:
                suggestions.append(
                    OptimizationSuggestion(
                        template_name=score.template_name,
                        reason="High cost ($%.2f avg) with low success (%.0f%%)"
                        % (
                            score.avg_cost_usd,
                            score.success_rate * 100,
                        ),
                        current_score=score,
                        suggestion_type="parameter",
                        details="Consider reducing max_budget_usd or simplifying prompt",
                    )
                )
            else:
                suggestions.append(
                    OptimizationSuggestion(
                        template_name=score.template_name,
                        reason="Low success rate (%.0f%% over %d runs)"
                        % (
                            score.success_rate * 100,
                            score.run_count,
                        ),
                        current_score=score,
                        suggestion_type="section",
                        details="Review task description clarity and instruction specificity",
                    )
                )

        return suggestions

    def format_report(self, suggestions: list[OptimizationSuggestion]) -> str:
        """Format suggestions as a human-readable Markdown report."""
        if not suggestions:
            return "No optimization suggestions — all prompts performing well."

        lines = ["# Prompt Optimization Report", ""]
        for i, s in enumerate(suggestions, 1):
            lines.append("## %d. %s" % (i, s.template_name))
            lines.append("- **Reason**: %s" % s.reason)
            lines.append("- **Type**: %s" % s.suggestion_type)
            lines.append("- **Suggestion**: %s" % s.details)
            lines.append(
                "- **Stats**: %d runs, %.0f%% success, $%.2f avg cost"
                % (
                    s.current_score.run_count,
                    s.current_score.success_rate * 100,
                    s.current_score.avg_cost_usd,
                )
            )
            lines.append("")

        return "\n".join(lines)
