# golem/verifier.py
"""Deterministic verification runner for Golem task output.

Runs black, pylint, and pytest independently in the agent's work directory.
Returns structured results -- no LLM judgment, just facts.

Part of the Quality Assurance Pipeline v2 (Layer 2).
See: docs/plans/2026-03-09-quality-assurance-pipeline-v2-design.md
"""

import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

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
    coverage_delta: "CoverageDelta | None" = None

    def to_dict(self) -> VerificationResultDict:
        """Serialize for JSON persistence."""
        result: VerificationResultDict = {
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
        if self.coverage_delta:
            result["coverage_delta"] = {
                "all_covered": self.coverage_delta.all_covered,
                "delta_pct": self.coverage_delta.delta_pct,
                "uncovered_lines": self.coverage_delta.uncovered_lines,
            }
        return result


@dataclass
class CoverageDelta:
    """Coverage analysis for changed files only."""

    all_covered: bool
    delta_pct: float
    uncovered_lines: dict[str, list[int]]  # {filepath: [line_numbers]}

    def summary(self) -> str:
        if self.all_covered:
            return "Coverage delta: 100% on changed files"
        parts = []
        for path, lines in self.uncovered_lines.items():
            parts.append(f"  {path}: lines {lines}")
        return f"Coverage delta: {self.delta_pct:.0f}%\n" + "\n".join(parts)


def parse_coverage_delta(cov_data: dict, changed_files: list[str]) -> CoverageDelta:
    """Analyze coverage for changed files only."""
    if not changed_files:
        return CoverageDelta(all_covered=True, delta_pct=100.0, uncovered_lines={})

    files_data = cov_data.get("files", {})
    uncovered: dict[str, list[int]] = {}
    total_lines = 0
    covered_lines = 0

    for filepath in changed_files:
        # Skip test files
        if "/test_" in filepath or filepath.startswith("test_"):
            continue

        file_cov = files_data.get(filepath)
        if file_cov is None:
            continue

        executed = file_cov.get("executed_lines", [])
        missing = file_cov.get("missing_lines", [])
        total_lines += len(executed) + len(missing)
        covered_lines += len(executed)

        if missing:
            uncovered[filepath] = missing

    if total_lines == 0:
        return CoverageDelta(all_covered=True, delta_pct=100.0, uncovered_lines={})

    delta_pct = (covered_lines / total_lines) * 100
    return CoverageDelta(
        all_covered=len(uncovered) == 0,
        delta_pct=delta_pct,
        uncovered_lines=uncovered,
    )


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


def _get_changed_files(work_dir: str) -> list[str]:
    """Get list of files changed relative to HEAD~1 or merge-base."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().splitlines()
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("Failed to get changed files: %s", exc)
    return []


def _load_coverage_delta(cov_json_path: Path, work_dir: str) -> CoverageDelta | None:
    """Parse coverage delta from JSON, cleaning up the file afterwards."""
    if not cov_json_path.exists():
        return None
    try:
        cov_data = json.loads(cov_json_path.read_text())
        changed_files = _get_changed_files(work_dir)
        return parse_coverage_delta(cov_data, changed_files)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to parse coverage JSON: %s", exc)
        return None
    finally:
        cov_json_path.unlink(missing_ok=True)


@dataclass
class MutationResult:
    """Structured output from running mutmut mutation testing."""

    exit_code: int
    output: str
    passed: bool
    duration_s: float


def run_mutation_testing(
    file_paths: list[str], work_dir: str, *, timeout: int = 600
) -> MutationResult:
    """Run mutmut mutation testing on the given files.

    Returns early with passed=True if file_paths is empty.
    """
    if not file_paths:
        return MutationResult(
            exit_code=0,
            output="No files to mutate",
            passed=True,
            duration_s=0.0,
        )

    start = time.time()
    cmd = ["mutmut", "run", "--paths-to-mutate=" + ",".join(file_paths)]
    success, output = _run_cmd(cmd, work_dir, timeout)
    duration = round(time.time() - start, 2)

    logger.info(
        "Mutation testing %s in %.1fs",
        "PASSED" if success else "FAILED",
        duration,
    )

    return MutationResult(
        exit_code=0 if success else 1,
        output=output,
        passed=success,
        duration_s=duration,
    )


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
    cov_json_path = Path(work_dir) / "coverage.json"
    pytest_ok, pytest_output = _run_cmd(
        [
            "pytest",
            "--cov=golem",
            "--cov-fail-under=100",
            "--cov-report=term",
            f"--cov-report=json:{cov_json_path}",
        ],
        work_dir,
        timeout,
    )

    test_count, failures, coverage_pct = _parse_pytest_output(pytest_output)

    coverage_delta = _load_coverage_delta(cov_json_path, work_dir)
    passed = black_ok and pylint_ok and pytest_ok

    logger.info(
        "Verification %s: black=%s pylint=%s pytest=%s (%d tests, %.0f%% cov) in %.1fs",
        "PASSED" if passed else "FAILED",
        black_ok,
        pylint_ok,
        pytest_ok,
        test_count,
        coverage_pct,
        time.time() - start,
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
        duration_s=round(time.time() - start, 2),
        coverage_delta=coverage_delta,
    )
