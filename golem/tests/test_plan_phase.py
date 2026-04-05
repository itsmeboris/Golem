"""Tests for the orchestrate_planner_template.txt prompt file."""

import pathlib

import pytest

TEMPLATE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "prompts"
    / "orchestrate_planner_template.txt"
)


class TestPlannerTemplate:
    """Verify that the planner prompt template exists and contains required sections."""

    def test_template_file_exists(self):
        assert TEMPLATE_PATH.exists(), f"Template file not found: {TEMPLATE_PATH}"

    def test_template_has_file_map_section(self):
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert "File Map" in content

    @pytest.mark.parametrize(
        "keyword",
        [
            "placeholder",
            "No Placeholder",
        ],
        ids=["lowercase_placeholder", "titled_no_placeholder"],
    )
    def test_template_has_placeholder_rule(self, keyword):
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert keyword.lower() in content.lower()

    def test_template_has_test_strategy_section(self):
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert "Test Strategy" in content

    def test_template_has_plan_reviewer_section(self):
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert "Plan Reviewer" in content

    def test_template_has_read_only_scope(self):
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert "Do NOT modify" in content

    def test_template_has_checkbox_syntax(self):
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
        assert "- [ ]" in content
