"""Tests for golem.config_tui — interactive config TUI."""

from unittest.mock import MagicMock, patch

import pytest

from golem.config_tui import (
    CATEGORY_ORDER,
    ConfigTUIState,
    _cycle_choice,
    _render_field_display,
)
from golem.config_editor import FieldInfo, FieldMeta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fi(field_type, value, sensitive=False, choices=None):
    meta = FieldMeta(
        category="test",
        field_type=field_type,
        description="Test field",
        sensitive=sensitive,
        choices=choices,
    )
    return FieldInfo("test.field", value, meta)


# ---------------------------------------------------------------------------
# TestCycleChoice
# ---------------------------------------------------------------------------


class TestCycleChoice:
    @pytest.mark.parametrize(
        "current,direction,expected",
        [
            ("opus", 1, "sonnet"),
            ("sonnet", 1, "haiku"),
            ("haiku", 1, "opus"),  # wraps around
            ("opus", -1, "haiku"),  # wraps backward
            ("sonnet", -1, "opus"),
            ("haiku", -1, "sonnet"),
        ],
    )
    def test_cycle(self, current, direction, expected):
        choices = ["opus", "sonnet", "haiku"]
        assert _cycle_choice(current, choices, direction) == expected

    def test_value_not_in_choices_falls_back_to_index_zero(self):
        # ValueError path: current is not in choices -> idx defaults to 0
        choices = ["a", "b", "c"]
        result = _cycle_choice("unknown", choices, 1)
        assert result == "b"  # (0 + 1) % 3 = 1

    def test_value_not_in_choices_backward(self):
        choices = ["a", "b", "c"]
        result = _cycle_choice("unknown", choices, -1)
        assert result == "c"  # (0 - 1) % 3 = 2


# ---------------------------------------------------------------------------
# TestRenderFieldDisplay
# ---------------------------------------------------------------------------


class TestRenderFieldDisplay:
    def test_sensitive_redacted(self):
        fi = _make_fi("str", "secret123", sensitive=True)
        assert _render_field_display(fi) == "***"

    def test_choice_field_shows_brackets(self):
        fi = _make_fi("choice", "opus", choices=["opus", "sonnet", "haiku"])
        display = _render_field_display(fi)
        assert display == "[◀ opus ▶]"

    def test_bool_true_shows_on(self):
        fi = _make_fi("bool", True)
        assert _render_field_display(fi) == "[on]"

    def test_bool_false_shows_off(self):
        fi = _make_fi("bool", False)
        assert _render_field_display(fi) == "[off]"

    def test_list_joined_with_comma(self):
        fi = _make_fi("list", ["alpha", "beta", "gamma"])
        assert _render_field_display(fi) == "alpha, beta, gamma"

    def test_list_empty(self):
        fi = _make_fi("list", [])
        assert _render_field_display(fi) == ""

    def test_str_value(self):
        fi = _make_fi("str", "hello")
        assert _render_field_display(fi) == "hello"

    def test_none_value_returns_empty_string(self):
        fi = _make_fi("str", None)
        assert _render_field_display(fi) == ""

    def test_int_value(self):
        fi = _make_fi("int", 42)
        assert _render_field_display(fi) == "42"

    def test_choice_no_choices_attr_falls_through_to_str(self):
        # choice type but choices=None — falls through to str(value)
        fi = _make_fi("choice", "opus", choices=None)
        assert _render_field_display(fi) == "opus"


# ---------------------------------------------------------------------------
# TestCategoryOrder
# ---------------------------------------------------------------------------


class TestCategoryOrder:
    def test_has_eleven_entries(self):
        assert len(CATEGORY_ORDER) == 11

    def test_profile_is_first(self):
        assert CATEGORY_ORDER[0] == "profile"

    def test_expected_categories_present(self):
        expected = {
            "profile",
            "budget",
            "models",
            "heartbeat",
            "self_update",
            "integrations",
            "dashboard",
            "daemon",
            "logging",
            "health",
            "polling",
        }
        assert set(CATEGORY_ORDER) == expected


# ---------------------------------------------------------------------------
# TestConfigTUIState
# ---------------------------------------------------------------------------


@pytest.fixture()
def config_path(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "flows:\n  golem:\n    enabled: true\n    profile: github\n    projects:\n      - my/repo\n    task_model: sonnet\ndashboard:\n  port: 5000\nslack:\n  enabled: false\nteams:\n  enabled: false\n"
    )
    return cfg


class TestConfigTUIStateFromConfigPath:
    def test_creates_state(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        assert isinstance(state, ConfigTUIState)

    def test_config_path_stored(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        assert state.config_path == config_path

    def test_category_names_non_empty(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        assert len(state.category_names) > 0

    def test_initial_index_zero(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        assert state.current_category_index == 0
        assert state.current_field_index == 0

    def test_categories_loaded(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        assert "profile" in state.categories

    def test_category_order_filters_to_known_categories(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        # All returned names must be from CATEGORY_ORDER
        for name in state.category_names:
            assert name in CATEGORY_ORDER

    def test_accepts_string_path(self, config_path):
        state = ConfigTUIState.from_config_path(str(config_path))
        assert state.config_path == config_path


class TestConfigTUIStateProperties:
    def test_current_category(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        assert state.current_category == state.category_names[0]

    def test_current_fields_returns_list(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        assert isinstance(state.current_fields, list)

    def test_current_field_returns_field_info(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        fi = state.current_field
        assert fi is None or hasattr(fi, "key")

    def test_current_field_out_of_bounds_returns_none(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        # Set index beyond available fields
        state.current_field_index = 9999
        assert state.current_field is None

    def test_current_field_negative_index_returns_none(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        state.current_field_index = -1
        assert state.current_field is None

    def test_current_fields_unknown_category(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        state.category_names = ["nonexistent_category"]
        assert state.current_fields == []

    def test_unsaved_changes_initially_empty(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        assert state.unsaved_changes == {}

    def test_editing_initially_false(self, config_path):
        state = ConfigTUIState.from_config_path(config_path)
        assert state.editing is False
