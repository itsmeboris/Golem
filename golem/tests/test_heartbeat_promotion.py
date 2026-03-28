"""Tests for heartbeat Tier 1 promotion config.

Per-repo promotion state, _run_tier1_promoted, _submit_promoted, and the
tier2-completion counter now live on HeartbeatWorker and are tested in
test_heartbeat_worker.py.  This file retains only config-level tests.
"""

from golem.core.config import GolemFlowConfig


class TestTier1PromotionConfig:
    def test_default_value(self):
        cfg = GolemFlowConfig()
        assert cfg.heartbeat_tier1_every_n == 3

    def test_parsed_from_yaml(self, tmp_path):
        from golem.core.config import load_config

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "flows:\n"
            "  golem:\n"
            "    projects: [test/repo]\n"
            "    heartbeat_enabled: true\n"
            "    heartbeat_tier1_every_n: 5\n"
        )
        config = load_config(cfg_file)
        assert config.golem.heartbeat_tier1_every_n == 5

    def test_validation_rejects_zero(self):
        from golem.core.config import Config, validate_config

        cfg = Config(
            golem=GolemFlowConfig(
                projects=["test/repo"],
                heartbeat_enabled=True,
                heartbeat_tier1_every_n=0,
            )
        )
        errors = validate_config(cfg)
        assert any("heartbeat_tier1_every_n" in e for e in errors)
