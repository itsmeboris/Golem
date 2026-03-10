"""Lightweight LLM pre-check for task description clarity.

Uses haiku (~$0.01) to score whether a task description is clear enough
for autonomous agent execution. Fail-open: if the check itself fails,
return a passing score to avoid blocking tasks on infrastructure errors.
"""

import logging
from dataclasses import dataclass

from .core.cli_wrapper import CLIConfig, CLIType, invoke_cli
from .core.json_extract import extract_json

logger = logging.getLogger("golem.clarity")

_CLARITY_PROMPT = """\
Rate this task description for clarity on a scale of 1-5.

**Subject**: {subject}
**Description**:
{description}

Scoring:
- 5: Unambiguous. Specific files, behavior, or outcome described. Reproduction steps provided.
- 4: Mostly clear. Minor gaps but intent is obvious from context.
- 3: Adequate. Enough to start work but may require assumptions.
- 2: Vague. Multiple valid interpretations exist. Key details missing.
- 1: Unclear. Cannot determine what is being asked.

Respond with ONLY this JSON:
{{"score": N, "reason": "one sentence explanation"}}
"""


@dataclass
class ClarityResult:
    """Result of a task clarity assessment."""

    score: int
    reason: str
    cost_usd: float = 0.0

    def is_clear(self, threshold: int = 3) -> bool:
        """Return True if the score meets or exceeds the threshold."""
        return self.score >= threshold


def check_clarity(
    subject: str,
    description: str,
    *,
    model: str = "haiku",
    budget_usd: float = 0.05,
    timeout_seconds: int = 30,
) -> ClarityResult:
    """Score task clarity using a cheap LLM call. Fail-open on errors."""
    prompt = _CLARITY_PROMPT.format(
        subject=subject, description=description or "(empty)"
    )

    cli_config = CLIConfig(
        cli_type=CLIType.CLAUDE,
        model=model,
        max_budget_usd=budget_usd,
        timeout_seconds=timeout_seconds,
        mcp_servers=[],
    )

    try:
        result = invoke_cli(prompt, cli_config)
        raw = result.output.get("result", "")
        parsed = extract_json(str(raw), require_key="score") or {}
        score = int(parsed.get("score", 5))
        score = max(1, min(5, score))  # Clamp to 1-5
        return ClarityResult(
            score=score,
            reason=parsed.get("reason", ""),
            cost_usd=result.cost_usd,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.warning("Clarity check failed (fail-open): %s", exc)
        return ClarityResult(score=5, reason=f"Check failed: {exc}", cost_usd=0.0)
