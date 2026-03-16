# golem/tests/test_verifier.py
"""Tests for the deterministic verification runner."""

# pylint: disable=missing-function-docstring

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from golem.verifier import _parse_pytest_output, run_verification, VerificationResult


class TestParsePytestOutput:
    @pytest.mark.parametrize(
        "output, expected_count, expected_failures, expected_coverage",
        [
            # Normal: passed only
            ("64 passed in 1.01s\nTOTAL    1000    0   100%", 64, [], 100.0),
            # Mixed passed and failed
            (
                "FAILED golem/tests/test_foo.py::test_bar\n"
                "FAILED golem/tests/test_baz.py::test_qux\n"
                "2 failed, 10 passed in 3.5s\n"
                "TOTAL    500    50    90%",
                12,
                [
                    "golem/tests/test_foo.py::test_bar",
                    "golem/tests/test_baz.py::test_qux",
                ],
                90.0,
            ),
            # Zero tests
            ("0 passed in 0.01s", 0, [], 0.0),
            # No coverage line
            ("10 passed in 1.0s", 10, [], 0.0),
            # Only failed, no passed line
            (
                "FAILED test_x.py::test_y\n1 failed in 0.5s",
                1,
                ["test_x.py::test_y"],
                0.0,
            ),
            # Empty output
            ("", 0, [], 0.0),
            # Malformed — no numbers at all
            ("Some random output\nwith no test results", 0, [], 0.0),
        ],
    )
    def test_parse_pytest_output(
        self, output, expected_count, expected_failures, expected_coverage
    ):
        count, failures, coverage = _parse_pytest_output(output)
        assert count == expected_count
        assert failures == expected_failures
        assert coverage == expected_coverage


class TestVerificationResult:
    @patch("golem.verifier.subprocess.run")
    def test_all_pass_computed(self, mock_run):
        """run_verification computes passed=True when all tools succeed."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="64 passed in 1.01s\nTOTAL    1000    0   100%\n",
            stderr="",
        )
        result = run_verification("/tmp/test")
        assert result.passed is True
        assert result.black_ok is True
        assert result.pylint_ok is True
        assert result.pytest_ok is True
        assert result.test_count == 64
        assert result.coverage_pct == 100.0

    @patch("golem.verifier.subprocess.run")
    def test_partial_failure_computed(self, mock_run):
        """run_verification computes passed=False when pylint fails."""

        def side_effect(cmd, **kw):
            if "pylint" in cmd:
                return MagicMock(returncode=1, stdout="E0001: syntax error", stderr="")
            return MagicMock(
                returncode=0,
                stdout="10 passed in 0.5s\nTOTAL    100    0   100%\n",
                stderr="",
            )

        mock_run.side_effect = side_effect
        result = run_verification("/tmp/test")
        assert result.passed is False
        assert result.black_ok is True
        assert result.pylint_ok is False
        assert result.pytest_ok is True

    @patch("golem.verifier.subprocess.run")
    def test_to_dict_matches_contract(self, mock_run):
        """to_dict() output from run_verification matches VerificationResultDict."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="64 passed in 1.01s\nTOTAL    1000    0   100%\n",
            stderr="",
        )
        result = run_verification("/tmp/test")
        d = result.to_dict()
        from golem.types import VerificationResultDict

        for (
            key
        ) in VerificationResultDict.__required_keys__:  # pylint: disable=no-member
            assert key in d, f"Missing key: {key}"
        assert isinstance(d["passed"], bool)
        assert isinstance(d["test_count"], int)
        assert isinstance(d["coverage_pct"], float)
        assert d["test_count"] == 64
        assert d["coverage_pct"] == 100.0


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
        # Verify all three tools were invoked (order-independent)
        called_tools = {call.args[0][0] for call in mock_run.call_args_list}
        assert called_tools >= {"black", "pylint", "pytest"}

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
                return MagicMock(returncode=0, stdout="golem/foo.py\n", stderr="")
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

        delta = CoverageDelta(all_covered=True, delta_pct=100.0, uncovered_lines={})
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
        cd = d["coverage_delta"]
        assert cd["all_covered"] is True
        assert cd["delta_pct"] == 100.0
        assert cd["uncovered_lines"] == {}

    def test_to_dict_coverage_delta_absent(self):
        """to_dict omits coverage_delta when not set."""
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
        assert "coverage_delta" not in d
