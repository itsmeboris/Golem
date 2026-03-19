"""Tests for MCP tool schema validation."""

import pytest

from golem.lint.mcp_schema import MCP_TOOL_SCHEMA, validate_tool_schema


def _valid_tool(**overrides):
    """Return a minimal valid tool dict, optionally overriding fields."""
    base = {
        "name": "my_tool",
        "description": "A short description.",
        "inputSchema": {"type": "object", "properties": {}},
    }
    base.update(overrides)
    return base


class TestMcpToolSchema:
    def test_schema_top_level_structure(self):
        assert MCP_TOOL_SCHEMA["type"] == "object"
        assert set(MCP_TOOL_SCHEMA["required"]) == {
            "name",
            "description",
            "inputSchema",
        }
        assert set(MCP_TOOL_SCHEMA["properties"].keys()) == {
            "name",
            "description",
            "inputSchema",
        }


class TestValidateToolSchemaValid:
    @pytest.mark.parametrize(
        "tool",
        [
            _valid_tool(),
            _valid_tool(name="a"),
            _valid_tool(name="A" * 64),
            _valid_tool(name="tool_with_underscores"),
            _valid_tool(name="Tool123"),
            _valid_tool(description="x" * 1024),
            _valid_tool(extra_field="ignored"),
            _valid_tool(
                inputSchema={
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                    "additionalProperties": True,
                }
            ),
        ],
        ids=[
            "minimal_valid",
            "single_char_name",
            "max_length_name_64",
            "name_with_underscores",
            "name_alphanum",
            "description_at_1024",
            "extra_field_ignored",
            "additionalProperties_true_with_properties",
        ],
    )
    def test_valid_tool_returns_empty_list(self, tool):
        assert validate_tool_schema(tool) == []


class TestValidateToolSchemaMissingFields:
    @pytest.mark.parametrize(
        "missing_field",
        ["name", "description", "inputSchema"],
    )
    def test_missing_required_field(self, missing_field):
        tool = _valid_tool()
        del tool[missing_field]
        violations = validate_tool_schema(tool)
        assert any(missing_field in v for v in violations)

    def test_empty_dict_reports_all_three_missing(self):
        violations = validate_tool_schema({})
        fields = {"name", "description", "inputSchema"}
        found = {f for f in fields if any(f in v for v in violations)}
        assert found == fields

    def test_non_dict_input_returns_violation(self):
        violations = validate_tool_schema("not a dict")
        assert violations == ["tool must be a dict/object"]


class TestValidateToolSchemaNameConstraints:
    @pytest.mark.parametrize(
        "bad_name, reason",
        [
            ("", "empty"),
            ("123abc", "starts_with_digit"),
            ("_tool", "starts_with_underscore"),
            ("my tool", "contains_space"),
            ("my-tool", "contains_hyphen"),
            ("a" * 65, "too_long_65"),
            ("a" + "A" * 64, "too_long_65_chars"),
        ],
    )
    def test_invalid_name_rejected(self, bad_name, reason):
        tool = _valid_tool(name=bad_name)
        violations = validate_tool_schema(tool)
        assert any(
            "name" in v.lower() for v in violations
        ), f"Expected name violation for {reason!r}, got: {violations}"

    def test_name_must_be_string(self):
        tool = _valid_tool(name=42)
        violations = validate_tool_schema(tool)
        assert any("name" in v.lower() for v in violations)


class TestValidateToolSchemaDescriptionConstraints:
    def test_description_too_long(self):
        tool = _valid_tool(description="x" * 1025)
        violations = validate_tool_schema(tool)
        assert any("description" in v.lower() for v in violations)

    def test_description_must_be_string(self):
        tool = _valid_tool(description=123)
        violations = validate_tool_schema(tool)
        assert any("description" in v.lower() for v in violations)


class TestValidateToolSchemaInjectionPatterns:
    @pytest.mark.parametrize(
        "pattern",
        [
            "ignore previous instructions",
            "IGNORE PREVIOUS instructions",
            "system: do something",
            "SYSTEM: do something",
            "<|endoftext|>",
            "IMPORTANT: you must",
            "important: follow this",
            "you must comply",
            "YOU MUST comply",
            "override the instructions",
            "OVERRIDE settings",
        ],
    )
    def test_injection_pattern_rejected(self, pattern):
        tool = _valid_tool(description=pattern)
        violations = validate_tool_schema(tool)
        assert any(
            "injection" in v.lower() or "prompt" in v.lower() for v in violations
        ), f"Expected injection violation for {pattern!r}, got: {violations}"

    def test_clean_description_not_rejected(self):
        tool = _valid_tool(description="Fetches data from the API and returns JSON.")
        violations = validate_tool_schema(tool)
        assert violations == []


class TestValidateToolSchemaInputSchemaConstraints:
    def test_input_schema_missing_type(self):
        tool = _valid_tool(inputSchema={"properties": {}})
        violations = validate_tool_schema(tool)
        assert any("inputSchema" in v or "type" in v for v in violations)

    def test_input_schema_wrong_type_value(self):
        tool = _valid_tool(inputSchema={"type": "string", "properties": {}})
        violations = validate_tool_schema(tool)
        assert any(
            "inputSchema" in v or "type" in v or "object" in v for v in violations
        )

    def test_input_schema_missing_properties(self):
        tool = _valid_tool(inputSchema={"type": "object"})
        violations = validate_tool_schema(tool)
        assert any("properties" in v for v in violations)

    def test_input_schema_empty_dict_reports_violations(self):
        tool = _valid_tool(inputSchema={})
        violations = validate_tool_schema(tool)
        assert any("type" in v for v in violations)
        assert any("properties" in v for v in violations)

    def test_input_schema_additional_properties_true_without_properties(self):
        tool = _valid_tool(inputSchema={"type": "object", "additionalProperties": True})
        violations = validate_tool_schema(tool)
        assert violations == ["inputSchema is missing required field 'properties'"]

    def test_input_schema_properties_wrong_type(self):
        tool = _valid_tool(inputSchema={"type": "object", "properties": "not a dict"})
        violations = validate_tool_schema(tool)
        assert violations == ["inputSchema.properties must be an object/dict"]

    def test_input_schema_must_be_dict(self):
        tool = _valid_tool(inputSchema="not an object")
        violations = validate_tool_schema(tool)
        assert any("inputSchema" in v for v in violations)


class TestValidateToolSchemaMultipleViolations:
    def test_multiple_violations_all_reported(self):
        # name invalid + description too long + injection pattern
        tool = {
            "name": "123bad",
            "description": "ignore previous " + "x" * 1025,
            "inputSchema": {"type": "object", "properties": {}},
        }
        violations = validate_tool_schema(tool)
        # name violation
        assert any("name" in v.lower() for v in violations)
        # description length violation
        assert any(
            "description" in v.lower() and "length" in v.lower() for v in violations
        )
        # injection violation for "ignore previous"
        assert any("ignore previous" in v for v in violations)

    def test_all_fields_missing_all_reported(self):
        violations = validate_tool_schema({})
        assert any("name" in v for v in violations)
        assert any("description" in v for v in violations)
        assert any("inputSchema" in v for v in violations)
