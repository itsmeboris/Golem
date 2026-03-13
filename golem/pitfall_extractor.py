"""Extract and deduplicate pitfall strings from task session dicts."""


def normalize_pitfall(text: str) -> str:
    """Normalize a pitfall string: lowercase, strip, truncate to 120 chars."""
    normalized = text.lower().strip()
    return normalized[:120]


def _token_overlap(a: str, b: str) -> float:
    """Return Jaccard similarity of word tokens between two strings."""
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    intersection = tokens_a & tokens_b
    return len(intersection) / len(union)


def _is_duplicate(candidate: str, existing: list[str], threshold: float = 0.6) -> bool:
    """Check if candidate is a duplicate of any existing pitfall."""
    for item in existing:
        if _token_overlap(candidate, item) >= threshold:
            return True
    return False


def extract_pitfalls(sessions: list[dict]) -> list[str]:
    """Extract and deduplicate pitfall strings from session dicts.

    Extracts from:
    - validation_concerns
    - validation_test_failures
    - errors
    - retry_count > 0 (use validation_summary as the pitfall)
    - validation_summary sentences (split on '. ')

    Returns deduplicated list of concise pitfall strings.
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

        retry_count = session.get("retry_count", 0)
        validation_summary = session.get("validation_summary", "")
        if (
            retry_count
            and retry_count > 0
            and validation_summary
            and validation_summary.strip()
        ):
            candidates.append(normalize_pitfall(validation_summary))

        if validation_summary and validation_summary.strip():
            for sentence in validation_summary.split(". "):
                sentence = sentence.strip()
                if sentence:
                    candidates.append(normalize_pitfall(sentence))

    deduplicated: list[str] = []
    for candidate in candidates:
        if not _is_duplicate(candidate, deduplicated):
            deduplicated.append(candidate)

    return deduplicated
