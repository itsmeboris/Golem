"""Tests for MCP scope, KeywordToolProvider, and max_mcp_servers config."""

import logging
from unittest.mock import MagicMock, patch

import pytest

from golem.backends.mcp_tools import KeywordToolProvider
from golem.core.config import Config, GolemFlowConfig, validate_config
from golem.mcp_scope import determine_mcp_scope

# ---------------------------------------------------------------------------
# determine_mcp_scope — parametrized keyword matching
# ---------------------------------------------------------------------------


class TestDetermineMcpScope:
    @pytest.mark.parametrize(
        "subject,expected_servers",
        [
            ("Some generic task", []),
            ("Investigate Jenkins CI failure", ["jenkins"]),
            ("fix the build pipeline", ["jenkins"]),
            ("ci configuration update", ["jenkins"]),
            ("Review the gerrit change", ["gerrit"]),
            ("Update the wiki documentation", ["confluence"]),
            ("Fix confluence document layout", ["confluence"]),
            ("create a redmine ticket", ["redmine"]),
            ("check the issue tracker", ["redmine"]),
            ("resolve this ticket", ["redmine"]),
        ],
    )
    def test_keyword_matching(self, subject, expected_servers):
        result = determine_mcp_scope(subject)
        assert result == sorted(expected_servers)

    def test_multiple_keywords_returns_all_servers(self):
        result = determine_mcp_scope("Jenkins build review on gerrit")
        assert "jenkins" in result
        assert "gerrit" in result

    def test_empty_subject_returns_empty(self):
        result = determine_mcp_scope("")
        assert result == []

    def test_returns_sorted_list(self):
        result = determine_mcp_scope("Jenkins build review on gerrit")
        assert result == sorted(result)

    def test_case_insensitive(self):
        lower = determine_mcp_scope("jenkins ci failure")
        upper = determine_mcp_scope("JENKINS CI FAILURE")
        assert lower == upper


# ---------------------------------------------------------------------------
# KeywordToolProvider — role-based filtering (SPEC-4, SPEC-5)
# ---------------------------------------------------------------------------


class TestKeywordToolProviderRoleFiltering:
    def test_no_role_returns_all_servers(self):
        tp = KeywordToolProvider(
            base_servers=["base"],
            keyword_servers={"build": ["jenkins"]},
        )
        result = tp.servers_for_subject("build a thing")
        assert "base" in result
        assert "jenkins" in result

    def test_empty_role_returns_all_servers(self):
        """Empty role string means no filtering."""
        tp = KeywordToolProvider(
            base_servers=["base"],
            keyword_servers={"build": ["jenkins"]},
            role_servers={"builder": ["jenkins"], "reviewer": []},
        )
        result = tp.servers_for_subject("build a thing", role="")
        assert "base" in result
        assert "jenkins" in result

    def test_role_filters_to_allowed_servers(self):
        """When role is provided and in role_servers, restrict to allowed servers."""
        tp = KeywordToolProvider(
            base_servers=["base"],
            keyword_servers={"build": ["jenkins", "extra"]},
            role_servers={"builder": ["jenkins"]},
        )
        result = tp.servers_for_subject("build a thing", role="builder")
        assert "jenkins" in result
        assert "extra" not in result

    def test_role_with_empty_allowed_list_returns_empty(self):
        """A role mapped to [] means no servers for that role."""
        tp = KeywordToolProvider(
            base_servers=["base"],
            keyword_servers={"build": ["jenkins"]},
            role_servers={"reviewer": []},
        )
        result = tp.servers_for_subject("build a thing", role="reviewer")
        assert result == []

    def test_role_not_in_role_servers_returns_all(self):
        """Role not in role_servers mapping => no filtering, return all."""
        tp = KeywordToolProvider(
            base_servers=["base"],
            keyword_servers={"build": ["jenkins"]},
            role_servers={"builder": ["jenkins"]},
        )
        # "verifier" not in role_servers
        result = tp.servers_for_subject("build a thing", role="verifier")
        assert "base" in result
        assert "jenkins" in result

    def test_role_filtering_without_role_servers_configured(self):
        """When role_servers is None, role param is ignored (no filtering)."""
        tp = KeywordToolProvider(
            base_servers=["base"],
            keyword_servers={"build": ["jenkins"]},
        )
        result = tp.servers_for_subject("build a thing", role="builder")
        assert "base" in result
        assert "jenkins" in result

    def test_default_provider_accepts_role_kwarg(self):
        """Default (delegating) provider must accept role kwarg without error."""
        tp = KeywordToolProvider()
        result = tp.servers_for_subject("Jenkins CI failure", role="builder")
        assert "jenkins" in result


# ---------------------------------------------------------------------------
# KeywordToolProvider — max_servers truncation (SPEC-3)
# ---------------------------------------------------------------------------


class TestKeywordToolProviderMaxServers:
    def test_no_truncation_when_under_limit(self):
        tp = KeywordToolProvider(
            base_servers=["a", "b"],
            keyword_servers={},
            max_servers=5,
        )
        result = tp.servers_for_subject("some task")
        assert result == ["a", "b"]

    def test_truncation_when_over_limit(self):
        tp = KeywordToolProvider(
            base_servers=["a", "b", "c", "d"],
            keyword_servers={},
            max_servers=2,
        )
        result = tp.servers_for_subject("some task")
        assert len(result) == 2

    def test_zero_max_servers_means_no_limit(self):
        tp = KeywordToolProvider(
            base_servers=["a", "b", "c"],
            keyword_servers={},
            max_servers=0,
        )
        result = tp.servers_for_subject("some task")
        assert len(result) == 3

    def test_truncation_logs_warning(self, caplog):
        tp = KeywordToolProvider(
            base_servers=["a", "b", "c", "d"],
            keyword_servers={},
            max_servers=2,
        )
        with caplog.at_level(logging.WARNING, logger="golem.backends.mcp_tools"):
            result = tp.servers_for_subject("some task")
        assert len(result) == 2
        assert any("truncat" in r.message.lower() for r in caplog.records)

    def test_truncation_keeps_first_n(self):
        tp = KeywordToolProvider(
            base_servers=["x", "y", "z"],
            keyword_servers={},
            max_servers=2,
        )
        result = tp.servers_for_subject("some task")
        # sorted base servers are x, y, z — first 2 are x, y
        full = sorted(["x", "y", "z"])
        assert result == full[:2]


# ---------------------------------------------------------------------------
# GolemFlowConfig — max_mcp_servers field (SPEC-2)
# ---------------------------------------------------------------------------


class TestGolemFlowConfigMaxMcpServers:
    def test_default_max_mcp_servers_is_10(self):
        cfg = GolemFlowConfig()
        assert cfg.max_mcp_servers == 10

    def test_max_mcp_servers_can_be_set(self):
        cfg = GolemFlowConfig(max_mcp_servers=5)
        assert cfg.max_mcp_servers == 5

    def test_validate_config_rejects_zero(self):
        cfg = Config(golem=GolemFlowConfig(projects=["p"], max_mcp_servers=0))
        errors = validate_config(cfg)
        assert any("max_mcp_servers" in e for e in errors)

    def test_validate_config_rejects_negative(self):
        cfg = Config(golem=GolemFlowConfig(projects=["p"], max_mcp_servers=-1))
        errors = validate_config(cfg)
        assert any("max_mcp_servers" in e for e in errors)

    def test_validate_config_accepts_positive(self):
        cfg = Config(golem=GolemFlowConfig(projects=["p"], max_mcp_servers=5))
        errors = validate_config(cfg)
        assert not any("max_mcp_servers" in e for e in errors)

    def test_validate_config_accepts_one(self):
        cfg = Config(golem=GolemFlowConfig(projects=["p"], max_mcp_servers=1))
        errors = validate_config(cfg)
        assert not any("max_mcp_servers" in e for e in errors)


# ---------------------------------------------------------------------------
# _parse_golem_config — max_mcp_servers parsed from YAML data (SPEC-2)
# ---------------------------------------------------------------------------


class TestParseGolemConfigMaxMcpServers:
    def test_parses_max_mcp_servers(self):
        from golem.core.config import _parse_golem_config

        cfg = _parse_golem_config({"max_mcp_servers": 7})
        assert cfg.max_mcp_servers == 7

    def test_defaults_to_10_when_missing(self):
        from golem.core.config import _parse_golem_config

        cfg = _parse_golem_config({})
        assert cfg.max_mcp_servers == 10


# ---------------------------------------------------------------------------
# SubagentSupervisor — logging and max enforcement (SPEC-1, SPEC-3)
# ---------------------------------------------------------------------------


class TestSupervisorGetMcpServers:
    def _make_supervisor(self, servers, max_mcp_servers=10):
        from golem.orchestrator import TaskSession
        from golem.supervisor_v2_subagent import SubagentSupervisor

        session = TaskSession(parent_issue_id=1, parent_subject="Test")
        task_config = GolemFlowConfig(max_mcp_servers=max_mcp_servers)
        profile = MagicMock()
        profile.tool_provider.servers_for_subject.return_value = servers
        sup = SubagentSupervisor(
            session=session,
            config=MagicMock(),
            task_config=task_config,
            profile=profile,
        )
        return sup

    def test_logs_server_count_at_info(self, caplog):
        sup = self._make_supervisor(["jenkins", "gerrit"])
        with caplog.at_level(logging.INFO, logger="golem.supervisor_v2_subagent"):
            result = sup._get_mcp_servers("fix jenkins ci")
        assert result == ["jenkins", "gerrit"]
        messages = [r.message for r in caplog.records]
        assert any("jenkins" in m and "gerrit" in m for m in messages)

    def test_logs_include_subject(self, caplog):
        sup = self._make_supervisor(["jenkins"])
        with caplog.at_level(logging.INFO, logger="golem.supervisor_v2_subagent"):
            sup._get_mcp_servers("fix the build")
        messages = [r.message for r in caplog.records]
        assert any("fix the build" in m for m in messages)

    def test_truncates_when_over_max(self):
        servers = [f"server{i}" for i in range(15)]
        sup = self._make_supervisor(servers, max_mcp_servers=5)
        result = sup._get_mcp_servers("some task")
        assert len(result) == 5
        assert result == servers[:5]

    def test_no_truncation_when_under_max(self):
        servers = ["a", "b", "c"]
        sup = self._make_supervisor(servers, max_mcp_servers=10)
        result = sup._get_mcp_servers("some task")
        assert result == servers

    def test_truncation_logs_warning(self, caplog):
        servers = [f"s{i}" for i in range(12)]
        sup = self._make_supervisor(servers, max_mcp_servers=5)
        with caplog.at_level(logging.WARNING, logger="golem.supervisor_v2_subagent"):
            result = sup._get_mcp_servers("some task")
        assert len(result) == 5
        warning_msgs = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert any("truncat" in m.lower() for m in warning_msgs)

    def test_max_zero_means_no_limit(self):
        servers = [f"s{i}" for i in range(20)]
        sup = self._make_supervisor(servers, max_mcp_servers=0)
        result = sup._get_mcp_servers("some task")
        assert len(result) == 20


# ---------------------------------------------------------------------------
# ToolProvider protocol — role kwarg (SPEC-4)
# ---------------------------------------------------------------------------


class TestToolProviderProtocolWithRole:
    def test_null_tool_provider_accepts_role(self):
        from golem.backends.local import NullToolProvider

        provider = NullToolProvider()
        result = provider.servers_for_subject("some task", role="builder")
        assert result == []

    def test_dummy_in_test_interfaces_accepts_role(self):
        """The DummyToolProvider in test_interfaces must accept role kwarg."""
        from golem.tests.test_interfaces import DummyToolProvider

        dummy = DummyToolProvider()
        result = dummy.servers_for_subject("some task", role="reviewer")
        assert result == []
