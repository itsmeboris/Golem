"""Tests for golem.startup — dependency validation."""

import logging
from unittest.mock import patch

import pytest

from golem.startup import validate_dependencies


class TestValidateDependencies:
    def test_all_tools_present_returns_empty_warnings(self):
        with patch("golem.startup.shutil.which", return_value="/usr/bin/tool"):
            result = validate_dependencies()
        assert result == []

    def test_missing_required_tool_raises_runtime_error(self):
        def which_side_effect(tool):
            return None if tool == "git" else "/usr/bin/tool"

        with patch("golem.startup.shutil.which", side_effect=which_side_effect):
            with pytest.raises(RuntimeError, match="git not found in PATH"):
                validate_dependencies()

    def test_missing_optional_tool_returns_warning(self):
        def which_side_effect(tool):
            return None if tool == "claude" else "/usr/bin/tool"

        with patch("golem.startup.shutil.which", side_effect=which_side_effect):
            warnings = validate_dependencies()

        assert len(warnings) == 1
        assert "claude" in warnings[0]

    def test_missing_optional_tool_logs_warning(self, caplog):
        def which_side_effect(tool):
            return None if tool == "claude" else "/usr/bin/tool"

        with (
            patch("golem.startup.shutil.which", side_effect=which_side_effect),
            caplog.at_level(logging.WARNING, logger="golem.startup"),
        ):
            validate_dependencies()

        assert "claude" in caplog.text

    def test_missing_required_tool_does_not_check_optional(self):
        """RuntimeError is raised before optional tools are checked."""
        checked = []

        def which_side_effect(tool):
            checked.append(tool)
            return None

        with patch("golem.startup.shutil.which", side_effect=which_side_effect):
            with pytest.raises(RuntimeError):
                validate_dependencies()

        assert "git" in checked
        assert "claude" not in checked
