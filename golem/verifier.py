# golem/verifier.py
"""Deterministic verification runner for Golem task output.

Runs black, pylint, and pytest independently in the agent's work directory.
Returns structured results -- no LLM judgment, just facts.

Part of the Quality Assurance Pipeline v2 (Layer 2).
See: docs/plans/2026-03-09-quality-assurance-pipeline-v2-design.md
"""

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field

from golem.types import VerificationResultDict

logger = logging.getLogger("golem.verifier")


@dataclass
class VerificationResult:
    """Structured output from running black + pylint + pytest."""

    passed: bool
    black_ok: bool
    black_output: str
    pylint_ok: bool
    pylint_output: str
    pytest_ok: bool
    pytest_output: str
    test_count: int = 0
    failures: list[str] = field(default_factory=list)
    coverage_pct: float = 0.0
    duration_s: float = 0.0

    def to_dict(self) -> VerificationResultDict:
        """Serialize for JSON persistence."""
        return {
            "passed": self.passed,
            "black_ok": self.black_ok,
            "black_output": self.black_output,
            "pylint_ok": self.pylint_ok,
            "pylint_output": self.pylint_output,
            "pytest_ok": self.pytest_ok,
            "pytest_output": self.pytest_output,
            "test_count": self.test_count,
            "failures": list(self.failures),
            "coverage_pct": self.coverage_pct,
            "duration_s": self.duration_s,
        }


def _run_cmd(cmd: list[str], cwd: str, timeout: int) -> tuple[bool, str]:
    """Run a command and return (success, combined_output)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout + "\n" + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s: {' '.join(cmd)}"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, f"Command failed: {exc}"


_FAILED_RE = re.compile(r"FAILED\s+(\S+)")
_PASSED_FAILED_RE = re.compile(r"(\d+)\s+(?:failed|passed)", re.IGNORECASE)
_COVERAGE_RE = re.compile(r"TOTAL\s+\d+\s+\d+\s+(\d+)%")


def _parse_pytest_output(output: str) -> tuple[int, list[str], float]:
    """Extract test count, failure names, and coverage % from pytest output."""
    failures = _FAILED_RE.findall(output)

    counts = _PASSED_FAILED_RE.findall(output)
    test_count = sum(int(c) for c in counts)

    cov_match = _COVERAGE_RE.search(output)
    coverage = float(cov_match.group(1)) if cov_match else 0.0

    return test_count, failures, coverage


def run_verification(work_dir: str, *, timeout: int = 300) -> VerificationResult:
    """Run black, pylint, pytest and return structured results.

    All three commands run regardless of earlier failures to collect
    complete evidence.
    """
    start = time.time()

    black_ok, black_output = _run_cmd(["black", "--check", "."], work_dir, timeout)
    pylint_ok, pylint_output = _run_cmd(
        ["pylint", "--errors-only", "golem/"], work_dir, timeout
    )
    pytest_ok, pytest_output = _run_cmd(
        ["pytest", "--cov=golem", "--cov-fail-under=100"], work_dir, timeout
    )

    test_count, failures, coverage_pct = _parse_pytest_output(pytest_output)

    passed = black_ok and pylint_ok and pytest_ok
    duration = time.time() - start

    logger.info(
        "Verification %s: black=%s pylint=%s pytest=%s (%d tests, %.0f%% cov) in %.1fs",
        "PASSED" if passed else "FAILED",
        black_ok,
        pylint_ok,
        pytest_ok,
        test_count,
        coverage_pct,
        duration,
    )

    return VerificationResult(
        passed=passed,
        black_ok=black_ok,
        black_output=black_output,
        pylint_ok=pylint_ok,
        pylint_output=pylint_output,
        pytest_ok=pytest_ok,
        pytest_output=pytest_output,
        test_count=test_count,
        failures=failures,
        coverage_pct=coverage_pct,
        duration_s=round(duration, 2),
    )
