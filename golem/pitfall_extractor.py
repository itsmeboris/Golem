"""Extract, classify, and deduplicate pitfall strings from task session dicts."""

# ---------------------------------------------------------------------------
# Categories used by the writer to organize AGENTS.md sections.
# ---------------------------------------------------------------------------
CATEGORY_ANTIPATTERNS = "antipatterns"
CATEGORY_COVERAGE = "coverage"
CATEGORY_ARCHITECTURE = "architecture"

# ---------------------------------------------------------------------------
# Noise filter — positive outcomes and uninformative snippets
# ---------------------------------------------------------------------------
_NOISE_PHRASES = [
    "implemented correctly",
    "requirements implemented",
    "code is clean",
    "no regressions",
    "follows project conventions",
    "well-structured",
    "always run tests",
]

_MIN_PITFALL_LENGTH = 15


def _is_noise(text: str) -> bool:
    """Return True for positive outcomes and uninformative snippets."""
    stripped = text.strip().lower()
    if len(stripped) < _MIN_PITFALL_LENGTH:
        return True
    for phrase in _NOISE_PHRASES:
        if phrase in stripped:
            return True
    return False


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
_ANTIPATTERN_KEYWORDS = [
    "antipattern",
    "dead code",
    "empty exception",
    "string-matching control flow",
    "tightly coupling",
    "silently swallows",
    "unused",
]

_COVERAGE_KEYWORDS = [
    "no independent verification",
    "coverage",
    "no end-to-end test",
    "test pass claims",
    "verification was not",
]


def classify_pitfall(text: str) -> str:
    """Classify a pitfall string into a category."""
    lower = text.lower()
    for kw in _ANTIPATTERN_KEYWORDS:
        if kw in lower:
            return CATEGORY_ANTIPATTERNS
    for kw in _COVERAGE_KEYWORDS:
        if kw in lower:
            return CATEGORY_COVERAGE
    return CATEGORY_ARCHITECTURE


def normalize_pitfall(text: str) -> str:
    """Normalize a pitfall string: lowercase, strip, truncate to 200 chars."""
    normalized = text.lower().strip()
    return normalized[:200]


def _token_overlap(a: str, b: str) -> float:
    """Return Jaccard similarity of word tokens between two strings."""
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    intersection = tokens_a & tokens_b
    return len(intersection) / len(union)


def _is_duplicate(
    candidate: str, existing: list[str], threshold: float = 0.6
) -> int | None:
    """Check if candidate is a duplicate of any existing pitfall.

    Returns the index of the matched entry, or None if no match.
    Callers must use ``is None`` / ``is not None`` - never truthiness
    (index 0 is falsy).
    """
    for i, item in enumerate(existing):
        if _token_overlap(candidate, item) >= threshold:
            return i
    return None


def extract_pitfalls(sessions: list[dict]) -> list[str]:
    """Extract, filter, and deduplicate pitfall strings from session dicts.

    Extracts from:
    - validation_concerns
    - validation_test_failures
    - errors
    - retry_count > 0 (use validation_summary as the pitfall)
    - validation_summary sentences (split on '. ')

    Filters out positive outcomes and noise, then deduplicates.
    Returns list of concise pitfall strings.
    """
    candidates: list[str] = []

    for session in sessions:
        for concern in session.get("validation_concerns", []):
            if concern and concern.strip():
                candidates.append(normalize_pitfall(concern))

        for failure in session.get("validation_test_failures", []):
            if failure and failure.strip():
                candidates.append(normalize_pitfall(failure))

        for error in session.get("errors", []):
            if error and error.strip():
                candidates.append(normalize_pitfall(error))

        validation_summary = session.get("validation_summary", "")

        # Add individual sentences first so the full summary doesn't
        # suppress them via dedup (the full string overlaps every sentence).
        if validation_summary and validation_summary.strip():
            for sentence in validation_summary.split(". "):
                sentence = sentence.strip()
                if sentence:
                    candidates.append(normalize_pitfall(sentence))

        retry_count = session.get("retry_count", 0)
        if (
            retry_count
            and retry_count > 0
            and validation_summary
            and validation_summary.strip()
        ):
            candidates.append(normalize_pitfall(validation_summary))

    # Filter noise before dedup
    filtered = [c for c in candidates if not _is_noise(c)]

    deduplicated: list[str] = []
    for candidate in filtered:
        if _is_duplicate(candidate, deduplicated) is None:
            deduplicated.append(candidate)

    return deduplicated
