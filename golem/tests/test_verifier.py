# golem/tests/test_verifier.py
"""Tests for the deterministic verification runner."""

# pylint: disable=missing-function-docstring

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

from golem.verifier import run_verification, VerificationResult


class TestVerificationResult:
    def test_all_pass(self):
        r = VerificationResult(
            passed=True,
            black_ok=True,
            black_output="",
            pylint_ok=True,
            pylint_output="",
            pytest_ok=True,
            pytest_output="64 passed in 1.01s",
            test_count=64,
            failures=[],
            coverage_pct=100.0,
            duration_s=1.5,
        )
        assert r.passed is True

    def test_partial_failure(self):
        r = VerificationResult(
            passed=False,
            black_ok=True,
            black_output="",
            pylint_ok=False,
            pylint_output="E0001: syntax error",
            pytest_ok=True,
            pytest_output="64 passed",
            test_count=64,
            failures=[],
            coverage_pct=100.0,
            duration_s=2.0,
        )
        assert r.passed is False
        assert r.pylint_ok is False

    def test_to_dict(self):
        r = VerificationResult(
            passed=True,
            black_ok=True,
            black_output="",
            pylint_ok=True,
            pylint_output="",
            pytest_ok=True,
            pytest_output="",
            test_count=64,
            failures=[],
            coverage_pct=100.0,
            duration_s=1.0,
        )
        d = r.to_dict()
        assert d["passed"] is True
        assert d["duration_s"] == 1.0
        # Verify it matches VerificationResultDict keys
        from golem.types import VerificationResultDict

        for (
            key
        ) in VerificationResultDict.__required_keys__:  # pylint: disable=no-member
            assert key in d, f"Missing key: {key}"


class TestRunVerification:
    @patch("golem.verifier.subprocess.run")
    def test_all_commands_pass(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="All done!\n64 passed in 1.01s\n" "TOTAL    1000    0   100%\n",
            stderr="",
        )
        result = run_verification("/tmp/workdir")
        assert result.passed is True
        assert result.black_ok is True
        assert result.pylint_ok is True
        assert result.pytest_ok is True
        assert mock_run.call_count == 3  # black, pylint, pytest

    @patch("golem.verifier.subprocess.run")
    def test_black_fails(self, mock_run):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "black" in cmd:
                return MagicMock(
                    returncode=1, stdout="would reformat foo.py", stderr=""
                )
            return MagicMock(
                returncode=0, stdout="64 passed\nTOTAL 1000 0 100%", stderr=""
            )

        mock_run.side_effect = side_effect
        result = run_verification("/tmp/workdir")
        assert result.passed is False
        assert result.black_ok is False
        assert "would reformat" in result.black_output

    @patch("golem.verifier.subprocess.run")
    def test_pytest_fails_with_failures(self, mock_run):
        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "pytest" in cmd:
                return MagicMock(
                    returncode=1,
                    stdout="FAILED golem/tests/test_foo.py::test_bar\n"
                    "FAILED golem/tests/test_foo.py::test_baz\n"
                    "2 failed, 62 passed in 3.00s\n"
                    "TOTAL    1000    50    95%\n",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = run_verification("/tmp/workdir")
        assert result.passed is False
        assert result.pytest_ok is False
        assert result.test_count == 64  # 2 + 62
        assert len(result.failures) == 2
        assert "test_bar" in result.failures[0]
        assert result.coverage_pct == 95.0

    @patch("golem.verifier.subprocess.run")
    def test_all_three_run_even_if_first_fails(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="error", stderr="")
        _ = run_verification("/tmp/workdir")
        assert mock_run.call_count == 3  # all three still run

    @patch("golem.verifier.subprocess.run")
    def test_timeout_handled(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="black", timeout=300)
        result = run_verification("/tmp/workdir")
        assert result.passed is False
        assert "timed out" in result.black_output.lower()

    @patch("golem.verifier.subprocess.run")
    def test_oserror_handled(self, mock_run):
        mock_run.side_effect = OSError("No such file or directory: 'black'")
        result = run_verification("/tmp/workdir")
        assert result.passed is False
        assert "command failed" in result.black_output.lower()

    @patch("golem.verifier.subprocess.run")
    def test_coverage_json_parsed_when_exists(self, mock_run, tmp_path):
        """When coverage.json exists after pytest, it is parsed into coverage_delta."""
        cov_json = tmp_path / "coverage.json"
        cov_data = {
            "files": {
                "golem/foo.py": {
                    "executed_lines": [1, 2, 3],
                    "missing_lines": [],
                    "summary": {"percent_covered": 100.0},
                }
            }
        }
        cov_json.write_text(json.dumps(cov_data))

        def side_effect(*args, **kwargs):
            cmd = args[0]
            if "git" in cmd:
                return MagicMock(
                    returncode=0, stdout="golem/foo.py\n", stderr=""
                )
            return MagicMock(
                returncode=0,
                stdout="1 passed\nTOTAL 100 0 100%",
                stderr="",
            )

        mock_run.side_effect = side_effect
        result = run_verification(str(tmp_path))
        assert result.coverage_delta is not None
        assert result.coverage_delta.all_covered is True
        assert not cov_json.exists()  # cleaned up

    @patch("golem.verifier.subprocess.run")
    def test_coverage_json_parse_error_logged(self, mock_run, tmp_path):
        """When coverage.json contains invalid JSON, a warning is logged."""
        cov_json = tmp_path / "coverage.json"
        cov_json.write_text("not valid json {{{")

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1 passed\nTOTAL 100 0 100%",
            stderr="",
        )
        result = run_verification(str(tmp_path))
        assert result.coverage_delta is None
        assert not cov_json.exists()  # still cleaned up

    @patch("golem.verifier.subprocess.run")
    def test_coverage_delta_none_when_no_json(self, mock_run):
        """When coverage.json does not exist, coverage_delta is None."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="1 passed\nTOTAL 100 0 100%",
            stderr="",
        )
        result = run_verification("/tmp/workdir_no_json")
        assert result.coverage_delta is None

    def test_to_dict_includes_coverage_delta(self):
        """to_dict includes coverage_delta key."""
        from golem.verifier import CoverageDelta

        delta = CoverageDelta(
            all_covered=True, delta_pct=100.0, uncovered_lines={}
        )
        r = VerificationResult(
            passed=True,
            black_ok=True,
            black_output="",
            pylint_ok=True,
            pylint_output="",
            pytest_ok=True,
            pytest_output="",
            test_count=1,
            failures=[],
            coverage_pct=100.0,
            duration_s=1.0,
            coverage_delta=delta,
        )
        d = r.to_dict()
        assert d["coverage_delta"] is not None
        assert d["coverage_delta"]["all_covered"] is True
        assert d["coverage_delta"]["delta_pct"] == 100.0
        assert d["coverage_delta"]["uncovered_lines"] == {}

    def test_to_dict_coverage_delta_none(self):
        """to_dict returns None for coverage_delta when not set."""
        r = VerificationResult(
            passed=True,
            black_ok=True,
            black_output="",
            pylint_ok=True,
            pylint_output="",
            pytest_ok=True,
            pytest_output="",
            test_count=1,
            failures=[],
            coverage_pct=100.0,
            duration_s=1.0,
        )
        d = r.to_dict()
        assert d["coverage_delta"] is None
