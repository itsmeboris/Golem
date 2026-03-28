"""Tests for heartbeat Tier 2 batching — config validation.

Per-repo batching methods (_group_candidates, _submit_batch, _get_recent_batch_categories,
_get_recently_resolved_ids, etc.) are now on HeartbeatWorker and covered
in test_heartbeat_worker.py.  This file retains only config-level tests.
"""

from golem.core.config import GolemFlowConfig


class TestBatchSizeConfig:
    def test_default_value(self):
        cfg = GolemFlowConfig()
        assert cfg.heartbeat_batch_size == 5

    def test_parsed_from_yaml(self, tmp_path):
        from golem.core.config import load_config

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "flows:\n"
            "  golem:\n"
            "    projects: [test/repo]\n"
            "    heartbeat_enabled: true\n"
            "    heartbeat_batch_size: 3\n"
        )
        config = load_config(cfg_file)
        assert config.golem.heartbeat_batch_size == 3

    def test_validation_rejects_zero(self):
        from golem.core.config import Config, validate_config

        cfg = Config(
            golem=GolemFlowConfig(
                projects=["test/repo"],
                heartbeat_enabled=True,
                heartbeat_batch_size=0,
            )
        )
        errors = validate_config(cfg)
        assert any("heartbeat_batch_size" in e for e in errors)


class TestHeartbeatRecentCommitsLookback:
    def test_default_value(self):
        cfg = GolemFlowConfig()
        assert cfg.heartbeat_recent_commits_lookback == 20

    def test_parsed_from_yaml(self, tmp_path):
        from golem.core.config import load_config

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "flows:\n"
            "  golem:\n"
            "    projects: [test/repo]\n"
            "    heartbeat_enabled: true\n"
            "    heartbeat_recent_commits_lookback: 50\n"
        )
        config = load_config(cfg_file)
        assert config.golem.heartbeat_recent_commits_lookback == 50

    def test_validation_rejects_zero(self):
        from golem.core.config import Config, validate_config

        cfg = Config(
            golem=GolemFlowConfig(
                projects=["test/repo"],
                heartbeat_enabled=True,
                heartbeat_recent_commits_lookback=0,
            )
        )
        errors = validate_config(cfg)
        assert any("heartbeat_recent_commits_lookback" in e for e in errors)

    def test_validation_rejects_negative(self):
        from golem.core.config import Config, validate_config

        cfg = Config(
            golem=GolemFlowConfig(
                projects=["test/repo"],
                heartbeat_enabled=True,
                heartbeat_recent_commits_lookback=-5,
            )
        )
        errors = validate_config(cfg)
        assert any("heartbeat_recent_commits_lookback" in e for e in errors)
