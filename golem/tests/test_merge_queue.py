# pylint: disable=too-few-public-methods,redefined-outer-name
"""Tests for golem.merge_queue — sequential merge queue for cross-task coordination."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from golem.merge_queue import MergeEntry, MergeQueue, MergeResult
from golem.merge_review import ReconciliationResult
from golem.worktree_manager import MergeOutcome, MissingAddition


@pytest.fixture()
def base_entry():
    return MergeEntry(
        session_id=1,
        branch_name="golem/session-1",
        worktree_path="/tmp/wt-1",
        base_dir="/repo",
        changed_files=["a.py", "b.py"],
    )


@pytest.fixture()
def queue():
    return MergeQueue()


class TestMergeEntryDefaults:
    def test_defaults(self):
        e = MergeEntry(
            session_id=1,
            branch_name="b",
            worktree_path="/wt",
            base_dir="/repo",
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
    async def test_populates_changed_files_when_empty(self, mock_gcf, queue):
        entry = MergeEntry(
            session_id=2,
            branch_name="golem/session-2",
            worktree_path="/tmp/wt-2",
            base_dir="/repo",
        )
        await queue.enqueue(entry)
        mock_gcf.assert_called_once_with("/repo", "golem/session-2")
        assert entry.changed_files == ["x.py"]
        assert queue.pending == 1

    @patch("golem.merge_queue.get_changed_files")
    async def test_keeps_existing_changed_files(self, mock_gcf, queue, base_entry):
        await queue.enqueue(base_entry)
        mock_gcf.assert_not_called()
        assert base_entry.changed_files == ["a.py", "b.py"]


class TestDetectOverlaps:
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_no_overlaps(self, _m, queue):
        e1 = MergeEntry(
            session_id=1,
            branch_name="b1",
            worktree_path="/wt1",
            base_dir="/repo",
            changed_files=["a.py"],
        )
        e2 = MergeEntry(
            session_id=2,
            branch_name="b2",
            worktree_path="/wt2",
            base_dir="/repo",
            changed_files=["b.py"],
        )
        await queue.enqueue(e1)
        await queue.enqueue(e2)
        assert queue.detect_overlaps() == {}

    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_with_overlaps(self, _m, queue):
        e1 = MergeEntry(
            session_id=1,
            branch_name="b1",
            worktree_path="/wt1",
            base_dir="/repo",
            changed_files=["shared.py", "a.py"],
        )
        e2 = MergeEntry(
            session_id=2,
            branch_name="b2",
            worktree_path="/wt2",
            base_dir="/repo",
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

    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(sha="sha1", merge_branch="merge-ready/1"),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_priority_sorting(self, _gcf, mock_miw, _ff, _rg, queue):
        low = MergeEntry(
            session_id=10,
            branch_name="b10",
            worktree_path="/wt10",
            base_dir="/repo",
            changed_files=["f.py"],
            priority=9,
        )
        high = MergeEntry(
            session_id=20,
            branch_name="b20",
            worktree_path="/wt20",
            base_dir="/repo",
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

    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(sha="sha1", merge_branch="merge-ready/1"),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_results_accumulated(self, _gcf, _miw, _ff, _rg, queue, base_entry):
        await queue.enqueue(base_entry)
        await queue.process_all()
        assert len(queue._results) == 1


class TestMergeOneSuccess:
    @patch("golem.merge_queue._run_git")
    @patch("golem.merge_queue.fast_forward_if_safe", return_value=(True, ""))
    @patch(
        "golem.merge_queue.merge_in_worktree",
        return_value=MergeOutcome(
            sha="deadbeef", agent_diff="diff", merge_branch="merge-ready/1"
        ),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_successful_merge(self, _gcf, mock_miw, _ff, _rg, queue, base_entry):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].merge_sha == "deadbeef"
        assert results[0].error == ""
        mock_miw.assert_called_once_with("/repo", 1)


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
    async def test_on_merge_agent_resolves(self, _gcf, _miw, _ff, _rg, base_entry):
        handler = MagicMock(return_value=ReconciliationResult(resolved=True))
        q = MergeQueue(on_merge_agent=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "resolved_sha"
        handler.assert_called_once_with(
            "/repo",
            1,
            "",
            ["a.py", "b.py"],
            [],
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
    async def test_clean_verify_succeeds(self, _gcf, _miw, _ff, _rg, queue, base_entry):
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
    async def test_reconcile_succeeds(self, _gcf, _miw, _ff, _rg, base_entry):
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
        self, _gcf, _miw, _ff, _rg, queue, base_entry
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

    @patch("golem.merge_queue.time.sleep")
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
        self, _gcf, _miw, _ff, _rg, mock_sleep, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "ok_after_retry"
        mock_sleep.assert_called_once_with(MergeQueue.INFRA_RETRY_DELAY)

    @patch("golem.merge_queue.time.sleep")
    @patch(
        "golem.merge_queue.merge_in_worktree",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    )
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_retry_exhausted_returns_failure(
        self, _gcf, _miw, mock_sleep, queue, base_entry
    ):
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
    async def test_failure_result_has_success_false(self, _gcf, _miw):
        """A merge that yields an empty SHA must produce success=False."""
        q = MergeQueue()
        entry = MergeEntry(
            session_id=42,
            branch_name="agent/42",
            worktree_path="/wt",
            base_dir="/repo",
            changed_files=["x.py"],
        )
        await q.enqueue(entry)
        results = await q.process_all()
        assert results[0].success is False
        assert results[0].error == "merge conflict: failed"


class TestDeferredMerge:
    """Test that when fast_forward_if_safe returns failure, result is deferred."""

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
    async def test_deferred_when_ff_fails(self, _gcf, _miw, _ff, base_entry):
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
