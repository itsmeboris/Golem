"""Tests for the JS shared-state lint scanner."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from golem.lint.js_shared_state import (
    _strip_strings_and_comments,
    scan_shared_state_patterns,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(tmp_path, name, content):
    """Write content to a .js file under tmp_path and return the path."""
    p = tmp_path / name
    p.write_text(content)
    return p


def _findings_for(tmp_path, content, name="test.js"):
    _write(tmp_path, name, content)
    return scan_shared_state_patterns(tmp_path)


# ---------------------------------------------------------------------------
# Empty / no-JS cases
# ---------------------------------------------------------------------------


class TestEmptyCases:
    def test_empty_directory_returns_empty_list(self, tmp_path):
        result = scan_shared_state_patterns(tmp_path)
        assert result == []

    def test_directory_with_no_js_files_returns_empty_list(self, tmp_path):
        (tmp_path / "readme.txt").write_text("nothing here")
        result = scan_shared_state_patterns(tmp_path)
        assert result == []

    def test_empty_js_file_returns_empty_list(self, tmp_path):
        result = _findings_for(tmp_path, "")
        assert result == []

    def test_js_file_with_no_let_declarations(self, tmp_path):
        js = "const x = 1;\nvar y = 2;\n"
        result = _findings_for(tmp_path, js)
        assert result == []


# ---------------------------------------------------------------------------
# Result schema
# ---------------------------------------------------------------------------


class TestResultSchema:
    def test_result_schema(self, tmp_path):
        js = "let x = 0;\nasync function f() { x = 1; }\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        finding = results[0]
        assert set(finding.keys()) == {"file", "line", "variable", "message"}
        assert isinstance(finding["file"], str)
        assert os.path.isabs(finding["file"])
        assert isinstance(finding["line"], int)
        assert finding["variable"] == "x"
        assert isinstance(finding["message"], str)
        assert "x" in finding["message"]


# ---------------------------------------------------------------------------
# SPEC-1: scans .js files under root (recursively)
# ---------------------------------------------------------------------------


class TestFileDiscovery:
    def test_scans_nested_js_file(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.js").write_text("let x = 0;\nasync function f() { x = 1; }\n")
        results = scan_shared_state_patterns(tmp_path)
        assert len(results) == 1
        assert "nested.js" in results[0]["file"]

    def test_ignores_non_js_files(self, tmp_path):
        (tmp_path / "app.ts").write_text("let x = 0;\nasync function f() { x = 1; }\n")
        results = scan_shared_state_patterns(tmp_path)
        assert results == []

    def test_multiple_js_files_all_scanned(self, tmp_path):
        (tmp_path / "a.js").write_text("let x = 0;\nasync function f() { x = 1; }\n")
        (tmp_path / "b.js").write_text("let y = 0;\nasync function g() { y = 1; }\n")
        results = scan_shared_state_patterns(tmp_path)
        assert len(results) == 2
        variables = {r["variable"] for r in results}
        assert variables == {"x", "y"}


# ---------------------------------------------------------------------------
# SPEC-2: top-level let only
# ---------------------------------------------------------------------------


class TestTopLevelLet:
    def test_let_inside_function_not_flagged(self, tmp_path):
        js = (
            "function outer() {\n"
            "  let x = 0;\n"
            "  async function inner() { x = 1; }\n"
            "}\n"
        )
        results = _findings_for(tmp_path, js)
        assert results == []

    def test_let_inside_block_not_flagged(self, tmp_path):
        js = "{\n  let x = 0;\n}\nasync function f() { x = 1; }\n"
        results = _findings_for(tmp_path, js)
        assert results == []

    def test_const_not_flagged(self, tmp_path):
        js = "const x = 0;\nasync function f() { x = 1; }\n"
        results = _findings_for(tmp_path, js)
        assert results == []

    def test_var_not_flagged(self, tmp_path):
        js = "var x = 0;\nasync function f() { x = 1; }\n"
        results = _findings_for(tmp_path, js)
        assert results == []

    def test_top_level_let_flagged(self, tmp_path):
        js = "let x = 0;\nasync function f() { x = 1; }\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"


# ---------------------------------------------------------------------------
# SPEC-3: mutation operators
# ---------------------------------------------------------------------------


class TestMutationOperators:
    @pytest.mark.parametrize(
        "mutation_line",
        [
            "  x = 1;",
            "  x += 1;",
            "  x -= 1;",
            "  x++;",
            "  x--;",
            "  x.push(1);",
            "  x.pop();",
            "  x.splice(0, 1);",
        ],
    )
    def test_mutation_operators_flagged_in_async(self, tmp_path, mutation_line):
        js = f"let x = [];\nasync function f() {{\n{mutation_line}\n}}\n"
        results = _findings_for(tmp_path, js, name=f"op_{hash(mutation_line)}.js")
        assert len(results) == 1, f"Expected 1 finding for: {mutation_line!r}"
        assert results[0]["variable"] == "x"

    def test_mutation_outside_async_not_flagged(self, tmp_path):
        js = "let x = 0;\nfunction sync_fn() {\n  x = 1;\n}\n"
        results = _findings_for(tmp_path, js)
        assert results == []

    def test_mutation_reads_not_flagged(self, tmp_path):
        # Reading x is not a mutation
        js = "let x = 0;\nasync function f() {\n  const y = x + 1;\n}\n"
        results = _findings_for(tmp_path, js)
        assert results == []


# ---------------------------------------------------------------------------
# SPEC-4: async context types
# ---------------------------------------------------------------------------


class TestAsyncContextTypes:
    def test_async_function_body_is_context(self, tmp_path):
        js = "let x = 0;\nasync function doWork() {\n  x = 1;\n}\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_async_arrow_body_is_context(self, tmp_path):
        js = "let x = 0;\nconst fn = async () => {\n  x = 1;\n};\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_then_callback_is_context(self, tmp_path):
        js = "let x = 0;\nfetch('/api').then(() => {\n  x = 1;\n});\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_add_event_listener_callback_is_context(self, tmp_path):
        js = "let x = false;\nes.addEventListener('evt', () => {\n  x = true;\n});\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_set_interval_callback_is_context(self, tmp_path):
        js = "let x = 0;\nsetInterval(() => {\n  x += 1;\n}, 1000);\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_set_timeout_callback_is_context(self, tmp_path):
        js = "let x = 0;\nsetTimeout(() => {\n  x += 1;\n}, 500);\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_set_interval_async_callback_is_context(self, tmp_path):
        js = "let x = 0;\nsetInterval(async () => {\n  x += 1;\n}, 1000);\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_sync_function_body_not_context(self, tmp_path):
        js = "let x = 0;\nfunction notAsync() {\n  x = 1;\n}\n"
        results = _findings_for(tmp_path, js)
        assert results == []


# ---------------------------------------------------------------------------
# SPEC-5: line number accuracy
# ---------------------------------------------------------------------------


class TestLineNumbers:
    def test_line_number_points_to_mutation(self, tmp_path):
        # Line 1: let x = 0;
        # Line 2: async function f() {
        # Line 3:   x = 1;  <-- mutation on line 3
        # Line 4: }
        js = "let x = 0;\nasync function f() {\n  x = 1;\n}\n"
        results = _findings_for(tmp_path, js)
        assert results[0]["line"] == 3

    def test_line_number_for_add_event_listener(self, tmp_path):
        js = (
            "let _needsUpdate = false;\n"
            "\n"
            "es.addEventListener('evt', () => {\n"
            "  _needsUpdate = true;\n"
            "});\n"
        )
        results = _findings_for(tmp_path, js)
        assert results[0]["line"] == 4

    def test_multiple_mutations_multiple_lines(self, tmp_path):
        js = (
            "let x = 0;\n"
            "let y = 0;\n"
            "async function f() {\n"
            "  x = 1;\n"
            "  y = 2;\n"
            "}\n"
        )
        results = _findings_for(tmp_path, js)
        lines = {r["line"] for r in results}
        assert lines == {4, 5}


# ---------------------------------------------------------------------------
# SPEC-6: guard patterns suppress findings
# ---------------------------------------------------------------------------


class TestGuardPatterns:
    @pytest.mark.parametrize(
        "guard_word",
        ["mutex", "lock", "queue", "semaphore", "MUTEX", "Lock", "Queue"],
    )
    def test_guard_word_suppresses_finding(self, tmp_path, guard_word):
        js = (
            f"let x = 0;\n"
            f"async function f() {{\n"
            f"  // {guard_word} protected\n"
            f"  x = 1;\n"
            f"}}\n"
        )
        results = _findings_for(tmp_path, js, name=f"guard_{guard_word}.js")
        assert results == [], f"Expected no finding when {guard_word!r} guards mutation"

    def test_unguarded_mutation_flagged(self, tmp_path):
        js = "let x = 0;\nasync function f() {\n  x = 1;\n}\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1

    def test_guard_far_from_mutation_not_suppressed(self, tmp_path):
        # Guard on line 1, mutation on line 10 — too far away
        lines = ["let x = 0;"]
        lines.append("// mutex protected section starts")
        lines.append("async function f() {")
        for _ in range(6):
            lines.append("  const y = 1;")
        lines.append("  x = 1;")
        lines.append("}")
        js = "\n".join(lines) + "\n"
        results = _findings_for(tmp_path, js)
        # With guard far away from mutation, should be flagged
        assert len(results) == 1


# ---------------------------------------------------------------------------
# SPEC-7 / real-world: task_live.js patterns
# ---------------------------------------------------------------------------


class TestTaskLivePatterns:
    """Validate the scanner catches patterns from the actual task_live.js file."""

    def test_add_event_listener_mutation_flagged(self, tmp_path):
        js = (
            "let _needsSessionUpdate = false;\n"
            "\n"
            "es.addEventListener('session_update', () => {\n"
            "  _needsSessionUpdate = true;\n"
            "  scheduleRender();\n"
            "});\n"
        )
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "_needsSessionUpdate"
        assert results[0]["line"] == 4

    def test_set_interval_async_mutation_flagged(self, tmp_path):
        js = (
            "let _pollInFlight = false;\n"
            "\n"
            "S.pollTimer = setInterval(async () => {\n"
            "  if (_pollInFlight) return;\n"
            "  _pollInFlight = true;\n"
            "  try {\n"
            "    await doWork();\n"
            "  } finally {\n"
            "    _pollInFlight = false;\n"
            "  }\n"
            "}, 5000);\n"
        )
        results = _findings_for(tmp_path, js)
        variables = [r["variable"] for r in results]
        assert "_pollInFlight" in variables

    def test_async_function_mutations_flagged(self, tmp_path):
        js = (
            "let _renderTimeout = null;\n"
            "let _pollInFlight = false;\n"
            "\n"
            "async function _flushSSEUpdates() {\n"
            "  _renderTimeout = null;\n"
            "  if (_pollInFlight) return;\n"
            "  _pollInFlight = true;\n"
            "  try {\n"
            "    await doWork();\n"
            "  } finally {\n"
            "    _pollInFlight = false;\n"
            "  }\n"
            "}\n"
        )
        results = _findings_for(tmp_path, js)
        variables = {r["variable"] for r in results}
        assert "_renderTimeout" in variables
        assert "_pollInFlight" in variables

    def test_const_object_property_not_flagged(self, tmp_path):
        # task_api.js pattern: const S = {}; S.view = 'detail' — should NOT flag
        js = "const S = {};\nasync function f() {\n  S.view = 'detail';\n}\n"
        results = _findings_for(tmp_path, js)
        assert results == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_multiple_vars_on_same_let_line(self, tmp_path):
        js = "let a = 1, b = 2;\nasync function f() {\n  a = 10;\n  b = 20;\n}\n"
        results = _findings_for(tmp_path, js)
        variables = {r["variable"] for r in results}
        assert variables == {"a", "b"}

    def test_mutation_of_unrelated_var_not_flagged(self, tmp_path):
        # 'z' is not declared with top-level let
        js = "let x = 0;\nasync function f() {\n  z = 1;\n}\n"
        results = _findings_for(tmp_path, js)
        variables = {r["variable"] for r in results}
        assert "z" not in variables

    def test_document_add_event_listener_is_context(self, tmp_path):
        # document.addEventListener also counts
        js = (
            "let x = 0;\n"
            "document.addEventListener('DOMContentLoaded', async () => {\n"
            "  x = 1;\n"
            "});\n"
        )
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_window_add_event_listener_is_context(self, tmp_path):
        js = (
            "let x = 0;\n"
            "window.addEventListener('hashchange', async () => {\n"
            "  x = 1;\n"
            "});\n"
        )
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_no_false_positive_for_similar_var_name(self, tmp_path):
        # 'xy' should not be flagged when only 'x' is declared
        js = "let x = 0;\nasync function f() {\n  xy = 1;\n}\n"
        results = _findings_for(tmp_path, js)
        variables = {r["variable"] for r in results}
        assert "xy" not in variables

    def test_let_in_string_literal_not_treated_as_declaration(self, tmp_path):
        # 'let' inside a string should not be treated as a declaration
        js = (
            'const msg = "let x = 1";\n'
            "async function f() {\n"
            "  console.log(msg);\n"
            "}\n"
        )
        results = _findings_for(tmp_path, js)
        assert results == []

    @pytest.mark.parametrize(
        "comparison_line",
        [
            "  if (x == 5) return;",
            "  if (x === 5) return;",
        ],
    )
    def test_equality_comparisons_not_flagged_as_mutations(
        self, tmp_path, comparison_line
    ):
        """== and === comparisons inside async contexts must not be false-positive mutations."""
        js = f"let x = 0;\nasync function f() {{\n{comparison_line}\n}}\n"
        results = _findings_for(tmp_path, js, name=f"eq_{hash(comparison_line)}.js")
        assert results == [], (
            f"Expected no finding for comparison {comparison_line!r}, "
            f"got: {results}"
        )


# ---------------------------------------------------------------------------
# Issue 1: OSError handling
# ---------------------------------------------------------------------------


class TestOSErrorHandling:
    def test_unreadable_file_is_skipped_without_crash(self, tmp_path):
        """OSError when reading a file should log a warning and skip the file."""
        bad_path = tmp_path / "bad.js"
        good_path = tmp_path / "good.js"
        bad_path.write_text("placeholder")
        good_path.write_text("let x = 0;\nasync function f() { x = 1; }\n")

        original_read_text = Path.read_text

        def _side_effect(self_path, *args, **kwargs):
            if self_path.name == "bad.js":
                raise OSError("permission denied")
            return original_read_text(self_path, *args, **kwargs)

        with patch.object(Path, "read_text", autospec=True, side_effect=_side_effect):
            results = scan_shared_state_patterns(tmp_path)

        # The bad file is skipped; the good file still produces a finding
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_unreadable_only_file_returns_empty(self, tmp_path):
        """When the only JS file raises OSError, return empty list."""
        (tmp_path / "bad.js").write_text("placeholder")
        with patch.object(
            Path, "read_text", autospec=True, side_effect=OSError("no access")
        ):
            results = scan_shared_state_patterns(tmp_path)
        assert results == []


# ---------------------------------------------------------------------------
# Issue 2: Prefix increment/decrement detection
# ---------------------------------------------------------------------------


class TestPrefixIncrementDecrement:
    @pytest.mark.parametrize(
        "mutation_line",
        [
            "  ++x;",
            "  --x;",
        ],
    )
    def test_prefix_operators_flagged_in_async(self, tmp_path, mutation_line):
        """Prefix ++x and --x inside async contexts must be detected as mutations."""
        js = f"let x = 0;\nasync function f() {{\n{mutation_line}\n}}\n"
        results = _findings_for(tmp_path, js, name=f"prefix_{hash(mutation_line)}.js")
        assert len(results) == 1, f"Expected 1 finding for: {mutation_line!r}"
        assert results[0]["variable"] == "x"

    def test_prefix_increment_on_unrelated_var_not_flagged(self, tmp_path):
        """++y should not be a finding when only x is declared at top level."""
        js = "let x = 0;\nasync function f() {\n  ++y;\n}\n"
        results = _findings_for(tmp_path, js)
        variables = {r["variable"] for r in results}
        assert "x" not in variables


# ---------------------------------------------------------------------------
# Issue 3: Brace counting ignores strings/comments
# ---------------------------------------------------------------------------


class TestStripStringsAndComments:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Single-line comment removed
            ("x = 1; // { not a brace", "x = 1; "),
            # Double-quoted string braces stripped
            ('let msg = "{ hello }";', 'let msg = "";'),
            # Single-quoted string braces stripped
            ("let msg = '{ hello }';", "let msg = '';"),
            # Template literal braces stripped
            ("let msg = `{ hello }`;", "let msg = ``;"),
            # Mixed: string then comment
            ('let s = "{"; // {', 'let s = ""; '),
            # No strings or comments — unchanged
            ("let x = 1;", "let x = 1;"),
            # Empty string
            ("", ""),
            # Escaped quote inside string
            (r'let s = "he said \"hi\" {";', 'let s = "";'),
        ],
    )
    def test_strip_strings_and_comments(self, raw, expected):
        assert _strip_strings_and_comments(raw) == expected


class TestBraceCountingWithStrings:
    def test_brace_in_string_does_not_corrupt_depth(self, tmp_path):
        """A brace inside a string literal must not shift depth tracking."""
        js = (
            'let msg = "{";\n'  # top-level let; brace in string should not increase depth
            "async function f() {\n"
            "  msg = 'new';\n"
            "}\n"
        )
        results = _findings_for(tmp_path, js)
        # msg is a top-level let; without fix, the '{' in string would corrupt depth
        # and msg would not be detected as top-level
        assert len(results) == 1
        assert results[0]["variable"] == "msg"

    def test_brace_in_comment_does_not_corrupt_depth(self, tmp_path):
        """A brace inside a // comment must not shift depth tracking."""
        js = (
            "let x = 0; // initialise { counter\n"
            "async function f() {\n"
            "  x = 1;\n"
            "}\n"
        )
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_async_range_brace_in_string_not_misidentified(self, tmp_path):
        """Braces in strings inside async functions must not confuse range tracking."""
        js = (
            "let x = 0;\n"
            "async function f() {\n"
            '  const s = "{";\n'  # brace in string should not extend async range
            "  x = 1;\n"
            "}\n"
            "function sync() {\n"
            "  x = 2;\n"  # outside async — should NOT be flagged
            "}\n"
        )
        results = _findings_for(tmp_path, js)
        # Only line 4 (x = 1 inside async) should be flagged
        assert len(results) == 1
        assert results[0]["line"] == 4


# ---------------------------------------------------------------------------
# Malformed JS / file fragments — negative depth guards and unclosed blocks
# ---------------------------------------------------------------------------


class TestMalformedJSEdgeCases:
    def test_extra_closing_braces_let_still_detected(self, tmp_path):
        """Extra closing braces (more } than {) trigger depth < 0 in both
        _find_top_level_lets (line 174) and _find_async_ranges (line 206).
        After depth resets to 0, the following let declaration must still be
        recognised as top-level and its mutation flagged."""
        # The leading }} put depth at -2, which triggers depth = 0 in both helpers.
        # 'let x' then appears at depth 0 and should be treated as top-level.
        js = "}\n}\nlet x = 0;\nasync function f() { x = 1; }\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"

    def test_unclosed_async_block_extends_to_end_of_file(self, tmp_path):
        """An async function whose closing brace is missing triggers the
        end-of-file fallback in _find_async_ranges (lines 221-222).  The
        mutation inside the unclosed block must still be flagged."""
        js = "let x = 0;\nasync function f() {\n  x = 1;\n"
        results = _findings_for(tmp_path, js)
        assert len(results) == 1
        assert results[0]["variable"] == "x"
        assert results[0]["line"] == 3
