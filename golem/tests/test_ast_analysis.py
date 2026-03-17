"""Tests for AST-based diff analysis."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from golem.ast_analysis import _parse_sg_output, is_ast_grep_available, run_ast_analysis


class TestAstGrepAvailability:
    def test_returns_true_when_installed(self):
        """Returns True when ast-grep (sg) binary is on PATH."""
        with patch("shutil.which", return_value="/usr/bin/sg"):
            assert is_ast_grep_available() is True

    def test_returns_false_when_missing(self):
        """Returns False when ast-grep is not installed."""
        with patch("shutil.which", return_value=None):
            assert is_ast_grep_available() is False


class TestAstAnalysis:
    def test_returns_empty_when_unavailable(self):
        """When ast-grep is not installed, return empty concerns list."""
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=False):
            result = run_ast_analysis("/tmp/fake", ["golem/foo.py"])
            assert not result

    def test_returns_concerns_for_matches(self):
        """When ast-grep finds matches, return concern strings."""
        mock_output = (
            '{"file":"golem/foo.py","range":{"start":{"line":5}},'
            '"message":"unused import"}\n'
        )
        mock_proc = MagicMock(returncode=0, stdout=mock_output, stderr="")
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                result = run_ast_analysis("/tmp/fake", ["golem/foo.py"])
                assert len(result) >= 1
                assert "unused import" in result[0] or "golem/foo.py" in result[0]

    def test_returns_empty_on_no_matches(self):
        """When ast-grep finds nothing, return empty list."""
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                result = run_ast_analysis("/tmp/fake", ["golem/foo.py"])
                assert not result

    def test_returns_empty_when_no_rules(self):
        """When no rule files exist, return empty list."""
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("pathlib.Path.is_dir", return_value=False):
                result = run_ast_analysis("/tmp/fake", ["golem/foo.py"])
                assert not result

    def test_filters_to_python_files(self):
        """Non-Python files are excluded from analysis."""
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            result = run_ast_analysis("/tmp/fake", ["README.md", "config.yaml"])
            assert not result

    def test_returns_empty_when_no_yaml_rule_files(self):
        """When rules dir exists but has no yaml files, return empty list."""
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("pathlib.Path.is_dir", return_value=True):
                with patch("pathlib.Path.glob", return_value=iter([])):
                    result = run_ast_analysis("/tmp/fake", ["golem/foo.py"])
                    assert not result

    def test_skips_invalid_json_lines(self):
        """Invalid JSON output lines are silently skipped."""
        mock_output = "not-valid-json\n"
        mock_proc = MagicMock(returncode=0, stdout=mock_output, stderr="")
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                result = run_ast_analysis("/tmp/fake", ["golem/foo.py"])
                assert not result

    def test_handles_subprocess_error(self):
        """SubprocessError during ast-grep run is silently logged and skipped."""
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch(
                "subprocess.run", side_effect=subprocess.SubprocessError("timeout")
            ):
                result = run_ast_analysis("/tmp/fake", ["golem/foo.py"])
                assert not result

    def test_returns_empty_for_empty_file_list(self):
        """Empty changed_files list returns empty result."""
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            result = run_ast_analysis("/tmp/fake", [])
            assert not result

    def test_parses_json_array_output(self):
        """ast-grep --json outputs a JSON array, not JSONL."""
        mock_output = (
            '[{"file":"golem/foo.py","range":{"start":{"line":5}},'
            '"message":"unused import"},'
            '{"file":"golem/bar.py","range":{"start":{"line":10}},'
            '"message":"bare except"}]'
        )
        mock_proc = MagicMock(returncode=0, stdout=mock_output, stderr="")
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([Path("r.yaml")])):
                    result = run_ast_analysis("/tmp/fake", ["golem/foo.py"])
                    assert len(result) == 2
                    assert "unused import" in result[0]
                    assert "golem/foo.py" in result[0]
                    assert "bare except" in result[1]
                    assert "golem/bar.py" in result[1]

    def test_parses_multiline_json_array_output(self):
        """ast-grep --json pretty-printed array with one match per line."""
        mock_output = (
            "[\n"
            '{"file":"golem/foo.py","range":{"start":{"line":3}},'
            '"message":"unused import"}\n'
            "]"
        )
        mock_proc = MagicMock(returncode=0, stdout=mock_output, stderr="")
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([Path("r.yaml")])):
                    result = run_ast_analysis("/tmp/fake", ["golem/foo.py"])
                    assert len(result) == 1
                    assert "unused import" in result[0]


class TestParseSgOutput:
    """Unit tests for _parse_sg_output."""

    def test_empty_string(self):
        assert _parse_sg_output("") == []

    def test_json_array(self):
        result = _parse_sg_output('[{"file":"a.py"},{"file":"b.py"}]')
        assert len(result) == 2
        assert result[0]["file"] == "a.py"

    def test_single_json_object(self):
        result = _parse_sg_output('{"file":"a.py"}')
        assert result == [{"file": "a.py"}]

    def test_jsonl_fallback(self):
        result = _parse_sg_output('{"file":"a.py"}\n{"file":"b.py"}\n')
        assert len(result) == 2

    def test_filters_non_dict_array_elements(self):
        result = _parse_sg_output('[{"file":"a.py"}, "stray string", 42]')
        assert len(result) == 1
        assert result[0]["file"] == "a.py"

    def test_non_json_returns_empty(self):
        result = _parse_sg_output("not json at all")
        assert result == []

    def test_non_dict_non_list_json(self):
        result = _parse_sg_output("42")
        assert result == []
