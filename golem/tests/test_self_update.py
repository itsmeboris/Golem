"""Tests for golem.self_update — SelfUpdateManager."""

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from golem.self_update import SelfUpdateManager
from golem.core.config import GolemFlowConfig


@pytest.fixture
def config():
    cfg = GolemFlowConfig()
    cfg.self_update_enabled = True
    cfg.self_update_branch = "master"
    cfg.self_update_interval_seconds = 60
    cfg.self_update_strategy = "merged_only"
    return cfg


@pytest.fixture
def state_dir(tmp_path):
    return tmp_path


@pytest.fixture
def manager(config, state_dir):
    return SelfUpdateManager(config, state_dir=state_dir)


class TestStateManagement:
    def test_save_and_load(self, manager, state_dir):
        manager._last_checked_sha = "abc123"
        manager._last_review_verdict = "ACCEPT"
        manager._update_history.append(
            {"sha": "abc123", "verdict": "ACCEPT", "applied": True}
        )
        manager.save_state()

        new_mgr = SelfUpdateManager(manager._config, state_dir=state_dir)
        new_mgr.load_state()
        assert new_mgr._last_checked_sha == "abc123"
        assert new_mgr._last_review_verdict == "ACCEPT"
        assert len(new_mgr._update_history) == 1

    def test_history_capped_at_50(self, manager):
        manager._update_history = [{"sha": str(i)} for i in range(60)]
        manager.save_state()
        manager.load_state()
        assert len(manager._update_history) == 50

    def test_load_missing_file(self, manager):
        manager.load_state()  # should not raise
        assert manager._last_checked_sha == ""

    def test_load_all_fields(self, manager, state_dir):
        """load_state populates all persisted fields."""
        manager._last_checked_sha = "sha1"
        manager._last_check_timestamp = "2026-01-01T00:00:00+00:00"
        manager._last_update_sha = "sha2"
        manager._last_update_timestamp = "2026-01-02T00:00:00+00:00"
        manager._last_review_verdict = "REJECT"
        manager._last_review_reasoning = "too risky"
        manager._pre_update_sha = "sha0"
        manager._last_startup_timestamp = "2026-01-03T00:00:00+00:00"
        manager._consecutive_crash_count = 3
        manager.save_state()

        new_mgr = SelfUpdateManager(manager._config, state_dir=state_dir)
        new_mgr.load_state()
        assert new_mgr._last_check_timestamp == "2026-01-01T00:00:00+00:00"
        assert new_mgr._last_update_sha == "sha2"
        assert new_mgr._last_update_timestamp == "2026-01-02T00:00:00+00:00"
        assert new_mgr._last_review_reasoning == "too risky"
        assert new_mgr._pre_update_sha == "sha0"
        assert new_mgr._last_startup_timestamp == "2026-01-03T00:00:00+00:00"
        assert new_mgr._consecutive_crash_count == 3

    def test_load_corrupt_file(self, manager, state_dir):
        """load_state logs warning on corrupt JSON and returns without crashing."""
        state_path = state_dir / "self_update_state.json"
        state_path.write_text("not-json", encoding="utf-8")
        manager.load_state()  # should not raise
        assert manager._last_checked_sha == ""

    def test_default_state_dir(self, config):
        """Default state_dir is Path('data')."""
        mgr = SelfUpdateManager(config)
        assert mgr._state_dir == Path("data")


class TestPollCycle:
    @pytest.mark.asyncio
    async def test_no_new_commits(self, manager):
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="abc"),
            patch.object(manager, "_get_remote_sha", return_value="abc"),
        ):
            await manager._check_for_updates()
        assert manager._last_review_verdict == ""

    @pytest.mark.asyncio
    async def test_fetch_failure_skips(self, manager):
        with patch.object(manager, "_fetch", return_value=False):
            await manager._check_for_updates()

    @pytest.mark.asyncio
    async def test_merged_only_rejects_force_push(self, manager):
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="aaa"),
            patch.object(manager, "_get_remote_sha", return_value="bbb"),
            patch.object(manager, "_is_fast_forward", return_value=False),
        ):
            await manager._check_for_updates()
        assert manager._last_review_verdict == ""

    @pytest.mark.asyncio
    async def test_any_commit_accepts_force_push(self, manager, config):
        config.self_update_strategy = "any_commit"
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="aaa"),
            patch.object(manager, "_get_remote_sha", return_value="bbb"),
            patch.object(manager, "_is_fast_forward", return_value=False),
            patch.object(manager, "_get_diff", return_value="diff content"),
            patch.object(manager, "_get_commit_log", return_value="fix: thing"),
            patch.object(manager, "_review", return_value=("REJECT", "unsafe")),
        ):
            await manager._check_for_updates()
        assert manager._last_review_verdict == "REJECT"

    @pytest.mark.asyncio
    async def test_empty_remote_sha_skips(self, manager):
        """When remote SHA is empty string, skip update."""
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="aaa"),
            patch.object(manager, "_get_remote_sha", return_value=""),
            patch.object(manager, "_review") as mock_review,
        ):
            await manager._check_for_updates()
        mock_review.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_diff_skips_review(self, manager):
        """When diff is empty, skip review."""
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="aaa"),
            patch.object(manager, "_get_remote_sha", return_value="bbb"),
            patch.object(manager, "_is_fast_forward", return_value=True),
            patch.object(manager, "_get_diff", return_value=""),
            patch.object(manager, "_get_commit_log", return_value="fix: thing"),
            patch.object(manager, "_review") as mock_review,
        ):
            await manager._check_for_updates()
        mock_review.assert_not_called()


class TestReviewGate:
    @pytest.mark.asyncio
    async def test_accept_proceeds_to_verification(self, manager):
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="aaa"),
            patch.object(manager, "_get_remote_sha", return_value="bbb"),
            patch.object(manager, "_is_fast_forward", return_value=True),
            patch.object(manager, "_get_diff", return_value="diff"),
            patch.object(manager, "_get_commit_log", return_value="commit"),
            patch.object(manager, "_review", return_value=("ACCEPT", "looks good")),
            patch.object(
                manager, "_verify_in_worktree", return_value=True
            ) as mock_verify,
        ):
            await manager._check_for_updates()
        mock_verify.assert_called_once()

    @pytest.mark.asyncio
    async def test_reject_skips_verification(self, manager):
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="aaa"),
            patch.object(manager, "_get_remote_sha", return_value="bbb"),
            patch.object(manager, "_is_fast_forward", return_value=True),
            patch.object(manager, "_get_diff", return_value="diff"),
            patch.object(manager, "_get_commit_log", return_value="commit"),
            patch.object(manager, "_review", return_value=("REJECT", "unsafe")),
            patch.object(manager, "_verify_in_worktree") as mock_verify,
        ):
            await manager._check_for_updates()
        mock_verify.assert_not_called()
        assert len(manager._update_history) == 1
        assert manager._update_history[0]["verdict"] == "REJECT"


class TestVerificationGate:
    @pytest.mark.asyncio
    async def test_verification_pass_triggers_reload(self, manager):
        reload_event = asyncio.Event()
        manager._reload_event = reload_event
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="aaa"),
            patch.object(manager, "_get_remote_sha", return_value="bbb"),
            patch.object(manager, "_is_fast_forward", return_value=True),
            patch.object(manager, "_get_diff", return_value="diff"),
            patch.object(manager, "_get_commit_log", return_value="commit"),
            patch.object(manager, "_review", return_value=("ACCEPT", "ok")),
            patch.object(manager, "_verify_in_worktree", return_value=True),
        ):
            await manager._check_for_updates()
        assert reload_event.is_set()
        assert manager._verified_sha == "bbb"

    @pytest.mark.asyncio
    async def test_verification_fail_no_reload(self, manager):
        reload_event = asyncio.Event()
        manager._reload_event = reload_event
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="aaa"),
            patch.object(manager, "_get_remote_sha", return_value="bbb"),
            patch.object(manager, "_is_fast_forward", return_value=True),
            patch.object(manager, "_get_diff", return_value="diff"),
            patch.object(manager, "_get_commit_log", return_value="commit"),
            patch.object(manager, "_review", return_value=("ACCEPT", "ok")),
            patch.object(manager, "_verify_in_worktree", return_value=False),
        ):
            await manager._check_for_updates()
        assert not reload_event.is_set()

    @pytest.mark.asyncio
    async def test_verification_pass_no_reload_event(self, manager):
        """When reload_event is None, no error even after verification passes."""
        manager._reload_event = None
        with (
            patch.object(manager, "_fetch", return_value=True),
            patch.object(manager, "_get_head_sha", return_value="aaa"),
            patch.object(manager, "_get_remote_sha", return_value="bbb"),
            patch.object(manager, "_is_fast_forward", return_value=True),
            patch.object(manager, "_get_diff", return_value="diff"),
            patch.object(manager, "_get_commit_log", return_value="commit"),
            patch.object(manager, "_review", return_value=("ACCEPT", "ok")),
            patch.object(manager, "_verify_in_worktree", return_value=True),
        ):
            await manager._check_for_updates()
        assert manager._verified_sha == "bbb"


class TestCrashLoopDetection:
    def test_quick_restart_increments_count(self, manager):
        manager._pre_update_sha = "old_sha"
        manager._last_startup_timestamp = datetime.now(timezone.utc).isoformat()
        manager._consecutive_crash_count = 0
        # Simulate quick restart
        manager._check_crash_loop()
        assert manager._consecutive_crash_count == 1

    def test_two_crashes_triggers_rollback(self, manager):
        manager._pre_update_sha = "old_sha"
        manager._last_startup_timestamp = datetime.now(timezone.utc).isoformat()
        manager._consecutive_crash_count = 1
        with patch.object(manager, "_rollback_to") as mock_rb:
            manager._check_crash_loop()
        mock_rb.assert_called_once_with("old_sha")
        assert manager._consecutive_crash_count == 0

    def test_normal_restart_clears_count(self, manager):
        manager._pre_update_sha = "old_sha"
        manager._last_startup_timestamp = "2020-01-01T00:00:00+00:00"
        manager._consecutive_crash_count = 1
        manager._check_crash_loop()
        assert manager._consecutive_crash_count == 0
        assert manager._pre_update_sha is None

    def test_no_pre_update_sha_noop(self, manager):
        """If no pre_update_sha, _check_crash_loop returns immediately."""
        manager._pre_update_sha = None
        manager._last_startup_timestamp = datetime.now(timezone.utc).isoformat()
        manager._consecutive_crash_count = 0
        manager._check_crash_loop()
        assert manager._consecutive_crash_count == 0

    def test_no_startup_timestamp_noop(self, manager):
        """If no last_startup_timestamp, _check_crash_loop returns immediately."""
        manager._pre_update_sha = "old_sha"
        manager._last_startup_timestamp = None
        manager._consecutive_crash_count = 0
        manager._check_crash_loop()
        assert manager._consecutive_crash_count == 0

    def test_invalid_timestamp_returns(self, manager):
        """If timestamp is invalid, _check_crash_loop returns without modifying count."""
        manager._pre_update_sha = "old_sha"
        manager._last_startup_timestamp = "not-a-timestamp"
        manager._consecutive_crash_count = 0
        manager._check_crash_loop()
        assert manager._consecutive_crash_count == 0

    def test_rollback_failure_logs_error(self, manager):
        """_rollback_to logs error on CalledProcessError."""
        with patch(
            "golem.self_update.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git", stderr="err"),
        ):
            manager._rollback_to("abc")  # Should not raise

    def test_rollback_success(self, manager):
        """_rollback_to calls git reset --hard with given sha."""
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            manager._rollback_to("abc123")
        cmd = mock_run.call_args[0][0]
        assert cmd == ["git", "reset", "--hard", "abc123"]


class TestApplyUpdate:
    @pytest.mark.asyncio
    async def test_merged_only_uses_ff_merge(self, manager):
        manager._verified_sha = "abc123"
        manager._config.self_update_strategy = "merged_only"
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            await manager.apply_update()
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["git", "merge", "--ff-only", "abc123"]

    @pytest.mark.asyncio
    async def test_any_commit_uses_hard_reset(self, manager):
        manager._verified_sha = "abc123"
        manager._config.self_update_strategy = "any_commit"
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            await manager.apply_update()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["git", "reset", "--hard", "abc123"]

    @pytest.mark.asyncio
    async def test_failure_clears_verified_sha(self, manager):
        manager._verified_sha = "abc123"
        with patch(
            "golem.self_update.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git", stderr="err"),
        ):
            await manager.apply_update()
        assert manager._verified_sha is None

    @pytest.mark.asyncio
    async def test_no_op_without_verified_sha(self, manager):
        manager._verified_sha = None
        with patch("golem.self_update.subprocess.run") as mock_run:
            await manager.apply_update()
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_apply_saves_state(self, manager, state_dir):
        """apply_update saves state with last_update_sha after success."""
        manager._verified_sha = "abc123"
        manager._config.self_update_strategy = "merged_only"
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            await manager.apply_update()
        assert manager._last_update_sha == "abc123"
        assert (state_dir / "self_update_state.json").exists()


class TestGitHelpers:
    def test_get_head_sha_success(self, manager):
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="abc123\n", returncode=0)
            sha = manager._get_head_sha()
        assert sha == "abc123"

    def test_get_head_sha_failure(self, manager):
        with patch(
            "golem.self_update.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            sha = manager._get_head_sha()
        assert sha == ""

    def test_get_remote_sha_success(self, manager):
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="def456\n", returncode=0)
            sha = manager._get_remote_sha()
        assert sha == "def456"

    def test_get_remote_sha_failure(self, manager):
        with patch(
            "golem.self_update.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            sha = manager._get_remote_sha()
        assert sha == ""

    def test_fetch_success(self, manager):
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = manager._fetch()
        assert result is True

    def test_fetch_failure(self, manager):
        with patch(
            "golem.self_update.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git", stderr="err"),
        ):
            result = manager._fetch()
        assert result is False

    def test_is_fast_forward_true(self, manager):
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = manager._is_fast_forward("abc")
        assert result is True

    def test_is_fast_forward_false(self, manager):
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            result = manager._is_fast_forward("abc")
        assert result is False

    def test_is_fast_forward_exception(self, manager):
        with patch(
            "golem.self_update.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = manager._is_fast_forward("abc")
        assert result is False

    def test_get_diff_success(self, manager):
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="diff output", returncode=0)
            result = manager._get_diff("abc")
        assert result == "diff output"

    def test_get_diff_failure(self, manager):
        with patch(
            "golem.self_update.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = manager._get_diff("abc")
        assert result == ""

    def test_get_commit_log_success(self, manager):
        with patch("golem.self_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="log output", returncode=0)
            result = manager._get_commit_log("abc")
        assert result == "log output"

    def test_get_commit_log_failure(self, manager):
        with patch(
            "golem.self_update.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git"),
        ):
            result = manager._get_commit_log("abc")
        assert result == ""


class TestReviewMethod:
    @pytest.mark.asyncio
    async def test_accept_response(self, manager):
        with patch.object(
            manager, "_run_review_agent", return_value="ACCEPT\nLooks good"
        ):
            verdict, reasoning = await manager._review("diff", "log")
        assert verdict == "ACCEPT"
        assert reasoning == "Looks good"

    @pytest.mark.asyncio
    async def test_reject_response(self, manager):
        with patch.object(
            manager, "_run_review_agent", return_value="REJECT\nToo risky"
        ):
            verdict, reasoning = await manager._review("diff", "log")
        assert verdict == "REJECT"
        assert reasoning == "Too risky"

    @pytest.mark.asyncio
    async def test_ambiguous_response(self, manager):
        with patch.object(manager, "_run_review_agent", return_value="MAYBE\nUnclear"):
            verdict, reasoning = await manager._review("diff", "log")
        assert verdict == "REJECT"
        assert "Ambiguous" in reasoning

    @pytest.mark.asyncio
    async def test_response_without_reasoning(self, manager):
        """Single-line response without reasoning returns empty string."""
        with patch.object(manager, "_run_review_agent", return_value="ACCEPT"):
            verdict, reasoning = await manager._review("diff", "log")
        assert verdict == "ACCEPT"
        assert reasoning == ""

    @pytest.mark.asyncio
    async def test_agent_exception(self, manager):
        with patch.object(
            manager, "_run_review_agent", side_effect=RuntimeError("connection failed")
        ):
            verdict, reasoning = await manager._review("diff", "log")
        assert verdict == "REJECT"
        assert "Agent error" in reasoning

    def test_run_review_agent_success(self, manager):
        mock_result = MagicMock(returncode=0, stdout="ACCEPT\nok")
        with patch("golem.self_update.subprocess.run", return_value=mock_result):
            result = manager._run_review_agent("some prompt")
        assert result == "ACCEPT\nok"

    def test_run_review_agent_failure(self, manager):
        mock_result = MagicMock(returncode=1, stderr="error msg")
        with patch("golem.self_update.subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="Claude review failed"):
                manager._run_review_agent("some prompt")


class TestVerifyInWorktree:
    @pytest.mark.asyncio
    async def test_success(self, manager):
        mock_vr = MagicMock(passed=True)
        with (
            patch("golem.self_update.subprocess.run") as mock_run,
            patch("golem.verifier.run_verification", return_value=mock_vr),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = await manager._verify_in_worktree("abc123")
        assert result is True

    @pytest.mark.asyncio
    async def test_failure_returns_false(self, manager):
        mock_vr = MagicMock(passed=False)
        with (
            patch("golem.self_update.subprocess.run") as mock_run,
            patch("golem.verifier.run_verification", return_value=mock_vr),
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = await manager._verify_in_worktree("abc123")
        assert result is False

    @pytest.mark.asyncio
    async def test_exception_returns_false(self, manager):
        with patch(
            "golem.self_update.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "git", stderr="err"),
        ):
            result = await manager._verify_in_worktree("abc123")
        assert result is False

    @pytest.mark.asyncio
    async def test_worktree_cleanup_on_exception(self, manager):
        """Worktree remove is called even when verification raises."""
        call_count = [0]

        def side_effect(cmd, **kwargs):
            call_count[0] += 1
            if "add" in cmd:
                return MagicMock(returncode=0)
            if "remove" in cmd:
                return MagicMock(returncode=0)
            return MagicMock(returncode=0)

        with (
            patch("golem.self_update.subprocess.run", side_effect=side_effect),
            patch(
                "golem.verifier.run_verification", side_effect=RuntimeError("test fail")
            ),
        ):
            result = await manager._verify_in_worktree("abc123")
        assert result is False

    @pytest.mark.asyncio
    async def test_worktree_cleanup_exception_ignored(self, manager):
        """Exception in worktree removal does not propagate."""
        mock_vr = MagicMock(passed=True)
        run_calls = []

        def side_effect(cmd, **kwargs):
            run_calls.append(cmd)
            if "remove" in cmd:
                raise OSError("cannot remove")
            return MagicMock(returncode=0)

        with (
            patch("golem.self_update.subprocess.run", side_effect=side_effect),
            patch("golem.verifier.run_verification", return_value=mock_vr),
        ):
            result = await manager._verify_in_worktree("abc123")
        assert result is True


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, manager):
        with (
            patch.object(manager, "_update_loop", new=AsyncMock()) as mock_loop,
            patch("golem.self_update.asyncio.create_task") as mock_ct,
        ):
            mock_ct.return_value = MagicMock()
            manager.start()
        mock_ct.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, manager):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        manager._task = mock_task
        manager.stop()
        mock_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_does_not_cancel_done_task(self, manager):
        mock_task = MagicMock()
        mock_task.done.return_value = True
        manager._task = mock_task
        manager.stop()
        mock_task.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_with_no_task(self, manager):
        manager._task = None
        manager.stop()  # should not raise

    def test_snapshot_returns_correct_keys(self, manager):
        with patch.object(manager, "_get_head_sha", return_value="current"):
            snap = manager.snapshot()
        assert "enabled" in snap
        assert "branch" in snap
        assert "strategy" in snap
        assert "last_checked_sha" in snap
        assert "last_check_timestamp" in snap
        assert "last_review_verdict" in snap
        assert "last_review_reasoning" in snap
        assert "current_sha" in snap
        assert "update_history" in snap
        assert snap["current_sha"] == "current"


class TestUpdateLoop:
    @pytest.mark.asyncio
    async def test_loop_cancels_on_cancelled_error(self, manager):
        """_update_loop breaks cleanly on CancelledError."""
        call_count = [0]

        async def fake_sleep(_):
            call_count[0] += 1
            raise asyncio.CancelledError()

        with patch("golem.self_update.asyncio.sleep", side_effect=fake_sleep):
            await manager._update_loop()
        assert call_count[0] == 1

    @pytest.mark.asyncio
    async def test_loop_continues_on_exception(self, manager):
        """_update_loop catches generic exceptions and continues."""
        call_count = [0]

        async def fake_sleep(_):
            pass

        async def fake_check():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ValueError("transient error")
            raise asyncio.CancelledError()

        with (
            patch("golem.self_update.asyncio.sleep", side_effect=fake_sleep),
            patch.object(manager, "_check_for_updates", side_effect=fake_check),
        ):
            await manager._update_loop()
        assert call_count[0] == 3


class TestDisabled:
    def test_not_enabled(self, state_dir):
        config = GolemFlowConfig()
        config.self_update_enabled = False
        mgr = SelfUpdateManager(config, state_dir=state_dir)
        assert mgr._config.self_update_enabled is False
