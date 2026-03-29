# pylint: disable=too-few-public-methods,redefined-outer-name
"""Tests for golem.merge_queue — sequential merge queue for cross-task coordination."""

import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.merge_queue import MergeEntry, MergeQueue, MergeResult
from golem.merge_review import ReconciliationResult
from golem.verifier import VerificationResult
from golem.worktree_manager import MergeOutcome, MissingAddition

_PASSING_VR = VerificationResult(
    passed=True,
    black_ok=True,
    black_output="",
    pylint_ok=True,
    pylint_output="",
    pytest_ok=True,
    pytest_output="",
)

_FAILING_VR = VerificationResult(
    passed=False,
    black_ok=False,
    black_output="reformatting needed",
    pylint_ok=True,
    pylint_output="",
    pytest_ok=True,
    pytest_output="",
)


@pytest.fixture()
def base_entry(tmp_path):
    return MergeEntry(
        session_id=1,
        branch_name="golem/session-1",
        worktree_path=str(tmp_path / "wt-1"),
        base_dir=str(tmp_path / "repo"),
        changed_files=["a.py", "b.py"],
    )


@pytest.fixture()
def queue():
    return MergeQueue()


class TestMergeEntryDefaults:
    def test_defaults(self, tmp_path):
        e = MergeEntry(
            session_id=1,
            branch_name="b",
            worktree_path=str(tmp_path / "wt"),
            base_dir=str(tmp_path / "repo"),
        )
        assert not e.changed_files
        assert e.priority == 5
        assert e.group_id == ""


class TestMergeResultDefaults:
    def test_defaults(self):
        r = MergeResult(session_id=1, success=True)
        assert r.merge_sha == ""
        assert not r.conflict_files
        assert r.error == ""
        assert r.deferred is False
        assert r.merge_branch == ""


class TestPending:
    def test_empty(self, queue):
        assert queue.pending == 0

    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_after_enqueue(self, _mock_gcf, queue, base_entry):
        await queue.enqueue(base_entry)
        assert queue.pending == 1


class TestEnqueue:
    @patch("golem.merge_queue.get_changed_files", return_value=["x.py"])
    async def test_populates_changed_files_when_empty(self, mock_gcf, queue, tmp_path):
        entry = MergeEntry(
            session_id=2,
            branch_name="golem/session-2",
            worktree_path=str(tmp_path / "wt-2"),
            base_dir=str(tmp_path / "repo"),
        )
        await queue.enqueue(entry)
        mock_gcf.assert_called_once_with(str(tmp_path / "repo"), "golem/session-2")
        assert entry.changed_files == ["x.py"]
        assert queue.pending == 1

    @patch("golem.merge_queue.get_changed_files")
    async def test_keeps_existing_changed_files(self, mock_gcf, queue, base_entry):
        await queue.enqueue(base_entry)
        mock_gcf.assert_not_called()
        assert base_entry.changed_files == ["a.py", "b.py"]


class TestDetectOverlaps:
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_no_overlaps(self, _m, queue, tmp_path):
        e1 = MergeEntry(
            session_id=1,
            branch_name="b1",
            worktree_path=str(tmp_path / "wt1"),
            base_dir=str(tmp_path / "repo"),
            changed_files=["a.py"],
        )
        e2 = MergeEntry(
            session_id=2,
            branch_name="b2",
            worktree_path=str(tmp_path / "wt2"),
            base_dir=str(tmp_path / "repo"),
            changed_files=["b.py"],
        )
        await queue.enqueue(e1)
        await queue.enqueue(e2)
        assert queue.detect_overlaps() == {}

    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_with_overlaps(self, _m, queue, tmp_path):
        e1 = MergeEntry(
            session_id=1,
            branch_name="b1",
            worktree_path=str(tmp_path / "wt1"),
            base_dir=str(tmp_path / "repo"),
            changed_files=["shared.py", "a.py"],
        )
        e2 = MergeEntry(
            session_id=2,
            branch_name="b2",
            worktree_path=str(tmp_path / "wt2"),
            base_dir=str(tmp_path / "repo"),
            changed_files=["shared.py", "b.py"],
        )
        await queue.enqueue(e1)
        await queue.enqueue(e2)
        overlaps = queue.detect_overlaps()
        assert overlaps == {"shared.py": [1, 2]}


class TestProcessAll:
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(sha="abc123", merge_branch="merge-ready/1"),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_empty_queue(self, _gcf, _miw, _ff, _rg, queue):
        results = await queue.process_all()
        assert results == []

    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(sha="sha1", merge_branch="merge-ready/1"),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_priority_sorting(
        self, _gcf, _mock_miw, _ff, _rg, _vm, queue, tmp_path
    ):
        low = MergeEntry(
            session_id=10,
            branch_name="b10",
            worktree_path=str(tmp_path / "wt10"),
            base_dir=str(tmp_path / "repo"),
            changed_files=["f.py"],
            priority=9,
        )
        high = MergeEntry(
            session_id=20,
            branch_name="b20",
            worktree_path=str(tmp_path / "wt20"),
            base_dir=str(tmp_path / "repo"),
            changed_files=["g.py"],
            priority=1,
        )
        await queue.enqueue(low)
        await queue.enqueue(high)
        results = await queue.process_all()
        assert len(results) == 2
        assert results[0].session_id == 20
        assert results[1].session_id == 10
        assert queue.pending == 0

    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(sha="sha1", merge_branch="merge-ready/1"),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_results_accumulated(
        self, _gcf, _miw, _ff, _rg, _vm, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        await queue.process_all()
        assert len([r for _, r in queue._history]) == 1


class TestMergeOneSuccess:
    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="deadbeef", agent_diff="diff", merge_branch="merge-ready/1"
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_successful_merge(
        self, _gcf, mock_miw, _ff, _rg, _vm, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].merge_sha == "deadbeef"
        assert results[0].error == ""
        mock_miw.assert_called_once_with(base_entry.base_dir, 1)


class TestMergeOneFailureNoHandler:
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="",
            error="merge conflict: conflicting changes",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_no_sha_no_conflict_handler(self, _gcf, _miw, queue, base_entry):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is False
        assert results[0].error == "merge conflict: conflicting changes"
        assert results[0].conflict_files == ["a.py", "b.py"]


class TestMergeAgentResolvesConflict:
    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        side_effect=[
            MergeOutcome(
                sha="",
                error="merge conflict: conflicting changes",
                merge_branch="merge-ready/1",
            ),
            MergeOutcome(
                sha="resolved_sha",
                agent_diff="diff",
                merge_branch="merge-ready/1",
            ),
        ],
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_on_merge_agent_resolves(self, _gcf, _miw, _ff, _rg, _vm, base_entry):
        handler = MagicMock(return_value=ReconciliationResult(resolved=True))
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "resolved_sha"
        handler.assert_called_once_with(
            base_entry.base_dir,
            1,
            "",
            ["a.py", "b.py"],
            [],
            "",
        )


class TestMergeAgentFailsConflict:
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="",
            error="merge conflict: conflicting changes",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_on_merge_agent_returns_false(self, _gcf, _miw, base_entry):
        handler = MagicMock(return_value=ReconciliationResult(resolved=False))
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is False
        assert results[0].error == "merge conflict: conflicting changes"


class TestMergeAgentResolvesButRetryFails:
    @patch(
        "golem.merge_queue.merge_in_worktree",
        side_effect=[
            MergeOutcome(
                sha="",
                error="merge conflict: conflicting changes",
                merge_branch="merge-ready/1",
            ),
            MergeOutcome(
                sha="",
                error="merge conflict: still broken",
                merge_branch="merge-ready/1",
            ),
        ],
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_on_merge_agent_true_but_retry_fails(
        self, _gcf, mock_miw, base_entry
    ):
        handler = MagicMock(return_value=ReconciliationResult(resolved=True))
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is False
        assert mock_miw.call_count == 2


class TestMergeOneException:
    @patch(
        "golem.merge_queue.merge_in_worktree",
        side_effect=RuntimeError("git broke"),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_exception_during_merge(self, _gcf, _miw, queue, base_entry):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is False
        assert results[0].error == "git broke"
        assert results[0].merge_sha == ""


class TestOnMergeAgentType:
    def test_type_alias_is_none_by_default(self):
        q = MergeQueue()
        assert q._on_merge_agent is None


class TestMergeWithVerifyClean:
    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="clean123",
            agent_diff="some diff",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_clean_verify_succeeds(
        self, _gcf, _miw, _ff, _rg, _vm, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "clean123"


class TestMergeWithVerifyMissingNoHandler:
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="sha1",
            missing_additions=[
                MissingAddition(
                    file="lost.py", expected_lines=["x"], description="gone"
                )
            ],
            agent_diff="diff",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_missing_no_reconciler_fails(self, _gcf, _miw, queue, base_entry):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is False
        assert results[0].merge_sha == "sha1"
        assert results[0].error == "agent additions lost during merge"
        assert results[0].conflict_files == ["lost.py"]


class TestMergeAgentReconcileSuccess:
    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="sha1",
            missing_additions=[
                MissingAddition(file="f.py", expected_lines=["x"], description="d")
            ],
            agent_diff="diff text",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_reconcile_succeeds(self, _gcf, _miw, _ff, _rg, _vm, base_entry):
        handler = MagicMock(
            return_value=ReconciliationResult(resolved=True, commit_sha="fix1")
        )
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "sha1"
        handler.assert_called_once()


class TestMergeAgentReconcileFailure:
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="sha1",
            missing_additions=[
                MissingAddition(file="f.py", expected_lines=["x"], description="d")
            ],
            agent_diff="diff text",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_reconcile_fails(self, _gcf, _miw, base_entry):
        handler = MagicMock(
            return_value=ReconciliationResult(resolved=False, explanation="cannot fix")
        )
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is False
        assert "reconciliation failed" in results[0].error
        assert results[0].conflict_files == ["f.py"]


class TestMergeOutcomeAgentDiffPopulated:
    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="sha",
            agent_diff="the diff",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_agent_diff_from_outcome(
        self, _gcf, _miw, _ff, _rg, _vm, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "sha"


class TestTransientRetry:
    def test_timeout_is_transient(self):
        exc = subprocess.TimeoutExpired(cmd="git", timeout=30)
        assert MergeQueue._is_transient(exc) is True

    def test_os_error_is_transient(self):
        assert MergeQueue._is_transient(OSError("NFS stale")) is True

    def test_runtime_error_not_transient(self):
        assert MergeQueue._is_transient(RuntimeError("bad")) is False

    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        side_effect=[
            subprocess.TimeoutExpired(cmd="git", timeout=30),
            MergeOutcome(
                sha="ok_after_retry",
                merge_branch="merge-ready/1",
            ),
        ],
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_retry_on_timeout_then_succeed(
        self, _gcf, _miw, _ff, _rg, _vm, queue, base_entry
    ):
        mock_sleep = AsyncMock()
        with patch("golem.merge_queue.asyncio.sleep", mock_sleep):
            await queue.enqueue(base_entry)
            results = await queue.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "ok_after_retry"
        mock_sleep.assert_called_once_with(MergeQueue.INFRA_RETRY_DELAY)

    @patch(
        "golem.merge_queue.merge_in_worktree",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_retry_exhausted_returns_failure(self, _gcf, _miw, queue, base_entry):
        mock_sleep = AsyncMock()
        with patch("golem.merge_queue.asyncio.sleep", mock_sleep):
            await queue.enqueue(base_entry)
            results = await queue.process_all()
        assert results[0].success is False
        assert "timed out" in results[0].error
        # Should retry INFRA_RETRIES times
        assert mock_sleep.call_count == MergeQueue.INFRA_RETRIES

    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_all_attempts_return_none_hits_fallback(
        self, _gcf, queue, base_entry
    ):
        """Cover the defensive fallback when _try_merge returns None every time."""
        with patch.object(queue, "_try_merge", return_value=None):
            await queue.enqueue(base_entry)
            results = await queue.process_all()
        assert results[0].success is False
        assert results[0].error == "merge retries exhausted"


class TestMergeFailureResultShape:
    """Verify merge failure produces correct result fields."""

    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="",
            error="merge conflict: failed",
            merge_branch="merge-ready/42",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_failure_result_has_success_false(self, _gcf, _miw, tmp_path):
        """A merge that yields an empty SHA must produce success=False."""
        q = MergeQueue()
        entry = MergeEntry(
            session_id=42,
            branch_name="agent/42",
            worktree_path=str(tmp_path / "wt"),
            base_dir=str(tmp_path / "repo"),
            changed_files=["x.py"],
        )
        await q.enqueue(entry)
        results = await q.process_all()
        assert results[0].success is False
        assert results[0].error == "merge conflict: failed"


class TestDeferredMerge:
    """Test that when fast_forward_if_safe returns failure, result is deferred."""

    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch(
        "golem.merge_queue.fast_forward_if_safe",
        return_value=(False, "dirty working tree overlaps with merge-ready/1"),
    )
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="abc123",
            agent_diff="diff",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_deferred_when_ff_fails(self, _gcf, _miw, _ff, _vm, base_entry):
        q = MergeQueue()
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is False
        assert results[0].deferred is True
        assert results[0].merge_branch == "merge-ready/1"
        assert results[0].merge_sha == "abc123"
        assert "dirty" in results[0].error
        assert results[0].changed_files == ["a.py", "b.py"]


class TestMergeNoNewCommits:
    """When merge_in_worktree finds no new commits, return success without ff."""

    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(sha="head123"),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_no_commits_returns_success(self, _gcf, _miw, queue, base_entry):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "head123"
        assert results[0].deferred is False


class TestMergeAgentRunsInThread:
    """Verify merge agent callback is offloaded via asyncio.to_thread."""

    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        side_effect=[
            MergeOutcome(
                sha="",
                error="conflict",
                merge_branch="merge-ready/1",
            ),
            MergeOutcome(sha="resolved", merge_branch="merge-ready/1"),
        ],
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_conflict_callback_uses_to_thread(
        self, _gcf, _miw, _ff, _rg, _vm, base_entry
    ):
        handler = MagicMock(return_value=ReconciliationResult(resolved=True))
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        with patch(
            "golem.merge_queue.asyncio.to_thread", wraps=asyncio.to_thread
        ) as mock_tt:
            results = await q.process_all()
        assert results[0].success is True
        # Verify the handler was passed to to_thread (may be called multiple times)
        assert mock_tt.call_count >= 1
        first_args = [call[0][0] for call in mock_tt.call_args_list]
        assert handler in first_args

    @patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR)
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="sha1",
            missing_additions=[
                MissingAddition(file="f.py", expected_lines=["x"], description="d")
            ],
            agent_diff="diff",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_reconcile_callback_uses_to_thread(
        self, _gcf, _miw, _ff, _rg, _vm, base_entry
    ):
        handler = MagicMock(
            return_value=ReconciliationResult(resolved=True, commit_sha="fix1")
        )
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        with patch(
            "golem.merge_queue.asyncio.to_thread", wraps=asyncio.to_thread
        ) as mock_tt:
            results = await q.process_all()
        assert results[0].success is True
        # Verify the handler was passed to to_thread (may be called multiple times)
        assert mock_tt.call_count >= 1
        first_args = [call[0][0] for call in mock_tt.call_args_list]
        assert handler in first_args


class TestMergeEmptyShaNoError:
    """When merge_in_worktree returns empty sha with no error — no changes."""

    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(sha="", error=""),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_empty_sha_no_error_succeeds(self, _gcf, _miw, queue, base_entry):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == ""


class TestPostMergeVerification:
    """Verification runs for all merges that have a merge_branch, including clean merges."""

    # ------------------------------------------------------------------ #
    # Path 1: conflict resolution
    # ------------------------------------------------------------------ #

    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        side_effect=[
            MergeOutcome(
                sha="",
                error="merge conflict",
                merge_branch="merge-ready/1",
            ),
            MergeOutcome(
                sha="resolved_sha",
                merge_branch="merge-ready/1",
            ),
        ],
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_conflict_resolution_runs_verification(
        self, _gcf, _miw, _ff, _rg, base_entry
    ):
        """After agent resolves conflict, verification passes -> merge succeeds."""
        handler = MagicMock(return_value=ReconciliationResult(resolved=True))
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        with patch.object(
            MergeQueue, "_verify_merge", return_value=_PASSING_VR
        ) as mock_vm:
            results = await q.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "resolved_sha"
        mock_vm.assert_called_once_with(
            base_entry.base_dir, "merge-ready/1", base_entry.session_id
        )

    @patch(
        "golem.merge_queue.merge_in_worktree",
        side_effect=[
            MergeOutcome(
                sha="",
                error="merge conflict",
                merge_branch="merge-ready/1",
            ),
            MergeOutcome(
                sha="resolved_sha",
                merge_branch="merge-ready/1",
            ),
        ],
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_conflict_resolution_verification_fails(self, _gcf, _miw, base_entry):
        """After agent resolves conflict, verification fails -> merge fails with branch preserved."""
        handler = MagicMock(return_value=ReconciliationResult(resolved=True))
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        with patch.object(MergeQueue, "_verify_merge", return_value=_FAILING_VR):
            results = await q.process_all()
        assert results[0].success is False
        assert results[0].merge_branch == "merge-ready/1"
        assert "post-merge verification failed" in results[0].error

    # ------------------------------------------------------------------ #
    # Path 2: reconciliation of missing additions
    # ------------------------------------------------------------------ #

    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="sha1",
            missing_additions=[
                MissingAddition(file="f.py", expected_lines=["x"], description="d")
            ],
            agent_diff="diff text",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_reconciliation_runs_verification(
        self, _gcf, _miw, _ff, _rg, base_entry
    ):
        """After reconciliation, verification passes -> merge succeeds."""
        handler = MagicMock(
            return_value=ReconciliationResult(resolved=True, commit_sha="fix1")
        )
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        with patch.object(
            MergeQueue, "_verify_merge", return_value=_PASSING_VR
        ) as mock_vm:
            results = await q.process_all()
        assert results[0].success is True
        mock_vm.assert_called_once()

    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="sha1",
            missing_additions=[
                MissingAddition(file="f.py", expected_lines=["x"], description="d")
            ],
            agent_diff="diff text",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_reconciliation_verification_fails(self, _gcf, _miw, base_entry):
        """After reconciliation, verification fails -> merge fails with branch preserved."""
        handler = MagicMock(
            return_value=ReconciliationResult(resolved=True, commit_sha="fix1")
        )
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        with patch.object(MergeQueue, "_verify_merge", return_value=_FAILING_VR):
            results = await q.process_all()
        assert results[0].success is False
        assert results[0].merge_branch == "merge-ready/1"
        assert "post-merge verification failed" in results[0].error

    # ------------------------------------------------------------------ #
    # SPEC-5: Clean merges also run verification
    # ------------------------------------------------------------------ #

    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="clean123",
            agent_diff="diff",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_clean_merge_runs_verification(
        self, _gcf, _miw, _ff, _rg, queue, base_entry
    ):
        """Clean merges must call _verify_merge before fast-forwarding."""
        await queue.enqueue(base_entry)
        with patch.object(
            MergeQueue, "_verify_merge", return_value=_PASSING_VR
        ) as mock_vm:
            results = await queue.process_all()
        assert results[0].success is True
        mock_vm.assert_called_once()

    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="clean456",
            agent_diff="diff",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_clean_merge_verification_fails(
        self, _gcf, _miw, _ff, _rg, queue, base_entry
    ):
        """Clean merge where _verify_merge returns failing result must return failure."""
        await queue.enqueue(base_entry)
        with patch.object(MergeQueue, "_verify_merge", return_value=_FAILING_VR):
            results = await queue.process_all()
        assert results[0].success is False
        assert results[0].merge_branch == "merge-ready/1"
        assert results[0].error == "post-merge verification failed"

    # ------------------------------------------------------------------ #
    # SPEC-6: _verify_merge unit tests
    # ------------------------------------------------------------------ #

    def test_verify_merge_creates_and_cleans_worktree(self, tmp_path):
        """_verify_merge creates a temporary worktree, runs verification, cleans up."""
        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stderr = ""

        with (
            patch("golem.merge_queue._run_git", return_value=success_result) as mock_rg,
            patch(
                "golem.merge_queue.run_verification", return_value=_PASSING_VR
            ) as mock_rv,
        ):
            result = MergeQueue._verify_merge(
                str(tmp_path), "merge-ready/1", session_id=99
            )

        assert result is _PASSING_VR
        mock_rv.assert_called_once()
        # worktree add --detach must have been called
        add_calls = [
            c
            for c in mock_rg.call_args_list
            if c[0][0][0:3] == ["worktree", "add", "--detach"]
        ]
        assert len(add_calls) == 1
        assert "merge-ready/1" in add_calls[0][0][0]
        # worktree remove must have been called (cleanup)
        remove_calls = [
            c for c in mock_rg.call_args_list if c[0][0][:2] == ["worktree", "remove"]
        ]
        assert len(remove_calls) >= 1

    def test_verify_merge_worktree_creation_fails(self, tmp_path):
        """_verify_merge returns failed VerificationResult when worktree add fails."""
        fail_result = MagicMock()
        fail_result.returncode = 1
        fail_result.stderr = "worktree conflict"

        # First call (worktree add) fails; subsequent calls succeed (prune, etc.)
        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stderr = ""

        def side_effect(args, **_kwargs):
            if args[:2] == ["worktree", "add"]:
                return fail_result
            return success_result

        with patch("golem.merge_queue._run_git", side_effect=side_effect):
            result = MergeQueue._verify_merge(
                str(tmp_path), "merge-ready/1", session_id=7
            )

        assert result.passed is False

    def test_verify_merge_cleans_stale_worktree(self, tmp_path):
        """_verify_merge removes stale worktree directory before creating a new one."""
        # Create the stale worktree directory so Path.exists() is True
        stale_path = tmp_path / "data" / "agent" / "verify-worktrees" / "42"
        stale_path.mkdir(parents=True)

        success_result = MagicMock()
        success_result.returncode = 0
        success_result.stderr = ""

        with (
            patch("golem.merge_queue._run_git", return_value=success_result) as mock_rg,
            patch("golem.merge_queue.run_verification", return_value=_PASSING_VR),
        ):
            MergeQueue._verify_merge(str(tmp_path), "merge-ready/42", session_id=42)

        # Verify that worktree remove was called for the stale path
        remove_calls = [
            c for c in mock_rg.call_args_list if c[0][0][:2] == ["worktree", "remove"]
        ]
        # Should have at least 2 remove calls: one for stale cleanup, one for final cleanup
        assert len(remove_calls) >= 2


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Dataclass field tests
# ---------------------------------------------------------------------------


def test_merge_entry_has_queued_at():
    """MergeEntry has a queued_at field defaulting to empty string."""
    entry = MergeEntry(
        session_id=1, branch_name="agent/1", worktree_path="/tmp", base_dir="/proj"
    )
    assert entry.queued_at == ""


def test_merge_result_has_timestamp():
    """MergeResult has a timestamp field defaulting to empty string."""
    result = MergeResult(session_id=1, success=True)
    assert result.timestamp == ""


# ---------------------------------------------------------------------------
# Task 3: on_state_change callback, _history deque, _active tracking
# ---------------------------------------------------------------------------

from collections import deque


async def test_on_state_change_called_on_enqueue():
    """on_state_change callback fires when an entry is enqueued."""
    cb = MagicMock()
    mq = MergeQueue(on_state_change=cb)
    entry = MergeEntry(
        session_id=99,
        branch_name="agent/99",
        worktree_path="/tmp/wt",
        base_dir="/tmp/base",
    )
    with patch("golem.merge_queue.get_changed_files", return_value=["f.py"]):
        await mq.enqueue(entry)
    cb.assert_called()


async def test_history_is_deque_with_maxlen():
    """_history is a deque with maxlen=50."""
    mq = MergeQueue()
    assert isinstance(mq._history, deque)
    assert mq._history.maxlen == 50


async def test_enqueue_sets_queued_at():
    """enqueue() populates queued_at with an ISO timestamp."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=99,
        branch_name="agent/99",
        worktree_path="/tmp/wt",
        base_dir="/tmp/base",
    )
    with patch("golem.merge_queue.get_changed_files", return_value=["f.py"]):
        await mq.enqueue(entry)
    assert entry.queued_at != ""
    # Should be a valid ISO-ish timestamp
    assert "T" in entry.queued_at


async def test_on_state_change_called_during_process_all():
    """on_state_change is called for each merge processed in process_all."""
    cb = MagicMock()
    mq = MergeQueue(on_state_change=cb)
    entry = MergeEntry(
        session_id=77,
        branch_name="agent/77",
        worktree_path="/tmp/wt",
        base_dir="/tmp/base",
        changed_files=["x.py"],
    )
    with (
        patch(
            "golem.merge_queue.merge_in_worktree",
            return_value=MergeOutcome(sha="abc", merge_branch="merge-ready/77"),
        ),
        patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, "")),
        patch("golem.merge_queue._run_git"),
        patch("golem.merge_queue.get_changed_files", return_value=["x.py"]),
        patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR),
    ):
        await mq.enqueue(entry)
        cb.reset_mock()
        await mq.process_all()
    # At minimum, called once per merge (active set) and once (active cleared)
    assert cb.call_count >= 1


async def test_active_is_none_when_idle():
    """_active is None before and after processing."""
    mq = MergeQueue()
    assert mq._active is None

    entry = MergeEntry(
        session_id=55,
        branch_name="agent/55",
        worktree_path="/tmp/wt",
        base_dir="/tmp/base",
        changed_files=["a.py"],
    )
    with (
        patch(
            "golem.merge_queue.merge_in_worktree",
            return_value=MergeOutcome(sha="def", merge_branch="merge-ready/55"),
        ),
        patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, "")),
        patch("golem.merge_queue._run_git"),
        patch("golem.merge_queue.get_changed_files", return_value=["a.py"]),
        patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR),
    ):
        await mq.enqueue(entry)
        await mq.process_all()

    assert mq._active is None


async def test_history_populated_after_process_all():
    """_history contains (entry, result) tuples after processing."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=33,
        branch_name="agent/33",
        worktree_path="/tmp/wt",
        base_dir="/tmp/base",
        changed_files=["z.py"],
    )
    with (
        patch(
            "golem.merge_queue.merge_in_worktree",
            return_value=MergeOutcome(sha="ghi", merge_branch="merge-ready/33"),
        ),
        patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, "")),
        patch("golem.merge_queue._run_git"),
        patch("golem.merge_queue.get_changed_files", return_value=["z.py"]),
        patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR),
    ):
        await mq.enqueue(entry)
        await mq.process_all()

    assert len(mq._history) == 1
    hist_entry, hist_result = mq._history[0]
    assert hist_entry.session_id == 33
    assert hist_result.success is True


async def test_history_result_timestamp_set():
    """MergeResult.timestamp is populated with an ISO timestamp after process_all."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=44,
        branch_name="agent/44",
        worktree_path="/tmp/wt",
        base_dir="/tmp/base",
        changed_files=["t.py"],
    )
    with (
        patch(
            "golem.merge_queue.merge_in_worktree",
            return_value=MergeOutcome(sha="jkl", merge_branch="merge-ready/44"),
        ),
        patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, "")),
        patch("golem.merge_queue._run_git"),
        patch("golem.merge_queue.get_changed_files", return_value=["t.py"]),
        patch("golem.merge_queue.MergeQueue._verify_merge", return_value=_PASSING_VR),
    ):
        await mq.enqueue(entry)
        results = await mq.process_all()

    assert results[0].timestamp != ""
    assert "T" in results[0].timestamp


async def test_notify_not_called_when_no_callback():
    """_notify does nothing when on_state_change is None."""
    mq = MergeQueue()  # no callback
    # Should not raise
    mq._notify()


async def test_history_maxlen_evicts_oldest():
    """_history deque evicts oldest entries when maxlen=50 is exceeded."""
    mq = MergeQueue()
    # Manually fill history beyond maxlen
    for i in range(55):
        e = MergeEntry(
            session_id=i,
            branch_name=f"agent/{i}",
            worktree_path="/tmp/wt",
            base_dir="/tmp/base",
        )
        r = MergeResult(session_id=i, success=True)
        mq._history.append((e, r))
    assert len(mq._history) == 50
    # Oldest (session_id=0..4) should be evicted; newest (session_id=5..54) remain
    first_entry, _ = mq._history[0]
    assert first_entry.session_id == 5


# ---------------------------------------------------------------------------
# Task 4: snapshot() method
# ---------------------------------------------------------------------------


async def test_snapshot_empty_queue():
    """snapshot() returns empty structure when queue is idle."""
    mq = MergeQueue()
    snap = mq.snapshot()
    assert snap["pending"] == []
    assert snap["active"] is None
    assert snap["deferred"] == []
    assert snap["conflicts"] == []
    assert snap["history"] == []


async def test_snapshot_shows_pending():
    """snapshot() includes pending entries."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=1,
        branch_name="agent/1",
        worktree_path="/tmp/wt",
        base_dir="/proj",
        queued_at="2026-03-15T10:00:00Z",
    )
    with patch("golem.merge_queue.get_changed_files", return_value=["f.py"]):
        await mq.enqueue(entry)
    snap = mq.snapshot()
    assert len(snap["pending"]) == 1
    assert snap["pending"][0]["session_id"] == 1


async def test_snapshot_derives_deferred():
    """snapshot() filters deferred entries from _history."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=5, branch_name="agent/5", worktree_path="/tmp", base_dir="/proj"
    )
    result = MergeResult(
        session_id=5,
        success=False,
        deferred=True,
        error="dirty tree",
        timestamp="2026-03-15T10:00:00Z",
    )
    mq._history.append((entry, result))
    snap = mq.snapshot()
    assert len(snap["deferred"]) == 1
    assert snap["deferred"][0]["session_id"] == 5


async def test_snapshot_derives_conflicts():
    """snapshot() filters conflict entries from _history."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=7, branch_name="agent/7", worktree_path="/tmp", base_dir="/proj"
    )
    result = MergeResult(
        session_id=7,
        success=False,
        conflict_files=["a.py"],
        timestamp="2026-03-15T10:00:00Z",
    )
    mq._history.append((entry, result))
    snap = mq.snapshot()
    assert len(snap["conflicts"]) == 1
    assert snap["conflicts"][0]["session_id"] == 7


async def test_snapshot_dedup_after_retry():
    """After retry succeeds, old deferred entry excluded from snapshot."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=42, branch_name="agent/42", worktree_path="/tmp", base_dir="/proj"
    )
    r1 = MergeResult(
        session_id=42,
        success=False,
        deferred=True,
        error="dirty",
        timestamp="2026-03-15T10:00:00Z",
    )
    mq._history.append((entry, r1))
    r2 = MergeResult(
        session_id=42,
        success=True,
        merge_sha="abc123",
        timestamp="2026-03-15T10:01:00Z",
    )
    mq._history.append((entry, r2))
    snap = mq.snapshot()
    assert len(snap["deferred"]) == 0  # old deferred entry excluded
    assert len(snap["history"]) == 2  # both entries in history


# ---------------------------------------------------------------------------
# Task 5: retry() method
# ---------------------------------------------------------------------------


async def test_retry_re_enqueues_from_history():
    """retry() finds a failed entry in _history and re-enqueues it."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=42,
        branch_name="agent/42",
        worktree_path="/tmp/wt",
        base_dir="/proj",
        changed_files=["f.py"],
    )
    result = MergeResult(
        session_id=42, success=False, deferred=True, error="dirty tree"
    )
    mq._history.append((entry, result))

    with patch("golem.merge_queue.get_changed_files", return_value=["f.py"]):
        re_entry = await mq.retry(42)

    assert re_entry.session_id == 42
    assert re_entry.branch_name == "agent/42"
    assert mq.pending == 1


async def test_retry_unknown_session_raises():
    """retry() raises ValueError for unknown session_id."""
    mq = MergeQueue()
    with pytest.raises(ValueError, match="No retryable entry"):
        await mq.retry(999)


async def test_retry_skips_successful_entries():
    """retry() does not retry entries that already succeeded."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=42, branch_name="agent/42", worktree_path="/tmp", base_dir="/proj"
    )
    result = MergeResult(session_id=42, success=True, merge_sha="abc")
    mq._history.append((entry, result))
    with pytest.raises(ValueError, match="No retryable entry"):
        await mq.retry(42)


async def test_retry_calls_on_state_change():
    """retry() invokes the on_state_change callback."""
    cb = MagicMock()
    mq = MergeQueue(on_state_change=cb)
    entry = MergeEntry(
        session_id=42,
        branch_name="agent/42",
        worktree_path="/tmp/wt",
        base_dir="/proj",
        changed_files=["f.py"],
    )
    result = MergeResult(
        session_id=42,
        success=False,
        error="conflict",
        conflict_files=["f.py"],
    )
    mq._history.append((entry, result))
    cb.reset_mock()

    with patch("golem.merge_queue.get_changed_files", return_value=["f.py"]):
        await mq.retry(42)

    assert cb.call_count >= 1


# ---------------------------------------------------------------------------
# Task 38: Thread safety — _processing field, pending/detect_overlaps/snapshot
# ---------------------------------------------------------------------------


async def test_pending_includes_inflight_entries():
    """pending counts entries currently being processed by process_all()."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=1,
        branch_name="agent/1",
        worktree_path="/tmp/wt",
        base_dir="/tmp/base",
        changed_files=["a.py"],
    )
    merge_started = asyncio.Event()
    allow_finish = asyncio.Event()

    async def slow_merge_one(_entry):
        merge_started.set()
        await allow_finish.wait()
        return MergeResult(session_id=_entry.session_id, success=True)

    mq._queue.append(entry)

    with patch.object(mq, "_merge_one", side_effect=slow_merge_one):
        task = asyncio.ensure_future(mq.process_all())
        await asyncio.wait_for(merge_started.wait(), timeout=2.0)
        # While merge is in flight: _queue is empty but entry should be counted
        assert mq.pending == 1, (
            "pending must include in-flight entries; got %d" % mq.pending
        )
        allow_finish.set()
        await task


async def test_detect_overlaps_includes_inflight_entries():
    """detect_overlaps() includes entries currently being merged."""
    mq = MergeQueue()
    e1 = MergeEntry(
        session_id=1,
        branch_name="agent/1",
        worktree_path="/tmp/wt1",
        base_dir="/tmp/base",
        changed_files=["shared.py"],
    )
    e2 = MergeEntry(
        session_id=2,
        branch_name="agent/2",
        worktree_path="/tmp/wt2",
        base_dir="/tmp/base",
        changed_files=["shared.py"],
    )
    merge_started = asyncio.Event()
    allow_finish = asyncio.Event()
    call_count = 0

    async def slow_merge_one(_entry):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            merge_started.set()
            await allow_finish.wait()
        return MergeResult(session_id=_entry.session_id, success=True)

    mq._queue.extend([e1, e2])

    with patch.object(mq, "_merge_one", side_effect=slow_merge_one):
        task = asyncio.ensure_future(mq.process_all())
        await asyncio.wait_for(merge_started.wait(), timeout=2.0)
        # Both e1 and e2 are in _processing (all moved atomically)
        overlaps = mq.detect_overlaps()
        assert "shared.py" in overlaps, (
            "detect_overlaps must see in-flight entry; got %r" % overlaps
        )
        assert 1 in overlaps["shared.py"]
        allow_finish.set()
        await task


async def test_snapshot_shows_inflight_entries():
    """snapshot() active and pending reflect in-flight state correctly."""
    mq = MergeQueue()
    e1 = MergeEntry(
        session_id=5,
        branch_name="agent/5",
        worktree_path="/tmp/wt1",
        base_dir="/tmp/base",
        changed_files=["x.py"],
    )
    e2 = MergeEntry(
        session_id=6,
        branch_name="agent/6",
        worktree_path="/tmp/wt2",
        base_dir="/tmp/base",
        changed_files=["y.py"],
    )
    merge_started = asyncio.Event()
    allow_finish = asyncio.Event()
    call_count = 0

    async def slow_merge_one(_entry):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            merge_started.set()
            await allow_finish.wait()
        return MergeResult(session_id=_entry.session_id, success=True)

    mq._queue.extend([e1, e2])

    with patch.object(mq, "_merge_one", side_effect=slow_merge_one):
        task = asyncio.ensure_future(mq.process_all())
        await asyncio.wait_for(merge_started.wait(), timeout=2.0)
        snap = mq.snapshot()
        # Active entry (e1) should be in active, not pending
        assert snap["active"] is not None
        assert snap["active"]["session_id"] == 5
        # Remaining in-flight entry (e2) should be in pending
        pending_ids = [p["session_id"] for p in snap["pending"]]
        assert 6 in pending_ids, (
            "snapshot pending must include non-active in-flight entry; got %r"
            % pending_ids
        )
        assert 5 not in pending_ids, "active entry must not be in pending"
        allow_finish.set()
        await task


async def test_retry_acquires_lock_safely():
    """retry() holds the lock while iterating history and releases before enqueue."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=99,
        branch_name="agent/99",
        worktree_path="/tmp/wt",
        base_dir="/tmp/base",
        changed_files=["f.py"],
    )
    result = MergeResult(session_id=99, success=False, error="conflict")
    mq._history.append((entry, result))

    # If retry() tries to acquire the lock while holding it, it would deadlock.
    # We verify it completes successfully (no deadlock / no ValueError).
    with patch("golem.merge_queue.get_changed_files", return_value=["f.py"]):
        re_entry = await asyncio.wait_for(mq.retry(99), timeout=2.0)

    assert re_entry.session_id == 99
    assert mq.pending == 1


async def test_processing_cleared_after_process_all():
    """_processing is empty after process_all() completes."""
    mq = MergeQueue()
    entry = MergeEntry(
        session_id=3,
        branch_name="agent/3",
        worktree_path="/tmp/wt",
        base_dir="/tmp/base",
        changed_files=["z.py"],
    )
    mq._queue.append(entry)

    with patch.object(
        mq,
        "_merge_one",
        return_value=MergeResult(session_id=3, success=True),
    ):
        await mq.process_all()

    assert mq._processing == [], "processing must be empty after process_all"


class TestMergeAgentCallbackSafety:
    """REL-004: on_merge_agent callback exceptions are caught; merge fails gracefully."""

    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="",
            error="merge conflict: conflicting changes",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_callback_exception_during_conflict_resolution_fails_gracefully(
        self, _gcf, _miw, base_entry
    ):
        """If on_merge_agent raises during conflict resolution, merge fails gracefully."""
        handler = MagicMock(side_effect=RuntimeError("agent crashed"))
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is False
        assert "merge agent error" in results[0].error

    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="sha1",
            missing_additions=[
                MissingAddition(file="f.py", expected_lines=["x"], description="d")
            ],
            agent_diff="diff text",
            merge_branch="merge-ready/1",
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_callback_exception_during_missing_additions_fails_gracefully(
        self, _gcf, _miw, base_entry
    ):
        """If on_merge_agent raises during missing additions, merge fails gracefully."""
        handler = MagicMock(side_effect=ValueError("callback exploded"))
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is False
        assert "merge agent error" in results[0].error


# ---------------------------------------------------------------------------
# BUG-005: Thread-safe reads — _thread_lock protects sync read methods
# ---------------------------------------------------------------------------

import threading


class TestThreadLockExists:
    """_thread_lock attribute is a threading.Lock instance."""

    def test_thread_lock_is_threading_lock(self):
        mq = MergeQueue()
        assert hasattr(mq, "_thread_lock"), "_thread_lock attribute must exist"
        assert isinstance(
            mq._thread_lock, type(threading.Lock())
        ), "_thread_lock must be a threading.Lock"


class TestSnapshotThreadSafe:
    """snapshot() is safe to call from asyncio.to_thread while process_all() mutates state."""

    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_snapshot_concurrent_with_process_all(self, _gcf, tmp_path):
        """snapshot() called via asyncio.to_thread while process_all() is running must not raise."""
        mq = MergeQueue()
        e1 = MergeEntry(
            session_id=10,
            branch_name="agent/10",
            worktree_path=str(tmp_path / "wt1"),
            base_dir=str(tmp_path / "base"),
            changed_files=["x.py"],
        )
        e2 = MergeEntry(
            session_id=11,
            branch_name="agent/11",
            worktree_path=str(tmp_path / "wt2"),
            base_dir=str(tmp_path / "base"),
            changed_files=["y.py"],
        )
        mq._queue.extend([e1, e2])

        merge_started = asyncio.Event()
        allow_finish = asyncio.Event()
        snapshot_result: list = []
        snapshot_error: list = []

        async def slow_merge_one(_entry):
            merge_started.set()
            await allow_finish.wait()
            return MergeResult(session_id=_entry.session_id, success=True)

        async def background_snapshot():
            await merge_started.wait()
            try:
                snap = await asyncio.to_thread(mq.snapshot)
                snapshot_result.append(snap)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                snapshot_error.append(exc)
            finally:
                allow_finish.set()

        with patch.object(mq, "_merge_one", side_effect=slow_merge_one):
            process_task = asyncio.ensure_future(mq.process_all())
            snap_task = asyncio.ensure_future(background_snapshot())
            await asyncio.wait_for(asyncio.gather(process_task, snap_task), timeout=5.0)

        assert not snapshot_error, (
            "snapshot() raised under concurrent access: %s" % snapshot_error
        )
        assert len(snapshot_result) == 1
        snap = snapshot_result[0]
        # Snapshot must contain the required keys
        assert "pending" in snap
        assert "active" in snap
        assert "deferred" in snap
        assert "conflicts" in snap
        assert "history" in snap
