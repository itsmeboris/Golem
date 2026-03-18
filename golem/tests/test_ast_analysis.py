"""Tests for AST-based diff analysis."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from golem.ast_analysis import (
    _is_import_used,
    _is_test_file,
    _is_unused_import_concern,
    _parse_sg_output,
    is_ast_grep_available,
    run_ast_analysis,
)


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


class TestIsTestFile:
    """Unit tests for _is_test_file."""

    @pytest.mark.parametrize(
        "filepath,expected",
        [
            # test_*.py basenames
            ("test_foo.py", True),
            ("test_bar.py", True),
            ("path/to/test_foo.py", True),
            # *_test.py basenames
            ("foo_test.py", True),
            ("bar_test.py", True),
            ("path/to/foo_test.py", True),
            # Non-test files
            ("foo.py", False),
            ("testing_utils.py", False),
            ("attest.py", False),
            ("golem/ast_analysis.py", False),
            # Edge: just the basename, no directory
            ("test_bar_test.py", True),
            # Edge: test_ prefix only (no .py suffix match for *_test.py)
            ("test_.py", True),
        ],
    )
    def test_is_test_file(self, filepath: str, expected: bool):
        assert _is_test_file(filepath) is expected


class TestIsUnusedImportConcern:
    """Unit tests for _is_unused_import_concern."""

    @pytest.mark.parametrize(
        "message,expected",
        [
            ("Potentially unused import: json", True),
            ("Potentially unused import: os.path", True),
            ("Potentially unused import: ", True),
            ("bare except clause used", False),
            ("", False),
            ("unused import json", False),
            ("potentially unused import: json", False),  # case-sensitive
        ],
    )
    def test_is_unused_import_concern(self, message: str, expected: bool):
        assert _is_unused_import_concern(message) is expected


class TestIsImportUsed:
    """Unit tests for _is_import_used."""

    def test_module_used_in_non_import_line(self, tmp_path):
        """Returns True when module appears in a non-import line."""
        f = tmp_path / "foo.py"
        f.write_text("import json\ndata = json.loads('{}')\n")
        assert _is_import_used(str(tmp_path), "foo.py", "json") is True

    def test_module_not_used(self, tmp_path):
        """Returns False when module only appears in the import line."""
        f = tmp_path / "foo.py"
        f.write_text("import json\n\ndef do_nothing():\n    pass\n")
        assert _is_import_used(str(tmp_path), "foo.py", "json") is False

    def test_from_import_line_excluded(self, tmp_path):
        """'from X import Y' lines are treated as import lines and excluded."""
        f = tmp_path / "foo.py"
        f.write_text("from os import path\n\ndef foo():\n    pass\n")
        assert _is_import_used(str(tmp_path), "foo.py", "os") is False

    def test_module_used_after_from_import(self, tmp_path):
        """Module referenced in non-import code is detected."""
        f = tmp_path / "foo.py"
        f.write_text("from os import path\nos.getcwd()\n")
        assert _is_import_used(str(tmp_path), "foo.py", "os") is True

    def test_file_not_readable_returns_false(self, tmp_path):
        """When file cannot be read, returns False so the concern is kept (not suppressed)."""
        assert _is_import_used(str(tmp_path), "nonexistent.py", "json") is False

    def test_empty_file(self, tmp_path):
        """Empty file means module is not used."""
        f = tmp_path / "empty.py"
        f.write_text("")
        assert _is_import_used(str(tmp_path), "empty.py", "json") is False

    def test_module_name_in_string_literal(self, tmp_path):
        """Module name appearing in a string counts as 'used' (heuristic)."""
        f = tmp_path / "foo.py"
        f.write_text("import json\nx = 'json module is cool'\n")
        assert _is_import_used(str(tmp_path), "foo.py", "json") is True

    def test_uses_work_dir_and_filepath_together(self, tmp_path):
        """File is resolved as work_dir/filepath."""
        subdir = tmp_path / "sub"
        subdir.mkdir()
        f = subdir / "bar.py"
        f.write_text("import os\nresult = os.path.join('a', 'b')\n")
        assert _is_import_used(str(tmp_path), "sub/bar.py", "os") is True


class TestRunAstAnalysisFiltering:
    """Integration tests for unused-import filtering in run_ast_analysis."""

    _FAKE_RULE = Path("unused_import.yaml")

    def _make_mock_proc(self, matches: list[dict]) -> MagicMock:
        import json as _json

        stdout = _json.dumps(matches)
        return MagicMock(returncode=0, stdout=stdout, stderr="")

    def test_test_file_unused_import_suppressed(self, tmp_path):
        """Unused-import concern for test files is suppressed (SPEC-1)."""
        (tmp_path / "test_foo.py").write_text("import json\n")
        matches = [
            {
                "file": "test_foo.py",
                "range": {"start": {"line": 1}},
                "message": "Potentially unused import: json",
            }
        ]
        mock_proc = self._make_mock_proc(matches)
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([self._FAKE_RULE])):
                    result = run_ast_analysis(str(tmp_path), ["test_foo.py"])
        assert result == []

    def test_non_test_file_unused_import_actually_used_suppressed(self, tmp_path):
        """Unused-import concern suppressed when module IS used (SPEC-2)."""
        (tmp_path / "foo.py").write_text("import json\ndata = json.loads('{}')\n")
        matches = [
            {
                "file": "foo.py",
                "range": {"start": {"line": 1}},
                "message": "Potentially unused import: json",
            }
        ]
        mock_proc = self._make_mock_proc(matches)
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([self._FAKE_RULE])):
                    result = run_ast_analysis(str(tmp_path), ["foo.py"])
        assert result == []

    def test_non_test_file_unused_import_truly_unused_kept(self, tmp_path):
        """Unused-import concern kept when module is genuinely unused (SPEC-2)."""
        (tmp_path / "foo.py").write_text("import json\n\ndef do_nothing():\n    pass\n")
        matches = [
            {
                "file": "foo.py",
                "range": {"start": {"line": 1}},
                "message": "Potentially unused import: json",
            }
        ]
        mock_proc = self._make_mock_proc(matches)
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([self._FAKE_RULE])):
                    result = run_ast_analysis(str(tmp_path), ["foo.py"])
        assert len(result) == 1
        assert "Potentially unused import: json" in result[0]
        assert "foo.py" in result[0]

    def test_bare_except_passes_through_unfiltered(self, tmp_path):
        """bare_except concerns are never filtered (SPEC-3)."""
        (tmp_path / "foo.py").write_text("try:\n    pass\nexcept:\n    pass\n")
        matches = [
            {
                "file": "foo.py",
                "range": {"start": {"line": 3}},
                "message": "bare except clause used",
            }
        ]
        mock_proc = self._make_mock_proc(matches)
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([self._FAKE_RULE])):
                    result = run_ast_analysis(str(tmp_path), ["foo.py"])
        assert len(result) == 1
        assert "bare except clause used" in result[0]

    def test_bare_except_in_test_file_passes_through(self, tmp_path):
        """bare_except in a test file is NOT filtered — only unused-import is (SPEC-3)."""
        (tmp_path / "test_foo.py").write_text("try:\n    pass\nexcept:\n    pass\n")
        matches = [
            {
                "file": "test_foo.py",
                "range": {"start": {"line": 3}},
                "message": "bare except clause used",
            }
        ]
        mock_proc = self._make_mock_proc(matches)
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([self._FAKE_RULE])):
                    result = run_ast_analysis(str(tmp_path), ["test_foo.py"])
        assert len(result) == 1
        assert "bare except clause used" in result[0]

    def test_mixed_test_and_non_test_files(self, tmp_path):
        """Mixed results: test file unused-import suppressed, non-test kept (SPEC-5)."""
        (tmp_path / "test_foo.py").write_text("import json\n")
        (tmp_path / "bar.py").write_text("import os\n\ndef f(): pass\n")
        matches = [
            {
                "file": "test_foo.py",
                "range": {"start": {"line": 1}},
                "message": "Potentially unused import: json",
            },
            {
                "file": "bar.py",
                "range": {"start": {"line": 1}},
                "message": "Potentially unused import: os",
            },
        ]
        mock_proc = self._make_mock_proc(matches)
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([self._FAKE_RULE])):
                    result = run_ast_analysis(str(tmp_path), ["test_foo.py", "bar.py"])
        assert len(result) == 1
        assert "bar.py" in result[0]
        assert "Potentially unused import: os" in result[0]

    def test_unreadable_file_keeps_concern(self, tmp_path):
        """When file can't be read, unused-import concern is kept conservatively."""
        matches = [
            {
                "file": "ghost.py",
                "range": {"start": {"line": 1}},
                "message": "Potentially unused import: json",
            }
        ]
        mock_proc = self._make_mock_proc(matches)
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([self._FAKE_RULE])):
                    result = run_ast_analysis(str(tmp_path), ["ghost.py"])
        assert len(result) == 1
        assert "ghost.py" in result[0]

    def test_empty_files_list_returns_empty(self, tmp_path):
        """Empty changed_files list returns empty result (SPEC-5)."""
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            result = run_ast_analysis(str(tmp_path), [])
        assert result == []

    def test_no_matches_returns_empty(self, tmp_path):
        """No matches from ast-grep returns empty list (SPEC-5)."""
        mock_proc = MagicMock(returncode=0, stdout="", stderr="")
        with patch("golem.ast_analysis.is_ast_grep_available", return_value=True):
            with patch("subprocess.run", return_value=mock_proc):
                with patch("pathlib.Path.glob", return_value=iter([self._FAKE_RULE])):
                    result = run_ast_analysis(str(tmp_path), ["foo.py"])
        assert result == []
