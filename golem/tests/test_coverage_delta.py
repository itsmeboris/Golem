"""Tests for coverage delta analysis on changed files."""

import subprocess
from unittest.mock import MagicMock, patch

from golem.verifier import CoverageDelta, _get_changed_files, parse_coverage_delta


class TestCoverageDelta:
    def test_all_changed_lines_covered(self):
        """When all changed lines are covered, delta is 100%."""
        cov_data = {
            "files": {
                "golem/foo.py": {
                    "executed_lines": [1, 2, 3, 4, 5],
                    "missing_lines": [],
                    "summary": {"percent_covered": 100.0},
                }
            }
        }
        changed_files = ["golem/foo.py"]
        result = parse_coverage_delta(cov_data, changed_files)
        assert result.all_covered is True
        assert result.delta_pct == 100.0
        assert not result.uncovered_lines

    def test_some_changed_lines_uncovered(self):
        """When changed files have missing lines, report them."""
        cov_data = {
            "files": {
                "golem/bar.py": {
                    "executed_lines": [1, 2, 3],
                    "missing_lines": [4, 5],
                    "summary": {"percent_covered": 60.0},
                }
            }
        }
        changed_files = ["golem/bar.py"]
        result = parse_coverage_delta(cov_data, changed_files)
        assert result.all_covered is False
        assert result.delta_pct < 100.0
        assert "golem/bar.py" in result.uncovered_lines
        assert result.uncovered_lines["golem/bar.py"] == [4, 5]

    def test_unchanged_files_ignored(self):
        """Files not in the changed set are not checked."""
        cov_data = {
            "files": {
                "golem/unchanged.py": {
                    "executed_lines": [1],
                    "missing_lines": [2, 3, 4, 5],
                    "summary": {"percent_covered": 20.0},
                },
                "golem/changed.py": {
                    "executed_lines": [1, 2, 3],
                    "missing_lines": [],
                    "summary": {"percent_covered": 100.0},
                },
            }
        }
        changed_files = ["golem/changed.py"]
        result = parse_coverage_delta(cov_data, changed_files)
        assert result.all_covered is True
        assert "golem/unchanged.py" not in result.uncovered_lines

    def test_test_files_excluded(self):
        """Test files are excluded from coverage delta analysis."""
        cov_data = {
            "files": {
                "golem/tests/test_foo.py": {
                    "executed_lines": [1],
                    "missing_lines": [2, 3],
                    "summary": {"percent_covered": 33.3},
                }
            }
        }
        changed_files = ["golem/tests/test_foo.py"]
        result = parse_coverage_delta(cov_data, changed_files)
        assert result.all_covered is True  # Test files are excluded

    def test_empty_changed_files(self):
        """No changed files means coverage delta is vacuously 100%."""
        result = parse_coverage_delta({"files": {}}, [])
        assert result.all_covered is True
        assert result.delta_pct == 100.0

    def test_changed_file_not_in_coverage_data(self):
        """Changed file missing from coverage data is skipped gracefully."""
        cov_data = {"files": {}}
        changed_files = ["golem/not_in_coverage.py"]
        result = parse_coverage_delta(cov_data, changed_files)
        assert result.all_covered is True
        assert result.delta_pct == 100.0


class TestCoverageDeltaSummary:
    def test_summary_all_covered(self):
        """Summary returns 100% message when all covered."""
        delta = CoverageDelta(all_covered=True, delta_pct=100.0, uncovered_lines={})
        assert delta.summary() == "Coverage delta: 100% on changed files"

    def test_summary_some_uncovered(self):
        """Summary lists uncovered files and lines when not all covered."""
        delta = CoverageDelta(
            all_covered=False,
            delta_pct=60.0,
            uncovered_lines={"golem/foo.py": [4, 5]},
        )
        summary = delta.summary()
        assert "60%" in summary
        assert "golem/foo.py" in summary
        assert "[4, 5]" in summary


class TestGetChangedFiles:
    @patch("golem.verifier.subprocess.run")
    def test_returns_changed_files_on_success(self, mock_run):
        """Returns list of files from git diff output."""
        mock_run.return_value = MagicMock(
            returncode=0, stdout="golem/foo.py\ngolem/bar.py\n", stderr=""
        )
        result = _get_changed_files("/some/dir")
        assert result == ["golem/foo.py", "golem/bar.py"]

    @patch("golem.verifier.subprocess.run")
    def test_returns_empty_list_on_nonzero_exit(self, mock_run):
        """Returns empty list when git command exits with error."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="fatal: bad revision"
        )
        result = _get_changed_files("/some/dir")
        assert result == []

    @patch("golem.verifier.subprocess.run")
    def test_returns_empty_list_on_empty_output(self, mock_run):
        """Returns empty list when git diff produces no output."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = _get_changed_files("/some/dir")
        assert result == []

    @patch("golem.verifier.subprocess.run")
    def test_returns_empty_list_on_subprocess_error(self, mock_run):
        """Returns empty list when subprocess raises an error."""
        mock_run.side_effect = subprocess.SubprocessError("git not found")
        result = _get_changed_files("/some/dir")
        assert result == []

    @patch("golem.verifier.subprocess.run")
    def test_returns_empty_list_on_oserror(self, mock_run):
        """Returns empty list when OSError is raised."""
        mock_run.side_effect = OSError("No such file or directory")
        result = _get_changed_files("/some/dir")
        assert result == []
