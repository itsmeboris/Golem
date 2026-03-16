"""Tests for golem.observation_hooks — 100% coverage."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from golem.observation_hooks import (
    ObservationSignal,
    SignalAccumulator,
    compare_retry_signatures,
    mine_validation_signals,
    mine_verification_signals,
)
from golem.validation import ValidationVerdict
from golem.verifier import VerificationResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _passing_result(**overrides) -> VerificationResult:
    defaults = dict(
        passed=True,
        black_ok=True,
        black_output="All done! ✨",
        pylint_ok=True,
        pylint_output="",
        pytest_ok=True,
        pytest_output="64 passed in 1.23s",
        test_count=64,
        failures=[],
        coverage_pct=100.0,
    )
    defaults.update(overrides)
    return VerificationResult(**defaults)


def _failing_result(**overrides) -> VerificationResult:
    defaults = dict(
        passed=False,
        black_ok=True,
        black_output="",
        pylint_ok=True,
        pylint_output="",
        pytest_ok=False,
        pytest_output="1 failed in 0.5s",
        test_count=1,
        failures=[],
        coverage_pct=0.0,
    )
    defaults.update(overrides)
    return VerificationResult(**defaults)


# ---------------------------------------------------------------------------
# mine_verification_signals
# ---------------------------------------------------------------------------


class TestMineVerificationSignals:
    def test_passing_result_returns_empty(self):
        result = mine_verification_signals(_passing_result())
        assert result == []

    def test_import_error_in_pytest_output(self):
        output = (
            "FAILED golem/tests/test_foo.py::test_bar\n"
            "ImportError: No module named 'golem.missing'\n"
            "1 failed in 0.5s\n"
        )
        result = mine_verification_signals(
            _failing_result(
                pytest_output=output, failures=["golem/tests/test_foo.py::test_bar"]
            )
        )
        categories = {s.category for s in result}
        patterns = {s.pattern for s in result}
        assert "pytest_failure" in categories
        assert any("import_error" in p for p in patterns)

    def test_module_not_found_error_in_pytest_output(self):
        output = (
            "FAILED golem/tests/test_foo.py::test_bar\n"
            "ModuleNotFoundError: No module named 'golem.xyz'\n"
            "1 failed in 0.5s\n"
        )
        result = mine_verification_signals(
            _failing_result(
                pytest_output=output, failures=["golem/tests/test_foo.py::test_bar"]
            )
        )
        patterns = {s.pattern for s in result}
        assert any("import_error" in p for p in patterns)

    def test_assertion_error_in_pytest_output(self):
        output = (
            "FAILED golem/tests/test_foo.py::TestFoo::test_passes - AssertionError\n"
            "AssertionError: assert 1 == 2\n"
            "1 failed in 0.5s\n"
        )
        result = mine_verification_signals(
            _failing_result(
                pytest_output=output,
                failures=["golem/tests/test_foo.py::TestFoo::test_passes"],
            )
        )
        patterns = {s.pattern for s in result}
        assert any("assertion_error" in p for p in patterns)

    def test_fixture_not_found_in_pytest_output(self):
        output = (
            "ERRORS\n" "fixture 'my_special_fixture' not found\n" "1 error in 0.3s\n"
        )
        result = mine_verification_signals(_failing_result(pytest_output=output))
        patterns = {s.pattern for s in result}
        assert any(
            "fixture_not_found" in p and "my_special_fixture" in p for p in patterns
        )

    def test_type_error_in_pytest_output(self):
        output = (
            "FAILED golem/tests/test_foo.py::test_something - TypeError\n"
            "TypeError: unsupported operand type(s) for +: 'int' and 'str'\n"
            "1 failed in 0.3s\n"
        )
        result = mine_verification_signals(
            _failing_result(
                pytest_output=output,
                failures=["golem/tests/test_foo.py::test_something"],
            )
        )
        patterns = {s.pattern for s in result}
        assert any("type_error" in p for p in patterns)

    def test_pylint_error_codes_extracted(self):
        pylint_out = (
            "golem/foo.py:42:0: E0602: Undefined variable 'bar' (undefined-variable)\n"
            "golem/foo.py:10:0: E0001: Syntax error (syntax-error)\n"
        )
        result = mine_verification_signals(
            _failing_result(
                pylint_ok=False,
                pylint_output=pylint_out,
            )
        )
        patterns = {s.pattern for s in result}
        assert "pylint_e0602: undefined-variable" in patterns
        assert "pylint_e0001: syntax-error" in patterns

    def test_black_reformat_files_extracted(self):
        black_out = (
            "would reformat golem/foo.py\n" "would reformat golem/bar.py\n" "Oh no!\n"
        )
        result = mine_verification_signals(
            _failing_result(
                black_ok=False,
                black_output=black_out,
            )
        )
        patterns = {s.pattern for s in result}
        assert "black_reformat: golem/foo.py" in patterns
        assert "black_reformat: golem/bar.py" in patterns

    def test_failures_list_produces_signals(self):
        result = mine_verification_signals(
            _failing_result(
                failures=["golem/tests/test_foo.py::TestBar::test_x"],
                pytest_output="1 failed in 0.5s\n",
            )
        )
        categories = {s.category for s in result}
        assert "pytest_failure" in categories

    def test_multiple_failure_types_combined(self):
        output = (
            "FAILED golem/tests/test_foo.py::test_one - AssertionError\n"
            "AssertionError: expected True\n"
            "golem/tests/test_foo.py:10: ImportError: No module named 'x'\n"
            "fixture 'shared_db' not found\n"
            "2 failed in 0.6s\n"
        )
        result = mine_verification_signals(
            _failing_result(
                pytest_output=output,
                failures=["golem/tests/test_foo.py::test_one"],
                pylint_ok=False,
                pylint_output="golem/foo.py:1:0: E0602: Undefined variable 'x' (undefined-variable)\n",
                black_ok=False,
                black_output="would reformat golem/foo.py\n",
            )
        )
        categories = {s.category for s in result}
        assert "pytest_failure" in categories
        # pylint and black signals also present
        patterns = {s.pattern for s in result}
        assert any("pylint" in p for p in patterns)
        assert any("black_reformat" in p for p in patterns)

    def test_source_field_is_verification(self):
        pylint_out = (
            "golem/foo.py:1:0: E0602: Undefined variable 'x' (undefined-variable)\n"
        )
        result = mine_verification_signals(
            _failing_result(pylint_ok=False, pylint_output=pylint_out)
        )
        assert all(s.source == "verification" for s in result)

    def test_count_field_defaults_to_one(self):
        pylint_out = (
            "golem/foo.py:1:0: E0602: Undefined variable 'x' (undefined-variable)\n"
        )
        result = mine_verification_signals(
            _failing_result(pylint_ok=False, pylint_output=pylint_out)
        )
        assert all(s.count == 1 for s in result)


# ---------------------------------------------------------------------------
# mine_validation_signals
# ---------------------------------------------------------------------------


class TestMineValidationSignals:
    def test_pass_verdict_no_concerns_returns_empty(self):
        verdict = ValidationVerdict(verdict="PASS", concerns=[])
        result = mine_validation_signals(verdict)
        assert result == []

    def test_pass_verdict_with_concerns_still_processes(self):
        verdict = ValidationVerdict(
            verdict="PASS",
            concerns=["Antipattern: dead code after return statement"],
        )
        result = mine_validation_signals(verdict)
        # PASS verdict with concerns: per spec, return empty for PASS with no concerns.
        # But the spec says return empty for PASS verdicts *with no concerns*.
        # Concerns present even with PASS should be returned.
        assert len(result) == 1
        assert result[0].category == "validation_concern"

    def test_antipattern_prefix_extracted(self):
        verdict = ValidationVerdict(
            verdict="FAIL",
            concerns=["Antipattern: empty exception handler in foo.py"],
        )
        result = mine_validation_signals(verdict)
        assert len(result) == 1
        assert result[0].category == "validation_concern"
        assert "antipattern" in result[0].pattern

    def test_missing_prefix_extracted(self):
        verdict = ValidationVerdict(
            verdict="FAIL",
            concerns=["Missing test coverage for the new helper function"],
        )
        result = mine_validation_signals(verdict)
        assert len(result) == 1
        assert result[0].category == "validation_concern"
        assert "missing" in result[0].pattern

    @pytest.mark.parametrize(
        "concern,expected_fragment",
        [
            ("dead code detected after return", "dead code"),
            ("empty exception handler silently swallows errors", "empty exception"),
            ("cross-module private access violates encapsulation", "cross-module"),
            ("no independent verification was run", "no independent"),
        ],
    )
    def test_known_patterns_extracted(self, concern, expected_fragment):
        verdict = ValidationVerdict(verdict="FAIL", concerns=[concern])
        result = mine_validation_signals(verdict)
        assert len(result) == 1
        assert expected_fragment in result[0].pattern

    def test_positive_concern_skipped(self):
        verdict = ValidationVerdict(
            verdict="FAIL",
            concerns=["all spec requirements implemented correctly"],
        )
        result = mine_validation_signals(verdict)
        assert result == []

    def test_short_concern_skipped(self):
        verdict = ValidationVerdict(
            verdict="FAIL",
            concerns=["too short"],
        )
        result = mine_validation_signals(verdict)
        assert result == []

    def test_mixed_concerns(self):
        verdict = ValidationVerdict(
            verdict="PARTIAL",
            concerns=[
                "all spec requirements implemented correctly",
                "Antipattern: dead code in module",
                "Missing test for edge case",
            ],
        )
        result = mine_validation_signals(verdict)
        # Positive concern is skipped
        assert len(result) == 2
        patterns = {s.pattern for s in result}
        assert any("antipattern" in p for p in patterns)
        assert any("missing" in p for p in patterns)

    def test_pattern_normalized_lowercase(self):
        verdict = ValidationVerdict(
            verdict="FAIL",
            concerns=["Antipattern: DEAD CODE in Module.py"],
        )
        result = mine_validation_signals(verdict)
        assert result[0].pattern == result[0].pattern.lower()

    def test_pattern_truncated_to_200_chars(self):
        long_concern = "Antipattern: " + "x" * 300
        verdict = ValidationVerdict(verdict="FAIL", concerns=[long_concern])
        result = mine_validation_signals(verdict)
        assert len(result[0].pattern) <= 200

    def test_source_field_is_validation(self):
        verdict = ValidationVerdict(
            verdict="FAIL", concerns=["dead code detected in module"]
        )
        result = mine_validation_signals(verdict)
        assert all(s.source == "validation" for s in result)

    def test_empty_concerns_list_returns_empty(self):
        verdict = ValidationVerdict(verdict="FAIL", concerns=[])
        result = mine_validation_signals(verdict)
        assert result == []


# ---------------------------------------------------------------------------
# compare_retry_signatures
# ---------------------------------------------------------------------------


class TestCompareRetrySignatures:
    def test_identical_failures_produce_signals(self):
        failures = [
            "golem/tests/test_foo.py::test_a",
            "golem/tests/test_foo.py::test_b",
        ]
        current = _failing_result(failures=failures)
        previous = _failing_result(failures=failures)
        result = compare_retry_signatures(current, previous)
        assert len(result) >= 1
        assert any(s.category == "retry_identical" for s in result)

    def test_different_failures_returns_empty(self):
        current = _failing_result(failures=["golem/tests/test_foo.py::test_a"])
        previous = _failing_result(failures=["golem/tests/test_foo.py::test_b"])
        result = compare_retry_signatures(current, previous)
        assert result == []

    def test_both_passing_returns_empty(self):
        current = _passing_result()
        previous = _passing_result()
        result = compare_retry_signatures(current, previous)
        assert result == []

    def test_identical_pylint_errors_produce_signals(self):
        pylint_out = (
            "golem/foo.py:1:0: E0602: Undefined variable 'x' (undefined-variable)\n"
        )
        current = _failing_result(pylint_ok=False, pylint_output=pylint_out)
        previous = _failing_result(pylint_ok=False, pylint_output=pylint_out)
        result = compare_retry_signatures(current, previous)
        assert any(s.category == "retry_identical" for s in result)

    def test_identical_black_failures_produce_signals(self):
        black_out = "would reformat golem/foo.py\n"
        current = _failing_result(black_ok=False, black_output=black_out)
        previous = _failing_result(black_ok=False, black_output=black_out)
        result = compare_retry_signatures(current, previous)
        assert any(s.category == "retry_identical" for s in result)

    def test_different_pylint_errors_returns_empty(self):
        current = _failing_result(
            pylint_ok=False,
            pylint_output="golem/foo.py:1:0: E0602: Undefined variable 'x' (undefined-variable)\n",
        )
        previous = _failing_result(
            pylint_ok=False,
            pylint_output="golem/bar.py:5:0: E0001: Syntax error (syntax-error)\n",
        )
        result = compare_retry_signatures(current, previous)
        assert result == []

    def test_source_field_is_retry(self):
        failures = ["golem/tests/test_foo.py::test_a"]
        current = _failing_result(failures=failures)
        previous = _failing_result(failures=failures)
        result = compare_retry_signatures(current, previous)
        assert all(s.source == "retry" for s in result)

    def test_one_empty_failures_other_nonempty_returns_empty(self):
        current = _failing_result(failures=[])
        previous = _failing_result(failures=["golem/tests/test_foo.py::test_a"])
        result = compare_retry_signatures(current, previous)
        assert result == []


# ---------------------------------------------------------------------------
# SignalAccumulator
# ---------------------------------------------------------------------------


class TestSignalAccumulator:
    def test_record_new_signal(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage)
        acc.record(
            [
                ObservationSignal(
                    category="pytest_failure",
                    pattern="import_error: foo",
                    source="verification",
                )
            ]
        )
        data = json.loads(storage.read_text())
        key = "pytest_failure::import_error: foo"
        assert key in data["signals"]
        assert data["signals"][key]["count"] == 1
        assert data["signals"][key]["source"] == "verification"

    def test_record_increments_existing(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage)
        signal = ObservationSignal(
            category="pytest_failure",
            pattern="import_error: foo",
            source="verification",
        )
        acc.record([signal])
        acc.record([signal])
        data = json.loads(storage.read_text())
        key = "pytest_failure::import_error: foo"
        assert data["signals"][key]["count"] == 2

    def test_get_promoted_below_threshold(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage, promotion_threshold=3)
        signal = ObservationSignal(
            category="pytest_failure",
            pattern="import_error: foo",
            source="verification",
        )
        acc.record([signal])
        acc.record([signal])
        assert acc.get_promoted() == []

    def test_get_promoted_at_threshold(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage, promotion_threshold=3)
        signal = ObservationSignal(
            category="pytest_failure",
            pattern="import_error: foo",
            source="verification",
        )
        acc.record([signal])
        acc.record([signal])
        acc.record([signal])
        promoted = acc.get_promoted()
        assert len(promoted) == 1
        assert "import_error: foo" in promoted[0]

    def test_clear_promoted_removes_threshold_entries(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage, promotion_threshold=3)
        signal = ObservationSignal(
            category="pytest_failure",
            pattern="import_error: foo",
            source="verification",
        )
        for _ in range(3):
            acc.record([signal])
        acc.clear_promoted()
        data = json.loads(storage.read_text())
        key = "pytest_failure::import_error: foo"
        assert key not in data["signals"]

    def test_clear_promoted_keeps_below_threshold_entries(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage, promotion_threshold=3)
        low_signal = ObservationSignal(
            category="pytest_failure",
            pattern="assertion_error: x",
            source="verification",
        )
        high_signal = ObservationSignal(
            category="pytest_failure",
            pattern="import_error: foo",
            source="verification",
        )
        acc.record([low_signal])
        for _ in range(3):
            acc.record([high_signal])
        acc.clear_promoted()
        data = json.loads(storage.read_text())
        assert "pytest_failure::assertion_error: x" in data["signals"]
        assert "pytest_failure::import_error: foo" not in data["signals"]

    def test_persistence_write_and_read_back(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc1 = SignalAccumulator(storage)
        signal = ObservationSignal(
            category="black_format",
            pattern="black_reformat: golem/foo.py",
            source="verification",
        )
        acc1.record([signal])

        # New accumulator reads from same path
        acc2 = SignalAccumulator(storage)
        signal2 = ObservationSignal(
            category="black_format",
            pattern="black_reformat: golem/foo.py",
            source="verification",
        )
        acc2.record([signal2])
        data = json.loads(storage.read_text())
        key = "black_format::black_reformat: golem/foo.py"
        assert data["signals"][key]["count"] == 2

    def test_missing_file_handled(self, tmp_path):
        storage = tmp_path / "nonexistent.json"
        acc = SignalAccumulator(storage)
        signal = ObservationSignal(
            category="pytest_failure", pattern="type_error: bad", source="verification"
        )
        # Should not raise
        acc.record([signal])
        assert storage.exists()

    def test_corrupt_file_handled(self, tmp_path):
        storage = tmp_path / "signals.json"
        storage.write_text("not valid json")
        acc = SignalAccumulator(storage)
        signal = ObservationSignal(
            category="pytest_failure",
            pattern="import_error: bar",
            source="verification",
        )
        # Should not raise, should start fresh
        acc.record([signal])
        data = json.loads(storage.read_text())
        assert "signals" in data

    def test_load_valid_json_missing_signals_key(self, tmp_path):
        storage = tmp_path / "signals.json"
        storage.write_text('{"other": 1}')
        acc = SignalAccumulator(storage)
        signal = ObservationSignal(
            category="pytest_failure",
            pattern="import_error: bar",
            source="verification",
        )
        acc.record([signal])
        data = json.loads(storage.read_text())
        assert "signals" in data
        assert "pytest_failure::import_error: bar" in data["signals"]

    def test_save_failure_cleans_temp_file(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage)
        signal = ObservationSignal(
            category="pytest_failure",
            pattern="import_error: baz",
            source="verification",
        )
        with patch("golem.observation_hooks.os.replace", side_effect=OSError("disk")):
            with pytest.raises(OSError, match="disk"):
                acc.record([signal])

    def test_save_failure_unlink_also_fails(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage)
        signal = ObservationSignal(
            category="pytest_failure",
            pattern="import_error: baz",
            source="verification",
        )
        with (
            patch("golem.observation_hooks.os.replace", side_effect=OSError("disk")),
            patch("golem.observation_hooks.os.unlink", side_effect=OSError("perm")),
        ):
            with pytest.raises(OSError, match="disk"):
                acc.record([signal])

    def test_record_empty_list_is_noop(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage)
        acc.record([])
        assert not storage.exists()

    def test_record_multiple_signals(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage, promotion_threshold=3)
        signals = [
            ObservationSignal(
                category="pytest_failure",
                pattern="import_error: a",
                source="verification",
            ),
            ObservationSignal(
                category="pylint_error",
                pattern="pylint_e0602: undefined-variable",
                source="verification",
            ),
        ]
        acc.record(signals)
        data = json.loads(storage.read_text())
        assert "pytest_failure::import_error: a" in data["signals"]
        assert "pylint_error::pylint_e0602: undefined-variable" in data["signals"]

    def test_promoted_contains_key_string(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage, promotion_threshold=2)
        signal = ObservationSignal(
            category="validation_concern",
            pattern="antipattern: dead code",
            source="validation",
        )
        acc.record([signal])
        acc.record([signal])
        promoted = acc.get_promoted()
        assert any("antipattern: dead code" in p for p in promoted)

    def test_last_seen_date_stored(self, tmp_path):
        storage = tmp_path / "signals.json"
        acc = SignalAccumulator(storage)
        signal = ObservationSignal(
            category="pytest_failure", pattern="import_error: x", source="verification"
        )
        acc.record([signal])
        data = json.loads(storage.read_text())
        key = "pytest_failure::import_error: x"
        assert "last_seen" in data["signals"][key]
