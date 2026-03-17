"""Extract structured observation signals from verification and validation results.

Signals are mined deterministically (no LLM judgment) from:
- VerificationResult  (pytest failures, pylint errors, black formatting)
- ValidationVerdict   (concern categories via regex)
- Retry comparisons   (identical error patterns across attempts)

Signals accumulate in a JSON file and are promoted to pitfalls once a
pattern has been observed >= N times (configurable threshold, default 3).
"""

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .validation import ValidationVerdict
from .verifier import VerificationResult

logger = logging.getLogger("golem.observation_hooks")

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Positive-outcome noise phrases — mirrors pitfall_extractor._NOISE_PHRASES
_NOISE_PHRASES = [
    "implemented correctly",
    "requirements implemented",
    "code is clean",
    "no regressions",
    "follows project conventions",
    "well-structured",
    "always run tests",
]

_MIN_CONCERN_LENGTH = 15


@dataclass
class ObservationSignal:
    """A single structured observation extracted from verification or validation output."""

    category: str  # "pytest_failure", "pylint_error", "black_format", "validation_concern", "retry_identical"
    pattern: str  # normalized pattern string (lowercase, stripped)
    source: str  # "verification" | "validation" | "retry"
    count: int = 1


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    """Lowercase, strip, and truncate to 200 chars."""
    return text.lower().strip()[:200]


def _is_noise_concern(text: str) -> bool:
    """Return True for positive outcomes and short uninformative strings."""
    stripped = text.strip().lower()
    if len(stripped) < _MIN_CONCERN_LENGTH:
        return True
    return any(phrase in stripped for phrase in _NOISE_PHRASES)


# ---------------------------------------------------------------------------
# Pytest output parsers
# ---------------------------------------------------------------------------

_IMPORT_ERROR_RE = re.compile(
    r"(?:ImportError|ModuleNotFoundError): No module named '([^']+)'"
)
_FIXTURE_NOT_FOUND_RE = re.compile(r"fixture '([^']+)' not found")
_TYPE_ERROR_RE = re.compile(r"TypeError: (.+)")
_ASSERTION_ERROR_RE = re.compile(r"FAILED\s+(\S+).*AssertionError", re.IGNORECASE)


def _mine_pytest_signals(result: VerificationResult) -> list[ObservationSignal]:
    """Extract pytest failure signals from result.pytest_output and result.failures."""
    signals: list[ObservationSignal] = []
    output = result.pytest_output

    # ImportError / ModuleNotFoundError
    for match in _IMPORT_ERROR_RE.finditer(output):
        module = match.group(1)
        signals.append(
            ObservationSignal(
                category="pytest_failure",
                pattern=_normalize(f"import_error: {module}"),
                source="verification",
            )
        )

    # Fixture not found
    for match in _FIXTURE_NOT_FOUND_RE.finditer(output):
        name = match.group(1)
        signals.append(
            ObservationSignal(
                category="pytest_failure",
                pattern=_normalize(f"fixture_not_found: {name}"),
                source="verification",
            )
        )

    # TypeError
    for match in _TYPE_ERROR_RE.finditer(output):
        brief = match.group(1).strip()[:80]
        signals.append(
            ObservationSignal(
                category="pytest_failure",
                pattern=_normalize(f"type_error: {brief}"),
                source="verification",
            )
        )

    # AssertionError — look for FAILED lines with AssertionError in the same line
    # or just produce a signal per test that asserted
    for match in _ASSERTION_ERROR_RE.finditer(output):
        test_name = match.group(1)
        signals.append(
            ObservationSignal(
                category="pytest_failure",
                pattern=_normalize(f"assertion_error: {test_name}"),
                source="verification",
            )
        )

    # Signals from failures list (test paths)
    for failure in result.failures:
        # Only add a path-based signal if we haven't already created one for it
        signals.append(
            ObservationSignal(
                category="pytest_failure",
                pattern=_normalize(f"test_failed: {failure}"),
                source="verification",
            )
        )

    return signals


# ---------------------------------------------------------------------------
# Pylint output parsers
# ---------------------------------------------------------------------------

# e.g. "golem/foo.py:42:0: E0602: Undefined variable 'bar' (undefined-variable)"
_PYLINT_ERROR_RE = re.compile(r":\s+(E\d{4}):\s+.+\(([^)]+)\)")


def _mine_pylint_signals(result: VerificationResult) -> list[ObservationSignal]:
    """Extract pylint error-code signals from result.pylint_output."""
    if result.pylint_ok:
        return []
    signals: list[ObservationSignal] = []
    for match in _PYLINT_ERROR_RE.finditer(result.pylint_output):
        code = match.group(1).lower()
        message_id = match.group(2).lower()
        signals.append(
            ObservationSignal(
                category="pylint_error",
                pattern=f"pylint_{code}: {message_id}",
                source="verification",
            )
        )
    return signals


# ---------------------------------------------------------------------------
# Black output parsers
# ---------------------------------------------------------------------------

_BLACK_REFORMAT_RE = re.compile(r"would reformat (.+)")


def _mine_black_signals(result: VerificationResult) -> list[ObservationSignal]:
    """Extract black formatting signals from result.black_output."""
    if result.black_ok:
        return []
    signals: list[ObservationSignal] = []
    for match in _BLACK_REFORMAT_RE.finditer(result.black_output):
        filename = match.group(1).strip()
        signals.append(
            ObservationSignal(
                category="black_format",
                pattern=f"black_reformat: {filename}",
                source="verification",
            )
        )
    return signals


# ---------------------------------------------------------------------------
# Public API — verification
# ---------------------------------------------------------------------------


def mine_verification_signals(result: VerificationResult) -> list[ObservationSignal]:
    """Extract structured signals from a VerificationResult.

    Returns an empty list when the result passed (nothing to mine).
    """
    if result.passed:
        return []

    signals: list[ObservationSignal] = []
    signals.extend(_mine_pytest_signals(result))
    signals.extend(_mine_pylint_signals(result))
    signals.extend(_mine_black_signals(result))
    return signals


# ---------------------------------------------------------------------------
# Public API — validation
# ---------------------------------------------------------------------------


def mine_validation_signals(verdict: ValidationVerdict) -> list[ObservationSignal]:
    """Extract structured signals from a ValidationVerdict via regex.

    Returns an empty list for PASS verdicts with no concerns, and when
    concerns are filtered as noise.
    """
    if verdict.verdict == "PASS" and not verdict.concerns:
        return []

    signals: list[ObservationSignal] = []
    for concern in verdict.concerns:
        if _is_noise_concern(concern):
            continue
        pattern = _normalize(concern)
        signals.append(
            ObservationSignal(
                category="validation_concern",
                pattern=pattern,
                source="validation",
            )
        )
    return signals


# ---------------------------------------------------------------------------
# Public API — retry comparison
# ---------------------------------------------------------------------------


def _extract_pylint_codes(output: str) -> frozenset[str]:
    """Return the set of pylint error codes present in output."""
    return frozenset(m.group(1).upper() for m in re.finditer(r"(E\d{4})", output))


def _extract_black_files(output: str) -> frozenset[str]:
    """Return the set of files that would be reformatted."""
    return frozenset(m.group(1).strip() for m in _BLACK_REFORMAT_RE.finditer(output))


def compare_retry_signatures(
    current: VerificationResult,
    previous: VerificationResult,
) -> list[ObservationSignal]:
    """Detect identical error patterns across retry attempts.

    Returns signals when both attempts have exactly the same failures,
    indicating a systematic (not transient) issue.
    Returns an empty list when errors differ across retries.
    """
    signals: list[ObservationSignal] = []

    # pytest failures comparison
    current_failures = frozenset(current.failures)
    previous_failures = frozenset(previous.failures)
    if current_failures and current_failures == previous_failures:
        common = sorted(current_failures)
        signals.append(
            ObservationSignal(
                category="retry_identical",
                pattern=_normalize("test_failures: " + ", ".join(common)),
                source="retry",
            )
        )

    # pylint error codes comparison
    current_codes = _extract_pylint_codes(current.pylint_output)
    previous_codes = _extract_pylint_codes(previous.pylint_output)
    if current_codes and current_codes == previous_codes:
        codes_str = ", ".join(sorted(current_codes))
        signals.append(
            ObservationSignal(
                category="retry_identical",
                pattern=_normalize(f"pylint_codes: {codes_str}"),
                source="retry",
            )
        )

    # black file comparison
    current_black = _extract_black_files(current.black_output)
    previous_black = _extract_black_files(previous.black_output)
    if current_black and current_black == previous_black:
        files_str = ", ".join(sorted(current_black))
        signals.append(
            ObservationSignal(
                category="retry_identical",
                pattern=_normalize(f"black_files: {files_str}"),
                source="retry",
            )
        )

    return signals


# ---------------------------------------------------------------------------
# SignalAccumulator
# ---------------------------------------------------------------------------

_STORAGE_KEY_SEP = "::"


def _make_key(signal: ObservationSignal) -> str:
    return f"{signal.category}{_STORAGE_KEY_SEP}{signal.pattern}"


class SignalAccumulator:
    """Persist observation signals to a JSON file, promoting on threshold.

    Storage format::

        {
            "signals": {
                "pytest_failure::import_error: golem.foo": {
                    "count": 3,
                    "last_seen": "2026-03-17",
                    "source": "verification"
                },
                ...
            }
        }
    """

    def __init__(self, storage_path: Path, promotion_threshold: int = 3) -> None:
        self._path = storage_path
        self._threshold = promotion_threshold

    def _load(self) -> dict:
        """Load existing data from disk, returning empty structure on failure."""
        if not self._path.exists():
            return {"signals": {}}
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict) or "signals" not in data:
                return {"signals": {}}
            return data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "Observation signals file unreadable, starting fresh: %s", exc
            )
            return {"signals": {}}

    def _save(self, data: dict) -> None:
        """Atomically write data to storage path."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=self._path.parent, prefix=".observation_signals_"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fobj:
                json.dump(data, fobj, indent=2)
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError as exc:
                logger.debug("Failed to unlink temp file: %s", exc)
            raise

    def record(self, signals: list[ObservationSignal]) -> None:
        """Record new signals, incrementing counts for existing patterns."""
        if not signals:
            return

        data = self._load()
        today = date.today().isoformat()

        for signal in signals:
            key = _make_key(signal)
            if key in data["signals"]:
                data["signals"][key]["count"] += signal.count
                data["signals"][key]["last_seen"] = today
            else:
                data["signals"][key] = {
                    "count": signal.count,
                    "last_seen": today,
                    "source": signal.source,
                }

        self._save(data)

    def get_promoted(self) -> list[str]:
        """Return patterns that have reached the promotion threshold.

        Returns each key string (``category::pattern``) that has been seen
        >= promotion_threshold times.  These are ready to be written to
        AGENTS.md as pitfalls.
        """
        data = self._load()
        promoted = []
        for key, entry in data["signals"].items():
            if entry["count"] >= self._threshold:
                promoted.append(key)
        return promoted

    def clear_promoted(self) -> None:
        """Remove signals that have reached the promotion threshold.

        Called after promoted patterns have been written to AGENTS.md.
        """
        data = self._load()
        data["signals"] = {
            key: entry
            for key, entry in data["signals"].items()
            if entry["count"] < self._threshold
        }
        self._save(data)
