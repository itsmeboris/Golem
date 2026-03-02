# pylint: disable=too-few-public-methods
"""Tests for GolemFlow merge conflict/reconciliation callbacks."""
from unittest.mock import MagicMock

from golem.core.config import Config, GolemFlowConfig
from golem.merge_queue import MergeEntry
from golem.merge_review import ReconciliationResult
from golem.validation import ValidationVerdict
from golem.worktree_manager import MissingAddition


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


def _make_flow(monkeypatch, tmp_path):
    from golem.flow import GolemFlow

    sessions_path = tmp_path / "sessions.json"
    monkeypatch.setattr("golem.orchestrator.SESSIONS_FILE", sessions_path)

    profile = _make_test_profile()
    config = Config(
        golem=GolemFlowConfig(enabled=True, projects=["test-project"], profile="test")
    )
    monkeypatch.setattr(
        "golem.flow.build_profile",
        lambda _name, _cfg: profile,
    )
    return GolemFlow(config)


def _make_entry(session_id):
    return MergeEntry(
        session_id=session_id,
        branch_name="b",
        worktree_path="/wt",
        base_dir="/repo",
    )


class TestHandleMergeConflict:
    def test_resolution_succeeds_validation_passes(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "golem.flow.run_conflict_resolution",
            lambda *a, **kw: ReconciliationResult(resolved=True),
        )
        monkeypatch.setattr(
            "golem.validation.run_validation",
            lambda *a, **kw: ValidationVerdict(verdict="PASS", confidence=0.9),
        )
        assert flow._handle_merge_conflict(_make_entry(100), ["a.py"]) is True

    def test_resolution_fails(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "golem.flow.run_conflict_resolution",
            lambda *a, **kw: ReconciliationResult(resolved=False),
        )
        assert flow._handle_merge_conflict(_make_entry(101), ["a.py"]) is False

    def test_resolution_succeeds_validation_fails(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "golem.flow.run_conflict_resolution",
            lambda *a, **kw: ReconciliationResult(resolved=True),
        )
        monkeypatch.setattr(
            "golem.validation.run_validation",
            lambda *a, **kw: ValidationVerdict(verdict="FAIL", summary="broken"),
        )
        assert flow._handle_merge_conflict(_make_entry(102), ["a.py"]) is False


class TestHandleMergeReconciliation:
    def test_reconciliation_succeeds_validation_passes(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        missing = [MissingAddition(file="x.py", expected_lines=["code"])]
        monkeypatch.setattr(
            "golem.flow.run_merge_reconciliation",
            lambda *a, **kw: ReconciliationResult(resolved=True, commit_sha="fix1"),
        )
        monkeypatch.setattr(
            "golem.validation.run_validation",
            lambda *a, **kw: ValidationVerdict(verdict="PASS", confidence=0.9),
        )
        result = flow._handle_merge_reconciliation(_make_entry(200), "diff", missing)
        assert result.resolved is True
        assert result.commit_sha == "fix1"

    def test_reconciliation_fails(self, monkeypatch, tmp_path):
        flow = _make_flow(monkeypatch, tmp_path)
        missing = [MissingAddition(file="x.py", expected_lines=["code"])]
        monkeypatch.setattr(
            "golem.flow.run_merge_reconciliation",
            lambda *a, **kw: ReconciliationResult(
                resolved=False, explanation="cannot fix"
            ),
        )
        result = flow._handle_merge_reconciliation(_make_entry(201), "diff", missing)
        assert result.resolved is False
        assert result.explanation == "cannot fix"

    def test_reconciliation_succeeds_validation_fails_reverts(
        self, monkeypatch, tmp_path
    ):
        flow = _make_flow(monkeypatch, tmp_path)
        missing = [MissingAddition(file="x.py", expected_lines=["code"])]
        monkeypatch.setattr(
            "golem.flow.run_merge_reconciliation",
            lambda *a, **kw: ReconciliationResult(resolved=True, commit_sha="fix2"),
        )
        monkeypatch.setattr(
            "golem.validation.run_validation",
            lambda *a, **kw: ValidationVerdict(verdict="FAIL", summary="tests broke"),
        )
        revert_calls = []

        def mock_run(*args, **kwargs):
            if args and isinstance(args[0], list) and "revert" in args[0]:
                revert_calls.append(args[0])
                m = MagicMock()
                m.returncode = 0
                return m
            m = MagicMock()
            m.returncode = 0
            return m

        monkeypatch.setattr("subprocess.run", mock_run)
        result = flow._handle_merge_reconciliation(_make_entry(202), "diff", missing)
        assert result.resolved is False
        assert "validation failed" in result.explanation
        assert len(revert_calls) == 1
