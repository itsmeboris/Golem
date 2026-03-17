"""Tests for golem.config_editor — shared config editing backend."""

import os
import signal
from unittest.mock import patch

import pytest
import yaml

from golem.config_editor import (
    FIELD_REGISTRY,
    FieldInfo,
    FieldMeta,
    _resolve_value,
    _set_yaml_value,
    _validate_field,
    get_config_by_category,
    signal_daemon_reload,
    update_config,
)
from golem.core.config import Config


class TestFieldMeta:
    def test_basic_construction(self):
        fm = FieldMeta(
            category="models",
            field_type="choice",
            description="Task model",
            choices=["opus", "sonnet", "haiku"],
        )
        assert fm.category == "models"
        assert fm.field_type == "choice"
        assert fm.sensitive is False

    def test_sensitive_field(self):
        fm = FieldMeta(
            category="dashboard",
            field_type="str",
            description="Admin token",
            sensitive=True,
        )
        assert fm.sensitive is True


class TestFieldInfo:
    def test_basic_construction(self):
        meta = FieldMeta(category="models", field_type="choice", description="x")
        fi = FieldInfo(key="golem.task_model", value="opus", meta=meta)
        assert fi.key == "golem.task_model"
        assert fi.value == "opus"


class TestFieldRegistry:
    def test_all_categories_present(self):
        categories = {m.category for m in FIELD_REGISTRY.values()}
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
        assert categories == expected

    def test_all_keys_have_valid_section_prefix(self):
        valid_prefixes = {
            "golem",
            "claude",
            "daemon",
            "dashboard",
            "webhook",
            "logging",
            "health",
            "polling",
            "slack",
            "teams",
        }
        for key in FIELD_REGISTRY:
            prefix = key.split(".")[0]
            assert prefix in valid_prefixes, f"Invalid prefix in {key}"

    def test_choice_fields_have_choices(self):
        for key, meta in FIELD_REGISTRY.items():
            if meta.field_type == "choice":
                assert meta.choices, f"{key} is choice type but has no choices"


class TestGetConfigByCategory:
    def test_returns_all_categories(self):
        config = Config()
        result = get_config_by_category(config)
        assert "models" in result
        assert "heartbeat" in result
        assert "self_update" in result

    def test_field_values_match_defaults(self):
        config = Config()
        result = get_config_by_category(config)
        models = {fi.key: fi for fi in result["models"]}
        assert models["golem.task_model"].value == "sonnet"

    def test_sensitive_fields_present(self):
        config = Config()
        result = get_config_by_category(config)
        dashboard_fields = {fi.key: fi for fi in result["dashboard"]}
        assert dashboard_fields["dashboard.admin_token"].meta.sensitive is True


class TestUpdateConfig:
    def _write_config(self, tmp_path, data):
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(data, f)
        return p

    def test_valid_update_writes_yaml(self, tmp_path):
        cfg = {
            "flows": {
                "golem": {
                    "profile": "github",
                    "task_model": "sonnet",
                    "projects": ["myrepo"],
                }
            }
        }
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"golem.task_model": "opus"})
        assert errors == []
        with open(p) as f:
            written = yaml.safe_load(f)
        assert written["flows"]["golem"]["task_model"] == "opus"

    def test_invalid_choice_rejected(self, tmp_path):
        cfg = {"flows": {"golem": {"profile": "github", "projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"golem.task_model": "gpt4"})
        assert any("must be one of" in e for e in errors)

    def test_unknown_field_rejected(self, tmp_path):
        cfg = {"flows": {"golem": {}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"golem.nonexistent": "x"})
        assert any("Unknown" in e for e in errors)

    def test_all_or_nothing(self, tmp_path):
        """If one field is invalid, no fields are written."""
        cfg = {"flows": {"golem": {"task_model": "sonnet", "projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(
            p,
            {
                "golem.task_model": "opus",
                "golem.self_update_strategy": "bad",
            },
        )
        assert len(errors) > 0
        with open(p) as f:
            written = yaml.safe_load(f)
        assert written["flows"]["golem"]["task_model"] == "sonnet"

    def test_bool_coercion(self, tmp_path):
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"golem.heartbeat_enabled": "true"})
        assert errors == []
        with open(p) as f:
            written = yaml.safe_load(f)
        assert written["flows"]["golem"]["heartbeat_enabled"] is True

    def test_list_coercion(self, tmp_path):
        cfg = {"flows": {"golem": {}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"golem.projects": "repo1,repo2"})
        assert errors == []
        with open(p) as f:
            written = yaml.safe_load(f)
        assert written["flows"]["golem"]["projects"] == ["repo1", "repo2"]

    def test_min_val_enforced(self, tmp_path):
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"golem.budget_per_task_usd": "-1"})
        assert any(">=" in e for e in errors)

    def test_max_val_enforced(self, tmp_path):
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"dashboard.port": "99999"})
        assert any("<=" in e for e in errors)

    def test_invalid_int_rejected(self, tmp_path):
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"dashboard.port": "not-a-number"})
        assert any("Invalid value" in e for e in errors)

    def test_unknown_section_rejected(self, tmp_path):
        """A key with an unknown section prefix is rejected."""
        cfg = {"flows": {"golem": {}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"nosuchsection.field": "x"})
        assert any("Unknown config section" in e for e in errors)

    def test_non_registry_field_valid(self, tmp_path):
        """A key not in FIELD_REGISTRY but present on the dataclass is accepted."""
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        # tick_interval exists on GolemFlowConfig but is not in FIELD_REGISTRY
        errors = update_config(p, {"golem.tick_interval": 60})
        assert errors == []

    def test_full_validate_config_errors_returned(self, tmp_path):
        """validate_config errors after patching prevent the write."""
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        original_content = p.read_text()
        # Patch validate_config to return an error after patching
        with patch("golem.config_editor.validate_config", return_value=["some error"]):
            errors = update_config(p, {"golem.task_model": "opus"})
        assert errors == ["some error"]
        # File should be unchanged
        assert p.read_text() == original_content

    def test_exception_during_rename_cleans_up(self, tmp_path):
        """If os.rename raises, the temp file is cleaned up and exception propagates."""
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        with patch("golem.config_editor.os.rename", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                update_config(p, {"golem.task_model": "opus"})
        # No .yaml.tmp files should remain
        tmp_files = list(tmp_path.glob("*.yaml.tmp"))
        assert tmp_files == []

    def test_exception_during_rename_and_unlink(self, tmp_path):
        """If both os.rename and os.unlink raise, the original exception propagates."""
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        with (
            patch("golem.config_editor.os.rename", side_effect=OSError("disk full")),
            patch("golem.config_editor.os.unlink", side_effect=OSError("unlink fail")),
        ):
            with pytest.raises(OSError, match="disk full"):
                update_config(p, {"golem.task_model": "opus"})

    def test_exception_during_rename_and_unlink_logs_debug(self, tmp_path, caplog):
        """When both os.rename and os.unlink fail, a debug message is logged for the unlink."""
        import logging

        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        with caplog.at_level(logging.DEBUG, logger="golem.config_editor"):
            with (
                patch(
                    "golem.config_editor.os.rename", side_effect=OSError("disk full")
                ),
                patch(
                    "golem.config_editor.os.unlink",
                    side_effect=OSError("unlink fail"),
                ),
            ):
                with pytest.raises(OSError, match="disk full"):
                    update_config(p, {"golem.task_model": "opus"})

        assert any(
            "Failed to clean up temp file" in r.message and r.levelno == logging.DEBUG
            for r in caplog.records
        )

    def test_non_golem_section_written_correctly(self, tmp_path):
        """update_config writes non-golem sections at root level."""
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"dashboard.port": "8082"})
        assert errors == []
        with open(p) as f:
            written = yaml.safe_load(f)
        assert written["dashboard"]["port"] == 8082

    def test_bool_raw_true_passthrough(self, tmp_path):
        """When raw bool True is passed, it passes through without str conversion."""
        cfg = {"flows": {"golem": {"projects": ["r"]}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"golem.heartbeat_enabled": True})
        assert errors == []
        with open(p) as f:
            written = yaml.safe_load(f)
        assert written["flows"]["golem"]["heartbeat_enabled"] is True

    def test_list_raw_list_passthrough(self, tmp_path):
        """When a list is passed directly for a list field, it passes through."""
        cfg = {"flows": {"golem": {}}}
        p = self._write_config(tmp_path, cfg)
        errors = update_config(p, {"golem.projects": ["a", "b"]})
        assert errors == []
        with open(p) as f:
            written = yaml.safe_load(f)
        assert written["flows"]["golem"]["projects"] == ["a", "b"]


class TestResolveValueEdgeCases:
    """Unit tests for _resolve_value covering all branches."""

    def test_bool_from_bool(self):
        assert _resolve_value("bool", True) is True

    def test_bool_from_non_bool_non_str(self):
        # int 1 → bool True
        assert _resolve_value("bool", 1) is True

    def test_int_coercion(self):
        assert _resolve_value("int", "42") == 42

    def test_float_coercion(self):
        assert _resolve_value("float", "3.14") == pytest.approx(3.14)

    def test_list_from_list(self):
        assert _resolve_value("list", ["a", "b"]) == ["a", "b"]

    def test_list_from_non_list_non_str(self):
        # tuple → list
        assert _resolve_value("list", ("x", "y")) == ["x", "y"]

    def test_str_passthrough(self):
        assert _resolve_value("str", "hello") == "hello"

    def test_choice_passthrough(self):
        assert _resolve_value("choice", "opus") == "opus"


class TestValidateFieldEdgeCases:
    """Unit tests for _validate_field covering max_val branch."""

    def test_max_val_exceeded(self):
        meta = FieldMeta("dashboard", "int", "Port", min_val=1, max_val=65535)
        errors = _validate_field("dashboard.port", 99999, meta)
        assert any("<=" in e for e in errors)

    def test_min_and_max_both_ok(self):
        meta = FieldMeta("dashboard", "int", "Port", min_val=1, max_val=65535)
        errors = _validate_field("dashboard.port", 8080, meta)
        assert errors == []


class TestSetYamlValue:
    """Unit tests for _set_yaml_value covering non-golem branch."""

    def test_golem_section(self):
        d = {}
        _set_yaml_value(d, "golem.task_model", "opus")
        assert d == {"flows": {"golem": {"task_model": "opus"}}}

    def test_non_golem_section(self):
        d = {}
        _set_yaml_value(d, "dashboard.port", 8081)
        assert d == {"dashboard": {"port": 8081}}


class TestSignalDaemonReload:
    def test_no_pid_file(self, tmp_path):
        assert signal_daemon_reload(tmp_path / "missing.pid") is False

    def test_stale_pid(self, tmp_path):
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("999999999")  # nonexistent PID
        assert signal_daemon_reload(pid_file) is False

    def test_valid_pid(self, tmp_path):
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(str(os.getpid()))
        with patch("golem.config_editor.os.kill") as mock_kill:
            result = signal_daemon_reload(pid_file)
        assert result is True
        mock_kill.assert_called_once_with(os.getpid(), signal.SIGHUP)

    def test_corrupt_pid_file(self, tmp_path):
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text("not-a-number")
        assert signal_daemon_reload(pid_file) is False

    def test_oserror_on_kill(self, tmp_path):
        """OSError (other than ProcessLookupError) from os.kill returns False."""
        pid_file = tmp_path / "daemon.pid"
        pid_file.write_text(str(os.getpid()))
        with patch(
            "golem.config_editor.os.kill", side_effect=OSError("permission denied")
        ):
            result = signal_daemon_reload(pid_file)
        assert result is False
