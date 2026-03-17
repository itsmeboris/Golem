"""Tests for golem config CLI subcommand."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from golem.cli import cmd_config


def _make_args(**kwargs):
    """Build a minimal argparse-like namespace."""
    defaults = {
        "config": "config.yaml",
        "verbose": False,
        "config_action": None,
        "field": None,
        "value": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class TestConfigGet:
    def test_get_existing_field(self, tmp_path, capsys):
        cfg = {"flows": {"golem": {"task_model": "opus"}}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(config=str(p), config_action="get", field="golem.task_model")
        rc = cmd_config(args)
        assert rc == 0
        assert "opus" in capsys.readouterr().out

    def test_get_unknown_field(self, tmp_path, capsys):
        cfg = {"flows": {"golem": {}}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(config=str(p), config_action="get", field="golem.nonexistent")
        rc = cmd_config(args)
        assert rc == 1
        assert "Unknown field" in capsys.readouterr().err


class TestConfigSet:
    def test_set_valid_field(self, tmp_path):
        cfg = {"flows": {"golem": {"task_model": "sonnet", "projects": ["myrepo"]}}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(
            config=str(p),
            config_action="set",
            field="golem.task_model",
            value="opus",
        )
        with patch("golem.config_editor.signal_daemon_reload", return_value=False):
            rc = cmd_config(args)
        assert rc == 0
        with open(p) as f:
            written = yaml.safe_load(f)
        assert written["flows"]["golem"]["task_model"] == "opus"

    def test_set_valid_field_daemon_reloaded(self, tmp_path, capsys):
        cfg = {"flows": {"golem": {"task_model": "sonnet", "projects": ["myrepo"]}}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(
            config=str(p),
            config_action="set",
            field="golem.task_model",
            value="opus",
        )
        with patch("golem.config_editor.signal_daemon_reload", return_value=True):
            rc = cmd_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "reload triggered" in out

    def test_set_valid_field_no_daemon(self, tmp_path, capsys):
        cfg = {"flows": {"golem": {"task_model": "sonnet", "projects": ["myrepo"]}}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(
            config=str(p),
            config_action="set",
            field="golem.task_model",
            value="opus",
        )
        with patch("golem.config_editor.signal_daemon_reload", return_value=False):
            rc = cmd_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "next start" in out

    def test_set_invalid_value(self, tmp_path, capsys):
        cfg = {"flows": {"golem": {}}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(
            config=str(p),
            config_action="set",
            field="golem.task_model",
            value="gpt4",
        )
        rc = cmd_config(args)
        assert rc == 1
        assert "must be one of" in capsys.readouterr().err


class TestConfigList:
    def test_list_all(self, tmp_path, capsys):
        cfg = {"flows": {"golem": {"task_model": "opus"}}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(config=str(p), config_action="list")
        rc = cmd_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "golem.task_model" in out

    def test_list_sensitive_masked(self, tmp_path, capsys):
        cfg = {"dashboard": {"api_key": "mysecret"}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(config=str(p), config_action="list")
        rc = cmd_config(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "mysecret" not in out
        assert "***" in out


class TestConfigInteractive:
    def test_interactive_falls_back_on_missing_import(self, tmp_path, capsys):
        cfg = {"flows": {"golem": {}}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(config=str(p), config_action=None)
        with patch("golem.cli._config_interactive") as mock_interactive:
            mock_interactive.return_value = 42
            rc = cmd_config(args)
        assert rc == 42
        mock_interactive.assert_called_once_with(Path(str(p)))

    def test_interactive_import_error(self, tmp_path, capsys):
        cfg = {"flows": {"golem": {}}}
        p = tmp_path / "config.yaml"
        with open(p, "w") as f:
            yaml.safe_dump(cfg, f)
        args = _make_args(config=str(p), config_action=None)
        with patch.dict("sys.modules", {"golem.config_tui": None}):
            rc = cmd_config(args)
        assert rc == 1
        assert "prompt_toolkit" in capsys.readouterr().err

    def test_interactive_success_calls_run_config_tui(self, tmp_path):
        """_config_interactive delegates to run_config_tui when import succeeds."""
        import types

        from golem.cli import _config_interactive

        fake_tui = types.ModuleType("golem.config_tui")
        fake_tui.run_config_tui = lambda path: 0
        with patch.dict("sys.modules", {"golem.config_tui": fake_tui}):
            rc = _config_interactive(tmp_path / "config.yaml")
        assert rc == 0


class TestBuildParserConfigSubcommand:
    """Verify the config subcommand is registered in the argparse tree."""

    def test_config_subcommand_registered(self):
        from golem.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["config", "get", "golem.task_model"])
        assert args.config_action == "get"
        assert args.field == "golem.task_model"

    def test_config_set_subcommand(self):
        from golem.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["config", "set", "golem.task_model", "opus"])
        assert args.config_action == "set"
        assert args.field == "golem.task_model"
        assert args.value == "opus"

    def test_config_list_subcommand(self):
        from golem.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["config", "list"])
        assert args.config_action == "list"
