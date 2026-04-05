"""Tests for golem.repo_registry."""

import json

from golem.repo_registry import RepoRegistry


class TestRepoRegistryAttachDetach:
    """Core attach/detach/list operations."""

    def test_attach_creates_entry(self, tmp_path):
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg.attach("/home/user/projects/foo")
        repos = reg.list_repos()
        assert len(repos) == 1
        assert repos[0]["path"] == "/home/user/projects/foo"
        assert repos[0]["heartbeat"] is True
        assert "attached_at" in repos[0]

    def test_attach_no_heartbeat(self, tmp_path):
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg.attach("/home/user/projects/foo", heartbeat=False)
        repos = reg.list_repos()
        assert repos[0]["heartbeat"] is False

    def test_attach_idempotent_updates_settings(self, tmp_path):
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg.attach("/home/user/projects/foo", heartbeat=False)
        reg.attach("/home/user/projects/foo", heartbeat=True)
        repos = reg.list_repos()
        assert len(repos) == 1
        assert repos[0]["heartbeat"] is True

    def test_attach_normalizes_path(self, tmp_path):
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg.attach("/home/user/projects/foo/")
        repos = reg.list_repos()
        assert repos[0]["path"] == "/home/user/projects/foo"

    def test_attach_root_path_preserved(self, tmp_path):
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg.attach("/")
        repos = reg.list_repos()
        assert repos[0]["path"] == "/"

    def test_detach_removes_entry(self, tmp_path):
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg.attach("/home/user/projects/foo")
        reg.attach("/home/user/projects/bar")
        removed = reg.detach("/home/user/projects/foo")
        assert removed is True
        repos = reg.list_repos()
        assert len(repos) == 1
        assert repos[0]["path"] == "/home/user/projects/bar"

    def test_detach_nonexistent_returns_false(self, tmp_path):
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        removed = reg.detach("/home/user/projects/nope")
        assert removed is False

    def test_heartbeat_repos_filters(self, tmp_path):
        reg = RepoRegistry(registry_path=tmp_path / "repos.json")
        reg.attach("/home/user/projects/a", heartbeat=True)
        reg.attach("/home/user/projects/b", heartbeat=False)
        reg.attach("/home/user/projects/c", heartbeat=True)
        hb = reg.heartbeat_repos()
        assert len(hb) == 2
        paths = {r["path"] for r in hb}
        assert paths == {"/home/user/projects/a", "/home/user/projects/c"}


class TestRepoRegistryPersistence:
    """Save/load round-trip and file handling."""

    def test_save_load_round_trip(self, tmp_path):
        path = tmp_path / "repos.json"
        reg1 = RepoRegistry(registry_path=path)
        reg1.attach("/home/user/projects/foo")

        reg2 = RepoRegistry(registry_path=path)
        repos = reg2.list_repos()
        assert len(repos) == 1
        assert repos[0]["path"] == "/home/user/projects/foo"

    def test_load_missing_file_is_empty(self, tmp_path):
        reg = RepoRegistry(registry_path=tmp_path / "nope.json")
        assert reg.list_repos() == []

    def test_load_corrupt_json_is_empty(self, tmp_path):
        path = tmp_path / "repos.json"
        path.write_text("not json{{{")
        reg = RepoRegistry(registry_path=path)
        assert reg.list_repos() == []

    def test_load_missing_repos_key_is_empty(self, tmp_path):
        path = tmp_path / "repos.json"
        path.write_text(json.dumps({"version": 1}))
        reg = RepoRegistry(registry_path=path)
        assert reg.list_repos() == []

    def test_save_creates_parent_dirs(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "repos.json"
        reg = RepoRegistry(registry_path=path)
        reg.attach("/home/user/projects/foo")
        assert path.exists()

    def test_attach_auto_saves(self, tmp_path):
        path = tmp_path / "repos.json"
        reg = RepoRegistry(registry_path=path)
        reg.attach("/home/user/projects/foo")
        data = json.loads(path.read_text())
        assert len(data["repos"]) == 1

    def test_detach_auto_saves(self, tmp_path):
        path = tmp_path / "repos.json"
        reg = RepoRegistry(registry_path=path)
        reg.attach("/home/user/projects/foo")
        reg.detach("/home/user/projects/foo")
        data = json.loads(path.read_text())
        assert len(data["repos"]) == 0


class TestRepoRegistryEnvOverride:
    """GOLEM_REGISTRY_PATH env var override."""

    def test_default_path(self, monkeypatch):
        monkeypatch.delenv("GOLEM_REGISTRY_PATH", raising=False)
        reg = RepoRegistry()
        assert "golem" in str(reg._registry_path).lower()

    def test_env_override(self, tmp_path, monkeypatch):
        custom = tmp_path / "custom.json"
        monkeypatch.setenv("GOLEM_REGISTRY_PATH", str(custom))
        reg = RepoRegistry()
        assert reg._registry_path == custom


class TestRepoRegistryDetection:
    def test_attach_triggers_detection_when_enabled(self, tmp_path):
        from unittest.mock import patch

        from golem.verify_config import VerifyConfig

        mock_cfg = VerifyConfig(
            version=1, commands=[], detected_at="2026-04-05T00:00:00Z", stack=[]
        )
        with (
            patch(
                "golem.repo_registry.detect_verify_config", return_value=mock_cfg
            ) as mock_detect,
            patch("golem.repo_registry.save_verify_config") as mock_save,
        ):
            reg = RepoRegistry(registry_path=tmp_path / "repos.json")
            reg.attach(str(tmp_path), run_detection=True)
        mock_detect.assert_called_once_with(str(tmp_path), dry_run=True)
        mock_save.assert_called_once()

    def test_attach_skips_detection_by_default(self, tmp_path):
        from unittest.mock import patch

        with patch("golem.repo_registry.detect_verify_config") as mock_detect:
            reg = RepoRegistry(registry_path=tmp_path / "repos.json")
            reg.attach(str(tmp_path))
        mock_detect.assert_not_called()

    def test_attach_detection_failure_does_not_block(self, tmp_path):
        from unittest.mock import patch

        with patch(
            "golem.repo_registry.detect_verify_config",
            side_effect=OSError("disk full"),
        ):
            reg = RepoRegistry(registry_path=tmp_path / "repos.json")
            reg.attach(str(tmp_path), run_detection=True)
        assert len(reg.list_repos()) == 1

    def test_reattach_with_detection(self, tmp_path):
        from unittest.mock import patch

        from golem.verify_config import VerifyConfig

        mock_cfg = VerifyConfig(
            version=1, commands=[], detected_at="2026-04-05T00:00:00Z", stack=[]
        )
        with (
            patch("golem.repo_registry.detect_verify_config", return_value=mock_cfg),
            patch("golem.repo_registry.save_verify_config"),
        ):
            reg = RepoRegistry(registry_path=tmp_path / "repos.json")
            reg.attach(str(tmp_path))
            reg.attach(str(tmp_path), run_detection=True)
        assert len(reg.list_repos()) == 1
