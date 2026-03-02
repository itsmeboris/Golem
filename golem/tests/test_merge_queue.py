# pylint: disable=too-few-public-methods,redefined-outer-name
"""Tests for golem.merge_queue — sequential merge queue for cross-task coordination."""

from unittest.mock import MagicMock, patch

import pytest

from golem.merge_queue import MergeEntry, MergeQueue, MergeResult
from golem.merge_review import ReconciliationResult
from golem.worktree_manager import MissingAddition


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
    @patch("golem.merge_queue.verify_merge_integrity", return_value=[])
    @patch("golem.merge_queue.merge_and_cleanup", return_value="abc123")
    @patch("golem.merge_queue.get_agent_diff", return_value="")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_empty_queue(self, _gcf, _gad, _mac, _vmi, queue):
        results = await queue.process_all()
        assert results == []

    @patch("golem.merge_queue.verify_merge_integrity", return_value=[])
    @patch("golem.merge_queue.merge_and_cleanup", return_value="sha1")
    @patch("golem.merge_queue.get_agent_diff", return_value="")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_priority_sorting(self, _gcf, _gad, mock_mac, _vmi, queue):
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

    @patch("golem.merge_queue.verify_merge_integrity", return_value=[])
    @patch("golem.merge_queue.merge_and_cleanup", return_value="sha1")
    @patch("golem.merge_queue.get_agent_diff", return_value="")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_results_accumulated(self, _gcf, _gad, _mac, _vmi, queue, base_entry):
        await queue.enqueue(base_entry)
        await queue.process_all()
        assert len(queue._results) == 1


class TestMergeOneSuccess:
    @patch("golem.merge_queue.verify_merge_integrity", return_value=[])
    @patch("golem.merge_queue.merge_and_cleanup", return_value="deadbeef")
    @patch("golem.merge_queue.get_agent_diff", return_value="diff")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_successful_merge(
        self, _gcf, _gad, mock_mac, _vmi, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert len(results) == 1
        assert results[0].success is True
        assert results[0].merge_sha == "deadbeef"
        assert results[0].error == ""
        mock_mac.assert_called_once_with("/repo", 1, "/tmp/wt-1")


class TestMergeOneFailureNoHandler:
    @patch("golem.merge_queue.cleanup_worktree")
    @patch("golem.merge_queue.merge_and_cleanup", return_value=None)
    @patch("golem.merge_queue.get_agent_diff", return_value="")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_no_sha_no_conflict_handler(
        self, _gcf, _gad, _mac, mock_cw, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is False
        assert results[0].error == "merge failed or no changes"
        assert results[0].conflict_files == ["a.py", "b.py"]
        mock_cw.assert_called_once_with("/repo", "/tmp/wt-1", keep_branch=True)


class TestMergeOneConflictHandlerSucceeds:
    @patch("golem.merge_queue.verify_merge_integrity", return_value=[])
    @patch("golem.merge_queue.merge_and_cleanup", side_effect=["", "resolved_sha"])
    @patch("golem.merge_queue.get_agent_diff", return_value="")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_on_conflict_resolves(self, _gcf, _gad, _mac, _vmi, base_entry):
        handler = MagicMock(return_value=True)
        q = MergeQueue(on_conflict=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "resolved_sha"
        handler.assert_called_once_with(base_entry, ["a.py", "b.py"])


class TestMergeOneConflictHandlerFails:
    @patch("golem.merge_queue.cleanup_worktree")
    @patch("golem.merge_queue.merge_and_cleanup", return_value=None)
    @patch("golem.merge_queue.get_agent_diff", return_value="")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_on_conflict_returns_false(
        self, _gcf, _gad, _mac, mock_cw, base_entry
    ):
        handler = MagicMock(return_value=False)
        q = MergeQueue(on_conflict=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is False
        assert results[0].error == "merge failed or no changes"
        mock_cw.assert_called_once()


class TestMergeOneConflictHandlerRetryFails:
    @patch("golem.merge_queue.cleanup_worktree")
    @patch("golem.merge_queue.merge_and_cleanup", return_value=None)
    @patch("golem.merge_queue.get_agent_diff", return_value="")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_on_conflict_true_but_retry_fails(
        self, _gcf, _gad, mock_mac, mock_cw, base_entry
    ):
        handler = MagicMock(return_value=True)
        q = MergeQueue(on_conflict=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is False
        assert mock_mac.call_count == 2
        mock_cw.assert_called_once()


class TestMergeOneException:
    @patch("golem.merge_queue.merge_and_cleanup", side_effect=RuntimeError("git broke"))
    @patch("golem.merge_queue.get_agent_diff", return_value="")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_exception_during_merge(self, _gcf, _gad, _mac, queue, base_entry):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is False
        assert results[0].error == "git broke"
        assert results[0].merge_sha == ""


class TestOnReconcileType:
    def test_type_alias_is_none_by_default(self):
        q = MergeQueue()
        assert q._on_reconcile is None


class TestMergeWithVerifyClean:
    @patch("golem.merge_queue.verify_merge_integrity", return_value=[])
    @patch("golem.merge_queue.merge_and_cleanup", return_value="clean123")
    @patch("golem.merge_queue.get_agent_diff", return_value="some diff")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_clean_verify_succeeds(
        self, _gcf, _gad, _mac, _vmi, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "clean123"


class TestMergeWithVerifyMissingNoHandler:
    @patch(
        "golem.merge_queue.verify_merge_integrity",
        return_value=[
            MissingAddition(file="lost.py", expected_lines=["x"], description="gone")
        ],
    )
    @patch("golem.merge_queue.merge_and_cleanup", return_value="sha1")
    @patch("golem.merge_queue.get_agent_diff", return_value="diff")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_missing_no_reconciler_succeeds_anyway(
        self, _gcf, _gad, _mac, _vmi, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        results = await queue.process_all()
        assert results[0].success is True


class TestMergeWithReconcileSuccess:
    @patch(
        "golem.merge_queue.verify_merge_integrity",
        return_value=[
            MissingAddition(file="f.py", expected_lines=["x"], description="d")
        ],
    )
    @patch("golem.merge_queue.merge_and_cleanup", return_value="sha1")
    @patch("golem.merge_queue.get_agent_diff", return_value="diff text")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_reconcile_succeeds(self, _gcf, _gad, _mac, _vmi, base_entry):
        handler = MagicMock(
            return_value=ReconciliationResult(resolved=True, commit_sha="fix1")
        )
        q = MergeQueue(on_reconcile=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is True
        assert results[0].merge_sha == "sha1"
        handler.assert_called_once()


class TestMergeWithReconcileFailure:
    @patch(
        "golem.merge_queue.verify_merge_integrity",
        return_value=[
            MissingAddition(file="f.py", expected_lines=["x"], description="d")
        ],
    )
    @patch("golem.merge_queue.merge_and_cleanup", return_value="sha1")
    @patch("golem.merge_queue.get_agent_diff", return_value="diff text")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_reconcile_fails(self, _gcf, _gad, _mac, _vmi, base_entry):
        handler = MagicMock(
            return_value=ReconciliationResult(resolved=False, explanation="cannot fix")
        )
        q = MergeQueue(on_reconcile=handler)
        await q.enqueue(base_entry)
        results = await q.process_all()
        assert results[0].success is False
        assert "reconciliation failed" in results[0].error
        assert results[0].conflict_files == ["f.py"]


class TestMergeGetAgentDiffCalled:
    @patch("golem.merge_queue.verify_merge_integrity", return_value=[])
    @patch("golem.merge_queue.merge_and_cleanup", return_value="sha")
    @patch("golem.merge_queue.get_agent_diff", return_value="the diff")
    @patch("golem.merge_queue.get_changed_files", return_value=[])
    async def test_get_agent_diff_called_with_correct_args(
        self, _gcf, mock_gad, _mac, _vmi, queue, base_entry
    ):
        await queue.enqueue(base_entry)
        await queue.process_all()
        mock_gad.assert_called_once_with("/repo", "golem/session-1")
