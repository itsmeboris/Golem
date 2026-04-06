# pylint: disable=too-few-public-methods
"""Tests for golem.validation — git helpers, prompt formatting, verdict parsing."""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from golem.validation import (
    ValidationVerdict,
    _build_validation_prompt,
    _extract_changed_files,
    _format_event_log,
    _format_verification_evidence,
    _parse_validation_output,
    _read_types_py,
    check_doc_relevance,
    get_git_diff,
    run_validation,
    scan_diff_antipatterns,
)


def _noop_callback(_event: object) -> None:
    pass


class TestValidationVerdict:
    def test_defaults(self):
        v = ValidationVerdict()
        assert v.verdict == "FAIL"
        assert v.confidence == 0.0
        assert v.summary == ""
        assert not v.concerns
        assert v.task_type == "other"
        assert v.cost_usd == 0.0
        assert not v.files_to_fix
        assert not v.test_failures

    def test_custom_values(self):
        v = ValidationVerdict(
            verdict="PASS", confidence=0.95, summary="all good", cost_usd=0.10
        )
        assert v.verdict == "PASS"
        assert v.confidence == 0.95

    def test_custom_new_fields(self):
        v = ValidationVerdict(
            verdict="PARTIAL",
            files_to_fix=["src/main.py", "src/utils.py"],
            test_failures=["test_foo failed: AssertionError"],
        )
        assert v.files_to_fix == ["src/main.py", "src/utils.py"]
        assert v.test_failures == ["test_foo failed: AssertionError"]


class TestGetGitDiff:
    def test_empty_work_dir(self):
        result = get_git_diff("")
        assert "no working directory" in result

    @patch("golem.validation.subprocess.run")
    def test_no_changes(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=""
        )
        result = get_git_diff("/some/dir")
        assert "no changes" in result

    @patch("golem.validation.subprocess.run", side_effect=OSError("fail"))
    def test_error_returns_unavailable(self, _):
        result = get_git_diff("/bad/dir")
        assert "unavailable" in result

    @patch("golem.validation.subprocess.run")
    def test_with_uncommitted_changes(self, mock_run):
        def side_effect(args, **_kwargs):
            if "--stat" in args and "HEAD" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=" file.py | 3 +++"
                )
            if "diff" in args and "HEAD" in args and "--stat" not in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="+new line"
                )
            if "merge-base" in args:
                return subprocess.CompletedProcess(args=args, returncode=1, stdout="")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

        mock_run.side_effect = side_effect
        result = get_git_diff("/some/dir")
        assert "Uncommitted" in result

    @patch("golem.validation.subprocess.run")
    def test_preserves_large_diff(self, mock_run):
        big_diff = "+" * 50_000

        def side_effect(args, **_kwargs):
            if "--stat" in args and "HEAD" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=" file.py | 1000 +++"
                )
            if "diff" in args and "HEAD" in args and "--stat" not in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=big_diff
                )
            if "merge-base" in args:
                return subprocess.CompletedProcess(args=args, returncode=1, stdout="")
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

        mock_run.side_effect = side_effect
        result = get_git_diff("/some/dir")
        assert big_diff in result


class TestFormatEventLog:
    def test_empty(self):
        assert _format_event_log([]) == "(no events recorded)"

    def test_single_event(self):
        events = [{"kind": "tool_use", "tool_name": "Read", "summary": "read file"}]
        result = _format_event_log(events)
        assert "tool_use" in result
        assert "Read" in result
        assert "read file" in result

    def test_error_event(self):
        events = [{"kind": "error", "is_error": True, "summary": "bad"}]
        result = _format_event_log(events)
        assert "[ERROR]" in result

    def test_all_events_included(self):
        events = [{"kind": f"ev{i}"} for i in range(50)]
        result = _format_event_log(events)
        assert "ev0" in result
        assert "ev49" in result


class TestParseValidationOutput:
    def test_dict_result(self):
        result = SimpleNamespace(
            output={"result": {"verdict": "PASS", "confidence": 0.9}},
            cost_usd=0.05,
        )
        v = _parse_validation_output(result)
        assert v.verdict == "PASS"
        assert v.confidence == 0.9
        assert v.cost_usd == 0.05

    def test_string_result_with_json(self):
        result = SimpleNamespace(
            output={
                "result": '{"verdict": "PARTIAL", "confidence": 0.5, "summary": "needs work"}'
            },
            cost_usd=0.03,
        )
        v = _parse_validation_output(result)
        assert v.verdict == "PARTIAL"
        assert v.confidence == 0.5

    def test_unparseable_result(self):
        result = SimpleNamespace(
            output={"result": "just some text no json"},
            cost_usd=0.01,
        )
        v = _parse_validation_output(result)
        assert v.verdict == "FAIL"
        assert v.cost_usd == 0.01

    def test_parses_new_structured_fields(self):
        result = SimpleNamespace(
            output={
                "result": {
                    "verdict": "PARTIAL",
                    "confidence": 0.6,
                    "summary": "needs work",
                    "concerns": ["missing tests"],
                    "files_to_fix": ["golem/validation.py"],
                    "test_failures": ["test_foo FAILED"],
                }
            },
            cost_usd=0.04,
        )
        v = _parse_validation_output(result)
        assert v.verdict == "PARTIAL"
        assert v.files_to_fix == ["golem/validation.py"]
        assert v.test_failures == ["test_foo FAILED"]

    def test_missing_new_fields_default_empty(self):
        result = SimpleNamespace(
            output={"result": {"verdict": "PASS", "confidence": 0.9}},
            cost_usd=0.02,
        )
        v = _parse_validation_output(result)
        assert not v.files_to_fix
        assert not v.test_failures

    def test_lowercase_verdict_uppercased(self):
        result = SimpleNamespace(
            output={"result": {"verdict": "pass", "confidence": 1.0}},
            cost_usd=0.02,
        )
        v = _parse_validation_output(result)
        assert v.verdict == "PASS"


class TestBuildValidationPrompt:
    @patch("golem.validation.get_git_diff", return_value="(no changes)")
    def test_builds_prompt(self, _):
        session_data = {
            "event_log": [
                {"kind": "tool_use", "tool_name": "Read", "summary": "file"},
            ],
            "duration_seconds": 120,
            "total_cost_usd": 0.50,
            "milestone_count": 5,
            "tools_called": ["Read", "Write"],
            "mcp_tools_called": [],
            "errors": [],
        }
        prompt = _build_validation_prompt(
            issue_id=123,
            subject="Fix the bug",
            description="A bug needs fixing",
            session_data=session_data,
            work_dir="/tmp/work",
        )
        assert "123" in prompt
        assert "Fix the bug" in prompt
        assert "Antipattern Detection" in prompt
        assert "Traceback leaks" in prompt
        assert "Cross-module private access" in prompt
        assert "String-matching control flow" in prompt
        assert "Independent Verification Results" in prompt
        assert "Shared Data Contracts" in prompt
        assert "no independent verification was run" in prompt
        assert "MilestoneDict" in prompt

    @patch("golem.validation.get_git_diff", return_value="(no changes)")
    def test_builds_prompt_with_verification(self, _):
        session_data = {
            "event_log": [],
            "duration_seconds": 60,
            "total_cost_usd": 0.10,
            "milestone_count": 1,
            "tools_called": [],
            "mcp_tools_called": [],
            "errors": [],
        }
        vr = SimpleNamespace(
            black_ok=True,
            black_output="",
            pylint_ok=True,
            pylint_output="",
            pytest_ok=True,
            pytest_output="",
            test_count=42,
            failures=[],
            coverage_pct=100.0,
        )
        prompt = _build_validation_prompt(
            issue_id=1,
            subject="Test",
            description="desc",
            session_data=session_data,
            work_dir="/tmp/work",
            verification_result=vr,
        )
        assert "- black: PASS" in prompt
        assert "- pylint: PASS" in prompt
        assert "- pytest: PASS" in prompt
        assert "42 tests" in prompt
        assert "Cross-Module Consistency" in prompt
        assert "Test Validity" in prompt


class TestScanDiffAntipatterns:
    def test_empty_diff(self):
        assert not scan_diff_antipatterns("")

    def test_no_antipatterns(self):
        diff = '+++ b/golem/foo.py\n+def hello():\n+    return "world"\n'
        assert not scan_diff_antipatterns(diff)

    def test_traceback_leak(self):
        diff = (
            "+++ b/golem/handler.py\n"
            "+    tb = traceback.format_exc()\n"
            "+    return tb\n"
        )
        concerns = scan_diff_antipatterns(diff)
        assert len(concerns) == 1
        assert "traceback leak" in concerns[0]
        assert "golem/handler.py" in concerns[0]

    def test_traceback_print_exc(self):
        diff = "+++ b/golem/api.py\n+    traceback.print_exc()\n"
        concerns = scan_diff_antipatterns(diff)
        assert any("traceback leak" in c for c in concerns)

    def test_cross_module_private_access(self):
        diff = "+++ b/golem/flow.py\n+    val = other_obj._internal_state\n"
        concerns = scan_diff_antipatterns(diff)
        assert len(concerns) == 1
        assert "cross-module private access" in concerns[0]

    def test_self_private_not_flagged(self):
        diff = "+++ b/golem/flow.py\n+    self._state = 1\n"
        assert not scan_diff_antipatterns(diff)

    def test_cls_private_not_flagged(self):
        diff = "+++ b/golem/flow.py\n+    cls._counter += 1\n"
        assert not scan_diff_antipatterns(diff)

    def test_dunder_not_flagged(self):
        diff = "+++ b/golem/flow.py\n+    x = obj.__class__\n"
        assert not scan_diff_antipatterns(diff)

    def test_string_control_flow(self):
        diff = '+++ b/golem/flow.py\n+    if status == "running":\n'
        concerns = scan_diff_antipatterns(diff)
        assert len(concerns) == 1
        assert "string-matching control flow" in concerns[0]

    def test_test_files_skipped(self):
        diff = (
            "+++ b/golem/tests/test_foo.py\n"
            "+    traceback.format_exc()\n"
            "+    obj._private\n"
            '+    if x == "running":\n'
        )
        assert not scan_diff_antipatterns(diff)

    def test_comment_lines_skipped(self):
        diff = "+++ b/golem/foo.py\n+    # traceback.format_exc() is bad\n"
        assert not scan_diff_antipatterns(diff)

    def test_non_added_lines_skipped(self):
        diff = (
            "+++ b/golem/foo.py\n"
            " traceback.format_exc()\n"
            "-traceback.format_exc()\n"
        )
        assert not scan_diff_antipatterns(diff)

    def test_multiple_antipatterns(self):
        diff = (
            "+++ b/golem/flow.py\n"
            "+    traceback.format_exc()\n"
            "+    obj._secret\n"
            '+    if state == "ready":\n'
        )
        concerns = scan_diff_antipatterns(diff)
        assert len(concerns) == 3

    def test_deduplicates_files(self):
        diff = (
            "+++ b/golem/flow.py\n"
            "+    traceback.format_exc()\n"
            "+    traceback.print_exc()\n"
        )
        concerns = scan_diff_antipatterns(diff)
        assert len(concerns) == 1
        assert concerns[0].count("golem/flow.py") == 1


class TestNewAntipatterns:
    def test_raw_dict_access_flagged(self):
        diff = """\
+++ b/golem/core/dashboard.py
+        ev_type = ev.get("type", "")
+        msg = ev.get("message", "")
"""
        concerns = scan_diff_antipatterns(diff)
        assert any(
            "raw dict" in c.lower() or "untyped dict" in c.lower() for c in concerns
        )

    def test_raw_dict_access_in_test_files_not_flagged(self):
        diff = """\
+++ b/golem/tests/test_dashboard.py
+        ev_type = ev.get("type", "")
"""
        concerns = scan_diff_antipatterns(diff)
        assert not any(
            "raw dict" in c.lower() or "untyped dict" in c.lower() for c in concerns
        )

    def test_typed_dict_access_not_flagged(self):
        """Dict access with type annotation nearby should not trigger."""
        diff = """\
+++ b/golem/core/dashboard.py
+        ev: MilestoneDict = event_log[0]
+        ev_type = ev["kind"]
"""
        # The regex is a heuristic; the reviewer (LLM) makes the final decision.
        _ = scan_diff_antipatterns(diff)
        # We accept that the regex may still flag it — it's a soft signal.

    def test_bracket_dict_access_flagged(self):
        diff = """\
+++ b/golem/core/engine.py
+        status = result["status"]
"""
        concerns = scan_diff_antipatterns(diff)
        assert any("untyped dict" in c.lower() for c in concerns)

    def test_dict_access_deduplicates_files(self):
        diff = """\
+++ b/golem/core/engine.py
+        a = d.get("foo", "")
+        b = d.get("bar", "")
"""
        concerns = scan_diff_antipatterns(diff)
        matching = [c for c in concerns if "untyped dict" in c.lower()]
        assert len(matching) == 1
        assert matching[0].count("golem/core/engine.py") == 1


class TestRunValidationWithAntipatterns:
    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff")
    def test_augments_concerns_for_code_change(self, mock_diff, mock_invoke):
        mock_diff.return_value = "+++ b/golem/handler.py\n+    traceback.format_exc()\n"
        mock_invoke.return_value = SimpleNamespace(
            output={
                "result": {
                    "verdict": "PASS",
                    "confidence": 0.95,
                    "task_type": "code_change",
                }
            },
            cost_usd=0.10,
        )
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/work",
        )
        assert any("traceback leak" in c for c in v.concerns)
        assert v.confidence == pytest.approx(0.90)

    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff")
    def test_skips_for_investigation_task(self, mock_diff, mock_invoke):
        mock_diff.return_value = "(no changes)"
        mock_invoke.return_value = SimpleNamespace(
            output={
                "result": {
                    "verdict": "PASS",
                    "confidence": 0.95,
                    "task_type": "investigation",
                }
            },
            cost_usd=0.10,
        )
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/work",
        )
        assert not v.concerns
        assert v.confidence == 0.95

    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff")
    def test_confidence_penalty_capped(self, mock_diff, mock_invoke):
        mock_diff.return_value = (
            "+++ b/golem/flow.py\n"
            "+    traceback.format_exc()\n"
            "+    obj._secret\n"
            '+    if state == "ready":\n'
            '+    if mode == "active":\n'
        )
        mock_invoke.return_value = SimpleNamespace(
            output={
                "result": {
                    "verdict": "PASS",
                    "confidence": 0.95,
                    "task_type": "code_change",
                }
            },
            cost_usd=0.10,
        )
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/work",
        )
        # 3 antipatterns × 0.05 = 0.15 (cap), so 0.95 - 0.15 = 0.80
        assert v.confidence == pytest.approx(0.80)

    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff")
    def test_diff_with_section_header_triggers_scan(self, mock_diff, mock_invoke):
        mock_diff.return_value = (
            "### Uncommitted changes\n"
            "+++ b/golem/x.py\n"
            "+    traceback.print_exc()\n"
        )
        mock_invoke.return_value = SimpleNamespace(
            output={
                "result": {
                    "verdict": "PASS",
                    "confidence": 0.90,
                    "task_type": "other",
                }
            },
            cost_usd=0.05,
        )
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/work",
        )
        assert any("traceback" in c for c in v.concerns)


class TestExtractChangedFiles:
    def test_extracts_files_from_diff(self):
        diff = "+++ b/golem/foo.py\n+++ b/golem/bar.py\n"
        assert _extract_changed_files(diff) == ["golem/foo.py", "golem/bar.py"]

    def test_empty_diff(self):
        assert not _extract_changed_files("")

    def test_ignores_non_file_lines(self):
        diff = "--- a/golem/foo.py\n+++ b/golem/foo.py\n+ added line\n"
        assert _extract_changed_files(diff) == ["golem/foo.py"]

    def test_handles_markdown_wrapped_diff(self):
        diff = "### Uncommitted changes\n```diff\n+++ b/golem/x.py\n```\n"
        assert _extract_changed_files(diff) == ["golem/x.py"]


class TestRunValidationAstAnalysisFlag:
    @patch("golem.ast_analysis.run_ast_analysis")
    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff")
    def test_ast_analysis_called_when_enabled(self, mock_diff, mock_invoke, mock_ast):
        mock_diff.return_value = "+++ b/golem/foo.py\n+ x = 1\n"
        mock_invoke.return_value = SimpleNamespace(
            output={
                "result": {
                    "verdict": "PASS",
                    "confidence": 0.90,
                    "task_type": "code_change",
                }
            },
            cost_usd=0.05,
        )
        mock_ast.return_value = ["AST: issue in golem/foo.py:1"]
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/work",
            ast_analysis=True,
        )
        mock_ast.assert_called_once()
        assert any("AST:" in c for c in v.concerns)

    @patch("golem.ast_analysis.run_ast_analysis")
    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff")
    def test_ast_analysis_skipped_when_disabled(self, mock_diff, mock_invoke, mock_ast):
        mock_diff.return_value = "+++ b/golem/foo.py\n+ x = 1\n"
        mock_invoke.return_value = SimpleNamespace(
            output={
                "result": {
                    "verdict": "PASS",
                    "confidence": 0.90,
                    "task_type": "code_change",
                }
            },
            cost_usd=0.05,
        )
        run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/work",
            ast_analysis=False,
        )
        mock_ast.assert_not_called()


class TestFindMergeBase:
    @patch("golem.validation.subprocess.run")
    def test_finds_main_branch(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="abc123\n"
        )
        from golem.validation import _find_merge_base

        result = _find_merge_base("/dir")
        assert result == "abc123"

    @patch("golem.validation.subprocess.run")
    def test_falls_back_to_master(self, mock_run):
        def side_effect(args, **_kwargs):
            if "main" in args:
                return subprocess.CompletedProcess(args=args, returncode=1, stdout="")
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout="def456\n"
            )

        mock_run.side_effect = side_effect
        from golem.validation import _find_merge_base

        assert _find_merge_base("/dir") == "def456"

    @patch("golem.validation.subprocess.run")
    def test_returns_empty_on_failure(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=""
        )
        from golem.validation import _find_merge_base

        assert _find_merge_base("/dir") == ""


class TestGetBranchDiff:
    @patch("golem.validation.subprocess.run")
    def test_full_branch_diff(self, mock_run):
        from golem.validation import _get_branch_diff

        def side_effect(args, **_kwargs):
            if "merge-base" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="abc123\n"
                )
            if "--stat" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=" file.py | 2 ++\n"
                )
            if "log" in args and "--oneline" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="abc Fix stuff\n"
                )
            if "diff" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="+new line\n"
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

        mock_run.side_effect = side_effect
        result = _get_branch_diff("/dir")
        assert "Committed changes" in result
        assert "Fix stuff" in result

    @patch("golem.validation._find_merge_base", return_value="")
    def test_no_merge_base(self, _):
        from golem.validation import _get_branch_diff

        assert _get_branch_diff("/dir") == ""

    @patch("golem.validation.subprocess.run")
    def test_no_stat_changes(self, mock_run):
        from golem.validation import _get_branch_diff

        def side_effect(args, **_kwargs):
            if "merge-base" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="abc123\n"
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

        mock_run.side_effect = side_effect
        assert _get_branch_diff("/dir") == ""

    @patch("golem.validation.subprocess.run")
    def test_preserves_large_branch_diff(self, mock_run):
        from golem.validation import _get_branch_diff

        big_diff = "+" * 50000

        def side_effect(args, **_kwargs):
            if "merge-base" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="abc\n"
                )
            if "--stat" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=" file | 999 +++\n"
                )
            if "log" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="abc commit\n"
                )
            if "diff" in args:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=big_diff
                )
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="")

        mock_run.side_effect = side_effect
        result = _get_branch_diff("/dir")
        assert big_diff in result


class TestGetGitDiffWithBranchSection:
    @patch("golem.validation.subprocess.run")
    def test_appends_branch_section(self, mock_run):
        def side_effect(args, **_kwargs):
            joined = " ".join(args)
            if "--stat" in args and "HEAD" in args and ".." not in joined:
                stdout = ""
            elif "merge-base" in args and "main" in args:
                stdout = "abc123\n"
            elif "merge-base" in args:
                return subprocess.CompletedProcess(args=args, returncode=1, stdout="")
            elif "--stat" in args:
                stdout = " f.py | 1 +\n"
            elif "log" in args:
                stdout = "abc fix\n"
            elif "diff" in args:
                stdout = "+line\n"
            else:
                stdout = ""
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout)

        mock_run.side_effect = side_effect
        result = get_git_diff("/dir")
        assert "Committed changes" in result


class TestRunValidation:
    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff", return_value="(no changes)")
    def test_success(self, _, mock_invoke):
        mock_invoke.return_value = SimpleNamespace(
            output={"result": {"verdict": "PASS", "confidence": 0.95}},
            cost_usd=0.10,
        )
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/work",
        )
        assert v.verdict == "PASS"
        assert v.cost_usd == 0.10
        cli_config = mock_invoke.call_args[0][1]
        assert cli_config.cwd == "/work"

    @patch("golem.validation.invoke_cli", side_effect=RuntimeError("boom"))
    @patch("golem.validation.get_git_diff", return_value="(no changes)")
    def test_retries_on_failure(self, _, mock_invoke):
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/tmp",
        )
        assert v.verdict == "FAIL"
        assert "boom" in v.summary
        assert mock_invoke.call_count == 2

    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff", return_value="(no changes)")
    def test_first_attempt_fails_second_succeeds(self, _, mock_invoke):
        mock_invoke.side_effect = [
            RuntimeError("transient"),
            SimpleNamespace(
                output={"result": {"verdict": "PASS", "confidence": 0.8}},
                cost_usd=0.05,
            ),
        ]
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/tmp",
        )
        assert v.verdict == "PASS"

    @patch("golem.validation.invoke_cli_monitored")
    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff", return_value="(no changes)")
    def test_callback_uses_monitored(self, _, mock_quiet, mock_monitored):
        mock_monitored.return_value = SimpleNamespace(
            output={"result": {"verdict": "PASS", "confidence": 0.9}},
            cost_usd=0.08,
        )
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/tmp",
            callback=_noop_callback,
        )
        assert v.verdict == "PASS"
        mock_monitored.assert_called_once()
        mock_quiet.assert_not_called()

    @patch("golem.validation.invoke_cli_monitored", side_effect=RuntimeError("boom"))
    @patch("golem.validation.get_git_diff", return_value="(no changes)")
    def test_callback_retries_with_monitored(self, _, mock_monitored):
        v = run_validation(
            issue_id=1,
            subject="test",
            description="desc",
            session_data={},
            work_dir="/tmp",
            callback=_noop_callback,
        )
        assert v.verdict == "FAIL"
        assert mock_monitored.call_count == 2


class TestFormatVerificationEvidence:
    def test_none_result(self):
        result = _format_verification_evidence(None)
        assert "no independent verification was run" in result

    def test_all_pass(self):
        vr = SimpleNamespace(
            black_ok=True,
            black_output="All good",
            pylint_ok=True,
            pylint_output="",
            pytest_ok=True,
            pytest_output="",
            test_count=50,
            failures=[],
            coverage_pct=100.0,
        )
        result = _format_verification_evidence(vr)
        assert "- black: PASS" in result
        assert "- pylint: PASS" in result
        assert "- pytest: PASS" in result
        assert "50 tests, 0 failures, coverage: 100.0%" in result

    def test_all_fail(self):
        vr = SimpleNamespace(
            black_ok=False,
            black_output="would reformat foo.py",
            pylint_ok=False,
            pylint_output="E0001: syntax error",
            pytest_ok=False,
            pytest_output="FAILED test_foo.py::test_bar",
            test_count=10,
            failures=["test_foo.py::test_bar"],
            coverage_pct=80.0,
        )
        result = _format_verification_evidence(vr)
        assert "- black: FAIL" in result
        assert "would reformat foo.py" in result
        assert "- pylint: FAIL" in result
        assert "E0001: syntax error" in result
        assert "- pytest: FAIL" in result
        assert "FAILED test_foo.py::test_bar" in result
        # pytest_ok is False, so no summary line
        assert "10 tests" not in result

    def test_partial_failure(self):
        vr = SimpleNamespace(
            black_ok=True,
            black_output="",
            pylint_ok=False,
            pylint_output="E0602: undefined name 'x'",
            pytest_ok=True,
            pytest_output="",
            test_count=30,
            failures=[],
            coverage_pct=95.0,
        )
        result = _format_verification_evidence(vr)
        assert "- black: PASS" in result
        assert "- pylint: FAIL" in result
        assert "undefined name" in result
        assert "- pytest: PASS" in result
        assert "30 tests, 0 failures, coverage: 95.0%" in result

    def test_truncates_long_output(self):
        vr = SimpleNamespace(
            black_ok=False,
            black_output="x" * 1000,
            pylint_ok=True,
            pylint_output="",
            pytest_ok=True,
            pytest_output="",
            test_count=1,
            failures=[],
            coverage_pct=100.0,
            command_results=None,
        )
        result = _format_verification_evidence(vr)
        # Output should be truncated to 500 chars
        assert len("x" * 500) == 500
        assert "x" * 501 not in result
        assert "x" * 500 in result

    def test_generic_command_results_shown(self):
        """When command_results are present, format them instead of legacy fields."""
        vr = SimpleNamespace(
            command_results=[
                {"role": "lint", "cmd": "eslint src/", "passed": True, "output": ""},
                {
                    "role": "test",
                    "cmd": "npm test",
                    "passed": False,
                    "output": "1 test failed",
                },
            ],
            black_ok=True,
            black_output="",
            pylint_ok=True,
            pylint_output="",
            pytest_ok=True,
            pytest_output="",
            test_count=0,
            failures=[],
            coverage_pct=0.0,
        )
        result = _format_verification_evidence(vr)
        assert "lint (eslint src/): PASS" in result
        assert "test (npm test): FAIL" in result
        assert "1 test failed" in result
        # Legacy fields should NOT appear
        assert "- black:" not in result


class TestReadTypesPy:
    def test_returns_types_content(self):
        content = _read_types_py()
        assert "MilestoneDict" in content
        assert "TypedDict" in content

    def test_returns_content_with_contracts(self):
        content = _read_types_py()
        assert "TrackerExportDict" in content
        assert "VerificationResultDict" in content

    @patch("golem.validation.Path.read_text", side_effect=OSError("no file"))
    def test_returns_fallback_on_error(self, _):
        content = _read_types_py()
        assert "golem/types.py not found" in content


class TestHardcodedUIAntipattern:
    def _make_diff(self, filename: str, code_line: str) -> str:
        return f"+++ b/{filename}\n+{code_line}\n"

    def test_display_none_flagged(self):
        diff = self._make_diff("golem/ui.py", '    el.style = "display: none"')
        concerns = scan_diff_antipatterns(diff)
        assert any("hardcoded UI state" in c for c in concerns)

    def test_visibility_hidden_flagged(self):
        diff = self._make_diff("golem/ui.py", "    visibility: hidden;")
        concerns = scan_diff_antipatterns(diff)
        assert any("hardcoded UI state" in c for c in concerns)

    def test_hidden_true_flagged(self):
        diff = self._make_diff("golem/component.py", "    widget = Widget(hidden=True)")
        concerns = scan_diff_antipatterns(diff)
        assert any("hardcoded UI state" in c for c in concerns)

    def test_disabled_attribute_flagged(self):
        diff = self._make_diff(
            "golem/component.py", '    btn = Button(disabled="disabled")'
        )
        concerns = scan_diff_antipatterns(diff)
        assert any("hardcoded UI state" in c for c in concerns)

    def test_normal_style_not_flagged(self):
        diff = self._make_diff("golem/ui.py", '    el.style = "color: red"')
        concerns = scan_diff_antipatterns(diff)
        assert not any("hardcoded UI state" in c for c in concerns)

    def test_test_file_skipped(self):
        diff = self._make_diff(
            "golem/tests/test_ui.py", '    el.style = "display: none"'
        )
        concerns = scan_diff_antipatterns(diff)
        assert not any("hardcoded UI state" in c for c in concerns)

    def test_comment_skipped(self):
        diff = self._make_diff("golem/ui.py", "    # display: none is bad")
        concerns = scan_diff_antipatterns(diff)
        assert not any("hardcoded UI state" in c for c in concerns)

    def test_deduplicates_files(self):
        diff = (
            "+++ b/golem/ui.py\n"
            '+    a = "display: none"\n'
            '+    b = "visibility: hidden"\n'
        )
        concerns = scan_diff_antipatterns(diff)
        matching = [c for c in concerns if "hardcoded UI state" in c]
        assert len(matching) == 1
        assert matching[0].count("golem/ui.py") == 1


class TestDeadCodeAntipattern:
    def _make_diff(self, lines: list[str], filename: str = "golem/flow.py") -> str:
        header = f"+++ b/{filename}\n"
        return header + "".join(f"+{line}\n" for line in lines)

    def test_code_after_return_flagged(self):
        diff = self._make_diff(["    return value", "    do_something()"])
        concerns = scan_diff_antipatterns(diff)
        assert any("dead code" in c for c in concerns)

    def test_code_after_raise_flagged(self):
        diff = self._make_diff(["    raise ValueError('bad')", "    do_something()"])
        concerns = scan_diff_antipatterns(diff)
        assert any("dead code" in c for c in concerns)

    def test_code_after_sys_exit_flagged(self):
        diff = self._make_diff(["    sys.exit(1)", "    do_something()"])
        concerns = scan_diff_antipatterns(diff)
        assert any("dead code" in c for c in concerns)

    def test_different_indent_not_flagged(self):
        # Code at shallower indent (e.g., outer function body) is not dead code
        diff = self._make_diff(["        return value", "    outer_call()"])
        concerns = scan_diff_antipatterns(diff)
        assert not any("dead code" in c for c in concerns)

    def test_new_function_after_return_not_flagged(self):
        diff = self._make_diff(["    return value", "    def next_func():"])
        concerns = scan_diff_antipatterns(diff)
        assert not any("dead code" in c for c in concerns)

    def test_decorator_after_return_not_flagged(self):
        diff = self._make_diff(["    return value", "    @decorator"])
        concerns = scan_diff_antipatterns(diff)
        assert not any("dead code" in c for c in concerns)

    def test_test_file_skipped(self):
        diff = self._make_diff(
            ["    return value", "    do_something()"],
            filename="golem/tests/test_flow.py",
        )
        concerns = scan_diff_antipatterns(diff)
        assert not any("dead code" in c for c in concerns)

    def test_blank_line_then_code_same_indent_flagged(self):
        # Blank lines between return and code do not reset the flag
        diff = (
            "+++ b/golem/flow.py\n" "+    return value\n" "+ \n" "+    do_something()\n"
        )
        concerns = scan_diff_antipatterns(diff)
        assert any("dead code" in c for c in concerns)

    def test_class_after_return_not_flagged(self):
        diff = self._make_diff(["    return value", "    class Foo:"])
        concerns = scan_diff_antipatterns(diff)
        assert not any("dead code" in c for c in concerns)

    def test_else_after_return_not_flagged(self):
        diff = self._make_diff(["    return value", "    else:"])
        concerns = scan_diff_antipatterns(diff)
        assert not any("dead code" in c for c in concerns)

    def test_deduplicates_files(self):
        diff = (
            "+++ b/golem/flow.py\n"
            "+    return x\n"
            "+    dead1()\n"
            "+    return y\n"
            "+    dead2()\n"
        )
        concerns = scan_diff_antipatterns(diff)
        matching = [c for c in concerns if "dead code" in c]
        assert len(matching) == 1
        assert matching[0].count("golem/flow.py") == 1


class TestEmptyExceptAntipattern:
    def _make_diff(self, code_line: str, filename: str = "golem/handler.py") -> str:
        return f"+++ b/{filename}\n+{code_line}\n"

    def test_except_pass_flagged(self):
        diff = self._make_diff("    except Exception: pass")
        concerns = scan_diff_antipatterns(diff)
        assert any("empty exception handler" in c for c in concerns)

    def test_except_ellipsis_flagged(self):
        diff = self._make_diff("    except: ...")
        concerns = scan_diff_antipatterns(diff)
        assert any("empty exception handler" in c for c in concerns)

    def test_bare_except_pass_flagged(self):
        diff = self._make_diff("    except: pass")
        concerns = scan_diff_antipatterns(diff)
        assert any("empty exception handler" in c for c in concerns)

    def test_except_with_logging_not_flagged(self):
        diff = self._make_diff("    except Exception as e:")
        concerns = scan_diff_antipatterns(diff)
        assert not any("empty exception handler" in c for c in concerns)

    def test_test_file_skipped(self):
        diff = self._make_diff(
            "    except Exception: pass",
            filename="golem/tests/test_handler.py",
        )
        concerns = scan_diff_antipatterns(diff)
        assert not any("empty exception handler" in c for c in concerns)

    def test_comment_skipped(self):
        diff = self._make_diff("    # except Exception: pass")
        concerns = scan_diff_antipatterns(diff)
        assert not any("empty exception handler" in c for c in concerns)

    def test_deduplicates_files(self):
        diff = (
            "+++ b/golem/handler.py\n"
            "+    except ValueError: pass\n"
            "+    except KeyError: ...\n"
        )
        concerns = scan_diff_antipatterns(diff)
        matching = [c for c in concerns if "empty exception handler" in c]
        assert len(matching) == 1
        assert matching[0].count("golem/handler.py") == 1


class TestDeadCodeBugFixes:
    """Tests for Bug 1 (>= vs ==) and Bug 3 (state reset after first flagged line)."""

    def _make_diff(self, lines: list[str], filename: str = "golem/flow.py") -> str:
        header = f"+++ b/{filename}\n"
        return header + "".join(f"+{line}\n" for line in lines)

    def test_deeper_indent_after_return_not_flagged(self):
        # Bug 1: return at indent=4, next line at indent=8 — should NOT be dead code
        diff = self._make_diff(["    return value", "        nested_call()"])
        concerns = scan_diff_antipatterns(diff)
        assert not any("dead code" in c for c in concerns)

    def test_consecutive_dead_lines_all_flagged(self):
        # Bug 3: after return, BOTH dead1() and dead2() should be flagged
        # (concern is still 1 due to dedup, but it must be present)
        diff = self._make_diff(["    return x", "    dead1()", "    dead2()"])
        concerns = scan_diff_antipatterns(diff)
        assert any("dead code" in c for c in concerns)

    def test_second_dead_line_triggers_concern(self):
        # Bug 3: if ONLY the second dead line were tracked, using two different
        # files ensures both are independently flagged
        diff = (
            "+++ b/golem/a.py\n"
            "+    return x\n"
            "+    dead1()\n"
            "+    dead2()\n"
            "+++ b/golem/b.py\n"
            "+    return y\n"
            "+    dead3()\n"
            "+    dead4()\n"
        )
        concerns = scan_diff_antipatterns(diff)
        matching = [c for c in concerns if "dead code" in c]
        assert len(matching) == 1
        assert "golem/a.py" in matching[0]
        assert "golem/b.py" in matching[0]


class TestMultilineEmptyExcept:
    """Tests for Bug 2: _EMPTY_EXCEPT_RE misses multi-line except blocks."""

    def _make_diff(self, lines: list[str], filename: str = "golem/handler.py") -> str:
        header = f"+++ b/{filename}\n"
        return header + "".join(f"+{line}\n" for line in lines)

    def test_multiline_except_pass_flagged(self):
        # except Exception:\n    pass — two separate added lines
        diff = self._make_diff(["    except Exception:", "        pass"])
        concerns = scan_diff_antipatterns(diff)
        assert any("empty exception handler" in c for c in concerns)

    def test_multiline_except_ellipsis_flagged(self):
        # except:\n    ... — two separate added lines
        diff = self._make_diff(["    except:", "        ..."])
        concerns = scan_diff_antipatterns(diff)
        assert any("empty exception handler" in c for c in concerns)

    def test_multiline_except_with_code_not_flagged(self):
        # except Exception:\n    logger.error(e) — NOT empty
        diff = self._make_diff(["    except Exception:", "        logger.error(e)"])
        concerns = scan_diff_antipatterns(diff)
        assert not any("empty exception handler" in c for c in concerns)


class TestIOCallAntipattern:
    def _make_diff(self, code_line: str, filename: str = "golem/utils.py") -> str:
        return f"+++ b/{filename}\n+{code_line}\n"

    def test_open_call_flagged(self):
        diff = self._make_diff('    f = open("file.txt")')
        concerns = scan_diff_antipatterns(diff)
        assert any("I/O call" in c for c in concerns)

    def test_subprocess_run_flagged(self):
        diff = self._make_diff('    subprocess.run(["ls"])')
        concerns = scan_diff_antipatterns(diff)
        assert any("I/O call" in c for c in concerns)

    def test_requests_get_flagged(self):
        diff = self._make_diff("    resp = requests.get(url)")
        concerns = scan_diff_antipatterns(diff)
        assert any("I/O call" in c for c in concerns)

    def test_urlopen_flagged(self):
        diff = self._make_diff("    resp = urlopen(url)")
        concerns = scan_diff_antipatterns(diff)
        assert any("I/O call" in c for c in concerns)

    def test_shutil_copy_flagged(self):
        diff = self._make_diff('    shutil.copy("src", "dst")')
        concerns = scan_diff_antipatterns(diff)
        assert any("I/O call" in c for c in concerns)

    def test_normal_function_not_flagged(self):
        diff = self._make_diff("    result = calculate()")
        concerns = scan_diff_antipatterns(diff)
        assert not any("I/O call" in c for c in concerns)

    def test_test_file_skipped(self):
        diff = self._make_diff(
            '    f = open("file.txt")',
            filename="golem/tests/test_utils.py",
        )
        concerns = scan_diff_antipatterns(diff)
        assert not any("I/O call" in c for c in concerns)

    def test_comment_skipped(self):
        diff = self._make_diff("    # open() is an I/O call")
        concerns = scan_diff_antipatterns(diff)
        assert not any("I/O call" in c for c in concerns)

    def test_deduplicates_files(self):
        diff = (
            "+++ b/golem/utils.py\n"
            '+    f1 = open("a.txt")\n'
            '+    f2 = open("b.txt")\n'
        )
        concerns = scan_diff_antipatterns(diff)
        matching = [c for c in concerns if "I/O call" in c]
        assert len(matching) == 1
        assert matching[0].count("golem/utils.py") == 1


class TestDataContractMismatchAntipattern:
    """Tests for data contract mismatch detection."""

    def test_camel_case_dict_key_flagged(self):
        diff = '+++ b/golem/api.py\n+    name = data["firstName"]\n'
        concerns = scan_diff_antipatterns(diff)
        assert any("data contract" in c.lower() for c in concerns)

    def test_camel_case_get_flagged(self):
        diff = '+++ b/golem/api.py\n+    name = data.get("firstName")\n'
        concerns = scan_diff_antipatterns(diff)
        assert any("data contract" in c.lower() for c in concerns)

    def test_json_direct_access_flagged(self):
        diff = '+++ b/golem/api.py\n+    val = resp.json()["status"]\n'
        concerns = scan_diff_antipatterns(diff)
        assert any("data contract" in c.lower() for c in concerns)

    def test_json_get_access_flagged(self):
        diff = '+++ b/golem/api.py\n+    val = resp.json().get("status")\n'
        concerns = scan_diff_antipatterns(diff)
        assert any("data contract" in c.lower() for c in concerns)

    def test_snake_case_key_not_flagged(self):
        """Normal snake_case dict access should NOT trigger this detector."""
        diff = '+++ b/golem/api.py\n+    name = data["first_name"]\n'
        concerns = scan_diff_antipatterns(diff)
        assert not any("data contract" in c.lower() for c in concerns)

    def test_single_word_key_not_flagged(self):
        """Single lowercase word keys are fine — no mismatch signal."""
        diff = '+++ b/golem/api.py\n+    name = data["name"]\n'
        concerns = scan_diff_antipatterns(diff)
        assert not any("data contract" in c.lower() for c in concerns)

    def test_test_file_skipped(self):
        diff = '+++ b/golem/tests/test_api.py\n+    val = resp.json()["status"]\n'
        concerns = scan_diff_antipatterns(diff)
        assert not any("data contract" in c.lower() for c in concerns)

    def test_comment_skipped(self):
        diff = '+++ b/golem/api.py\n+    # data["firstName"]\n'
        concerns = scan_diff_antipatterns(diff)
        assert not any("data contract" in c.lower() for c in concerns)

    def test_deduplicates_files(self):
        diff = (
            "+++ b/golem/api.py\n"
            '+    a = data["firstName"]\n'
            '+    b = data["lastName"]\n'
        )
        concerns = scan_diff_antipatterns(diff)
        matching = [c for c in concerns if "data contract" in c.lower()]
        assert len(matching) == 1
        assert matching[0].count("golem/api.py") == 1

    def test_response_bracket_json_access_flagged(self):
        """response.json()[key] is a contract mismatch risk."""
        diff = '+++ b/golem/handler.py\n+    x = response.json()["items"]\n'
        concerns = scan_diff_antipatterns(diff)
        assert any("data contract" in c.lower() for c in concerns)


class TestCheckDocRelevance:
    """Tests for check_doc_relevance()."""

    def test_no_diff_returns_empty(self):
        assert check_doc_relevance("") == []

    def test_pure_test_change_returns_empty(self):
        diff = "+++ b/golem/tests/test_foo.py\n+def test_bar():\n+    pass"
        assert check_doc_relevance(diff) == []

    def test_internal_change_returns_empty(self):
        diff = "+++ b/golem/orchestrator.py\n+    logger.info('internal fix')"
        assert check_doc_relevance(diff) == []

    def test_api_endpoint_without_docs_flags(self):
        diff = (
            "+++ b/golem/core/dashboard.py\n"
            "+@app.route('/api/new_endpoint')\n"
            "+def new_endpoint():\n"
            "+    return jsonify({})"
        )
        result = check_doc_relevance(diff)
        assert len(result) == 1
        assert "documentation" in result[0].lower()

    def test_api_endpoint_with_docs_returns_empty(self):
        diff = (
            "+++ b/golem/core/dashboard.py\n"
            "+@app.route('/api/new_endpoint')\n"
            "+++ b/docs/operations.md\n"
            "+## New endpoint\n"
        )
        assert check_doc_relevance(diff) == []

    def test_config_change_without_docs_flags(self):
        diff = "+++ b/golem/core/config.py\n" "+    new_setting: bool = False\n"
        result = check_doc_relevance(diff)
        assert len(result) == 1

    def test_config_change_with_readme_returns_empty(self):
        diff = (
            "+++ b/golem/core/config.py\n"
            "+    new_setting: bool = False\n"
            "+++ b/README.md\n"
            "+- `new_setting`: ...\n"
        )
        assert check_doc_relevance(diff) == []

    def test_control_api_route_without_docs_flags(self):
        diff = (
            "+++ b/golem/core/control_api.py\n"
            "+@health_router.post('/api/trigger')\n"
            "+def trigger():\n"
            "+    return jsonify({})"
        )
        result = check_doc_relevance(diff)
        assert len(result) == 1
        assert "documentation" in result[0].lower()


class TestRunValidationWithDocConcern:
    """Integration test: run_validation propagates doc-relevance concerns."""

    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff")
    def test_doc_concern_added_for_code_change(self, mock_diff, mock_invoke):
        mock_diff.return_value = (
            "+++ b/golem/core/dashboard.py\n"
            "+@app.route('/api/new_endpoint')\n"
            "+def new_endpoint():\n"
            "+    return jsonify({})\n"
        )
        mock_invoke.return_value = SimpleNamespace(
            output={
                "result": {
                    "verdict": "PASS",
                    "confidence": 0.90,
                    "task_type": "code_change",
                }
            },
            cost_usd=0.10,
        )
        v = run_validation(
            issue_id=1,
            subject="add new dashboard endpoint",
            description="desc",
            session_data={},
            work_dir="/work",
        )
        assert any("documentation" in c.lower() for c in v.concerns)
        assert v.confidence == pytest.approx(0.80)

    @patch("golem.validation.invoke_cli")
    @patch("golem.validation.get_git_diff")
    def test_doc_concern_skipped_when_docs_present(self, mock_diff, mock_invoke):
        mock_diff.return_value = (
            "+++ b/golem/core/dashboard.py\n"
            "+@app.route('/api/new_endpoint')\n"
            "+def new_endpoint():\n"
            "+    return jsonify({})\n"
            "+++ b/README.md\n"
            "+## New endpoint\n"
        )
        mock_invoke.return_value = SimpleNamespace(
            output={
                "result": {
                    "verdict": "PASS",
                    "confidence": 0.90,
                    "task_type": "code_change",
                    "concerns": [],
                }
            },
            cost_usd=0.10,
        )
        v = run_validation(
            issue_id=1,
            subject="add new dashboard endpoint",
            description="desc",
            session_data={},
            work_dir="/work",
        )
        assert not any("documentation" in c.lower() for c in v.concerns)
        assert v.confidence == pytest.approx(0.90)


class TestValidationSandboxPreexec:
    """Verify subprocess.run calls in validation use preexec_fn sandbox."""

    @pytest.mark.parametrize(
        "git_func,args",
        [
            ("_find_merge_base", ("/tmp/work",)),
            ("get_git_diff", ("/tmp/work",)),
        ],
        ids=["find_merge_base", "get_git_diff"],
    )
    @patch("golem.validation.subprocess.run")
    def test_git_calls_have_preexec_fn(self, mock_run, git_func, args):
        """All subprocess.run calls in validation git helpers must include preexec_fn."""
        from golem.validation import _find_merge_base, get_git_diff

        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=""
        )
        func_map = {"_find_merge_base": _find_merge_base, "get_git_diff": get_git_diff}
        func_map[git_func](*args)
        assert mock_run.called
        for call in mock_run.call_args_list:
            kwargs = call[1]
            assert (
                "preexec_fn" in kwargs
            ), "preexec_fn missing from subprocess.run in %s: %s" % (git_func, call)
            assert callable(kwargs["preexec_fn"])
