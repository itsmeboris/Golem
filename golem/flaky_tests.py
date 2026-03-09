"""Known-flaky test registry.

Maintains a JSON file of test names known to be flaky (intermittent failures).
When the verifier encounters failures matching known-flaky tests, it retries
once and only counts them as failures if they fail consistently.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger("golem.flaky_tests")

_DEFAULT_FLAKY_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "flaky_tests.json"
)


class FlakyTestRegistry:
    """Registry of known-flaky test names."""

    def __init__(self, path: Path | None = None):
        self._path = path or _DEFAULT_FLAKY_FILE
        self.known_flaky: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self.known_flaky = set(data.get("known_flaky", []))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load flaky tests from %s: %s", self._path, exc)

    def is_flaky(self, test_name: str) -> bool:
        """Return True if *test_name* is known to be flaky."""
        return test_name in self.known_flaky

    def record_flaky(self, test_name: str, *, reason: str = "") -> None:
        """Add *test_name* to the known-flaky set and persist."""
        self.known_flaky.add(test_name)
        self._save()
        logger.info("Recorded flaky test: %s (%s)", test_name, reason)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"known_flaky": sorted(self.known_flaky)}
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def filter_flaky(self, failures: list[str]) -> tuple[list[str], list[str]]:
        """Split failures into (real_failures, flaky_failures)."""
        real = []
        flaky = []
        for f in failures:
            if self.is_flaky(f):
                flaky.append(f)
            else:
                real.append(f)
        return real, flaky


def is_flaky(test_name: str, path: Path | None = None) -> bool:
    """Convenience: check if a test name is known-flaky."""
    return FlakyTestRegistry(path).is_flaky(test_name)
