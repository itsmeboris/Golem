"""Tests for the 'golem setup' subcommand (cmd_setup)."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from golem.cli import _build_parser, cmd_setup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PLUGINS_ALL = {
    "plugins": [
        {"name": "superpowers"},
        {"name": "code-review"},
        {"name": "code-simplifier"},
    ]
}

_PLUGINS_NONE: dict = {"plugins": []}

_PLUGINS_PARTIAL = {
    "plugins": [
        {"name": "superpowers"},
    ]
}


def _make_args(**kwargs):
    """Return a minimal Namespace for cmd_setup."""
    defaults = {}
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# Parser registration
# ---------------------------------------------------------------------------


class TestSetupSubparserRegistered:
    """The 'setup' command must be reachable via argparse."""

    def test_setup_command_parses(self):
        parser = _build_parser()
        args = parser.parse_args(["setup"])
        assert args.command == "setup"
        assert args.func is cmd_setup


# ---------------------------------------------------------------------------
# All tools present, all plugins installed
# ---------------------------------------------------------------------------


class TestCmdSetupAllGreen:
    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_returns_zero(self, _mock_which, tmp_path):
        plugins_file = tmp_path / "installed_plugins.json"
        plugins_file.write_text(json.dumps(_PLUGINS_ALL), encoding="utf-8")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                result = cmd_setup(_make_args())

        assert result == 0

    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_all_ok_output(self, _mock_which, tmp_path, capsys):
        plugins_file = tmp_path / "installed_plugins.json"
        plugins_file.write_text(json.dumps(_PLUGINS_ALL), encoding="utf-8")
        config_file = tmp_path / "config.yaml"
        config_file.write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        assert "[ok]" in out
        assert "git" in out
        assert "superpowers" in out
        assert "code-review" in out
        assert "code-simplifier" in out

    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_environment_ready_message(self, _mock_which, tmp_path, capsys):
        plugins_file = tmp_path / "installed_plugins.json"
        plugins_file.write_text(json.dumps(_PLUGINS_ALL), encoding="utf-8")
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        assert "Environment ready" in out


# ---------------------------------------------------------------------------
# Missing claude CLI
# ---------------------------------------------------------------------------


class TestCmdSetupMissingClaude:
    @patch(
        "golem.cli.shutil.which",
        side_effect=lambda name: "/usr/bin/git" if name == "git" else None,
    )
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_returns_zero_despite_missing_claude(self, _mock_which, tmp_path):
        """setup is informational — missing claude still returns 0."""
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_NONE), encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                result = cmd_setup(_make_args())

        assert result == 0

    @patch(
        "golem.cli.shutil.which",
        side_effect=lambda name: "/usr/bin/git" if name == "git" else None,
    )
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_shows_x_for_claude(self, _mock_which, tmp_path, capsys):
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_NONE), encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        assert "[x]" in out
        assert "claude" in out.lower()


# ---------------------------------------------------------------------------
# No plugins installed (file missing)
# ---------------------------------------------------------------------------


class TestCmdSetupMissingPluginsFile:
    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_returns_zero(self, _mock_which, tmp_path):
        """Missing plugins file → assume no plugins; still return 0."""
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        # Point _PLUGINS_FILE at a nonexistent path
        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "nonexistent_plugins.json",
            ):
                result = cmd_setup(_make_args())

        assert result == 0

    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_shows_not_installed_for_all_plugins(self, _mock_which, tmp_path, capsys):
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "nonexistent_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        assert "[--]" in out
        assert "superpowers" in out
        assert "code-review" in out
        assert "code-simplifier" in out

    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_shows_install_hint_for_missing_plugins(
        self, _mock_which, tmp_path, capsys
    ):
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "nonexistent_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        assert "claude plugins install" in out


# ---------------------------------------------------------------------------
# Partial plugins (only superpowers installed)
# ---------------------------------------------------------------------------


class TestCmdSetupPartialPlugins:
    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_shows_ok_for_installed_and_dashes_for_missing(
        self, _mock_which, tmp_path, capsys
    ):
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_PARTIAL), encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        lines = out.splitlines()
        superpowers_line = next(l for l in lines if "superpowers" in l)
        code_review_line = next(l for l in lines if "code-review" in l)
        assert "[ok]" in superpowers_line
        assert "[--]" in code_review_line


# ---------------------------------------------------------------------------
# Missing ~/.golem/config.yaml
# ---------------------------------------------------------------------------


class TestCmdSetupMissingGolemConfig:
    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_shows_x_and_suggests_init(self, _mock_which, tmp_path, capsys):
        # No config.yaml in tmp_path
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_ALL), encoding="utf-8"
        )

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        assert "golem init" in out

    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_returns_zero_even_without_config(self, _mock_which, tmp_path):
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_ALL), encoding="utf-8"
        )

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                result = cmd_setup(_make_args())

        assert result == 0


# ---------------------------------------------------------------------------
# Python version check
# ---------------------------------------------------------------------------


class TestCmdSetupPythonVersion:
    @pytest.mark.parametrize(
        "version_info,expected_status",
        [
            ((3, 11, 0), "[ok]"),
            ((3, 12, 1), "[ok]"),
            ((3, 10, 0), "[x]"),
            ((3, 9, 5), "[x]"),
        ],
        ids=["py311", "py312", "py310_old", "py39_old"],
    )
    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    def test_python_version_status(
        self, _mock_which, version_info, expected_status, tmp_path, capsys
    ):
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_NONE), encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.sys.version_info", version_info):
            with patch("golem.cli.GOLEM_HOME", tmp_path):
                with patch(
                    "golem.cli._PLUGINS_FILE",
                    tmp_path / "installed_plugins.json",
                ):
                    cmd_setup(_make_args())

        out = capsys.readouterr().out
        python_line = next((l for l in out.splitlines() if "python" in l.lower()), "")
        assert expected_status in python_line


# ---------------------------------------------------------------------------
# Output structure / section headers
# ---------------------------------------------------------------------------


class TestCmdSetupOutputStructure:
    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_has_environment_section(self, _mock_which, tmp_path, capsys):
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_NONE), encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        assert "Environment" in out

    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_has_plugins_section(self, _mock_which, tmp_path, capsys):
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_NONE), encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        assert "Plugin" in out

    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_title_line(self, _mock_which, tmp_path, capsys):
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_NONE), encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                cmd_setup(_make_args())

        out = capsys.readouterr().out
        assert "Golem" in out
        assert "Setup" in out


# ---------------------------------------------------------------------------
# Via main()  — integration smoke test
# ---------------------------------------------------------------------------


class TestSetupViaMain:
    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_main_routes_to_cmd_setup(self, _mock_which, tmp_path, capsys):
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_NONE), encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        from golem.cli import main

        with patch("sys.argv", ["golem", "setup"]):
            with patch("golem.cli.GOLEM_HOME", tmp_path):
                with patch(
                    "golem.cli._PLUGINS_FILE",
                    tmp_path / "installed_plugins.json",
                ):
                    result = main()

        assert result == 0
        out = capsys.readouterr().out
        assert "Setup" in out


# ---------------------------------------------------------------------------
# Edge cases for full coverage
# ---------------------------------------------------------------------------


class TestCheckPluginsCorruptFile:
    """_check_plugins returns [] when the JSON is corrupt or unreadable."""

    @patch("golem.cli.shutil.which", return_value="/usr/bin/git")
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_corrupt_json_returns_empty(self, _mock_which, tmp_path):
        plugins_file = tmp_path / "installed_plugins.json"
        plugins_file.write_text("{corrupt", encoding="utf-8")
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch("golem.cli._PLUGINS_FILE", plugins_file):
                result = cmd_setup(_make_args())

        assert result == 0


class TestCmdSetupMissingGit:
    """Cover the branch where git is not found in PATH."""

    @patch(
        "golem.cli.shutil.which",
        side_effect=lambda name: None,
    )
    @patch("golem.cli.sys.version_info", (3, 11, 0))
    def test_missing_git_shows_error(self, _mock_which, tmp_path, capsys):
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps(_PLUGINS_ALL), encoding="utf-8"
        )
        (tmp_path / "config.yaml").write_text("", encoding="utf-8")

        with patch("golem.cli.GOLEM_HOME", tmp_path):
            with patch(
                "golem.cli._PLUGINS_FILE",
                tmp_path / "installed_plugins.json",
            ):
                result = cmd_setup(_make_args())

        assert result == 0
        out = capsys.readouterr().out
        assert "git not found" in out
        assert "Actions needed" in out
