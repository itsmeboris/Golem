"""Tests for the JS innerHTML pattern scanner."""

import pytest

from golem.lint.js_innerhtml import scan_innerhtml_patterns


class TestScanInnerhtmlPatterns:
    def test_empty_directory_returns_empty_list(self, tmp_path):
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_directory_with_no_js_files_returns_empty_list(self, tmp_path):
        (tmp_path / "readme.txt").write_text("nothing here")
        (tmp_path / "styles.css").write_text("body { color: red; }")
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_empty_js_file_returns_empty_list(self, tmp_path):
        (tmp_path / "empty.js").write_text("")
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_js_file_with_no_innerhtml_returns_empty_list(self, tmp_path):
        (tmp_path / "app.js").write_text(
            "function greet(name) {\n  return 'Hello ' + name;\n}\n"
        )
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_innerhtml_assignment_detected(self, tmp_path):
        (tmp_path / "app.js").write_text(
            "function render(el, html) {\n  el.innerHTML = html;\n}\n"
        )
        result = scan_innerhtml_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["file"] == "app.js"
        assert result[0]["line"] == 2
        assert result[0]["pattern"] == ".innerHTML ="
        assert (
            result[0]["message"]
            == "innerHTML assignment without state preservation in preceding 5 lines"
        )

    def test_innerhtml_plus_equals_detected(self, tmp_path):
        (tmp_path / "app.js").write_text(
            "function append(el, html) {\n  el.innerHTML += html;\n}\n"
        )
        result = scan_innerhtml_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["file"] == "app.js"
        assert result[0]["line"] == 2
        assert result[0]["pattern"] == ".innerHTML +="
        assert (
            result[0]["message"]
            == "innerHTML assignment without state preservation in preceding 5 lines"
        )

    @pytest.mark.parametrize("op", ["==", "==="])
    def test_innerhtml_equality_check_not_detected(self, tmp_path, op):
        (tmp_path / "app.js").write_text(f"if (el.innerHTML {op} other) {{}}\n")
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_innerhtml_preceded_by_scrolltop_not_detected(self, tmp_path):
        content = (
            "function update(el, html) {\n"
            "  var top = el.scrollTop;\n"
            "  el.innerHTML = html;\n"
            "}\n"
        )
        (tmp_path / "widget.js").write_text(content)
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_innerhtml_preceded_by_activeelement_not_detected(self, tmp_path):
        content = (
            "function update(el, html) {\n"
            "  var focused = document.activeElement;\n"
            "  el.innerHTML = html;\n"
            "}\n"
        )
        (tmp_path / "widget.js").write_text(content)
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_multiple_innerhtml_in_one_file_both_detected(self, tmp_path):
        content = (
            "function renderA(el) {\n"
            "  el.innerHTML = '<p>A</p>';\n"
            "}\n"
            "function renderB(el) {\n"
            "  el.innerHTML = '<p>B</p>';\n"
            "}\n"
        )
        (tmp_path / "multi.js").write_text(content)
        result = scan_innerhtml_patterns(tmp_path)
        assert len(result) == 2
        assert result[0]["line"] == 2
        assert result[1]["line"] == 5

    def test_innerhtml_at_line_1_detected(self, tmp_path):
        (tmp_path / "first.js").write_text("el.innerHTML = html;\n")
        result = scan_innerhtml_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["line"] == 1

    def test_state_save_exactly_5_lines_before_not_detected(self, tmp_path):
        content = (
            "function update(el, html) {\n"
            "  var top = el.scrollTop;\n"  # line 2 — 5 lines before line 7
            "  doSomething();\n"
            "  doMore();\n"
            "  doEvenMore();\n"
            "  doLast();\n"
            "  el.innerHTML = html;\n"  # line 7 — state save is at line 2, diff=5
            "}\n"
        )
        (tmp_path / "widget.js").write_text(content)
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_state_save_6_lines_before_detected(self, tmp_path):
        content = (
            "function update(el, html) {\n"
            "  var top = el.scrollTop;\n"  # line 2 — 6 lines before line 8
            "  doSomething();\n"
            "  doMore();\n"
            "  doEvenMore();\n"
            "  doLast();\n"
            "  doFinal();\n"
            "  el.innerHTML = html;\n"  # line 8 — state save is at line 2, diff=6
            "}\n"
        )
        (tmp_path / "widget.js").write_text(content)
        result = scan_innerhtml_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["line"] == 8

    @pytest.mark.parametrize(
        "keyword",
        [
            "scrollTop",
            "scrollLeft",
            "value",
            "activeElement",
            "checked",
            "selectionStart",
            "selectionEnd",
            "focus",
            "wasOpen",
            "offsetTop",
            "selectedIndex",
        ],
    )
    def test_each_state_preservation_keyword_suppresses_detection(
        self, tmp_path, keyword
    ):
        content = (
            "function update(el) {\n"
            f"  var saved = el.{keyword};\n"
            "  el.innerHTML = html;\n"
            "}\n"
        )
        (tmp_path / "widget.js").write_text(content)
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_result_dict_has_required_keys(self, tmp_path):
        (tmp_path / "app.js").write_text("el.innerHTML = html;\n")
        result = scan_innerhtml_patterns(tmp_path)
        assert len(result) == 1
        assert set(result[0].keys()) == {"file", "line", "pattern", "message"}

    def test_file_path_is_relative_to_root(self, tmp_path):
        subdir = tmp_path / "src" / "components"
        subdir.mkdir(parents=True)
        (subdir / "button.js").write_text("el.innerHTML = html;\n")
        result = scan_innerhtml_patterns(tmp_path)
        assert len(result) == 1
        assert result[0]["file"] == "src/components/button.js"

    def test_results_sorted_by_file_then_line(self, tmp_path):
        (tmp_path / "b.js").write_text("el.innerHTML = b1;\nel.innerHTML = b2;\n")
        (tmp_path / "a.js").write_text("el.innerHTML = a1;\n")
        result = scan_innerhtml_patterns(tmp_path)
        assert len(result) == 3
        assert result[0]["file"] == "a.js"
        assert result[0]["line"] == 1
        assert result[1]["file"] == "b.js"
        assert result[1]["line"] == 1
        assert result[2]["file"] == "b.js"
        assert result[2]["line"] == 2

    def test_non_js_file_ignored_even_with_innerhtml(self, tmp_path):
        (tmp_path / "page.html").write_text("<script>el.innerHTML = html;</script>\n")
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_innerhtml_in_string_literal_detected(self, tmp_path):
        # Scanner is regex-based; it flags even string literals containing .innerHTML =
        # This documents current behavior: no JS AST parsing
        (tmp_path / "app.js").write_text("var msg = 'el.innerHTML = x';\n")
        result = scan_innerhtml_patterns(tmp_path)
        # Pattern detection is line-based; string literals match the same regex
        assert len(result) == 1

    def test_innerhtml_in_comment_detected(self, tmp_path):
        # Same as above: regex-based scanner does not skip comments
        (tmp_path / "app.js").write_text("// el.innerHTML = x; example\n")
        result = scan_innerhtml_patterns(tmp_path)
        assert len(result) == 1

    def test_non_utf8_file_is_skipped(self, tmp_path):
        latin1_bytes = "el.innerHTML = html; // caf\xe9\n".encode("latin-1")
        (tmp_path / "latin1.js").write_bytes(latin1_bytes)
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []

    def test_wasopen_state_save_suppresses_detection(self, tmp_path):
        """Mirrors the real heartbeat_widget.js pattern described in the spec."""
        content = (
            "function updateWidget(data) {\n"
            "  var wasOpen = _hbPopoverOpen;\n"
            "  container.innerHTML = _hbChipHTML(data);\n"
            "}\n"
        )
        (tmp_path / "heartbeat_widget.js").write_text(content)
        result = scan_innerhtml_patterns(tmp_path)
        assert result == []
