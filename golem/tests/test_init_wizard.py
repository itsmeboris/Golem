# pylint: disable=too-few-public-methods
"""Tests for golem.init_wizard — interactive config wizard."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import yaml

from golem.cli import cmd_init, main
from golem.core.config import load_config
from golem.init_wizard import ask, run_wizard


class TestAsk:
    def test_empty_input_returns_default(self):
        with patch("builtins.input", return_value=""):
            result = ask("Prompt?", default="mydefault")
        assert result == "mydefault"

    def test_valid_choice_accepted(self):
        with patch("builtins.input", return_value="sonnet"):
            result = ask("Model?", default="sonnet", choices=["sonnet", "opus"])
        assert result == "sonnet"

    def test_invalid_choice_then_valid_retries(self, capsys):
        with patch("builtins.input", side_effect=["bad", "opus"]):
            result = ask("Model?", default="sonnet", choices=["sonnet", "opus"])
        assert result == "opus"
        out = capsys.readouterr().out
        assert "Invalid choice" in out

    def test_no_choices_accepts_any_value(self):
        with patch("builtins.input", return_value="anything"):
            result = ask("What?", default="")
        assert result == "anything"

    def test_no_default_empty_input_returns_empty(self):
        with patch("builtins.input", return_value=""):
            result = ask("What?")
        assert result == ""


class TestRunWizard:
    def test_defaults_mode(self, tmp_path):
        output = tmp_path / "config.yaml"
        result = run_wizard(output, use_defaults=True)
        assert result == 0
        assert output.exists()
        data = yaml.safe_load(output.read_text())
        assert data["flows"]["golem"]["profile"] == "local"
        assert data["flows"]["golem"]["task_model"] == "sonnet"
        assert data["flows"]["golem"]["budget_per_task_usd"] == 10.0
        assert data["dashboard"]["port"] == 8081
        assert data["slack"]["enabled"] is False
        assert data["teams"]["enabled"] is False

    def test_defaults_round_trip_load_config(self, tmp_path):
        output = tmp_path / "config.yaml"
        result = run_wizard(output, use_defaults=True)
        assert result == 0
        cfg = load_config(output)
        assert cfg.golem.profile == "local"
        assert cfg.golem.task_model == "sonnet"
        assert cfg.dashboard.port == 8081
        assert cfg.slack.enabled is False
        assert cfg.teams.enabled is False

    def test_interactive_full(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "5.0",  # budget
            "/tmp",  # work dir
            "proj1",  # projects
            "n",  # slack
            "n",  # teams
            "9000",  # dashboard port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert data["flows"]["golem"]["profile"] == "local"
        assert data["flows"]["golem"]["budget_per_task_usd"] == 5.0
        assert data["dashboard"]["port"] == 9000
        assert data["flows"]["golem"]["projects"] == ["proj1"]

    def test_overwrite_decline(self, tmp_path):
        output = tmp_path / "config.yaml"
        output.write_text("existing: true\n")
        with patch("builtins.input", return_value="n"):
            result = run_wizard(output, use_defaults=False)
        assert result == 1
        assert output.read_text() == "existing: true\n"

    def test_overwrite_accept(self, tmp_path):
        output = tmp_path / "config.yaml"
        output.write_text("existing: true\n")
        inputs = [
            "y",  # overwrite
            "local",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "",  # projects
            "n",  # slack
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert "flows" in data

    def test_keyboard_interrupt(self, tmp_path):
        output = tmp_path / "config.yaml"
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            result = run_wizard(output, use_defaults=False)
        assert result == 1

    def test_eof_error(self, tmp_path):
        output = tmp_path / "config.yaml"
        with patch("builtins.input", side_effect=EOFError):
            result = run_wizard(output, use_defaults=False)
        assert result == 1

    def test_invalid_budget_retry(self, tmp_path, capsys):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "abc",  # bad budget
            "5.0",  # good budget
            "/tmp",  # work dir
            "",  # projects
            "n",  # slack
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        out = capsys.readouterr().out
        assert "positive number" in out

    def test_invalid_port_retry(self, tmp_path, capsys):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "",  # projects
            "n",  # slack
            "n",  # teams
            "abc",  # bad port
            "8081",  # good port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        out = capsys.readouterr().out
        assert "1 and 65535" in out

    def test_slack_enabled(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "",  # projects
            "y",  # slack enabled
            "https://hooks.slack.com/xyz",  # slack webhook
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert data["slack"]["enabled"] is True
        assert data["slack"]["webhooks"]["default"] == "https://hooks.slack.com/xyz"

    def test_teams_enabled(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "",  # projects
            "n",  # slack
            "y",  # teams enabled
            "https://outlook.office.com/wh",  # teams webhook
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert data["teams"]["enabled"] is True
        assert data["teams"]["webhooks"]["default"] == "https://outlook.office.com/wh"

    def test_projects_parsing(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "proj1, proj2",  # projects with spaces
            "n",  # slack
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert data["flows"]["golem"]["projects"] == ["proj1", "proj2"]

    def test_defaults_overwrites_existing(self, tmp_path):
        output = tmp_path / "config.yaml"
        output.write_text("old: content\n")
        result = run_wizard(output, use_defaults=True)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert "flows" in data

    def test_slack_enabled_empty_webhook(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "",  # projects
            "y",  # slack enabled
            "",  # empty slack webhook
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert data["slack"]["enabled"] is True
        assert "webhooks" not in data["slack"]

    def test_teams_enabled_empty_webhook(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "",  # projects
            "n",  # slack
            "y",  # teams enabled
            "",  # empty teams webhook
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert data["teams"]["enabled"] is True
        assert "webhooks" not in data["teams"]

    def test_negative_budget_retry(self, tmp_path, capsys):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "-5.0",  # negative budget (invalid)
            "5.0",  # valid budget
            "/tmp",  # work dir
            "",  # projects
            "n",  # slack
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        out = capsys.readouterr().out
        assert "positive number" in out

    def test_port_out_of_range_retry(self, tmp_path, capsys):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "",  # projects
            "n",  # slack
            "n",  # teams
            "99999",  # port out of range
            "8081",  # valid port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        out = capsys.readouterr().out
        assert "1 and 65535" in out

    def test_defaults_include_heartbeat(self, tmp_path):
        """Default config includes heartbeat section disabled."""
        output = tmp_path / "config.yaml"
        run_wizard(output, use_defaults=True)
        data = yaml.safe_load(output.read_text())
        golem_cfg = data["flows"]["golem"]
        assert golem_cfg["heartbeat_enabled"] is False
        assert golem_cfg["heartbeat_interval_seconds"] == 300
        assert golem_cfg["heartbeat_idle_threshold_seconds"] == 900
        assert golem_cfg["heartbeat_daily_budget_usd"] == 1.0
        assert golem_cfg["heartbeat_max_inflight"] == 1
        assert golem_cfg["heartbeat_candidate_limit"] == 5
        assert golem_cfg["heartbeat_dedup_ttl_days"] == 30

    def test_heartbeat_round_trip_config(self, tmp_path):
        """Heartbeat fields survive load_config round-trip."""
        output = tmp_path / "config.yaml"
        run_wizard(output, use_defaults=True)
        cfg = load_config(output)
        assert cfg.golem.heartbeat_enabled is False
        assert cfg.golem.heartbeat_interval_seconds == 300
        assert cfg.golem.heartbeat_daily_budget_usd == 1.0


class TestCmdInit:
    def test_init_subcommand(self):
        with patch("golem.cli.cmd_init", return_value=0) as mock_init:
            with patch("sys.argv", ["golem", "init"]):
                result = main()
        assert result == 0
        mock_init.assert_called_once()

    def test_init_defaults_flag(self):
        with patch("golem.cli.cmd_init", return_value=0) as mock_init:
            with patch("sys.argv", ["golem", "init", "--defaults"]):
                result = main()
        assert result == 0
        args = mock_init.call_args[0][0]
        assert args.defaults is True

    def test_init_output_flag(self):
        with patch("golem.cli.cmd_init", return_value=0) as mock_init:
            with patch("sys.argv", ["golem", "init", "-o", "custom.yaml"]):
                result = main()
        assert result == 0
        args = mock_init.call_args[0][0]
        assert args.output == "custom.yaml"


class TestCmdInitHandler:
    def test_calls_run_wizard(self, tmp_path):
        output = tmp_path / "out.yaml"
        args = SimpleNamespace(output=str(output), defaults=True)
        with patch("golem.init_wizard.run_wizard", return_value=0) as mock_wizard:
            result = cmd_init(args)
        assert result == 0
        mock_wizard.assert_called_once()

    def test_passes_output_path(self, tmp_path):
        output = tmp_path / "myconfig.yaml"
        args = SimpleNamespace(output=str(output), defaults=False)

        def fake_wizard(path, use_defaults):
            assert str(path) == str(output)
            assert use_defaults is False
            return 0

        with patch("golem.init_wizard.run_wizard", side_effect=fake_wizard):
            result = cmd_init(args)
        assert result == 0

    def test_passes_defaults_flag(self, tmp_path):
        output = tmp_path / "config.yaml"
        args = SimpleNamespace(output=str(output), defaults=True)

        def fake_wizard(path, use_defaults):
            assert use_defaults is True
            return 0

        with patch("golem.init_wizard.run_wizard", side_effect=fake_wizard):
            result = cmd_init(args)
        assert result == 0

    def test_returns_wizard_result(self, tmp_path):
        output = tmp_path / "config.yaml"
        args = SimpleNamespace(output=str(output), defaults=False)
        with patch("golem.init_wizard.run_wizard", return_value=1):
            result = cmd_init(args)
        assert result == 1


class TestGitHubProfile:
    def test_github_interactive(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "github",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "owner/repo",  # GitHub repos
            "golem",  # detection label
            "n",  # slack
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert data["flows"]["golem"]["profile"] == "github"
        assert data["flows"]["golem"]["projects"] == ["owner/repo"]
        assert data["flows"]["golem"]["detection_tag"] == "golem"

    def test_github_detection_tag_default(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "github",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "owner/repo",  # GitHub repos
            "",  # detection label — accept default
            "n",  # slack
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert data["flows"]["golem"]["detection_tag"] == "golem"

    def test_github_shows_gh_hint(self, tmp_path, capsys):
        output = tmp_path / "config.yaml"
        inputs = [
            "github",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "owner/repo",  # repos
            "golem",  # label
            "n",  # slack
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            run_wizard(output, use_defaults=False)
        out = capsys.readouterr().out
        assert "gh auth login" in out

    def test_local_no_detection_tag(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "local",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "",  # projects
            "n",  # slack
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert "detection_tag" not in data["flows"]["golem"]

    def test_github_multiple_repos(self, tmp_path):
        output = tmp_path / "config.yaml"
        inputs = [
            "github",  # profile
            "sonnet",  # model
            "10.0",  # budget
            "/tmp",  # work dir
            "owner/repo1, owner/repo2",  # multiple repos
            "bot",  # custom label
            "n",  # slack
            "n",  # teams
            "8081",  # port
        ]
        with patch("builtins.input", side_effect=inputs):
            result = run_wizard(output, use_defaults=False)
        assert result == 0
        data = yaml.safe_load(output.read_text())
        assert data["flows"]["golem"]["projects"] == ["owner/repo1", "owner/repo2"]
        assert data["flows"]["golem"]["detection_tag"] == "bot"


class TestSelfUpdateWizard:
    def test_self_update_defaults_in_config(self, tmp_path):
        output = tmp_path / "config.yaml"
        run_wizard(output, use_defaults=True)
        with open(output) as f:
            cfg = yaml.safe_load(f)
        flow = cfg["flows"]["golem"]
        assert flow["self_update_enabled"] is False
        assert flow["self_update_branch"] == "master"
        assert flow["self_update_interval_seconds"] == 600
        assert flow["self_update_strategy"] == "merged_only"


class TestSetupGitHooks:
    def test_configures_hooks_path(self):
        """_setup_git_hooks sets core.hooksPath when .githooks/ exists."""
        from golem.init_wizard import _setup_git_hooks

        # The real repo has .githooks/, so we just mock subprocess
        with patch(
            "golem.init_wizard.subprocess.run",
            side_effect=[
                MagicMock(stdout=".git/hooks\n"),  # current value != .githooks
                MagicMock(),  # set call
            ],
        ) as mock_run:
            _setup_git_hooks()
        assert mock_run.call_count == 2

    def test_skips_when_already_configured(self):
        """_setup_git_hooks is a no-op when already set to .githooks."""
        from golem.init_wizard import _setup_git_hooks

        with patch(
            "golem.init_wizard.subprocess.run",
            return_value=MagicMock(stdout=".githooks\n"),
        ) as mock_run:
            _setup_git_hooks()
        mock_run.assert_called_once()

    def test_handles_missing_git(self):
        """_setup_git_hooks handles git not being available."""
        from golem.init_wizard import _setup_git_hooks

        with patch(
            "golem.init_wizard.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            _setup_git_hooks()  # should not raise

    def test_skips_when_no_githooks_dir(self):
        """_setup_git_hooks returns early when .githooks/ doesn't exist."""
        from golem.init_wizard import _setup_git_hooks

        with (
            patch("pathlib.Path.is_dir", return_value=False),
            patch(
                "golem.init_wizard.subprocess.run",
            ) as mock_run,
        ):
            _setup_git_hooks()
        mock_run.assert_not_called()
