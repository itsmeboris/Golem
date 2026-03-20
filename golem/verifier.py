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

from golem.types import MutationResultDict, SurvivedMutantDict, VerificationResultDict

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
    mutation_result: "MutationResult | None" = None

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
        if self.mutation_result is not None:
            result["mutation_result"] = self.mutation_result.to_dict()
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


@dataclass
class SurvivedMutant:
    """A single surviving mutant with location info."""

    file: str
    line: int
    mutant_id: int


@dataclass
class MutationResult:
    """Structured output from running mutmut mutation testing."""

    exit_code: int
    output: str
    passed: bool
    duration_s: float
    mutants_total: int = 0
    killed: int = 0
    survived: int = 0
    timeout: int = 0
    suspicious: int = 0
    skipped: int = 0
    survived_mutants: list[SurvivedMutant] = field(default_factory=list)

    def to_dict(self) -> MutationResultDict:
        """Serialize for JSON persistence."""
        survived_list: list[SurvivedMutantDict] = [
            {"file": m.file, "line": m.line, "mutant_id": m.mutant_id}
            for m in self.survived_mutants
        ]
        return {
            "exit_code": self.exit_code,
            "output": self.output,
            "passed": self.passed,
            "duration_s": self.duration_s,
            "mutants_total": self.mutants_total,
            "killed": self.killed,
            "survived": self.survived,
            "timeout": self.timeout,
            "suspicious": self.suspicious,
            "skipped": self.skipped,
            "survived_mutants": survived_list,
        }


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

# mutmut summary line: e.g. "⠸ 10/10  🎉 8  ⏰ 0  🤔 0  🙁 2  🔇 0"
# \u28xx = braille spinner, \U0001f389 = 🎉, \u23f0 = ⏰, \U0001f914 = 🤔,
# \U0001f641 = 🙁, \U0001f507 = 🔇
_MUTMUT_SUMMARY_RE = re.compile(
    r"[\u2800-\u28ff]\s+(\d+)/\d+"
    r"\s+\U0001f389\s+(\d+)"
    r"\s+\u23f0\s+(\d+)"
    r"\s+\U0001f914\s+(\d+)"
    r"\s+\U0001f641\s+(\d+)"
    r"\s+\U0001f507\s+(\d+)"
)

# mutmut results block: e.g. "---- golem/verifier.py (line 42) ----"
_MUTMUT_BLOCK_RE = re.compile(
    r"^----\s+(.+?)\s+\(line\s+(\d+)\)\s+----\s*$", re.MULTILINE
)


def parse_mutmut_summary(output: str) -> dict:
    """Parse the progress/summary line from mutmut run output.

    Returns a dict with keys: mutants_total, killed, survived, timeout,
    suspicious, skipped.  All values are 0 if no summary line is found.
    """
    match = _MUTMUT_SUMMARY_RE.search(output)
    if not match:
        return {
            "mutants_total": 0,
            "killed": 0,
            "survived": 0,
            "timeout": 0,
            "suspicious": 0,
            "skipped": 0,
        }
    total, killed, timeout, suspicious, survived, skipped = (
        int(g) for g in match.groups()
    )
    return {
        "mutants_total": total,
        "killed": killed,
        "survived": survived,
        "timeout": timeout,
        "suspicious": suspicious,
        "skipped": skipped,
    }


def parse_mutmut_results(output: str) -> list[SurvivedMutant]:
    """Parse the detail output from ``mutmut results``.

    Extracts file path, line number, and mutant ID for each survived
    mutant block.  Returns an empty list when no blocks are found or
    when a block is malformed.
    """
    mutants: list[SurvivedMutant] = []
    lines = output.splitlines()
    i = 0
    while i < len(lines):
        block_match = _MUTMUT_BLOCK_RE.match(lines[i])
        if block_match:
            filepath = block_match.group(1)
            line_no = int(block_match.group(2))
            # The mutant ID is on the next non-blank line
            j = i + 1
            mutant_id: int | None = None
            while j < len(lines):
                stripped = lines[j].strip()
                if stripped:
                    try:
                        mutant_id = int(stripped)
                    except ValueError:
                        pass
                    break
                j += 1
            if mutant_id is not None:
                mutants.append(
                    SurvivedMutant(file=filepath, line=line_no, mutant_id=mutant_id)
                )
                i = j + 1
            else:
                i = j
        else:
            i += 1
    return mutants


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
            mutants_total=0,
            killed=0,
            survived=0,
            timeout=0,
            suspicious=0,
            skipped=0,
            survived_mutants=[],
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

    counts = parse_mutmut_summary(output)

    # Collect survived mutant details via `mutmut results` (separate command)
    survived_mutants: list[SurvivedMutant] = []
    if counts["survived"] > 0:
        _, results_output = _run_cmd(["mutmut", "results"], work_dir, timeout)
        survived_mutants = parse_mutmut_results(results_output)

    return MutationResult(
        exit_code=0 if success else 1,
        output=output,
        passed=success,
        duration_s=duration,
        mutants_total=counts["mutants_total"],
        killed=counts["killed"],
        survived=counts["survived"],
        timeout=counts["timeout"],
        suspicious=counts["suspicious"],
        skipped=counts["skipped"],
        survived_mutants=survived_mutants,
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
    deadcode_ok, deadcode_output = _run_cmd(
        ["pylint", "--disable=all", "--enable=W0611,W0612,W0101", "golem/"],
        work_dir,
        timeout,
    )
    if not deadcode_ok:
        pylint_ok = False
        pylint_output = (
            pylint_output + "\n--- dead-code warnings ---\n" + deadcode_output
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

    mutation_result: MutationResult | None = None
    if pytest_ok:
        changed_files = _get_changed_files(work_dir)
        source_files = [
            f
            for f in changed_files
            if f.endswith(".py") and "/test_" not in f and not f.startswith("test_")
        ]
        mutation_result = run_mutation_testing(source_files, work_dir, timeout=timeout)

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
        mutation_result=mutation_result,
    )
