# pylint: disable=too-few-public-methods
"""Tests for GolemFlow merge agent callback and deferred merge handling."""

from unittest.mock import MagicMock, patch

from golem.core.config import Config, GolemFlowConfig
from golem.merge_queue import MergeResult
from golem.merge_review import ReconciliationResult
from golem.orchestrator import TaskSession, TaskSessionState


def _make_test_profile(tmp_path):
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
        task_source=LocalFileTaskSource(str(tmp_path / "test-tasks")),
        state_backend=NullStateBackend(),
        notifier=LogNotifier(),
        tool_provider=NullToolProvider(),
        prompt_provider=FilePromptProvider(None),
    )


def _make_flow(monkeypatch, tmp_path):
    from golem.flow import GolemFlow

    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

    profile = _make_test_profile(tmp_path)
    config = Config(
        golem=GolemFlowConfig(enabled=True, projects=["test-project"], profile="test")
    )
    monkeypatch.setattr(
        "golem.flow.build_profile",
        lambda _name, _cfg: profile,
    )
    return GolemFlow(config)


class TestHandleMergeAgent:
    def test_calls_run_merge_agent_with_correct_args(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        captured = {}

        def fake_run_merge_agent(base_dir, issue_id, agent_diff, **kwargs):
            captured["base_dir"] = base_dir
            captured["issue_id"] = issue_id
            captured["agent_diff"] = agent_diff
            captured["kwargs"] = kwargs
            return ReconciliationResult(resolved=True, commit_sha="abc123")

        monkeypatch.setattr("golem.flow.run_merge_agent", fake_run_merge_agent)

        result = flow._handle_merge_agent(
            base_dir=str(tmp_path / "repo"),
            issue_id=42,
            agent_diff="diff content",
            conflict_files=["a.py", "b.py"],
            missing=[],
        )

        assert result.resolved is True
        assert result.commit_sha == "abc123"
        assert captured["base_dir"] == str(tmp_path / "repo")
        assert captured["issue_id"] == 42
        assert captured["agent_diff"] == "diff content"
        assert captured["kwargs"]["conflict_files"] == ["a.py", "b.py"]
        assert captured["kwargs"]["missing"] == []

    def test_passes_budget_and_timeout(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        captured = {}

        def fake_run_merge_agent(base_dir, issue_id, agent_diff, **kwargs):
            captured.update(kwargs)
            return ReconciliationResult(resolved=False, explanation="test")

        monkeypatch.setattr("golem.flow.run_merge_agent", fake_run_merge_agent)

        flow._handle_merge_agent(
            base_dir=str(tmp_path / "repo"),
            issue_id=99,
            agent_diff="diff",
            conflict_files=[],
            missing=[],
        )

        assert "budget_usd" in captured
        assert "timeout_seconds" in captured

    def test_returns_failure_result(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "golem.flow.run_merge_agent",
            lambda *a, **kw: ReconciliationResult(
                resolved=False, explanation="cannot fix"
            ),
        )

        result = flow._handle_merge_agent(
            base_dir=str(tmp_path / "repo"),
            issue_id=55,
            agent_diff="diff",
            conflict_files=["x.py"],
            missing=[],
        )
        assert result.resolved is False
        assert result.explanation == "cannot fix"


class TestApplyMergeResultDeferred:
    def test_deferred_result_sets_session_fields(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=300,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_ready = True
        flow._sessions[300] = session

        result = MergeResult(
            session_id=300,
            success=False,
            deferred=True,
            merge_branch="merge/300",
            error="conflict with other session",
        )
        flow._apply_merge_result(result)

        assert session.merge_deferred is True
        assert session.merge_branch == "merge/300"
        assert session.merge_ready is False
        # Deferred should NOT add to errors
        assert not any("merge failed" in e for e in session.errors)

    def test_success_clears_deferred(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=301,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_deferred = True
        session.merge_branch = "merge/301"
        flow._sessions[301] = session

        result = MergeResult(
            session_id=301,
            success=True,
            merge_sha="deadbeef",
            changed_files=["a.py"],
        )
        flow._apply_merge_result(result)

        assert session.merge_deferred is False
        assert session.merge_branch == ""
        assert session.commit_sha == "deadbeef"

    def test_failure_clears_deferred(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=302,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_deferred = True
        session.merge_branch = "merge/302"
        flow._sessions[302] = session

        result = MergeResult(
            session_id=302,
            success=False,
            error="hard failure",
        )
        flow._apply_merge_result(result)

        assert session.merge_deferred is False
        assert session.merge_ready is False
        assert any("merge failed" in e for e in session.errors)


class TestApplyMergeResultNoNewCommits:
    def test_success_with_empty_merge_sha(self, monkeypatch, tmp_path):
        """When success=True but merge_sha is empty, session state is still updated."""
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=400,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_ready = True
        session.merge_deferred = True
        session.merge_branch = "merge/400"
        session.commit_sha = "original"
        flow._sessions[400] = session

        result = MergeResult(
            session_id=400,
            success=True,
            merge_sha="",
            changed_files=[],
        )
        flow._apply_merge_result(result)

        assert session.merge_ready is False
        assert session.merge_deferred is False
        assert session.merge_branch == ""
        # commit_sha should remain unchanged when merge_sha is empty
        assert session.commit_sha == "original"


class TestRetryDeferredMerges:
    async def test_retries_deferred_merge(self, monkeypatch, tmp_path):
        """When ff succeeds, deferred state is cleared and branches cleaned up."""
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=42,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_deferred = True
        session.merge_branch = "merge-ready/42"
        session.base_work_dir = str(tmp_path / "repo")
        flow._sessions[42] = session

        with (
            patch(
                "golem.flow.fast_forward_if_safe", return_value=(True, "")
            ) as _mock_ff,
            patch("golem.flow._run_git") as mock_git,
        ):
            mock_git.return_value = MagicMock(stdout="abc123\n")
            await flow._retry_deferred_merges()

        assert session.merge_deferred is False
        assert session.merge_branch == ""
        assert session.commit_sha == "abc123"
        # Verify branch cleanup used the original name
        branch_calls = [call.args[0] for call in mock_git.call_args_list]
        assert any("merge-ready/42" in str(c) for c in branch_calls)

    async def test_skips_non_deferred(self, monkeypatch, tmp_path):
        """Sessions not marked deferred are skipped."""
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=1,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        flow._sessions[1] = session

        with patch("golem.flow.fast_forward_if_safe") as mock_ff:
            await flow._retry_deferred_merges()

        mock_ff.assert_not_called()

    async def test_ff_fails_stays_deferred(self, monkeypatch, tmp_path):
        """When ff fails, session stays deferred."""
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=42,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_deferred = True
        session.merge_branch = "merge-ready/42"
        session.base_work_dir = str(tmp_path / "repo")
        flow._sessions[42] = session

        with patch("golem.flow.fast_forward_if_safe", return_value=(False, "dirty")):
            await flow._retry_deferred_merges()

        assert session.merge_deferred is True
        assert session.merge_branch == "merge-ready/42"

    async def test_stops_after_max_retries(self, monkeypatch, tmp_path):
        """Session at max retry count is skipped — fast_forward_if_safe is not called."""
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=43,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_deferred = True
        session.merge_branch = "merge-ready/43"
        session.base_work_dir = str(tmp_path / "repo")
        session.merge_retry_count = 3
        flow._sessions[43] = session

        with patch("golem.flow.fast_forward_if_safe") as mock_ff:
            await flow._retry_deferred_merges()

        mock_ff.assert_not_called()

    async def test_increments_retry_count_on_failure(self, monkeypatch, tmp_path):
        """Each failed fast_forward increments merge_retry_count by 1."""
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=44,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_deferred = True
        session.merge_branch = "merge-ready/44"
        session.base_work_dir = str(tmp_path / "repo")
        session.merge_retry_count = 0
        flow._sessions[44] = session

        with patch("golem.flow.fast_forward_if_safe", return_value=(False, "dirty")):
            await flow._retry_deferred_merges()
            assert session.merge_retry_count == 1
            await flow._retry_deferred_merges()
            assert session.merge_retry_count == 2

    async def test_logs_error_when_max_retries_reached(
        self, monkeypatch, tmp_path, caplog
    ):
        """An ERROR is logged when merge_retry_count reaches _MAX_MERGE_RETRIES."""
        import logging

        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=45,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_deferred = True
        session.merge_branch = "merge-ready/45"
        session.base_work_dir = str(tmp_path / "repo")
        session.merge_retry_count = 2  # one away from limit of 3
        flow._sessions[45] = session

        with patch("golem.flow.fast_forward_if_safe", return_value=(False, "conflict")):
            with caplog.at_level(logging.ERROR, logger="golem.flow"):
                await flow._retry_deferred_merges()

        assert session.merge_retry_count == 3
        assert any(
            "exceeded" in record.message and "45" in record.message
            for record in caplog.records
            if record.levelno == logging.ERROR
        )

    async def test_resets_retry_count_on_success(self, monkeypatch, tmp_path):
        """Successful retry resets merge_retry_count to 0."""
        flow = _make_flow(monkeypatch, tmp_path)

        session = TaskSession(
            parent_issue_id=46,
            parent_subject="test",
            state=TaskSessionState.COMPLETED,
            created_at="2025-01-01T00:00:00",
            updated_at="2025-01-01T00:00:00",
        )
        session.merge_deferred = True
        session.merge_branch = "merge-ready/46"
        session.base_work_dir = str(tmp_path / "repo")
        session.merge_retry_count = 2
        flow._sessions[46] = session

        with (
            patch("golem.flow.fast_forward_if_safe", return_value=(True, "")),
            patch("golem.flow._run_git") as mock_git,
        ):
            mock_git.return_value = MagicMock(stdout="deadbeef\n")
            await flow._retry_deferred_merges()

        assert session.merge_retry_count == 0
        assert session.merge_deferred is False
        assert session.commit_sha == "deadbeef"
