"""Tests for AST-based schema constant sync lint."""

import pytest

from golem.lint.schema_sync import check_schema_constant_sync


class TestCheckSchemaConstantSyncEmptyAndNoOp:
    def test_empty_directory_returns_no_violations(self, tmp_path):
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_directory_with_only_non_python_files_returns_no_violations(self, tmp_path):
        (tmp_path / "readme.txt").write_text("just text\n")
        (tmp_path / "data.json").write_text('{"key": "value"}\n')
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_python_file_with_no_schema_dicts_returns_no_violations(self, tmp_path):
        (tmp_path / "mod.py").write_text("x = 1\ny = 'hello'\n")
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_python_file_with_frozenset_no_schema_returns_no_violations(self, tmp_path):
        (tmp_path / "mod.py").write_text(
            "VALID = frozenset({'a', 'b', 'c'})\n" "DATA = {'items': [1, 2, 3]}\n"
        )
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_python_file_with_re_compile_no_schema_returns_no_violations(
        self, tmp_path
    ):
        (tmp_path / "mod.py").write_text(
            "import re\n"
            "_PAT = re.compile(r'^[a-z]+$')\n"
            "DATA = {'type': 'string'}\n"
        )
        result = check_schema_constant_sync(tmp_path)
        assert result == []


class TestEnumViolation:
    def test_frozenset_matching_schema_enum_reports_violation(self, tmp_path):
        code = (
            'SCHEMA = {"properties": {"access": {"enum": ["read", "write", "execute"]}}}\n'
            "_VALID_ACCESS = frozenset({'read', 'write', 'execute'})\n"
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1
        v = result[0]
        assert v["file"] == "mod.py"
        assert v["constant"] == "_VALID_ACCESS"
        assert "enum" in v["message"].lower() or "duplicate" in v["message"].lower()
        assert isinstance(v["line"], int)

    def test_set_matching_schema_enum_reports_violation(self, tmp_path):
        code = (
            'SCHEMA = {"properties": {"kind": {"enum": ["alpha", "beta"]}}}\n'
            "KINDS = set({'alpha', 'beta'})\n"
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1
        assert result[0]["constant"] == "KINDS"

    def test_frozenset_not_matching_enum_returns_no_violations(self, tmp_path):
        code = (
            'SCHEMA = {"properties": {"access": {"enum": ["read", "write", "execute"]}}}\n'
            "_VALID = frozenset({'alpha', 'beta'})\n"
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_partial_enum_overlap_returns_no_violations(self, tmp_path):
        # Only exact match should trigger — partial overlap is not a violation
        code = (
            'SCHEMA = {"properties": {"access": {"enum": ["read", "write", "execute"]}}}\n'
            "_VALID = frozenset({'read', 'write'})\n"
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_enum_order_independent_match_reports_violation(self, tmp_path):
        # Order in frozenset vs enum list should not matter
        code = (
            'SCHEMA = {"enum": ["c", "b", "a"]}\n' "VALS = frozenset({'a', 'b', 'c'})\n"
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1


class TestRegexViolation:
    def test_re_compile_matching_schema_pattern_reports_violation(self, tmp_path):
        code = (
            "import re\n"
            'MCP_SCHEMA = {"properties": {"name": {"pattern": "^[a-zA-Z][a-zA-Z0-9_]{0,63}$"}}}\n'
            '_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")\n'
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1
        v = result[0]
        assert v["constant"] == "_NAME_PATTERN"
        assert "pattern" in v["message"].lower() or "duplicate" in v["message"].lower()

    def test_re_compile_not_matching_pattern_returns_no_violations(self, tmp_path):
        code = (
            "import re\n"
            'SCHEMA = {"properties": {"name": {"pattern": "^[a-z]+$"}}}\n'
            '_PAT = re.compile(r"^[A-Z]+$")\n'
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_re_compile_with_flags_matching_pattern_reports_violation(self, tmp_path):
        # re.compile with extra flags argument — pattern string should still match
        code = (
            "import re\n"
            'SCHEMA = {"pattern": "^[a-z]+$"}\n'
            '_PAT = re.compile(r"^[a-z]+$", re.IGNORECASE)\n'
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1


class TestNestedSchemas:
    def test_enum_nested_several_levels_deep_reports_violation(self, tmp_path):
        code = (
            "SCHEMA = {\n"
            '    "properties": {\n'
            '        "permissions": {\n'
            '            "items": {\n'
            '                "properties": {\n'
            '                    "resource": {\n'
            '                        "enum": ["filesystem", "network", "ui", "process"]\n'
            "                    }\n"
            "                }\n"
            "            }\n"
            "        }\n"
            "    }\n"
            "}\n"
            "_VALID_RESOURCES = frozenset({'filesystem', 'network', 'ui', 'process'})\n"
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1
        assert result[0]["constant"] == "_VALID_RESOURCES"

    def test_pattern_nested_several_levels_deep_reports_violation(self, tmp_path):
        code = (
            "import re\n"
            "SCHEMA = {\n"
            '    "properties": {\n'
            '        "name": {\n'
            '            "type": "string",\n'
            '            "pattern": "^[a-z]{1,10}$"\n'
            "        }\n"
            "    }\n"
            "}\n"
            '_PAT = re.compile(r"^[a-z]{1,10}$")\n'
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1


class TestMultipleViolations:
    def test_two_enum_violations_in_one_file_both_reported(self, tmp_path):
        code = (
            'SCHEMA1 = {"enum": ["x", "y"]}\n'
            'SCHEMA2 = {"enum": ["a", "b", "c"]}\n'
            "SET1 = frozenset({'x', 'y'})\n"
            "SET2 = frozenset({'a', 'b', 'c'})\n"
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 2
        constants = {v["constant"] for v in result}
        assert constants == {"SET1", "SET2"}

    def test_enum_and_regex_violations_both_reported(self, tmp_path):
        code = (
            "import re\n"
            "SCHEMA = {\n"
            '    "properties": {\n'
            '        "kind": {"enum": ["foo", "bar"]},\n'
            '        "name": {"pattern": "^[a-z]+$"}\n'
            "    }\n"
            "}\n"
            "KINDS = frozenset({'foo', 'bar'})\n"
            '_PAT = re.compile(r"^[a-z]+$")\n'
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 2
        constants = {v["constant"] for v in result}
        assert "KINDS" in constants
        assert "_PAT" in constants


class TestFileSkipping:
    def test_files_in_tests_directory_are_skipped(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        code = 'SCHEMA = {"enum": ["x", "y"]}\n' "VALS = frozenset({'x', 'y'})\n"
        (tests_dir / "test_something.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_non_python_files_are_skipped(self, tmp_path):
        # A .txt file with Python-like content should not be parsed
        (tmp_path / "schema.txt").write_text(
            'SCHEMA = {"enum": ["x", "y"]}\nVALS = frozenset({"x", "y"})\n'
        )
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_python_files_outside_tests_directory_are_checked(self, tmp_path):
        code = 'SCHEMA = {"enum": ["x", "y"]}\n' "VALS = frozenset({'x', 'y'})\n"
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1


class TestViolationDict:
    def test_violation_has_required_keys(self, tmp_path):
        code = 'SCHEMA = {"enum": ["a", "b"]}\n' "VALS = frozenset({'a', 'b'})\n"
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1
        v = result[0]
        assert set(v.keys()) >= {"file", "line", "constant", "message"}

    def test_violation_file_is_relative_path(self, tmp_path):
        subdir = tmp_path / "pkg"
        subdir.mkdir()
        code = 'SCHEMA = {"enum": ["a", "b"]}\n' "VALS = frozenset({'a', 'b'})\n"
        (subdir / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1
        # Should be a relative path, not absolute
        assert not result[0]["file"].startswith("/")
        assert "pkg" in result[0]["file"]

    def test_violation_line_points_to_constant_assignment(self, tmp_path):
        code = (
            "# line 1 - comment\n"
            'SCHEMA = {"enum": ["x", "y"]}\n'  # line 2
            "# line 3 - comment\n"
            "VALS = frozenset({'x', 'y'})\n"  # line 4
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1
        assert result[0]["line"] == 4


class TestConstantDerivesFromSchema:
    def test_constant_derived_from_schema_via_variable_no_violation(self, tmp_path):
        # This is the GOOD pattern: constant derives from schema, not independent
        # The lint only detects static literals — subscription-based derivation is fine
        code = (
            'SCHEMA = {"properties": {"access": {"enum": ["read", "write", "execute"]}}}\n'
            "_VALID = frozenset(SCHEMA['properties']['access']['enum'])\n"
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []


class TestSyntaxError:
    def test_file_with_syntax_error_is_skipped_gracefully(self, tmp_path):
        (tmp_path / "bad.py").write_text("def broken(\n    ??? invalid\n")
        result = check_schema_constant_sync(tmp_path)
        assert result == []


class TestEdgeCasesForCoverage:
    def test_re_compile_with_no_args_returns_no_violations(self, tmp_path):
        # re.compile() with no arguments — not a violation (line 93 branch)
        code = (
            "import re\n" 'SCHEMA = {"pattern": "^[a-z]+$"}\n' "_PAT = re.compile()\n"
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_frozenset_with_no_args_returns_no_violations(self, tmp_path):
        # frozenset() with no arguments — not a violation (line 118 branch)
        code = 'SCHEMA = {"enum": ["a", "b"]}\n' "VALS = frozenset()\n"
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_frozenset_with_non_string_elements_returns_no_violations(self, tmp_path):
        # frozenset({1, 2}) — integer elements, not strings (line 125 branch)
        code = 'SCHEMA = {"enum": ["a", "b"]}\n' "VALS = frozenset({1, 2})\n"
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_augmented_assignment_is_not_detected_as_violation(self, tmp_path):
        # Multiple targets: a = b = frozenset({...}) — skipped (line 167 branch)
        code = 'SCHEMA = {"enum": ["x", "y"]}\n' "A = B = frozenset({'x', 'y'})\n"
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_bare_compile_call_matching_pattern_reports_violation(self, tmp_path):
        # compile("pat") via Name node (not attribute) — lines 101-102 branch
        code = 'SCHEMA = {"pattern": "^[a-z]+$"}\n' '_PAT = compile(r"^[a-z]+$")\n'
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert len(result) == 1
        assert result[0]["constant"] == "_PAT"

    def test_compile_call_non_matching_pattern_returns_no_violations(self, tmp_path):
        # compile("different") via Name node — non-matching pattern, no violation
        code = 'SCHEMA = {"pattern": "^[a-z]+$"}\n' '_PAT = compile("^[A-Z]+$")\n'
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []

    def test_non_compile_attribute_call_with_string_arg_no_violation(self, tmp_path):
        # re.search("pat") — attribute call but not .compile — hits line 103 return None
        code = (
            "import re\n"
            'SCHEMA = {"pattern": "^[a-z]+$"}\n'
            '_MATCH = re.search(r"^[a-z]+$", "test")\n'
        )
        (tmp_path / "mod.py").write_text(code)
        result = check_schema_constant_sync(tmp_path)
        assert result == []


@pytest.mark.parametrize(
    "enum_values, frozenset_values, should_match",
    [
        (["a", "b", "c"], {"a", "b", "c"}, True),
        (["x"], {"x"}, True),
        (["a", "b"], {"a", "b", "c"}, False),
        (["a", "b", "c"], {"a", "b"}, False),
        (["a", "b"], {"c", "d"}, False),
    ],
    ids=[
        "exact_match_3_elements",
        "exact_match_1_element",
        "subset_no_match",
        "superset_no_match",
        "disjoint_no_match",
    ],
)
def test_enum_matching_parametrized(
    tmp_path, enum_values, frozenset_values, should_match
):
    enum_str = str(enum_values).replace("'", '"')
    frozenset_str = repr(frozenset_values)
    code = f'SCHEMA = {{"enum": {enum_str}}}\nVALS = frozenset({frozenset_str})\n'
    (tmp_path / "mod.py").write_text(code)
    result = check_schema_constant_sync(tmp_path)
    if should_match:
        assert (
            len(result) == 1
        ), f"Expected violation for {enum_values} vs {frozenset_values}"
    else:
        assert (
            result == []
        ), f"Expected no violation for {enum_values} vs {frozenset_values}"


@pytest.mark.parametrize(
    "schema_pattern, compile_pattern, should_match",
    [
        (r"^[a-z]+$", r"^[a-z]+$", True),
        (r"^[a-z]+$", r"^[A-Z]+$", False),
        (r"^\d{4}$", r"^\d{4}$", True),
        (r"^abc$", r"^abcd$", False),
    ],
    ids=[
        "identical_lowercase_pattern",
        "different_case_pattern",
        "identical_digit_pattern",
        "prefix_substring_no_match",
    ],
)
def test_regex_matching_parametrized(
    tmp_path, schema_pattern, compile_pattern, should_match
):
    code = (
        f"import re\n"
        f'SCHEMA = {{"pattern": "{schema_pattern}"}}\n'
        f'_PAT = re.compile(r"{compile_pattern}")\n'
    )
    (tmp_path / "mod.py").write_text(code)
    result = check_schema_constant_sync(tmp_path)
    if should_match:
        assert len(result) == 1, f"Expected violation for pattern {schema_pattern!r}"
    else:
        assert result == [], f"Expected no violation for pattern mismatch"
