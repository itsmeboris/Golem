"""Tests for human feedback re-attempt loop."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.core.config import Config, GolemFlowConfig
from golem.orchestrator import TaskSession, TaskSessionState, _RESTARTABLE_STATES


class TestHumanReviewState:
    def test_human_review_state_exists(self):
        """HUMAN_REVIEW is a valid TaskSessionState."""
        assert TaskSessionState.HUMAN_REVIEW.value == "human_review"

    def test_session_has_human_feedback_fields(self):
        """TaskSession has fields for human feedback."""
        session = TaskSession(parent_issue_id=1)
        assert hasattr(session, "human_feedback")
        assert hasattr(session, "human_feedback_at")
        assert session.human_feedback == ""
        assert session.human_feedback_at == ""

    def test_escalated_session_transitions_to_human_review(self):
        """A FAILED session can transition to HUMAN_REVIEW when feedback arrives."""
        session = TaskSession(parent_issue_id=1, state=TaskSessionState.FAILED)
        session.state = TaskSessionState.HUMAN_REVIEW
        session.human_feedback = "Try using the SSO module instead of raw auth"
        assert session.state == TaskSessionState.HUMAN_REVIEW

    def test_session_serialization_roundtrip(self):
        """HUMAN_REVIEW state and feedback fields survive serialization."""
        session = TaskSession(
            parent_issue_id=42,
            state=TaskSessionState.HUMAN_REVIEW,
            human_feedback="Use SSO instead",
            human_feedback_at="2026-03-09T12:00:00Z",
        )
        data = session.to_dict()
        restored = TaskSession.from_dict(data)
        assert restored.state == TaskSessionState.HUMAN_REVIEW
        assert restored.human_feedback == "Use SSO instead"
        assert restored.human_feedback_at == "2026-03-09T12:00:00Z"

    def test_human_review_in_restartable_states(self):
        """HUMAN_REVIEW is included in _RESTARTABLE_STATES."""
        assert TaskSessionState.HUMAN_REVIEW in _RESTARTABLE_STATES


class TestHumanRetryPrompt:
    def test_prompt_template_exists(self):
        """human_retry_task.txt prompt template can be loaded."""
        from golem.prompts import format_prompt

        prompt = format_prompt(
            "human_retry_task.txt",
            issue_id=42,
            original_summary="Fixed the auth module",
            human_feedback="The fix should use SSO, not basic auth",
            validation_summary="PARTIAL: wrong auth method used",
            concerns="- Used basic auth instead of SSO",
        )
        assert "42" in prompt
        assert "SSO" in prompt


class TestTaskSourceComments:
    def test_get_task_comments_in_protocol(self):
        """TaskSource protocol includes get_task_comments method."""
        from golem.interfaces import TaskSource

        assert hasattr(TaskSource, "get_task_comments")

    def test_protocol_default_returns_empty(self):
        """Default get_task_comments returns empty list."""
        from golem.interfaces import TaskSource

        result = TaskSource.get_task_comments(TaskSource, 1)
        assert not result


class TestLocalBackendComments:
    def test_returns_empty_list(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        src = LocalFileTaskSource(tmp_path)
        assert not src.get_task_comments(1)

    def test_returns_empty_with_since(self, tmp_path):
        from golem.backends.local import LocalFileTaskSource

        src = LocalFileTaskSource(tmp_path)
        assert not src.get_task_comments(1, since="2026-01-01T00:00:00Z")


class TestRedmineBackendComments:
    def test_returns_comments(self):
        from golem.backends.redmine import RedmineTaskSource

        src = RedmineTaskSource()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "issue": {
                "journals": [
                    {
                        "notes": "Use SSO instead",
                        "created_on": "2026-03-09T12:00:00Z",
                        "user": {"name": "alice"},
                    },
                    {"notes": "", "created_on": "2026-03-09T11:00:00Z"},
                ]
            }
        }
        mock_resp.raise_for_status = MagicMock()
        with patch(
            "golem.backends.redmine._request_with_retry", return_value=mock_resp
        ):
            comments = src.get_task_comments(42)
        assert len(comments) == 1
        assert comments[0]["author"] == "alice"
        assert comments[0]["body"] == "Use SSO instead"

    def test_filters_by_since(self):
        from golem.backends.redmine import RedmineTaskSource

        src = RedmineTaskSource()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "issue": {
                "journals": [
                    {
                        "notes": "Old comment",
                        "created_on": "2026-01-01T00:00:00Z",
                        "user": {"name": "bob"},
                    },
                    {
                        "notes": "New comment",
                        "created_on": "2026-03-09T12:00:00Z",
                        "user": {"name": "carol"},
                    },
                ]
            }
        }
        mock_resp.raise_for_status = MagicMock()
        with patch(
            "golem.backends.redmine._request_with_retry", return_value=mock_resp
        ):
            comments = src.get_task_comments(42, since="2026-03-01T00:00:00Z")
        assert len(comments) == 1
        assert comments[0]["author"] == "carol"

    def test_handles_request_error(self):
        import requests

        from golem.backends.redmine import RedmineTaskSource

        src = RedmineTaskSource()
        with patch(
            "golem.backends.redmine._request_with_retry",
            side_effect=requests.RequestException("timeout"),
        ):
            assert not src.get_task_comments(42)


class TestGitHubBackendComments:
    def test_returns_comments(self):
        import json

        from golem.backends.github import GitHubTaskSource

        src = GitHubTaskSource()
        run_result = MagicMock()
        run_result.returncode = 0
        run_result.stdout = json.dumps(
            {
                "comments": [
                    {
                        "author": {"login": "dev1"},
                        "body": "Please fix the auth",
                        "createdAt": "2026-03-09T12:00:00Z",
                    }
                ]
            }
        )
        with patch("subprocess.run", return_value=run_result):
            comments = src.get_task_comments(99)
        assert len(comments) == 1
        assert comments[0]["author"] == "dev1"
        assert comments[0]["body"] == "Please fix the auth"

    def test_filters_by_since(self):
        import json

        from golem.backends.github import GitHubTaskSource

        src = GitHubTaskSource()
        run_result = MagicMock()
        run_result.returncode = 0
        run_result.stdout = json.dumps(
            {
                "comments": [
                    {
                        "author": {"login": "old"},
                        "body": "old",
                        "createdAt": "2026-01-01T00:00:00Z",
                    },
                    {
                        "author": {"login": "new"},
                        "body": "new",
                        "createdAt": "2026-03-09T12:00:00Z",
                    },
                ]
            }
        )
        with patch("subprocess.run", return_value=run_result):
            comments = src.get_task_comments(99, since="2026-03-01T00:00:00Z")
        assert len(comments) == 1
        assert comments[0]["author"] == "new"

    def test_returns_empty_on_nonzero_exit(self):
        from golem.backends.github import GitHubTaskSource

        src = GitHubTaskSource()
        run_result = MagicMock()
        run_result.returncode = 1
        with patch("subprocess.run", return_value=run_result):
            assert not src.get_task_comments(99)

    def test_handles_os_error(self):
        from golem.backends.github import GitHubTaskSource

        src = GitHubTaskSource()
        with patch("subprocess.run", side_effect=OSError("no gh")):
            assert not src.get_task_comments(99)


# ---------------------------------------------------------------------------
# Flow integration: _check_human_feedback
# ---------------------------------------------------------------------------


def _make_test_profile():
    from golem.backends.local import (
        LocalFileTaskSource,
        LogNotifier,
        NullStateBackend,
        NullToolProvider,
    )
    from golem.profile import GolemProfile
    from golem.prompts import FilePromptProvider

    return GolemProfile(
        name="test",
        task_source=LocalFileTaskSource("/tmp/test-tasks"),
        state_backend=NullStateBackend(),
        notifier=LogNotifier(),
        tool_provider=NullToolProvider(),
        prompt_provider=FilePromptProvider(None),
    )


def _make_flow(monkeypatch, tmp_path, profile=None, **flow_kwargs):
    from golem.flow import GolemFlow

    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

    profile = profile or _make_test_profile()
    fc_kwargs = {"enabled": True, "projects": ["test-project"], "profile": "test"}
    fc_kwargs.update(flow_kwargs)
    config = Config(golem=GolemFlowConfig(**fc_kwargs))
    monkeypatch.setattr(
        "golem.flow.build_profile",
        lambda _name, _cfg: profile,
    )
    return GolemFlow(config)


class TestCheckHumanFeedback:
    def test_skips_non_failed_sessions(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        session = TaskSession(parent_issue_id=1, state=TaskSessionState.COMPLETED)
        flow._sessions[1] = session
        flow._check_human_feedback()
        assert session.state == TaskSessionState.COMPLETED

    def test_detects_human_feedback_and_transitions(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.FAILED,
            updated_at="2026-03-01T00:00:00Z",
            retry_count=2,
        )
        flow._sessions[1] = session

        comments = [
            {
                "author": "alice",
                "body": "Please use SSO",
                "created_at": "2026-03-09T12:00:00Z",
            }
        ]
        flow._profile.task_source.get_task_comments = MagicMock(return_value=comments)
        spawned = []
        monkeypatch.setattr(flow, "_spawn_session_task", spawned.append)

        flow._check_human_feedback()

        assert session.state == TaskSessionState.HUMAN_REVIEW
        assert "alice" in session.human_feedback
        assert "SSO" in session.human_feedback
        assert session.human_feedback_at == "2026-03-09T12:00:00Z"
        assert session.retry_count == 0
        assert spawned == [1]

    def test_filters_out_golem_comments(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.FAILED,
            updated_at="2026-03-01T00:00:00Z",
        )
        flow._sessions[1] = session

        comments = [
            {
                "author": "Golem",
                "body": "Automated escalation",
                "created_at": "2026-03-09T12:00:00Z",
            }
        ]
        flow._profile.task_source.get_task_comments = MagicMock(return_value=comments)

        flow._check_human_feedback()
        assert session.state == TaskSessionState.FAILED

    def test_skips_old_comments(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.FAILED,
            updated_at="2026-03-09T00:00:00Z",
        )
        flow._sessions[1] = session

        comments = [
            {
                "author": "alice",
                "body": "Old comment",
                "created_at": "2026-03-01T00:00:00Z",
            }
        ]
        flow._profile.task_source.get_task_comments = MagicMock(return_value=comments)

        flow._check_human_feedback()
        assert session.state == TaskSessionState.FAILED

    @pytest.mark.parametrize("mode", ["prompt", "issue"])
    def test_skips_submitted_sessions(self, monkeypatch, tmp_path, mode):
        """Sessions from submit_task() use timestamp IDs, not issue numbers."""
        flow = _make_flow(monkeypatch, tmp_path)
        session = TaskSession(
            parent_issue_id=1773705576784,
            state=TaskSessionState.FAILED,
            updated_at="2026-03-01T00:00:00Z",
            execution_mode=mode,
        )
        flow._sessions[1773705576784] = session
        mock_comments = MagicMock()
        flow._profile.task_source.get_task_comments = mock_comments

        flow._check_human_feedback()

        assert session.state == TaskSessionState.FAILED
        mock_comments.assert_not_called()

    def test_handles_exception_gracefully(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.FAILED,
            updated_at="2026-03-01T00:00:00Z",
        )
        flow._sessions[1] = session

        flow._profile.task_source.get_task_comments = MagicMock(
            side_effect=RuntimeError("network error")
        )
        flow._check_human_feedback()
        assert session.state == TaskSessionState.FAILED


# ---------------------------------------------------------------------------
# Orchestrator: tick dispatches HUMAN_REVIEW
# ---------------------------------------------------------------------------


class TestTickHumanReview:
    async def test_tick_dispatches_human_review(self):
        from golem.orchestrator import TaskOrchestrator

        session = TaskSession(
            parent_issue_id=1,
            state=TaskSessionState.HUMAN_REVIEW,
            human_feedback="Use SSO",
        )
        config = Config(golem=GolemFlowConfig(enabled=True, projects=["p"]))
        flow_config = GolemFlowConfig(enabled=True, projects=["p"])
        profile = _make_test_profile()
        orch = TaskOrchestrator(session, config, flow_config, profile=profile)
        orch._run_agent = AsyncMock()

        await orch.tick()

        assert session.state == TaskSessionState.RUNNING
        assert session.started_at != ""
        orch._run_agent.assert_awaited_once()
