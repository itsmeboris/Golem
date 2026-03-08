# pylint: disable=too-few-public-methods
"""Tests for golem.validation — git helpers, prompt formatting, verdict parsing."""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from golem.validation import (
    ValidationVerdict,
    _build_validation_prompt,
    _format_event_log,
    _parse_validation_output,
    get_git_diff,
    has_uncommitted_changes,
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


class TestHasUncommittedChanges:
    def test_empty_work_dir(self):
        assert has_uncommitted_changes("") is False

    @patch("golem.validation.subprocess.run")
    def test_with_changes(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=" M file.py\n"
        )
        assert has_uncommitted_changes("/some/dir") is True

    @patch("golem.validation.subprocess.run")
    def test_no_changes(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=""
        )
        assert has_uncommitted_changes("/some/dir") is False

    @patch("golem.validation.subprocess.run", side_effect=OSError("nope"))
    def test_error_returns_false(self, _):
        assert has_uncommitted_changes("/bad/dir") is False


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
        def side_effect(args, **kwargs):
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

        def side_effect(args, **kwargs):
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
        def side_effect(args, **kwargs):
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

        def side_effect(args, **kwargs):
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

        def side_effect(args, **kwargs):
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

        def side_effect(args, **kwargs):
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
        def side_effect(args, **kwargs):
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
