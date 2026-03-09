"""Ensemble retry: spawn parallel attempts and pick the best result.

Used when a task fails its first retry. Instead of escalating, generate
2-3 candidates with different approaches and select the best one via
validation scoring.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger("golem.ensemble")

_VERDICT_RANK = {"PASS": 3, "PARTIAL": 2, "FAIL": 1, "": 0}


@dataclass
class EnsembleResult:
    """Result from a single ensemble candidate."""

    verdict: str
    confidence: float
    cost_usd: float
    work_dir: str
    summary: str


def pick_best_result(results: list[EnsembleResult]) -> EnsembleResult | None:
    """Select the best candidate from ensemble results.

    Selection priority:
    1. PASS verdict (prefer higher confidence)
    2. PARTIAL verdict (prefer higher confidence)
    3. FAIL verdict (prefer higher confidence — least bad)
    """
    if not results:
        return None

    return max(
        results,
        key=lambda r: (_VERDICT_RANK.get(r.verdict, 0), r.confidence),
    )
