# golem/tests/test_verify_config.py
"""Tests for golem.verify_config — per-repo verification config loader/saver."""

# pylint: disable=missing-function-docstring

from pathlib import Path

from golem.verify_config import (
    VerifyCommand,
    VerifyConfig,
    load_verify_config,
    save_verify_config,
)


class TestLoadVerifyConfig:
    def test_returns_none_when_no_file(self, tmp_path):
        result = load_verify_config(str(tmp_path))
        assert result is None

    def test_loads_minimal_valid_config(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: []\ncommands: []\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is not None
        assert result.version == 1
        assert result.commands == []

    def test_loads_commands(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: [node]\n"
            "commands:\n  - role: test\n    cmd: [npm, test]\n    source: auto-detected\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is not None
        assert len(result.commands) == 1
        assert result.commands[0].role == "test"
        assert result.commands[0].cmd == ["npm", "test"]

    def test_coverage_threshold_ignored_with_info_log(self, tmp_path):
        """coverage_threshold is not enforced — loader ignores it gracefully."""
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: []\n"
            "commands: []\ncoverage_threshold: 80.0\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is not None
        # Field removed from dataclass — should load without error
        assert not hasattr(result, "coverage_threshold")

    def test_returns_none_on_invalid_yaml(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text("{invalid: yaml: [")
        result = load_verify_config(str(tmp_path))
        assert result is None

    def test_returns_none_on_wrong_version(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 99\ndetected_at: '2026-01-01T00:00:00Z'\nstack: []\ncommands: []\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is None

    def test_returns_none_when_not_a_dict(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text("- just\n- a\n- list\n")
        result = load_verify_config(str(tmp_path))
        assert result is None

    def test_skips_command_with_invalid_role(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: []\n"
            "commands:\n  - role: invalid_role\n    cmd: [foo]\n    source: user\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is not None
        assert result.commands == []

    def test_skips_non_dict_command_entry(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: []\n"
            "commands:\n  - just a string\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is not None
        assert result.commands == []

    def test_skips_command_with_empty_cmd(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: []\n"
            "commands:\n  - role: test\n    cmd: []\n    source: user\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is not None
        assert result.commands == []

    def test_unknown_source_becomes_user(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: []\n"
            "commands:\n  - role: test\n    cmd: [pytest]\n    source: unknown-source\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is not None
        assert result.commands[0].source == "user"

    def test_path_traversal_symlink_rejected(self, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        evil = other / "evil.yaml"
        evil.write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: []\ncommands: []\n"
        )
        golem_dir = tmp_path / ".golem"
        golem_dir.mkdir()
        (golem_dir / "verify.yaml").symlink_to(evil)
        result = load_verify_config(str(tmp_path))
        assert result is None

    def test_resolve_outside_root_rejected(self, tmp_path):
        """Non-symlink path that resolves outside root is rejected."""
        from pathlib import Path
        from unittest.mock import patch

        from golem.verify_config import _resolve_config_path

        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text("version: 1\n")

        outside = Path("/outside/evil.yaml")
        orig_resolve = Path.resolve

        def mock_resolve(self_path, *a, **kw):
            if str(self_path).endswith("verify.yaml"):
                return outside
            return orig_resolve(self_path, *a, **kw)

        with (
            patch.object(Path, "resolve", mock_resolve),
            patch.object(Path, "is_symlink", return_value=False),
        ):
            result = _resolve_config_path(str(tmp_path))
        assert result is None

    def test_loads_command_with_timeout(self, tmp_path):
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 1\ndetected_at: '2026-01-01T00:00:00Z'\nstack: []\n"
            "commands:\n  - role: test\n    cmd: [cargo, test]\n"
            "    source: auto-detected\n    timeout: 600\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is not None
        assert result.commands[0].timeout == 600


class TestSaveVerifyConfig:
    def test_creates_golem_dir(self, tmp_path):
        cfg = VerifyConfig(
            version=1, commands=[], detected_at="2026-04-05T00:00:00Z", stack=["python"]
        )
        save_verify_config(str(tmp_path), cfg)
        assert (tmp_path / ".golem" / "verify.yaml").exists()

    def test_refuses_golem_dir_resolving_outside_root(self, tmp_path, monkeypatch):
        """save_verify_config refuses if .golem resolves outside repo root."""
        golem_dir = tmp_path / ".golem"
        golem_dir.mkdir()

        cfg = VerifyConfig(version=1, commands=[], detected_at="", stack=[])
        outside = tmp_path.parent / "elsewhere"
        real_resolve = Path.resolve

        def _fake_resolve(self):
            if self == golem_dir:
                return outside
            return real_resolve(self)

        monkeypatch.setattr(Path, "resolve", _fake_resolve)
        save_verify_config(str(tmp_path), cfg)
        monkeypatch.undo()
        # Must NOT create the file
        assert not (golem_dir / "verify.yaml").exists()

    def test_refuses_to_write_symlinked_golem_dir(self, tmp_path):
        """save_verify_config refuses if .golem directory is a symlink."""
        external_dir = tmp_path / "external"
        external_dir.mkdir()
        (tmp_path / ".golem").symlink_to(external_dir)

        cfg = VerifyConfig(version=1, commands=[], detected_at="", stack=[])
        save_verify_config(str(tmp_path), cfg)
        # Must NOT create verify.yaml in the symlink target
        assert not (external_dir / "verify.yaml").exists()

    def test_refuses_to_write_symlink(self, tmp_path):
        """save_verify_config refuses if .golem/verify.yaml is a symlink."""
        golem_dir = tmp_path / ".golem"
        golem_dir.mkdir()
        target = tmp_path / "external.yaml"
        target.write_text("original", encoding="utf-8")
        (golem_dir / "verify.yaml").symlink_to(target)

        cfg = VerifyConfig(
            version=1, commands=[], detected_at="", stack=[]
        )
        save_verify_config(str(tmp_path), cfg)
        # External file must NOT be overwritten
        assert target.read_text(encoding="utf-8") == "original"

    def test_refuses_to_write_outside_repo(self, tmp_path, monkeypatch):
        """save_verify_config refuses if resolved path escapes repo root."""
        golem_dir = tmp_path / ".golem"
        golem_dir.mkdir()
        cfg_path = golem_dir / "verify.yaml"
        cfg_path.write_text("original", encoding="utf-8")

        cfg = VerifyConfig(
            version=1, commands=[], detected_at="", stack=[]
        )
        # Patch relative_to to simulate resolving outside root
        real_relative_to = Path.relative_to

        def _fake_relative_to(self, other):
            if self == cfg_path.resolve() and other == tmp_path.resolve():
                raise ValueError("outside repo")
            return real_relative_to(self, other)

        monkeypatch.setattr(Path, "relative_to", _fake_relative_to)
        save_verify_config(str(tmp_path), cfg)
        monkeypatch.undo()
        # File must NOT be overwritten
        assert cfg_path.read_text(encoding="utf-8") == "original"

    def test_round_trip(self, tmp_path):
        cmd = VerifyCommand(role="test", cmd=["pytest"], source="auto-detected")
        cfg = VerifyConfig(
            version=1,
            commands=[cmd],
            detected_at="2026-04-05T00:00:00Z",
            stack=["python"],
        )
        save_verify_config(str(tmp_path), cfg)
        loaded = load_verify_config(str(tmp_path))
        assert loaded is not None
        assert len(loaded.commands) == 1
        assert loaded.commands[0].cmd == ["pytest"]
        assert loaded.stack == ["python"]

    def test_coverage_threshold_not_round_tripped(self, tmp_path):
        """coverage_threshold is removed from schema — not persisted."""
        cfg = VerifyConfig(
            version=1,
            commands=[],
            detected_at="2026-04-05T00:00:00Z",
            stack=[],
        )
        save_verify_config(str(tmp_path), cfg)
        loaded = load_verify_config(str(tmp_path))
        assert loaded is not None
        assert not hasattr(loaded, "coverage_threshold")

    def test_invalid_timeout_ignored_gracefully(self, tmp_path):
        """Non-numeric timeout does not crash — command loads with timeout=None."""
        cfg_path = tmp_path / ".golem" / "verify.yaml"
        cfg_path.parent.mkdir()
        cfg_path.write_text(
            "version: 1\ndetected_at: ''\nstack: []\n"
            "commands:\n  - role: test\n    cmd: [pytest]\n"
            "    source: user\n    timeout: fast\n"
        )
        result = load_verify_config(str(tmp_path))
        assert result is not None
        assert len(result.commands) == 1
        assert result.commands[0].timeout is None

    def test_round_trip_with_timeout(self, tmp_path):
        cmd = VerifyCommand(
            role="test", cmd=["cargo", "test"], source="auto-detected", timeout=600
        )
        cfg = VerifyConfig(
            version=1,
            commands=[cmd],
            detected_at="2026-04-05T00:00:00Z",
            stack=["rust"],
        )
        save_verify_config(str(tmp_path), cfg)
        loaded = load_verify_config(str(tmp_path))
        assert loaded is not None
        assert loaded.commands[0].timeout == 600

    def test_to_dict_excludes_none_timeout(self):
        cmd = VerifyCommand(role="test", cmd=["pytest"], source="auto-detected")
        d = cmd.to_dict()
        assert "timeout" not in d

    def test_to_dict_includes_timeout_when_set(self):
        cmd = VerifyCommand(
            role="test", cmd=["pytest"], source="auto-detected", timeout=300
        )
        d = cmd.to_dict()
        assert d["timeout"] == 300
