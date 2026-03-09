# golem/tests/test_verifier.py
"""Tests for the deterministic verification runner."""

# pylint: disable=missing-function-docstring

import subprocess
from unittest.mock import patch, MagicMock

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
