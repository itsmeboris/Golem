"""Integration tests for the golem profile system.

Tests cover:
- Profile building and registration
- Backend protocol compliance (runtime_checkable)
- Local backend (LocalFileTaskSource, NullStateBackend, LogNotifier, NullToolProvider)
- Profile switching via config
- Flow lifecycle with profile injection
- Orchestrator profile-aware helpers
"""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access,too-many-lines

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from golem.core.config import Config, GolemFlowConfig, SlackConfig, TeamsConfig

# ---------------------------------------------------------------------------
# Helpers — build a null-backends profile inline (no registry needed)
# ---------------------------------------------------------------------------


def _make_test_profile(tasks_dir="./tasks", name="test"):
    """Build a GolemProfile with null backends for testing."""
    from golem.backends.local import (
        LocalFileTaskSource,
        LogNotifier,
        NullStateBackend,
        NullToolProvider,
    )
    from golem.profile import GolemProfile
    from golem.prompts import FilePromptProvider

    return GolemProfile(
        name=name,
        task_source=LocalFileTaskSource(tasks_dir),
        state_backend=NullStateBackend(),
        notifier=LogNotifier(),
        tool_provider=NullToolProvider(),
        prompt_provider=FilePromptProvider(None),
    )


# ---------------------------------------------------------------------------
# Profile build / registry
# ---------------------------------------------------------------------------


class TestProfileRegistry:
    def test_build_redmine_profile(self):
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig())
        profile = build_profile("redmine", config)
        assert profile.name == "redmine"
        assert profile.task_source is not None
        assert profile.state_backend is not None
        assert profile.notifier is not None
        assert profile.tool_provider is not None
        assert profile.prompt_provider is not None

    def test_unknown_profile_raises(self):
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig())
        with pytest.raises(ValueError, match="Unknown golem profile"):
            build_profile("nonexistent_xyz", config)

    def test_available_profiles_lists_builtins(self):
        from golem.profile import available_profiles, build_profile

        # Trigger lazy registration
        config = Config(golem=GolemFlowConfig())
        build_profile("redmine", config)
        names = available_profiles()
        assert "redmine" in names

    def test_ensure_builtins_registers_on_empty(self):
        import sys

        from golem.profile import (
            _PROFILE_FACTORIES,
            _ensure_builtins_registered,
        )

        saved = dict(_PROFILE_FACTORIES)
        saved_mod = sys.modules.pop("golem.backends.profiles", None)
        _PROFILE_FACTORIES.clear()
        try:
            _ensure_builtins_registered()
            assert "redmine" in _PROFILE_FACTORIES
        finally:
            _PROFILE_FACTORIES.clear()
            _PROFILE_FACTORIES.update(saved)
            if saved_mod is not None:
                sys.modules["golem.backends.profiles"] = saved_mod

    def test_custom_profile_registration(self):
        from golem.profile import (
            GolemProfile,
            _PROFILE_FACTORIES,
            register_profile,
            build_profile,
        )
        from golem.backends.local import (
            LocalFileTaskSource,
            LogNotifier,
            NullStateBackend,
            NullToolProvider,
        )
        from golem.prompts import FilePromptProvider

        def _custom_factory(_cfg):
            return GolemProfile(
                name="custom_test",
                task_source=LocalFileTaskSource("/tmp/tasks"),
                state_backend=NullStateBackend(),
                notifier=LogNotifier(),
                tool_provider=NullToolProvider(),
                prompt_provider=FilePromptProvider(),
            )

        register_profile("custom_test", _custom_factory)
        try:
            config = Config(golem=GolemFlowConfig())
            profile = build_profile("custom_test", config)
            assert profile.name == "custom_test"
        finally:
            _PROFILE_FACTORIES.pop("custom_test", None)


# ---------------------------------------------------------------------------
# Protocol compliance (runtime_checkable)
# ---------------------------------------------------------------------------


class TestProtocolCompliance:
    def test_redmine_backends_satisfy_protocols(self):
        from golem.interfaces import (
            Notifier,
            PromptProvider,
            StateBackend,
            TaskSource,
            ToolProvider,
        )
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig())
        profile = build_profile("redmine", config)

        assert isinstance(profile.task_source, TaskSource)
        assert isinstance(profile.state_backend, StateBackend)
        assert isinstance(profile.notifier, Notifier)
        assert isinstance(profile.tool_provider, ToolProvider)
        assert isinstance(profile.prompt_provider, PromptProvider)

    def test_null_backends_satisfy_protocols(self):
        from golem.interfaces import (
            Notifier,
            PromptProvider,
            StateBackend,
            TaskSource,
            ToolProvider,
        )

        profile = _make_test_profile()

        assert isinstance(profile.task_source, TaskSource)
        assert isinstance(profile.state_backend, StateBackend)
        assert isinstance(profile.notifier, Notifier)
        assert isinstance(profile.tool_provider, ToolProvider)
        assert isinstance(profile.prompt_provider, PromptProvider)


# ---------------------------------------------------------------------------
# Local backends — functional tests
# ---------------------------------------------------------------------------


class TestLocalFileTaskSource:
    def test_poll_tasks_finds_agent_tagged(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001.json").write_text(
            json.dumps({"id": "001", "subject": "[AGENT] Refactor config parser"})
        )
        (tasks_dir / "002.json").write_text(
            json.dumps({"id": "002", "subject": "Regular task without tag"})
        )

        src = LocalFileTaskSource(tasks_dir)
        results = src.poll_tasks(["any"], detection_tag="[AGENT]")
        assert len(results) == 1
        assert results[0]["id"] == "001"
        assert "[AGENT]" in results[0]["subject"]

    def test_poll_tasks_empty_dir(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        src = LocalFileTaskSource(tmp_path / "nonexistent")
        results = src.poll_tasks(["any"], detection_tag="[AGENT]")
        assert not results

    def test_get_task_description(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "001.json").write_text(
            json.dumps(
                {
                    "id": "001",
                    "subject": "[AGENT] Test",
                    "description": "Detailed description here.",
                }
            )
        )

        src = LocalFileTaskSource(tasks_dir)
        desc = src.get_task_description("001")
        assert desc == "Detailed description here."

    def test_get_task_description_not_found(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        src = LocalFileTaskSource(tmp_path)
        assert src.get_task_description("999") == ""

    def test_get_child_tasks(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "parent.json").write_text(
            json.dumps(
                {
                    "id": "parent",
                    "subject": "[AGENT] Parent task",
                    "children": [
                        {"id": "child-1", "subject": "Sub 1"},
                        {"id": "child-2", "subject": "Sub 2"},
                    ],
                }
            )
        )

        src = LocalFileTaskSource(tasks_dir)
        children = src.get_child_tasks("parent")
        assert len(children) == 2
        assert children[0]["id"] == "child-1"

    def test_create_child_task(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()

        src = LocalFileTaskSource(tasks_dir)
        child_id = src.create_child_task("parent", "Sub task", "Do X")
        assert child_id is not None
        # Verify file was created
        files = list(tasks_dir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["subject"] == "Sub task"
        assert data["parent_id"] == "parent"


class TestNullStateBackend:
    def test_update_status_returns_true(self):
        from golem.backends.local import NullStateBackend

        backend = NullStateBackend()
        assert backend.update_status("123", "in_progress") is True

    def test_post_comment_returns_true(self):
        from golem.backends.local import NullStateBackend

        backend = NullStateBackend()
        assert backend.post_comment("123", "Some comment") is True

    def test_update_progress_returns_true(self):
        from golem.backends.local import NullStateBackend

        backend = NullStateBackend()
        assert backend.update_progress("123", 50) is True


class TestLogNotifier:
    def test_notify_started_does_not_raise(self):
        from golem.backends.local import LogNotifier

        n = LogNotifier()
        n.notify_started("123", "Test task")  # should not raise

    def test_notify_completed_does_not_raise(self):
        from golem.backends.local import LogNotifier

        n = LogNotifier()
        n.notify_completed("123", "Test task", cost_usd=1.5)

    def test_notify_failed_does_not_raise(self):
        from golem.backends.local import LogNotifier

        n = LogNotifier()
        n.notify_failed("123", "Test task", "Budget exceeded")

    def test_notify_escalated_does_not_raise(self):
        from golem.backends.local import LogNotifier

        n = LogNotifier()
        n.notify_escalated("123", "Test task", "FAIL", "Agent could not fix")


class TestSlackNotifier:
    def _make_notifier(self):
        from golem.backends.slack_notifier import SlackNotifier

        client = MagicMock()
        return SlackNotifier(client), client

    def test_notify_started(self):
        notifier, client = self._make_notifier()
        notifier.notify_started("42", "Test task")
        client.send_to_channel.assert_called_once()
        payload = client.send_to_channel.call_args[0][1]
        assert "blocks" in payload
        assert "text" in payload
        assert "#42" in payload["text"]

    def test_notify_completed(self):
        notifier, client = self._make_notifier()
        notifier.notify_completed(
            "42",
            "Test task",
            cost_usd=1.5,
            duration_s=60,
            verdict="PASS",
            confidence=0.95,
            commit_sha="abc123",
            retry_count=1,
            fix_iteration=2,
        )
        client.send_to_channel.assert_called_once()
        payload = client.send_to_channel.call_args[0][1]
        text = json.dumps(payload["blocks"])
        assert "$1.50" in text
        assert "PASS" in text
        assert "`abc123`" in text
        assert "Fix iterations" in text
        assert "Full retries" in text

    def test_notify_completed_with_concerns(self):
        notifier, client = self._make_notifier()
        notifier.notify_completed(
            "42", "Test", concerns=["concern A", "concern B"], cost_usd=0
        )
        payload = client.send_to_channel.call_args[0][1]
        text = json.dumps(payload["blocks"])
        assert "concern A" in text
        assert "concern B" in text

    def test_notify_failed(self):
        notifier, client = self._make_notifier()
        notifier.notify_failed("42", "Test task", "Budget exceeded")
        client.send_to_channel.assert_called_once()
        payload = client.send_to_channel.call_args[0][1]
        assert any("Budget exceeded" in str(b) for b in payload["blocks"])

    def test_notify_escalated(self):
        notifier, client = self._make_notifier()
        notifier.notify_escalated("42", "Test task", "FAIL", "Agent could not resolve")
        client.send_to_channel.assert_called_once()
        payload = client.send_to_channel.call_args[0][1]
        text = json.dumps(payload["blocks"])
        assert "FAIL" in text
        assert "Agent could not resolve" in text

    def test_notify_escalated_with_concerns(self):
        notifier, client = self._make_notifier()
        notifier.notify_escalated(
            "42",
            "Test",
            "FAIL",
            "summary",
            concerns=["c1"],
            cost_usd=2.0,
            retry_count=1,
            fix_iteration=2,
        )
        payload = client.send_to_channel.call_args[0][1]
        text = json.dumps(payload["blocks"])
        assert "c1" in text
        assert "Full retries" in text
        assert "Fix iterations" in text

    def test_send_failure_does_not_raise(self):
        notifier, client = self._make_notifier()
        client.send_to_channel.side_effect = RuntimeError("connection failed")
        notifier.notify_started("42", "Test task")

    def test_satisfies_notifier_protocol(self):
        from golem.interfaces import Notifier

        notifier, _ = self._make_notifier()
        assert isinstance(notifier, Notifier)


class TestNullToolProvider:
    def test_base_servers_empty(self):
        from golem.backends.local import NullToolProvider

        tp = NullToolProvider()
        assert not tp.base_servers()

    def test_servers_for_subject_empty(self):
        from golem.backends.local import NullToolProvider

        tp = NullToolProvider()
        assert not tp.servers_for_subject("Jenkins build failure")


# ---------------------------------------------------------------------------
# KeywordToolProvider
# ---------------------------------------------------------------------------


class TestKeywordToolProvider:
    def test_default_delegates_to_mcp_scope(self):
        from golem.backends.mcp_tools import KeywordToolProvider

        tp = KeywordToolProvider()
        servers = tp.servers_for_subject("Investigate Jenkins failure")
        assert "jenkins" in servers

    def test_custom_base_and_keywords(self):
        from golem.backends.mcp_tools import KeywordToolProvider

        tp = KeywordToolProvider(
            base_servers=["custom_base"],
            keyword_servers={"deploy": ["kubernetes", "docker"]},
        )
        assert tp.base_servers() == ["custom_base"]
        servers = tp.servers_for_subject("Deploy the service")
        assert "custom_base" in servers
        assert "kubernetes" in servers
        assert "docker" in servers

    def test_custom_no_keyword_match(self):
        from golem.backends.mcp_tools import KeywordToolProvider

        tp = KeywordToolProvider(
            base_servers=["base_only"],
            keyword_servers={"deploy": ["kubernetes"]},
        )
        servers = tp.servers_for_subject("Fix a bug in parser")
        assert servers == ["base_only"]


# ---------------------------------------------------------------------------
# FilePromptProvider
# ---------------------------------------------------------------------------


class TestFilePromptProvider:
    def test_default_prompts_dir(self):
        from golem.prompts import FilePromptProvider

        pp = FilePromptProvider()
        text = pp.format("run_task.txt", issue_id=42)
        assert "#42" in text

    def test_custom_prompts_dir(self, tmp_path):
        from golem.prompts import FilePromptProvider

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test.txt").write_text("Hello {name}!")

        pp = FilePromptProvider(prompts_dir)
        text = pp.format("test.txt", name="World")
        assert text == "Hello World!"

    def test_missing_template_raises(self, tmp_path):
        from golem.prompts import FilePromptProvider

        pp = FilePromptProvider(tmp_path)
        with pytest.raises(FileNotFoundError):
            pp.format("nonexistent.txt")

    def test_empty_description_fallback(self, tmp_path):
        from golem.prompts import FilePromptProvider

        tpl = tmp_path / "test.txt"
        tpl.write_text("Desc: {task_description}", encoding="utf-8")
        provider = FilePromptProvider(tmp_path)
        result = provider.format(
            "test.txt", task_description="", parent_subject="Fix Y"
        )
        assert "Fix Y" in result
        assert "{task_description}" not in result

    def test_prompts_exist(self):
        """Verify the prompts directory has all required templates."""
        prompts_dir = Path(__file__).parent.parent / "prompts"
        expected = [
            "run_task.txt",
            "orchestrate_task.txt",
            "retry_task.txt",
            "validate_task.txt",
        ]
        for name in expected:
            assert (prompts_dir / name).exists(), f"Missing prompt: {name}"

    def test_prompts_no_redmine_references(self):
        """Prompts should not reference Redmine-specific MCP tools."""
        prompts_dir = Path(__file__).parent.parent / "prompts"
        for txt_file in prompts_dir.glob("*.txt"):
            content = txt_file.read_text()
            assert (
                "redmine_get_issue" not in content
            ), f"{txt_file.name} references redmine_get_issue"
            assert (
                "redmine_update_issue" not in content
            ), f"{txt_file.name} references redmine_update_issue"


# ---------------------------------------------------------------------------
# Profile switching via config
# ---------------------------------------------------------------------------


class TestLocalProfile:
    def test_build_local_profile(self):
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig(profile="local"))
        profile = build_profile("local", config)
        assert profile.name == "local"
        from golem.backends.local import (
            LocalFileTaskSource,
            NullStateBackend,
        )

        assert isinstance(profile.task_source, LocalFileTaskSource)
        assert isinstance(profile.state_backend, NullStateBackend)

    def test_local_profile_with_mcp_enabled(self):
        from golem.backends.mcp_tools import KeywordToolProvider
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig(profile="local", mcp_enabled=True))
        profile = build_profile("local", config)
        assert isinstance(profile.tool_provider, KeywordToolProvider)

    def test_local_profile_with_mcp_disabled(self):
        from golem.backends.local import NullToolProvider
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig(profile="local", mcp_enabled=False))
        profile = build_profile("local", config)
        assert isinstance(profile.tool_provider, NullToolProvider)

    def test_local_profile_uses_configured_notifier(self):
        from golem.backends.slack_notifier import SlackNotifier
        from golem.profile import build_profile

        config = Config(
            golem=GolemFlowConfig(profile="local"),
            slack=SlackConfig(
                enabled=True, webhooks={"golem": "https://hooks.slack.com/test"}
            ),
        )
        profile = build_profile("local", config)
        assert isinstance(profile.notifier, SlackNotifier)

    def test_available_profiles_includes_local(self):
        from golem.profile import available_profiles, build_profile

        config = Config(golem=GolemFlowConfig())
        build_profile("redmine", config)
        names = available_profiles()
        assert "local" in names
        assert "redmine" in names


class TestProfileSwitching:
    def test_redmine_profile_has_redmine_backends(self):
        from golem.backends.redmine import (
            RedmineStateBackend,
            RedmineTaskSource,
        )
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig(profile="redmine"))
        profile = build_profile("redmine", config)
        assert isinstance(profile.task_source, RedmineTaskSource)
        assert isinstance(profile.state_backend, RedmineStateBackend)

    def test_redmine_profile_teams_disabled_uses_log_notifier(self):
        from golem.backends.local import LogNotifier
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig(profile="redmine"))
        assert config.teams.enabled is False
        assert config.slack.enabled is False
        profile = build_profile("redmine", config)
        assert isinstance(profile.notifier, LogNotifier)

    def test_redmine_profile_slack_enabled_uses_slack_notifier(self):
        from golem.backends.slack_notifier import SlackNotifier
        from golem.profile import build_profile

        config = Config(
            golem=GolemFlowConfig(profile="redmine"),
            slack=SlackConfig(
                enabled=True, webhooks={"golem": "https://hooks.slack.com/test"}
            ),
        )
        profile = build_profile("redmine", config)
        assert isinstance(profile.notifier, SlackNotifier)

    def test_redmine_profile_teams_enabled_uses_teams_notifier(self):
        from golem.backends.teams_notifier import TeamsNotifier
        from golem.profile import build_profile

        config = Config(
            golem=GolemFlowConfig(profile="redmine"),
            teams=TeamsConfig(
                enabled=True, webhooks={"golem": "https://teams.test/webhook"}
            ),
        )
        profile = build_profile("redmine", config)
        assert isinstance(profile.notifier, TeamsNotifier)

    def test_slack_takes_priority_over_teams(self):
        from golem.backends.slack_notifier import SlackNotifier
        from golem.profile import build_profile

        config = Config(
            golem=GolemFlowConfig(profile="redmine"),
            slack=SlackConfig(
                enabled=True, webhooks={"golem": "https://hooks.slack.com/test"}
            ),
            teams=TeamsConfig(
                enabled=True, webhooks={"golem": "https://teams.test/webhook"}
            ),
        )
        profile = build_profile("redmine", config)
        assert isinstance(profile.notifier, SlackNotifier)


# ---------------------------------------------------------------------------
# Flow integration with profile
# ---------------------------------------------------------------------------


class TestFlowWithProfile:
    def _make_flow(self, monkeypatch, tmp_path, profile=None):
        """Build a GolemFlow, injecting *profile* via monkeypatch.

        If *profile* is ``None`` the redmine profile is built normally.
        Otherwise ``build_profile`` is patched to return the given profile.
        """
        from golem.flow import GolemFlow

        sessions_path = tmp_path / "sessions.json"
        monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

        profile_name = profile.name if profile else "redmine"
        config = Config(
            golem=GolemFlowConfig(
                enabled=True,
                projects=["test-project"],
                profile=profile_name,
            ),
        )

        if profile is not None:
            monkeypatch.setattr(
                "golem.flow.build_profile",
                lambda _name, _cfg: profile,
            )

        return GolemFlow(config)

    def test_flow_builds_redmine_profile(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        assert flow._profile is not None
        assert flow._profile.name == "redmine"

    def test_flow_builds_test_profile(self, monkeypatch, tmp_path):
        profile = _make_test_profile()
        flow = self._make_flow(monkeypatch, tmp_path, profile=profile)
        assert flow._profile is not None
        assert flow._profile.name == "test"

    def test_flow_mcp_servers_default_empty(self, monkeypatch, tmp_path):
        flow = self._make_flow(monkeypatch, tmp_path)
        servers = flow.mcp_servers
        assert servers == []

    def test_flow_mcp_servers_null_provider(self, monkeypatch, tmp_path):
        profile = _make_test_profile()
        flow = self._make_flow(monkeypatch, tmp_path, profile=profile)
        servers = flow.mcp_servers
        assert servers == []

    def test_flow_poll_local_tasks(self, monkeypatch, tmp_path):
        """Null-backends profile poll_new_items reads from task files."""
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "t1.json").write_text(
            json.dumps({"id": "t1", "subject": "[AGENT] Local task"})
        )

        profile = _make_test_profile(tasks_dir=tasks_dir)

        from golem.flow import GolemFlow

        sessions_path = tmp_path / "sessions.json"
        monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)
        config = Config(
            golem=GolemFlowConfig(
                enabled=True,
                projects=["test"],
                profile="test",
            ),
        )
        monkeypatch.setattr(
            "golem.flow.build_profile",
            lambda _name, _cfg: profile,
        )
        flow = GolemFlow(config)
        items = flow.poll_new_items()
        assert len(items) == 1
        assert items[0]["subject"] == "[AGENT] Local task"

    async def test_handle_uses_profile_notifier(self, monkeypatch, tmp_path):
        """When handling an event, the profile's notifier is called."""
        from golem.core.triggers.base import TriggerEvent

        profile = _make_test_profile()
        flow = self._make_flow(monkeypatch, tmp_path, profile=profile)
        # Replace the notifier with a mock
        mock_notifier = MagicMock()
        flow._profile.notifier = mock_notifier

        event = TriggerEvent(
            flow_name="golem",
            event_id="test-1",
            data={"issue_id": 500, "subject": "[AGENT] Notifier test"},
            timestamp=datetime.now(),
            source="test",
        )
        result = await flow.handle(event)
        assert result.success
        mock_notifier.notify_started.assert_called_once_with(
            500, "[AGENT] Notifier test"
        )

    def test_flow_passes_profile_to_orchestrator(self, monkeypatch, tmp_path):
        """Verify profile is threaded to the TaskOrchestrator."""
        profile = _make_test_profile()
        flow = self._make_flow(monkeypatch, tmp_path, profile=profile)
        from golem.orchestrator import TaskSession, TaskSessionState

        session = TaskSession(
            parent_issue_id=600,
            parent_subject="[AGENT] Test",
            state=TaskSessionState.DETECTED,
            grace_deadline=(
                datetime.now(timezone.utc) + timedelta(seconds=300)
            ).isoformat(),
        )
        flow._sessions[600] = session
        flow._running = True

        created_orchs = []
        orig_init = None

        from golem.orchestrator import TaskOrchestrator

        orig_init = TaskOrchestrator.__init__

        def capture_init(self_orch, *args, **kwargs):
            orig_init(self_orch, *args, **kwargs)
            created_orchs.append(self_orch)

        monkeypatch.setattr(TaskOrchestrator, "__init__", capture_init)

        async def completing_tick(self_orch):
            self_orch.session.state = TaskSessionState.COMPLETED
            return self_orch.session

        monkeypatch.setattr(TaskOrchestrator, "tick", completing_tick)

        asyncio.run(flow._run_session(600))

        assert len(created_orchs) == 1
        assert created_orchs[0].profile is not None
        assert created_orchs[0].profile.name == "test"


# ---------------------------------------------------------------------------
# Orchestrator profile-aware helpers
# ---------------------------------------------------------------------------


class TestOrchestratorProfileHelpers:
    def _make_orchestrator(self, profile=None):
        from golem.orchestrator import (
            TaskOrchestrator,
            TaskSession,
            TaskSessionState,
        )

        session = TaskSession(
            parent_issue_id=700,
            parent_subject="[AGENT] Test task",
            state=TaskSessionState.RUNNING,
        )
        config = MagicMock()
        task_config = GolemFlowConfig()
        return TaskOrchestrator(session, config, task_config, profile=profile)

    def test_update_task_with_null_profile(self):
        profile = _make_test_profile()
        mock_backend = MagicMock()
        profile.state_backend = mock_backend

        orch = self._make_orchestrator(profile=profile)
        orch._update_task(700, status="in_progress", comment="Starting work")

        mock_backend.update_status.assert_called_once_with(700, "in_progress")
        mock_backend.post_comment.assert_called_once_with(700, "Starting work")

    def test_update_task_progress(self):
        profile = _make_test_profile()
        mock_backend = MagicMock()
        profile.state_backend = mock_backend

        orch = self._make_orchestrator(profile=profile)
        orch._update_task(700, progress=50)

        mock_backend.update_progress.assert_called_once_with(700, 50)

    def test_get_description_via_profile(self, tmp_path):
        # Set up a real local file task
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "700.json").write_text(
            json.dumps(
                {
                    "id": "700",
                    "subject": "[AGENT] Test",
                    "description": "Fix the parser bug in module X.",
                }
            )
        )

        profile = _make_test_profile(tasks_dir=tasks_dir)

        orch = self._make_orchestrator(profile=profile)
        desc = orch._get_description(700)
        assert desc == "Fix the parser bug in module X."

    def test_format_prompt_via_profile(self):
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig())
        profile = build_profile("redmine", config)

        orch = self._make_orchestrator(profile=profile)
        text = orch._format_prompt("run_task.txt", issue_id=700)
        assert "#700" in text

    def test_get_mcp_servers_via_null_profile(self):
        profile = _make_test_profile()

        orch = self._make_orchestrator(profile=profile)
        servers = orch._get_mcp_servers("[AGENT] Investigate Jenkins failure")
        assert servers == []  # NullToolProvider returns empty

    def test_get_mcp_servers_via_redmine_profile(self):
        from golem.profile import build_profile

        config = Config(golem=GolemFlowConfig())
        profile = build_profile("redmine", config)

        orch = self._make_orchestrator(profile=profile)
        servers = orch._get_mcp_servers("[AGENT] Investigate Jenkins failure")
        assert "jenkins" in servers

    def test_no_profile_raises_on_update_task(self):
        """When no profile is set, _update_task raises AttributeError."""
        orch = self._make_orchestrator(profile=None)
        with pytest.raises(AttributeError):
            orch._update_task(700, comment="Should fail without profile")


# ---------------------------------------------------------------------------
# State transition notifications through profile
# ---------------------------------------------------------------------------


class TestStateTransitionNotifications:
    def _make_flow_with_mock_notifier(self, monkeypatch, tmp_path):
        from golem.flow import GolemFlow

        profile = _make_test_profile()

        sessions_path = tmp_path / "sessions.json"
        monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)
        config = Config(
            golem=GolemFlowConfig(
                enabled=True,
                projects=["test"],
                profile="test",
            ),
        )
        monkeypatch.setattr(
            "golem.flow.build_profile",
            lambda _name, _cfg: profile,
        )
        flow = GolemFlow(config)
        mock_notifier = MagicMock()
        flow._profile.notifier = mock_notifier
        return flow, mock_notifier

    def test_detected_to_running_sends_started(self, monkeypatch, tmp_path):
        from golem.orchestrator import TaskSession, TaskSessionState

        flow, mock_notifier = self._make_flow_with_mock_notifier(monkeypatch, tmp_path)
        session = TaskSession(
            parent_issue_id=800,
            parent_subject="[AGENT] Notify test",
            state=TaskSessionState.RUNNING,
        )

        flow._handle_state_transition(session, TaskSessionState.DETECTED)
        mock_notifier.notify_started.assert_called_once_with(800, "[AGENT] Notify test")

    def test_running_to_completed_sends_completed(self, monkeypatch, tmp_path):
        from golem.orchestrator import TaskSession, TaskSessionState

        flow, mock_notifier = self._make_flow_with_mock_notifier(monkeypatch, tmp_path)
        session = TaskSession(
            parent_issue_id=801,
            parent_subject="[AGENT] Complete test",
            state=TaskSessionState.COMPLETED,
            total_cost_usd=2.50,
            duration_seconds=120.0,
            milestone_count=15,
            validation_verdict="PASS",
        )

        flow._handle_state_transition(session, TaskSessionState.RUNNING)
        mock_notifier.notify_completed.assert_called_once()
        call_kwargs = mock_notifier.notify_completed.call_args
        assert call_kwargs[0][0] == 801  # task_id
        assert call_kwargs[1]["cost_usd"] == 2.50

    def test_running_to_failed_sends_failure(self, monkeypatch, tmp_path):
        from golem.orchestrator import TaskSession, TaskSessionState

        flow, mock_notifier = self._make_flow_with_mock_notifier(monkeypatch, tmp_path)
        session = TaskSession(
            parent_issue_id=802,
            parent_subject="[AGENT] Fail test",
            state=TaskSessionState.FAILED,
            errors=["Budget exceeded"],
        )

        flow._handle_state_transition(session, TaskSessionState.RUNNING)
        mock_notifier.notify_failed.assert_called_once()

    def test_failed_with_verdict_sends_escalation(self, monkeypatch, tmp_path):
        from golem.orchestrator import TaskSession, TaskSessionState

        flow, mock_notifier = self._make_flow_with_mock_notifier(monkeypatch, tmp_path)
        session = TaskSession(
            parent_issue_id=803,
            parent_subject="[AGENT] Escalate test",
            state=TaskSessionState.FAILED,
            validation_verdict="FAIL",
            validation_summary="Agent could not resolve the issue",
        )

        flow._handle_state_transition(session, TaskSessionState.RUNNING)
        mock_notifier.notify_escalated.assert_called_once()
        mock_notifier.notify_failed.assert_not_called()


# ---------------------------------------------------------------------------
# End-to-end: null-backends profile poll -> detect -> handle
# ---------------------------------------------------------------------------


class TestNullProfileEndToEnd:
    def test_poll_detect_handle_lifecycle(  # pylint: disable=too-many-locals
        self, monkeypatch, tmp_path
    ):
        """Full lifecycle: create task file -> poll -> create event -> handle."""
        from golem.flow import GolemFlow
        from golem.orchestrator import TaskSessionState
        from golem.core.triggers.base import TriggerEvent

        # Set up local task files (numeric IDs required -- flow does int())
        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "100.json").write_text(
            json.dumps({"id": 100, "subject": "[AGENT] E2E local test"})
        )

        profile = _make_test_profile(tasks_dir=tasks_dir)

        sessions_path = tmp_path / "sessions.json"
        monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

        config = Config(
            golem=GolemFlowConfig(
                enabled=True,
                projects=["test"],
                profile="test",
            ),
        )
        monkeypatch.setattr(
            "golem.flow.build_profile",
            lambda _name, _cfg: profile,
        )
        flow = GolemFlow(config)

        # Step 1: Poll discovers the task
        items = flow.poll_new_items()
        assert len(items) == 1
        assert items[0]["subject"] == "[AGENT] E2E local test"

        # Step 2: Handle creates session
        event = TriggerEvent(
            flow_name="golem",
            event_id="e2e-test-1",
            data=items[0],
            timestamp=datetime.now(),
            source="test",
        )

        result = asyncio.run(flow.handle(event))
        assert result.success
        assert result.data.get("session_created") is True

        # Step 3: Session exists in flow state
        assert 100 in flow._sessions
        session = flow._sessions[100]
        assert session.state == TaskSessionState.DETECTED
        assert session.parent_subject == "[AGENT] E2E local test"

        # Step 4: Duplicate is skipped
        result2 = asyncio.run(flow.handle(event))
        assert result2.success
        assert result2.data.get("skipped") is True

    def test_poll_detect_handle_numeric_ids(self, monkeypatch, tmp_path):
        """Local tasks with numeric IDs work with the flow's int conversion."""
        from golem.flow import GolemFlow
        from golem.core.triggers.base import TriggerEvent

        tasks_dir = tmp_path / "tasks"
        tasks_dir.mkdir()
        (tasks_dir / "42.json").write_text(
            json.dumps({"id": 42, "subject": "[AGENT] Numeric ID task"})
        )

        profile = _make_test_profile(tasks_dir=tasks_dir)

        sessions_path = tmp_path / "sessions.json"
        monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

        config = Config(
            golem=GolemFlowConfig(
                enabled=True,
                projects=["test"],
                profile="test",
            ),
        )
        monkeypatch.setattr(
            "golem.flow.build_profile",
            lambda _name, _cfg: profile,
        )
        flow = GolemFlow(config)

        items = flow.poll_new_items()
        assert len(items) == 1

        event = TriggerEvent(
            flow_name="golem",
            event_id="num-test",
            data=items[0],
            timestamp=datetime.now(),
            source="test",
        )
        result = asyncio.run(flow.handle(event))
        assert result.success
        assert 42 in flow._sessions


# ---------------------------------------------------------------------------
# Redmine backend (mocked HTTP)
# ---------------------------------------------------------------------------


class TestRedmineBackendMocked:
    def test_redmine_state_backend_update_status(self, monkeypatch):
        from golem.backends.redmine import RedmineStateBackend

        put_calls = []
        get_calls = []

        def mock_put(*args, **kwargs):  # pylint: disable=unused-argument
            put_calls.append((args, kwargs))
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        def mock_get(*args, **kwargs):  # pylint: disable=unused-argument
            get_calls.append((args, kwargs))
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {"issue": {"status": {"id": 2}}}
            return resp

        monkeypatch.setattr("golem.backends.redmine._requests.put", mock_put)
        monkeypatch.setattr("golem.backends.redmine._requests.get", mock_get)

        backend = RedmineStateBackend()
        result = backend.update_status(123, "in_progress")
        assert result is True
        assert len(put_calls) == 1
        # Verify the PUT payload includes status_id=2
        payload = put_calls[0][1]["json"]["issue"]
        assert payload["status_id"] == 2

    def test_redmine_state_backend_post_comment(self, monkeypatch):
        from golem.backends.redmine import RedmineStateBackend

        put_calls = []

        def mock_put(*args, **kwargs):  # pylint: disable=unused-argument
            put_calls.append((args, kwargs))
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            return resp

        monkeypatch.setattr("golem.backends.redmine._requests.put", mock_put)

        backend = RedmineStateBackend()
        result = backend.post_comment(123, "Test comment")
        assert result is True
        payload = put_calls[0][1]["json"]["issue"]
        assert payload["notes"] == "Test comment"

    def test_redmine_task_source_get_description(self, monkeypatch):
        from golem.backends.redmine import RedmineTaskSource

        def mock_get(*_args, **_kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json.return_value = {
                "issue": {"description": "Full task description from Redmine"}
            }
            return resp

        monkeypatch.setattr("golem.backends.redmine._requests.get", mock_get)

        src = RedmineTaskSource()
        desc = src.get_task_description(456)
        assert desc == "Full task description from Redmine"

    def test_redmine_status_mapping(self):
        from golem.backends.redmine import _status_map
        from golem.interfaces import TaskStatus

        assert _status_map[TaskStatus.IN_PROGRESS] == 2
        assert _status_map[TaskStatus.FIXED] == 3
        assert _status_map[TaskStatus.CLOSED] == 5


# ---------------------------------------------------------------------------
# Slack config parsing
# ---------------------------------------------------------------------------


class TestSlackConfig:
    def test_default_slack_disabled(self):
        config = Config()
        assert config.slack.enabled is False
        assert not config.slack.webhooks

    def test_slack_config_from_yaml(self, tmp_path):
        from golem.core.config import load_config

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "slack:\n"
            "  enabled: true\n"
            "  webhooks:\n"
            "    golem: https://hooks.slack.com/services/T/B/X\n"
        )
        config = load_config(cfg_file)
        assert config.slack.enabled is True
        assert (
            config.slack.webhooks["golem"] == "https://hooks.slack.com/services/T/B/X"
        )

    def test_slack_and_teams_both_parsed(self, tmp_path):
        from golem.core.config import load_config

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "slack:\n"
            "  enabled: true\n"
            "  webhooks:\n"
            "    golem: https://slack.test\n"
            "teams:\n"
            "  enabled: true\n"
            "  webhooks:\n"
            "    golem: https://teams.test\n"
        )
        config = load_config(cfg_file)
        assert config.slack.enabled is True
        assert config.teams.enabled is True


# ---------------------------------------------------------------------------
# SlackClient
# ---------------------------------------------------------------------------


class TestSlackClient:
    def test_send_message_success(self, monkeypatch):
        from golem.core.slack import SlackClient

        def mock_post(*_args, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            return resp

        import golem.core.slack as slack_mod

        monkeypatch.setattr(slack_mod.requests, "post", mock_post)

        client = SlackClient(webhooks={"golem": "https://hooks.slack.com/test"})
        assert client.send_to_channel("golem", {"text": "hello"}) is True

    def test_send_message_missing_channel(self):
        from golem.core.slack import SlackClient

        client = SlackClient(webhooks={})
        assert client.send_to_channel("golem", {"text": "hello"}) is False

    def test_send_message_http_error(self, monkeypatch):
        from golem.core.slack import SlackClient

        def mock_post(*_args, **kwargs):
            resp = MagicMock()
            resp.status_code = 403
            resp.text = "invalid_token"
            return resp

        import golem.core.slack as slack_mod

        monkeypatch.setattr(slack_mod.requests, "post", mock_post)

        client = SlackClient(webhooks={"ch": "https://hooks.slack.com/test"})
        assert (
            client.send_message("https://hooks.slack.com/test", {"text": "hi"}) is False
        )

    def test_send_message_request_exception(self, monkeypatch):
        from golem.core.slack import SlackClient
        import requests as _req

        def mock_post(*_args, **kwargs):
            raise _req.ConnectionError("refused")

        import golem.core.slack as slack_mod

        monkeypatch.setattr(slack_mod.requests, "post", mock_post)

        client = SlackClient(webhooks={"ch": "https://hooks.slack.com/test"})
        assert (
            client.send_message("https://hooks.slack.com/test", {"text": "hi"}) is False
        )
