# pylint: disable=too-few-public-methods,redefined-outer-name
"""Tests for golem.merge_review — agent-assisted reconciliation and conflict resolution."""

from unittest.mock import MagicMock, patch

import pytest

from golem.merge_review import (
    ReconciliationResult,
    _format_current_files,
    _format_missing_summary,
    _get_short_sha,
    _read_file_content,
    run_merge_agent,
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

    def test_path_traversal_blocked(self, tmp_path):
        result = _read_file_content(str(tmp_path), "../../etc/passwd")
        assert "blocked" in result
        assert "path traversal" in result


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


class TestGetShortSha:
    def test_returns_sha(self, monkeypatch, tmp_path):
        mock_result = MagicMock()
        mock_result.stdout = "abc1234\n"
        monkeypatch.setattr(
            "golem.merge_review.subprocess.run", lambda *a, **kw: mock_result
        )
        assert _get_short_sha(str(tmp_path / "repo")) == "abc1234"


class TestRunMergeAgent:
    def test_no_conflicts_or_missing_returns_resolved(self, tmp_path):
        result = run_merge_agent(
            str(tmp_path / "repo"),
            123,
            agent_diff="diff",
            conflict_files=[],
            missing=[],
        )
        assert result.resolved is True
        assert result.explanation == "nothing to resolve"

    @patch("golem.merge_review._get_short_sha", return_value="fix123")
    @patch("golem.merge_review.invoke_cli")
    def test_conflict_resolved(self, mock_cli, _sha, tmp_path):
        (tmp_path / "c.py").write_text("content")
        mock_cli.return_value = MagicMock(
            output={"result": {"resolved": True, "explanation": "merged both sides"}}
        )
        result = run_merge_agent(
            str(tmp_path),
            123,
            agent_diff="diff",
            conflict_files=["c.py"],
            missing=[],
        )
        assert result.resolved is True
        assert result.commit_sha == "fix123"

    @patch("golem.merge_review.invoke_cli")
    def test_agent_declines(self, mock_cli, tmp_path):
        (tmp_path / "c.py").write_text("content")
        mock_cli.return_value = MagicMock(
            output={"result": {"resolved": False, "explanation": "too complex"}}
        )
        result = run_merge_agent(
            str(tmp_path),
            123,
            agent_diff="diff",
            conflict_files=["c.py"],
            missing=[],
        )
        assert result.resolved is False

    @patch("golem.merge_review.invoke_cli", side_effect=RuntimeError("crash"))
    def test_agent_error(self, _cli, tmp_path):
        (tmp_path / "c.py").write_text("content")
        result = run_merge_agent(
            str(tmp_path),
            123,
            agent_diff="diff",
            conflict_files=["c.py"],
            missing=[],
        )
        assert result.resolved is False
        assert "agent error" in result.explanation

    @patch("golem.merge_review._get_short_sha", return_value="fix456")
    @patch("golem.merge_review.invoke_cli")
    def test_missing_only_resolved(self, mock_cli, _sha, tmp_path):
        (tmp_path / "m.py").write_text("content")
        mock_cli.return_value = MagicMock(
            output={"result": {"resolved": True, "explanation": "re-applied additions"}}
        )
        result = run_merge_agent(
            str(tmp_path),
            123,
            agent_diff="diff",
            conflict_files=[],
            missing=[
                MissingAddition(
                    file="m.py",
                    expected_lines=["+code"],
                    description="missing addition",
                )
            ],
        )
        assert result.resolved is True

    @patch("golem.merge_review._get_short_sha", return_value="fix789")
    @patch("golem.merge_review.invoke_cli")
    def test_string_output_parsed(self, mock_cli, _sha, tmp_path):
        (tmp_path / "c.py").write_text("content")
        mock_cli.return_value = MagicMock(
            output={"result": '{"resolved": true, "explanation": "fixed"}'}
        )
        result = run_merge_agent(
            str(tmp_path),
            123,
            agent_diff="diff",
            conflict_files=["c.py"],
            missing=[],
        )
        assert result.resolved is True
