# golem/tests/test_verifier.py
"""Tests for the deterministic verification runner."""

# pylint: disable=missing-function-docstring

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from golem.types import MutationResultDict, VerificationResultDict
from golem.verifier import (
    MutationResult,
    MutmutSummary,
    SurvivedMutant,
    VerificationResult,
    _parse_pytest_output,
    _validate_coverage_data,
    parse_coverage_delta,
    parse_mutmut_results,
    parse_mutmut_summary,
    run_mutation_testing,
    run_verification,
)


class TestParseCoverageDelta:
    def _make_cov_data(self, files=None):
        """Build a minimal valid CoverageDataDict."""
        return {"files": files or {}}

    def test_empty_changed_files_returns_all_covered(self):
        cov_data = self._make_cov_data(
            {"golem/foo.py": {"executed_lines": [1, 2], "missing_lines": [3]}}
        )
        result = parse_coverage_delta(cov_data, [])
        assert result.all_covered is True
        assert result.delta_pct == 100.0
        assert result.uncovered_lines == {}

    def test_valid_data_returns_correct_delta(self):
        cov_data = self._make_cov_data(
            {
                "golem/foo.py": {
                    "executed_lines": [1, 2, 3],
                    "missing_lines": [4],
                }
            }
        )
        result = parse_coverage_delta(cov_data, ["golem/foo.py"])
        assert result.all_covered is False
        assert result.delta_pct == 75.0
        assert result.uncovered_lines == {"golem/foo.py": [4]}

    def test_all_lines_covered_returns_all_covered_true(self):
        cov_data = self._make_cov_data(
            {
                "golem/bar.py": {
                    "executed_lines": [1, 2, 3],
                    "missing_lines": [],
                }
            }
        )
        result = parse_coverage_delta(cov_data, ["golem/bar.py"])
        assert result.all_covered is True
        assert result.delta_pct == 100.0
        assert result.uncovered_lines == {}

    def test_test_files_are_skipped(self):
        cov_data = self._make_cov_data(
            {
                "golem/tests/test_foo.py": {
                    "executed_lines": [1],
                    "missing_lines": [2],
                }
            }
        )
        # Only the test file is in changed_files; it should be skipped
        result = parse_coverage_delta(cov_data, ["golem/tests/test_foo.py"])
        assert result.all_covered is True
        assert result.delta_pct == 100.0
        assert result.uncovered_lines == {}

    def test_missing_files_key_raises_key_error(self):
        """SPEC-1: direct key access on CoverageDataDict raises KeyError if 'files' absent."""
        bad_data = {}  # type: ignore[var-annotated]
        with pytest.raises(KeyError):
            parse_coverage_delta(bad_data, ["golem/foo.py"])

    @pytest.mark.parametrize(
        "file_entry",
        [
            {"missing_lines": [1]},  # missing executed_lines
            {"executed_lines": [1]},  # missing missing_lines
            {},  # missing both
        ],
        ids=["no_executed_lines", "no_missing_lines", "no_keys_at_all"],
    )
    def test_missing_file_entry_keys_raise_key_error(self, file_entry):
        """SPEC-2: direct key access on CoverageFileDataDict raises KeyError if keys absent."""
        cov_data = self._make_cov_data({"golem/foo.py": file_entry})  # type: ignore[arg-type]
        with pytest.raises(KeyError):
            parse_coverage_delta(cov_data, ["golem/foo.py"])


class TestValidateCoverageData:
    def test_valid_data_passes(self):
        data = {
            "files": {
                "golem/foo.py": {
                    "executed_lines": [1, 2, 3],
                    "missing_lines": [4],
                }
            }
        }
        result = _validate_coverage_data(data)
        assert result == data

    def test_valid_data_empty_files_passes(self):
        data = {"files": {}}
        result = _validate_coverage_data(data)
        assert result == data

    def test_valid_data_extra_keys_in_file_entry_passes(self):
        data = {
            "files": {
                "golem/foo.py": {
                    "executed_lines": [1],
                    "missing_lines": [],
                    "summary": {"percent_covered": 100.0},
                }
            }
        }
        result = _validate_coverage_data(data)
        assert result == data

    @pytest.mark.parametrize(
        "bad_input, expected_msg_fragment",
        [
            (None, "must be a dict"),
            ("string", "must be a dict"),
            (42, "must be a dict"),
            ([], "must be a dict"),
        ],
        ids=["none", "string", "int", "list"],
    )
    def test_non_dict_input_raises_type_error(self, bad_input, expected_msg_fragment):
        with pytest.raises(TypeError, match=expected_msg_fragment):
            _validate_coverage_data(bad_input)

    def test_missing_files_key_raises_type_error(self):
        with pytest.raises(TypeError, match="missing required key 'files'"):
            _validate_coverage_data({})

    def test_non_dict_files_value_raises_type_error(self):
        with pytest.raises(TypeError, match="'files' must be a dict"):
            _validate_coverage_data({"files": ["not", "a", "dict"]})

    @pytest.mark.parametrize(
        "file_data, missing_key",
        [
            ({"missing_lines": [1]}, "executed_lines"),
            ({"executed_lines": [1]}, "missing_lines"),
            ({}, "executed_lines"),
        ],
        ids=["no_executed_lines", "no_missing_lines", "no_keys_at_all"],
    )
    def test_file_entry_missing_required_key_raises_type_error(
        self, file_data, missing_key
    ):
        data = {"files": {"golem/foo.py": file_data}}
        with pytest.raises(TypeError, match=missing_key):
            _validate_coverage_data(data)

    @pytest.mark.parametrize(
        "line_key",
        ["executed_lines", "missing_lines"],
        ids=["executed_lines", "missing_lines"],
    )
    def test_non_list_line_field_raises_type_error(self, line_key):
        file_data: dict = {"executed_lines": [1], "missing_lines": []}
        file_data[line_key] = "not_a_list"
        data = {"files": {"golem/foo.py": file_data}}
        with pytest.raises(TypeError, match="must be a list"):
            _validate_coverage_data(data)

    def test_file_entry_non_dict_raises_type_error(self):
        data = {"files": {"golem/foo.py": "not_a_dict"}}
        with pytest.raises(TypeError, match="must be a dict"):
            _validate_coverage_data(data)


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

        def side_effect(cmd, **_kw):
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
        def side_effect(*args, **_kwargs):
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
        def side_effect(*args, **_kwargs):
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
        # Verify all tools were invoked regardless of failures (order-independent)
        called_tools = {call.args[0][0] for call in mock_run.call_args_list}
        assert called_tools >= {"black", "pylint", "pytest"}

    @patch("golem.verifier.subprocess.run")
    def test_deadcode_pylint_fails(self, mock_run):
        """When dead-code pylint returns non-zero, pylint_ok is False."""

        def side_effect(*args, **_kwargs):
            cmd = args[0]
            if "pytest" in cmd:
                return MagicMock(
                    returncode=0, stdout="10 passed\nTOTAL 100 0 100%", stderr=""
                )
            if "black" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            # pylint calls: first (--errors-only) passes, second (dead-code) fails
            if "--errors-only" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "--enable=W0611,W0612,W0101" in cmd:
                return MagicMock(
                    returncode=1,
                    stdout="W0611: Unused import os (unused-import)",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = run_verification("/tmp/workdir")
        assert result.pylint_ok is False
        assert result.passed is False
        assert result.black_ok is True
        assert result.pytest_ok is True

    @patch("golem.verifier.subprocess.run")
    def test_deadcode_pylint_output_combined(self, mock_run):
        """When dead-code pylint fails, output contains both error and dead-code sections."""

        def side_effect(*args, **_kwargs):
            cmd = args[0]
            if "pytest" in cmd:
                return MagicMock(
                    returncode=0, stdout="5 passed\nTOTAL 100 0 100%", stderr=""
                )
            if "black" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            if "--errors-only" in cmd:
                return MagicMock(returncode=0, stdout="errors-only output", stderr="")
            if "--enable=W0611,W0612,W0101" in cmd:
                return MagicMock(
                    returncode=1,
                    stdout="W0611: Unused import os (unused-import)",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = run_verification("/tmp/workdir")
        assert result.pylint_ok is False
        assert "dead-code warnings" in result.pylint_output
        assert "W0611" in result.pylint_output

    @patch("golem.verifier.subprocess.run")
    def test_all_pass_includes_deadcode_check(self, mock_run):
        """When all checks pass, 4 subprocess calls are made (black + 2 pylint + pytest)."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="64 passed in 1.01s\nTOTAL    1000    0   100%\n",
            stderr="",
        )
        result = run_verification("/tmp/workdir")
        assert result.passed is True
        assert result.pylint_ok is True
        # Verify both pylint invocations are present
        all_cmds = [call.args[0] for call in mock_run.call_args_list]
        pylint_cmds = [cmd for cmd in all_cmds if "pylint" in cmd]
        assert len(pylint_cmds) == 2
        errors_only_cmds = [cmd for cmd in pylint_cmds if "--errors-only" in cmd]
        deadcode_cmds = [
            cmd for cmd in pylint_cmds if "--enable=W0611,W0612,W0101" in cmd
        ]
        assert len(errors_only_cmds) == 1
        assert len(deadcode_cmds) == 1

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

        def side_effect(*args, **_kwargs):
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
    def test_coverage_json_missing_keys_returns_none(self, mock_run, tmp_path):
        """When coverage.json is valid JSON but missing required keys, coverage_delta is None."""
        cov_json = tmp_path / "coverage.json"
        cov_json.write_text("{}")

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

    @patch("golem.verifier.subprocess.run")
    def test_to_dict_includes_coverage_delta(self, mock_run, tmp_path):
        """to_dict serializes coverage_delta from a real run_verification call."""
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
        import json as _json

        cov_json.write_text(_json.dumps(cov_data))

        def side_effect(*args, **_kwargs):
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
        d = result.to_dict()
        cd = d["coverage_delta"]
        assert isinstance(cd, dict)
        assert cd["all_covered"] is True
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

    @patch("golem.verifier.subprocess.run")
    def test_mutation_runs_when_pytest_passes(self, mock_run, tmp_path):
        """When pytest passes, mutation testing runs with filtered changed files."""

        def side_effect(*args, **_kwargs):
            cmd = args[0]
            if "git" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="golem/verifier.py\ngolem/tests/test_verifier.py\n",
                    stderr="",
                )
            if "mutmut" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(
                returncode=0,
                stdout="10 passed\nTOTAL 100 0 100%",
                stderr="",
            )

        mock_run.side_effect = side_effect
        result = run_verification(str(tmp_path))
        assert result.mutation_result is not None
        assert result.mutation_result.passed is True
        assert result.mutation_result.exit_code == 0

    @patch("golem.verifier.subprocess.run")
    def test_mutation_skipped_when_pytest_fails(self, mock_run):
        """When pytest fails, mutation testing is skipped and mutation_result is None."""

        def side_effect(*args, **_kwargs):
            cmd = args[0]
            if "pytest" in cmd:
                return MagicMock(
                    returncode=1,
                    stdout="1 failed\nTOTAL 100 50 50%",
                    stderr="",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = side_effect
        result = run_verification("/tmp/workdir")
        assert result.mutation_result is None
        called_tools = {call.args[0][0] for call in mock_run.call_args_list}
        assert "mutmut" not in called_tools

    @patch("golem.verifier.subprocess.run")
    def test_mutation_result_in_to_dict(self, mock_run, tmp_path):
        """to_dict includes mutation_result when mutation testing was executed."""

        def side_effect(*args, **_kwargs):
            cmd = args[0]
            if "git" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="golem/verifier.py\n",
                    stderr="",
                )
            if "mutmut" in cmd:
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(
                returncode=0,
                stdout="5 passed\nTOTAL 100 0 100%",
                stderr="",
            )

        mock_run.side_effect = side_effect
        result = run_verification(str(tmp_path))
        d = result.to_dict()
        assert "mutation_result" in d
        assert d["mutation_result"]["passed"] is True
        assert d["mutation_result"]["exit_code"] == 0
        assert d["mutation_result"]["survived_mutants"] == []

    def test_mutation_result_absent_in_to_dict(self):
        """to_dict omits mutation_result when mutation_result is None."""
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
        assert "mutation_result" not in d

    @patch("golem.verifier.subprocess.run")
    def test_mutation_targets_only_source_files(self, mock_run, tmp_path):
        """mutation testing is called only with non-test .py files."""
        mutmut_calls = []

        def side_effect(*args, **_kwargs):
            cmd = args[0]
            if "git" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout=(
                        "golem/verifier.py\n"
                        "golem/tests/test_verifier.py\n"
                        "golem/runner.py\n"
                        "README.md\n"
                    ),
                    stderr="",
                )
            if "mutmut" in cmd:
                mutmut_calls.append(cmd)
                return MagicMock(returncode=0, stdout="", stderr="")
            return MagicMock(
                returncode=0,
                stdout="5 passed\nTOTAL 100 0 100%",
                stderr="",
            )

        mock_run.side_effect = side_effect
        run_verification(str(tmp_path))
        assert len(mutmut_calls) >= 1
        paths_arg = next(
            arg for arg in mutmut_calls[0] if arg.startswith("--paths-to-mutate=")
        )
        paths = paths_arg.split("=", 1)[1].split(",")
        assert "golem/verifier.py" in paths
        assert "golem/runner.py" in paths
        assert "golem/tests/test_verifier.py" not in paths
        assert "README.md" not in paths

    @patch("golem.verifier.subprocess.run")
    def test_passed_not_affected_by_mutation_failure(self, mock_run, tmp_path):
        """passed remains True even when mutation testing has survivors."""

        def side_effect(*args, **_kwargs):
            cmd = args[0]
            if "git" in cmd:
                return MagicMock(
                    returncode=0,
                    stdout="golem/verifier.py\n",
                    stderr="",
                )
            if "mutmut" in cmd:
                # Non-zero exit code = survivors
                return MagicMock(
                    returncode=1,
                    stdout="\u2838 10/10  \U0001f389 8  \u23f0 0  \U0001f914 0  \U0001f641 2  \U0001f507 0\n",
                    stderr="",
                )
            return MagicMock(
                returncode=0,
                stdout="10 passed\nTOTAL 100 0 100%",
                stderr="",
            )

        mock_run.side_effect = side_effect
        result = run_verification(str(tmp_path))
        assert result.passed is True
        assert result.mutation_result is not None
        assert result.mutation_result.passed is False


class TestRunMutationTesting:
    def test_empty_file_paths_returns_early(self):
        """When file_paths is empty, no subprocess is called and passed=True."""
        with patch("golem.verifier.subprocess.run") as mock_run:
            result = run_mutation_testing([], "/tmp/workdir")
            mock_run.assert_not_called()
        assert result.passed is True
        assert result.exit_code == 0
        assert result.output == "No files to mutate"
        assert result.duration_s == 0.0

    @patch("golem.verifier.subprocess.run")
    def test_successful_run(self, mock_run):
        """Successful mutmut run returns passed=True and exit_code=0."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Killed 10 out of 10 mutants (100%)\n",
            stderr="",
        )
        result = run_mutation_testing(["golem/verifier.py"], "/tmp/workdir")
        assert result.passed is True
        assert result.exit_code == 0
        assert "Killed" in result.output
        # Verify mutmut was called with the correct command
        called_cmd = mock_run.call_args.args[0]
        assert called_cmd[0] == "mutmut"
        assert called_cmd[1] == "run"
        assert "--paths-to-mutate=golem/verifier.py" in called_cmd

    @patch("golem.verifier.subprocess.run")
    def test_failed_run(self, mock_run):
        """When mutmut returns non-zero, passed=False and exit_code=1."""
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="2 out of 10 mutants survived\n",
            stderr="",
        )
        result = run_mutation_testing(["golem/verifier.py"], "/tmp/workdir")
        assert result.passed is False
        assert result.exit_code == 1
        assert "survived" in result.output

    @patch("golem.verifier.subprocess.run")
    def test_timeout_handled(self, mock_run):
        """TimeoutExpired is caught and returns passed=False with timed out in output."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="mutmut", timeout=600)
        result = run_mutation_testing(["golem/verifier.py"], "/tmp/workdir")
        assert result.passed is False
        assert "timed out" in result.output.lower()
        assert result.exit_code == 1
        assert result.duration_s >= 0.0

    @patch("golem.verifier.subprocess.run")
    def test_oserror_handled(self, mock_run):
        """OSError is caught and returns passed=False."""
        mock_run.side_effect = OSError("No such file or directory: 'mutmut'")
        result = run_mutation_testing(["golem/verifier.py"], "/tmp/workdir")
        assert result.passed is False
        assert "command failed" in result.output.lower()
        assert result.exit_code == 1
        assert result.duration_s >= 0.0

    @patch("golem.verifier.subprocess.run")
    def test_custom_timeout(self, mock_run):
        """The timeout kwarg is forwarded to the subprocess call."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = run_mutation_testing(
            ["golem/verifier.py"], "/tmp/workdir", timeout=120
        )
        assert mock_run.call_args.kwargs["timeout"] == 120
        assert result.passed is True
        assert result.exit_code == 0

    @patch("golem.verifier.subprocess.run")
    def test_paths_joined_with_commas(self, mock_run):
        """Multiple file paths are comma-separated in the mutmut command."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        run_mutation_testing(["golem/verifier.py", "golem/runner.py"], "/tmp/workdir")
        called_cmd = mock_run.call_args.args[0]
        assert "--paths-to-mutate=golem/verifier.py,golem/runner.py" in called_cmd

    def test_empty_file_paths_structured_fields_are_zero(self):
        """When file_paths is empty, all count fields are 0 and survived_mutants is empty."""
        with patch("golem.verifier.subprocess.run") as mock_run:
            result = run_mutation_testing([], "/tmp/workdir")
            mock_run.assert_not_called()
        assert result.mutants_total == 0
        assert result.killed == 0
        assert result.survived == 0
        assert result.timeout == 0
        assert result.suspicious == 0
        assert result.skipped == 0
        assert result.survived_mutants == []

    @patch("golem.verifier.subprocess.run")
    def test_successful_run_populates_counts(self, mock_run):
        """Structured count fields are populated from mutmut run output."""
        results_output = (
            "---- golem/verifier.py (line 42) ----\n"
            "1\n\n"
            "---- golem/verifier.py (line 55) ----\n"
            "3\n"
        )

        def side_effect(*args, **_kwargs):
            cmd = args[0]
            if cmd[1] == "results":
                return MagicMock(returncode=0, stdout=results_output, stderr="")
            return MagicMock(
                returncode=0,
                stdout="\u2838 10/10  \U0001f389 8  \u23f0 0  \U0001f914 0  \U0001f641 2  \U0001f507 0\n",
                stderr="",
            )

        mock_run.side_effect = side_effect
        result = run_mutation_testing(["golem/verifier.py"], "/tmp/workdir")
        assert result.mutants_total == 10
        assert result.killed == 8
        assert result.survived == 2
        assert result.timeout == 0
        assert result.suspicious == 0
        assert result.skipped == 0
        assert len(result.survived_mutants) == 2
        assert result.survived_mutants[0].file == "golem/verifier.py"
        assert result.survived_mutants[0].line == 42

    @patch("golem.verifier.subprocess.run")
    def test_run_with_no_summary_line_zero_counts(self, mock_run):
        """When output has no emoji summary line, counts default to 0."""
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Some output without summary line\n",
            stderr="",
        )
        result = run_mutation_testing(["golem/verifier.py"], "/tmp/workdir")
        assert result.mutants_total == 0
        assert result.killed == 0
        assert result.survived == 0


class TestMutmutSummary:
    def test_default_all_zeros(self):
        """MutmutSummary() with no args produces all-zero fields."""
        summary = MutmutSummary()
        assert summary.mutants_total == 0
        assert summary.killed == 0
        assert summary.survived == 0
        assert summary.timeout == 0
        assert summary.suspicious == 0
        assert summary.skipped == 0


class TestParseMutmutSummary:
    @pytest.mark.parametrize(
        "output, expected",
        [
            # Normal progress line with all mutants killed
            (
                "\u2838 10/10  \U0001f389 8  \u23f0 0  \U0001f914 0  \U0001f641 2  \U0001f507 0\n",
                MutmutSummary(
                    mutants_total=10,
                    killed=8,
                    survived=2,
                    timeout=0,
                    suspicious=0,
                    skipped=0,
                ),
            ),
            # All killed, no survivors
            (
                "\u2838 5/5  \U0001f389 5  \u23f0 0  \U0001f914 0  \U0001f641 0  \U0001f507 0",
                MutmutSummary(
                    mutants_total=5,
                    killed=5,
                    survived=0,
                    timeout=0,
                    suspicious=0,
                    skipped=0,
                ),
            ),
            # All zeros/one mutant with skipped
            (
                "\u2838 3/3  \U0001f389 0  \u23f0 1  \U0001f914 1  \U0001f641 0  \U0001f507 1",
                MutmutSummary(
                    mutants_total=3,
                    killed=0,
                    survived=0,
                    timeout=1,
                    suspicious=1,
                    skipped=1,
                ),
            ),
            # Empty output
            (
                "",
                MutmutSummary(
                    mutants_total=0,
                    killed=0,
                    survived=0,
                    timeout=0,
                    suspicious=0,
                    skipped=0,
                ),
            ),
            # No emoji summary line
            (
                "Some random output with no summary",
                MutmutSummary(
                    mutants_total=0,
                    killed=0,
                    survived=0,
                    timeout=0,
                    suspicious=0,
                    skipped=0,
                ),
            ),
            # Partial/truncated line (missing some fields)
            (
                "\u2838 7/7  \U0001f389 4",
                MutmutSummary(
                    mutants_total=0,
                    killed=0,
                    survived=0,
                    timeout=0,
                    suspicious=0,
                    skipped=0,
                ),
            ),
            # Summary line embedded in longer output
            (
                "Running mutmut...\n\u2838 20/20  \U0001f389 18  \u23f0 1  \U0001f914 0  \U0001f641 1  \U0001f507 0\nDone.\n",
                MutmutSummary(
                    mutants_total=20,
                    killed=18,
                    survived=1,
                    timeout=1,
                    suspicious=0,
                    skipped=0,
                ),
            ),
        ],
    )
    def test_parse_mutmut_summary(self, output, expected):
        result = parse_mutmut_summary(output)
        assert result == expected

    def test_parse_mutmut_summary_returns_dataclass_with_all_fields(self):
        """parse_mutmut_summary returns MutmutSummary with correct field values."""
        result = parse_mutmut_summary(
            "\u2838 10/10  \U0001f389 8  \u23f0 0  \U0001f914 0  \U0001f641 2  \U0001f507 0\n"
        )
        assert isinstance(result, MutmutSummary)
        assert result.mutants_total == 10
        assert result.killed == 8
        assert result.survived == 2
        assert result.timeout == 0
        assert result.suspicious == 0
        assert result.skipped == 0


class TestParseMutmutResults:
    @pytest.mark.parametrize(
        "output, expected_mutants",
        [
            # Normal output with two survived mutants
            (
                "To apply a mutant on disk:\n"
                "    mutmut apply <id>\n\n"
                "Survived \U0001f641 (2)\n\n"
                "---- golem/verifier.py (line 42) ----\n"
                "1\n\n"
                "---- golem/verifier.py (line 55) ----\n"
                "3\n",
                [
                    SurvivedMutant(file="golem/verifier.py", line=42, mutant_id=1),
                    SurvivedMutant(file="golem/verifier.py", line=55, mutant_id=3),
                ],
            ),
            # Single survived mutant
            (
                "Survived \U0001f641 (1)\n\n"
                "---- golem/foo.py (line 10) ----\n"
                "7\n",
                [SurvivedMutant(file="golem/foo.py", line=10, mutant_id=7)],
            ),
            # Multiple files
            (
                "Survived \U0001f641 (3)\n\n"
                "---- golem/a.py (line 1) ----\n"
                "2\n\n"
                "---- golem/b.py (line 99) ----\n"
                "5\n\n"
                "---- golem/c.py (line 200) ----\n"
                "10\n",
                [
                    SurvivedMutant(file="golem/a.py", line=1, mutant_id=2),
                    SurvivedMutant(file="golem/b.py", line=99, mutant_id=5),
                    SurvivedMutant(file="golem/c.py", line=200, mutant_id=10),
                ],
            ),
            # No survived mutants (empty results)
            (
                "To apply a mutant on disk:\n"
                "    mutmut apply <id>\n\n"
                "Survived \U0001f641 (0)\n",
                [],
            ),
            # Empty output
            ("", []),
            # Malformed output (no matching blocks)
            ("Random output without mutant blocks", []),
            # Malformed line number (non-numeric) — skipped gracefully
            (
                "---- golem/foo.py (line abc) ----\n" "1\n",
                [],
            ),
            # Missing mutant ID — block without id line skipped
            (
                "---- golem/foo.py (line 10) ----\n",
                [],
            ),
            # Consecutive headers: first block has no ID line (malformed),
            # second block must still be parsed
            (
                "---- golem/a.py (line 10) ----\n"
                "---- golem/b.py (line 20) ----\n"
                "5\n",
                [SurvivedMutant(file="golem/b.py", line=20, mutant_id=5)],
            ),
            # Blank line between header and mutant ID (exercises j += 1, line 262)
            (
                "---- golem/foo.py (line 10) ----\n" "\n" "7\n",
                [SurvivedMutant(file="golem/foo.py", line=10, mutant_id=7)],
            ),
        ],
    )
    def test_parse_mutmut_results(self, output, expected_mutants):
        result = parse_mutmut_results(output)
        assert result == expected_mutants


class TestMutationResultToDict:
    def test_to_dict_contains_all_required_keys(self):
        """to_dict() includes all MutationResultDict required keys."""
        mr = MutationResult(
            exit_code=0,
            output="output text",
            passed=True,
            duration_s=1.5,
            mutants_total=10,
            killed=8,
            survived=2,
            timeout=0,
            suspicious=0,
            skipped=0,
            survived_mutants=[
                SurvivedMutant(file="golem/foo.py", line=42, mutant_id=1)
            ],
        )
        d = mr.to_dict()
        for key in MutationResultDict.__required_keys__:  # pylint: disable=no-member
            assert key in d, f"Missing key: {key}"

    def test_to_dict_serializes_survived_mutants_as_dicts(self):
        """to_dict() converts SurvivedMutant dataclasses to plain dicts."""
        mr = MutationResult(
            exit_code=1,
            output="some output",
            passed=False,
            duration_s=3.7,
            mutants_total=5,
            killed=3,
            survived=2,
            timeout=0,
            suspicious=0,
            skipped=0,
            survived_mutants=[
                SurvivedMutant(file="golem/bar.py", line=10, mutant_id=7),
            ],
        )
        d = mr.to_dict()
        # Verify survived_mutants are serialized as plain dicts, not dataclasses
        assert isinstance(d["survived_mutants"][0], dict)
        assert not hasattr(d["survived_mutants"][0], "__dataclass_fields__")
        assert set(d["survived_mutants"][0].keys()) == {"file", "line", "mutant_id"}

    def test_to_dict_empty_survived_mutants(self):
        """to_dict() with no survived mutants has empty list."""
        mr = MutationResult(
            exit_code=0,
            output="all killed",
            passed=True,
            duration_s=2.0,
            mutants_total=3,
            killed=3,
            survived=0,
            timeout=0,
            suspicious=0,
            skipped=0,
        )
        d = mr.to_dict()
        assert d["survived_mutants"] == []
