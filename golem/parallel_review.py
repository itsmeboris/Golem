"""Multi-perspective parallel review with specialized reviewers.

Defines reviewer roles, finding/result dataclasses, and aggregation logic
for running multiple specialized code reviewers in parallel.
"""

import enum
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("golem.parallel_review")

__all__ = [
    "ReviewerRole",
    "ReviewFinding",
    "ReviewResult",
    "AggregatedReview",
    "default_reviewers",
    "enhanced_reviewers",
    "roles_from_config",
    "aggregate_reviews",
]


class ReviewerRole(enum.Enum):
    """Specialized reviewer roles for multi-perspective review."""

    SPEC = "spec"
    QUALITY = "quality"
    SECURITY = "security"
    CONSISTENCY = "consistency"
    TEST_QUALITY = "test_quality"

    @property
    def prompt_template(self) -> str:
        """Return the prompt template filename for this role."""
        _TEMPLATES = {
            "spec": "orchestrate_review_template.txt",
            "quality": "orchestrate_review_template.txt",
            "security": "review_security.txt",
            "consistency": "review_consistency.txt",
            "test_quality": "review_test_quality.txt",
        }
        return _TEMPLATES[self.value]

    @property
    def description(self) -> str:
        """Human-readable description of this reviewer's focus."""
        _DESCRIPTIONS = {
            "spec": "Spec compliance — does the code match the specification?",
            "quality": "Code quality — bugs, edge cases, naming, duplication",
            "security": "Security — OWASP Top 10, injection, auth, secrets",
            "consistency": "Consistency — naming, API contracts, TypedDict usage",
            "test_quality": "Test quality — tautological tests, shallow assertions, coverage gaps",
        }
        return _DESCRIPTIONS[self.value]


@dataclass
class ReviewFinding:
    """A single finding from a specialized reviewer."""

    confidence: int
    file_line: str
    description: str
    reviewer: str


@dataclass
class ReviewResult:
    """Result from a single reviewer."""

    role: ReviewerRole
    verdict: str  # "APPROVED" or "NEEDS_FIXES"
    findings: list[ReviewFinding] = field(default_factory=list)


@dataclass
class AggregatedReview:
    """Aggregated result from multiple parallel reviewers."""

    overall_verdict: str  # "APPROVED" or "NEEDS_FIXES"
    findings: list[ReviewFinding] = field(default_factory=list)
    reviewer_summaries: dict[str, str] = field(default_factory=dict)


def default_reviewers() -> list[ReviewerRole]:
    """Return the default 2-stage reviewer set (backward compatible)."""
    return [ReviewerRole.SPEC, ReviewerRole.QUALITY]


def enhanced_reviewers() -> list[ReviewerRole]:
    """Return all 5 reviewers for enhanced parallel review."""
    return list(ReviewerRole)


def roles_from_config(role_names: list[str]) -> list[ReviewerRole]:
    """Convert config role name strings to ReviewerRole enums.

    Unknown names are logged and skipped.
    """
    roles = []
    for name in role_names:
        try:
            roles.append(ReviewerRole(name))
        except ValueError:
            logger.warning("Unknown reviewer role: %s", name)
    return roles


def aggregate_reviews(
    results: list[ReviewResult],
    confidence_threshold: int = 80,
) -> AggregatedReview:
    """Aggregate findings from multiple reviewers.

    - Overall verdict: NEEDS_FIXES if ANY reviewer reports NEEDS_FIXES, else APPROVED
    - Findings are filtered to confidence >= threshold
    - Duplicate findings (same file_line + same reviewer) are merged; findings
      from different reviewers at the same line are kept separately
    """
    if not results:
        return AggregatedReview(overall_verdict="APPROVED")

    overall = "APPROVED"
    all_findings: list[ReviewFinding] = []
    summaries: dict[str, str] = {}

    for result in results:
        summaries[result.role.value] = result.verdict
        if result.verdict == "NEEDS_FIXES":
            overall = "NEEDS_FIXES"
        for finding in result.findings:
            if finding.confidence >= confidence_threshold:
                all_findings.append(finding)

    deduplicated = _deduplicate_findings(all_findings)

    return AggregatedReview(
        overall_verdict=overall,
        findings=deduplicated,
        reviewer_summaries=summaries,
    )


def _deduplicate_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Remove duplicate findings where the same reviewer flags the same file:line.

    When the same reviewer flags the same location more than once, keep only
    the finding with the highest confidence. Findings from different reviewers
    at the same location are preserved separately.
    """
    seen: dict[tuple[str, str], ReviewFinding] = {}
    for f in findings:
        key = (f.file_line, f.reviewer)
        if key not in seen or f.confidence > seen[key].confidence:
            seen[key] = f
    return list(seen.values())
