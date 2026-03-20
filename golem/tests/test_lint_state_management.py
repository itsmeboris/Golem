"""Tests for the state management audit module."""

import logging
from unittest.mock import patch

import pytest

from golem.lint.state_management import format_audit_report, run_state_management_audit


class TestRunStateManagementAudit:
    def test_empty_directory_returns_empty_list(self, tmp_path):
        result = run_state_management_audit(tmp_path)
        assert result == []

    def test_no_js_files_returns_empty_list(self, tmp_path):
        (tmp_path / "readme.txt").write_text("nothing here")
        (tmp_path / "styles.css").write_text("body { color: red; }")
        result = run_state_management_audit(tmp_path)
        assert result == []

    def test_innerhtml_result_has_category_field(self, tmp_path):
        (tmp_path / "app.js").write_text(
            "function render(el, html) {\n  el.innerHTML = html;\n}\n"
        )
        result = run_state_management_audit(tmp_path)
        # We expect at least the innerHTML finding
        innerhtml_results = [r for r in result if r["category"] == "innerHTML"]
        assert len(innerhtml_results) == 1
        assert innerhtml_results[0]["file"] == "app.js"
        assert innerhtml_results[0]["line"] == 2

    def test_polling_result_has_category_field(self, tmp_path):
        js = "setInterval(function() {\n" "  fetch('/api/data');\n" "}, 1000);\n"
        (tmp_path / "poll.js").write_text(js)
        result = run_state_management_audit(tmp_path)
        polling_results = [r for r in result if r["category"] == "polling"]
        assert len(polling_results) == 1
        assert polling_results[0]["file"] == "poll.js"
        assert polling_results[0]["line"] == 1

    def test_shared_state_result_has_category_field(self, tmp_path):
        js = "let x = 0;\n" "async function update() {\n" "  x += 1;\n" "}\n"
        (tmp_path / "state.js").write_text(js)
        result = run_state_management_audit(tmp_path)
        shared_state_results = [r for r in result if r["category"] == "shared_state"]
        assert len(shared_state_results) == 1
        assert shared_state_results[0]["file"] == "state.js"
        assert shared_state_results[0]["line"] == 3

    def test_results_sorted_by_file_then_line(self, tmp_path):
        # app.js has innerHTML issue at line 2
        (tmp_path / "app.js").write_text(
            "function render(el, html) {\n  el.innerHTML = html;\n}\n"
        )
        # util.js has shared_state issue at line 3
        (tmp_path / "util.js").write_text(
            "let x = 0;\n" "async function update() {\n" "  x += 1;\n" "}\n"
        )
        result = run_state_management_audit(tmp_path)
        assert len(result) == 2
        files = [r["file"] for r in result]
        # app.js should come before util.js
        app_idx = next(i for i, f in enumerate(files) if f == "app.js")
        util_idx = next(i for i, f in enumerate(files) if f == "util.js")
        assert app_idx < util_idx

    def test_results_sorted_by_line_within_same_file(self, tmp_path):
        # Create a file with both innerHTML and shared_state issues
        # innerHTML at line 5, shared_state mutation at a later line
        js = (
            "let counter = 0;\n"
            "async function run() {\n"
            "  counter += 1;\n"  # line 3: shared_state
            "}\n"
            "el.innerHTML = html;\n"  # line 5: innerHTML
        )
        (tmp_path / "multi.js").write_text(js)
        result = run_state_management_audit(tmp_path)
        same_file = [r for r in result if r["file"] == "multi.js"]
        assert len(same_file) == 2
        lines = [r["line"] for r in same_file]
        assert lines == sorted(lines)

    def test_one_scanner_raises_others_still_run(self, tmp_path):
        js = "function render(el, html) {\n  el.innerHTML = html;\n}\n"
        (tmp_path / "app.js").write_text(js)
        with patch(
            "golem.lint.state_management.scan_polling_patterns",
            side_effect=RuntimeError("scanner crashed"),
        ):
            result = run_state_management_audit(tmp_path)
        # innerHTML and shared_state scanners should still run
        categories = {r["category"] for r in result}
        assert "innerHTML" in categories
        assert "polling" not in categories

    def test_failing_scanner_logs_warning(self, tmp_path, caplog):
        with patch(
            "golem.lint.state_management.scan_innerhtml_patterns",
            side_effect=ValueError("bad scanner"),
        ):
            with caplog.at_level(logging.WARNING, logger="golem.lint.state_management"):
                run_state_management_audit(tmp_path)
        assert any("innerHTML" in record.message for record in caplog.records)

    def test_all_three_categories_present(self, tmp_path):
        # innerHTML issue
        (tmp_path / "a.js").write_text(
            "function render(el, html) {\n  el.innerHTML = html;\n}\n"
        )
        # polling issue
        (tmp_path / "b.js").write_text(
            "setInterval(function() {\n  fetch('/api');\n}, 1000);\n"
        )
        # shared_state issue
        (tmp_path / "c.js").write_text(
            "let x = 0;\nasync function f() {\n  x += 1;\n}\n"
        )
        result = run_state_management_audit(tmp_path)
        categories = {r["category"] for r in result}
        assert categories == {"innerHTML", "polling", "shared_state"}

    def test_no_issues_returns_empty_list(self, tmp_path):
        (tmp_path / "clean.js").write_text("function add(a, b) { return a + b; }\n")
        result = run_state_management_audit(tmp_path)
        assert result == []

    @pytest.mark.parametrize(
        "scanner_name",
        [
            "scan_innerhtml_patterns",
            "scan_polling_patterns",
            "scan_shared_state_patterns",
        ],
    )
    def test_each_scanner_exception_handled_independently(self, tmp_path, scanner_name):
        with patch(
            f"golem.lint.state_management.{scanner_name}",
            side_effect=Exception("crash"),
        ):
            # Should not raise — other scanners still run and produce empty results
            result = run_state_management_audit(tmp_path)
        assert result == []


class TestFormatAuditReport:
    def test_empty_results_returns_no_issues_message(self):
        result = format_audit_report([])
        assert result == "No state management issues found."

    def test_single_innerhtml_issue(self):
        results = [
            {
                "file": "app.js",
                "line": 5,
                "category": "innerHTML",
                "pattern": ".innerHTML =",
                "message": "innerHTML assignment without state preservation in preceding 5 lines",
            }
        ]
        report = format_audit_report(results)
        assert "1 issue(s) found" in report
        assert "innerHTML: 1" in report
        assert "app.js:5" in report
        assert "[innerHTML]" in report
        assert "innerHTML assignment without state preservation" in report

    def test_multiple_issues_counts_per_category(self):
        results = [
            {
                "file": "app.js",
                "line": 5,
                "category": "innerHTML",
                "pattern": ".innerHTML =",
                "message": "innerHTML assignment without state preservation in preceding 5 lines",
            },
            {
                "file": "app.js",
                "line": 10,
                "category": "polling",
                "pattern": "setInterval",
                "message": "Polling with fetch() but no concurrency guard (e.g., isFetching flag or AbortController)",
            },
            {
                "file": "util.js",
                "line": 3,
                "category": "shared_state",
                "variable": "x",
                "message": "Top-level let 'x' mutated inside async context",
            },
        ]
        report = format_audit_report(results)
        assert "3 issue(s) found" in report
        assert "innerHTML: 1" in report
        assert "polling: 1" in report
        assert "shared_state: 1" in report
        assert "app.js:5 [innerHTML]" in report
        assert "app.js:10 [polling]" in report
        assert "util.js:3 [shared_state]" in report

    def test_report_shows_only_categories_present(self):
        results = [
            {
                "file": "app.js",
                "line": 2,
                "category": "innerHTML",
                "pattern": ".innerHTML =",
                "message": "innerHTML assignment without state preservation in preceding 5 lines",
            }
        ]
        report = format_audit_report(results)
        assert "polling" not in report
        assert "shared_state" not in report

    def test_report_includes_issues_header(self):
        results = [
            {
                "file": "app.js",
                "line": 2,
                "category": "innerHTML",
                "pattern": ".innerHTML =",
                "message": "some message",
            }
        ]
        report = format_audit_report(results)
        assert "Issues:" in report

    def test_report_two_issues_same_category(self):
        results = [
            {
                "file": "a.js",
                "line": 1,
                "category": "innerHTML",
                "pattern": ".innerHTML =",
                "message": "msg1",
            },
            {
                "file": "b.js",
                "line": 1,
                "category": "innerHTML",
                "pattern": ".innerHTML =",
                "message": "msg2",
            },
        ]
        report = format_audit_report(results)
        assert "2 issue(s) found" in report
        assert "innerHTML: 2" in report

    def test_report_message_included_for_each_issue(self):
        results = [
            {
                "file": "app.js",
                "line": 5,
                "category": "innerHTML",
                "pattern": ".innerHTML =",
                "message": "specific message text here",
            }
        ]
        report = format_audit_report(results)
        assert "specific message text here" in report
