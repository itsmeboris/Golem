# pylint: disable=too-few-public-methods,redefined-outer-name
"""Tests for golem.merge_review — agent-assisted reconciliation and conflict resolution."""

from unittest.mock import MagicMock, patch

import pytest

from golem.merge_review import (
    ReconciliationResult,
    _format_current_files,
    _format_missing_summary,
    _get_short_sha,
    _read_conflict_content,
    _read_file_content,
    run_conflict_resolution,
    run_merge_reconciliation,
)
from golem.worktree_manager import MissingAddition


@pytest.fixture()
def sample_missing():
    return [
        MissingAddition(
            file="foo.py",
            expected_lines=["def hello():", '    return "world"'],
            description="2/2 added lines missing from foo.py",
        ),
    ]


@pytest.fixture()
def sample_diff():
    return (
        "diff --git a/foo.py b/foo.py\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1 +1,3 @@\n"
        "+def hello():\n"
        '+    return "world"\n'
    )


class TestReconciliationResultDefaults:
    def test_defaults(self):
        r = ReconciliationResult()
        assert r.resolved is False
        assert r.commit_sha == ""
        assert r.explanation == ""


class TestReadFileContent:
    def test_existing_file(self, tmp_path):
        (tmp_path / "x.py").write_text("content here")
        assert _read_file_content(str(tmp_path), "x.py") == "content here"

    def test_missing_file(self, tmp_path):
        result = _read_file_content(str(tmp_path), "nope.py")
        assert "does not exist" in result

    def test_large_file_preserved(self, tmp_path):
        (tmp_path / "big.py").write_text("x" * 100)
        result = _read_file_content(str(tmp_path), "big.py")
        assert len(result) == 100


class TestFormatMissingSummary:
    def test_basic(self, sample_missing):
        result = _format_missing_summary(sample_missing)
        assert "foo.py" in result
        assert "def hello():" in result

    def test_all_lines_included(self):
        m = MissingAddition(
            file="big.py",
            expected_lines=[f"line {i}" for i in range(30)],
            description="30/30 missing",
        )
        result = _format_missing_summary([m])
        assert "line 29" in result

    def test_empty(self):
        assert _format_missing_summary([]) == ""


class TestFormatCurrentFiles:
    def test_reads_files(self, tmp_path, sample_missing):
        (tmp_path / "foo.py").write_text("existing content")
        result = _format_current_files(str(tmp_path), sample_missing)
        assert "existing content" in result
        assert "foo.py" in result


class TestReadConflictContent:
    def test_reads_files(self, tmp_path):
        (tmp_path / "a.py").write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>>")
        result = _read_conflict_content(str(tmp_path), ["a.py"])
        assert "<<<<<<< HEAD" in result

    def test_missing_file(self, tmp_path):
        result = _read_conflict_content(str(tmp_path), ["gone.py"])
        assert "does not exist" in result


class TestGetShortSha:
    def test_returns_sha(self, monkeypatch):
        mock_result = MagicMock()
        mock_result.stdout = "abc1234\n"
        monkeypatch.setattr(
            "golem.merge_review.subprocess.run", lambda *a, **kw: mock_result
        )
        assert _get_short_sha("/repo") == "abc1234"


class TestRunMergeReconciliation:
    def test_nothing_missing_returns_resolved(self):
        result = run_merge_reconciliation("/repo", "diff", [])
        assert result.resolved is True
        assert result.explanation == "nothing missing"

    @patch("golem.merge_review._get_short_sha", return_value="fix123")
    @patch("golem.merge_review.invoke_cli")
    def test_resolved(self, mock_cli, _sha, tmp_path, sample_missing, sample_diff):
        (tmp_path / "foo.py").write_text("existing")
        mock_cli.return_value = MagicMock(
            output={"result": {"resolved": True, "explanation": "re-applied"}}
        )
        result = run_merge_reconciliation(
            str(tmp_path), sample_diff, sample_missing, budget_usd=0.5
        )
        assert result.resolved is True
        assert result.commit_sha == "fix123"
        assert result.explanation == "re-applied"
        mock_cli.assert_called_once()

    @patch("golem.merge_review.invoke_cli")
    def test_not_resolved(self, mock_cli, tmp_path, sample_missing, sample_diff):
        (tmp_path / "foo.py").write_text("existing")
        mock_cli.return_value = MagicMock(
            output={"result": {"resolved": False, "explanation": "diverged too far"}}
        )
        result = run_merge_reconciliation(str(tmp_path), sample_diff, sample_missing)
        assert result.resolved is False
        assert "diverged" in result.explanation

    @patch("golem.merge_review.invoke_cli")
    def test_string_output_parsed(
        self, mock_cli, tmp_path, sample_missing, sample_diff
    ):
        (tmp_path / "foo.py").write_text("existing")
        mock_cli.return_value = MagicMock(
            output={"result": '{"resolved": false, "explanation": "nope"}'}
        )
        result = run_merge_reconciliation(str(tmp_path), sample_diff, sample_missing)
        assert result.resolved is False

    @patch("golem.merge_review.invoke_cli", side_effect=RuntimeError("boom"))
    def test_agent_error(self, _cli, tmp_path, sample_missing, sample_diff):
        (tmp_path / "foo.py").write_text("existing")
        result = run_merge_reconciliation(str(tmp_path), sample_diff, sample_missing)
        assert result.resolved is False
        assert "agent error" in result.explanation


class TestRunConflictResolution:
    def test_no_conflicts_returns_resolved(self):
        result = run_conflict_resolution("/repo", [])
        assert result.resolved is True
        assert result.explanation == "no conflicts"

    @patch("golem.merge_review.invoke_cli")
    def test_resolved(self, mock_cli, tmp_path):
        (tmp_path / "c.py").write_text("conflict content")
        mock_cli.return_value = MagicMock(
            output={"result": {"resolved": True, "explanation": "merged both sides"}}
        )
        result = run_conflict_resolution(str(tmp_path), ["c.py"], budget_usd=0.5)
        assert result.resolved is True
        assert "merged" in result.explanation

    @patch("golem.merge_review.invoke_cli")
    def test_not_resolved(self, mock_cli, tmp_path):
        (tmp_path / "c.py").write_text("conflict content")
        mock_cli.return_value = MagicMock(
            output={"result": {"resolved": False, "explanation": "too complex"}}
        )
        result = run_conflict_resolution(str(tmp_path), ["c.py"])
        assert result.resolved is False

    @patch("golem.merge_review.invoke_cli")
    def test_string_output_parsed(self, mock_cli, tmp_path):
        (tmp_path / "c.py").write_text("conflict content")
        mock_cli.return_value = MagicMock(
            output={"result": '{"resolved": true, "explanation": "ok"}'}
        )
        result = run_conflict_resolution(str(tmp_path), ["c.py"])
        assert result.resolved is True

    @patch("golem.merge_review.invoke_cli", side_effect=RuntimeError("crash"))
    def test_agent_error(self, _cli, tmp_path):
        (tmp_path / "c.py").write_text("conflict content")
        result = run_conflict_resolution(str(tmp_path), ["c.py"])
        assert result.resolved is False
        assert "agent error" in result.explanation
