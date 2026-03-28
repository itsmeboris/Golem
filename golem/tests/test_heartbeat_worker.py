"""Tests for golem.heartbeat_worker."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from golem.core.config import GolemFlowConfig
from golem.heartbeat_worker import HeartbeatWorker, _path_hash


def _make_config(**overrides):
    defaults = {
        "profile": "local",
        "projects": [],
        "detection_tag": "[AGENT]",
        "heartbeat_enabled": True,
        "heartbeat_interval_seconds": 300,
        "heartbeat_idle_threshold_seconds": 900,
        "heartbeat_daily_budget_usd": 1.0,
        "heartbeat_max_inflight": 1,
        "heartbeat_candidate_limit": 5,
        "heartbeat_batch_size": 5,
        "heartbeat_tier1_every_n": 3,
        "heartbeat_dedup_ttl_days": 30,
        "heartbeat_not_automatable_ttl_days": 7,
        "heartbeat_category_failure_threshold": 3,
        "heartbeat_category_cooldown_hours": 6,
        "heartbeat_recent_commits_lookback": 20,
        "heartbeat_max_ticks": 0,
        "heartbeat_max_duration_seconds": 0,
    }
    defaults.update(overrides)
    return GolemFlowConfig(**defaults)


def _make_worker(tmp_path, repo_path="/fake/repo", **config_overrides):
    cfg = _make_config(**config_overrides)
    with patch("golem.heartbeat_worker.is_git_repo", return_value=False):
        return HeartbeatWorker(
            repo_path=repo_path,
            config=cfg,
            state_dir=tmp_path,
        )


class TestWorkerInit:
    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value="owner/repo")
    def test_detects_git_and_remote(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/my/project", _make_config(), state_dir=tmp_path)
        assert w.is_git is True
        assert w.github_remote == "owner/repo"

    @patch("golem.heartbeat_worker.is_git_repo", return_value=False)
    def test_non_git_skips_remote(self, _mock_git, tmp_path):
        w = HeartbeatWorker("/my/project", _make_config(), state_dir=tmp_path)
        assert w.is_git is False
        assert w.github_remote is None

    @patch("golem.heartbeat_worker.is_git_repo", return_value=False)
    def test_state_file_in_state_dir(self, _mock_git, tmp_path):
        w = HeartbeatWorker("/my/project", _make_config(), state_dir=tmp_path)
        assert w._state_file.parent == tmp_path

    @patch("golem.heartbeat_worker.is_git_repo", return_value=False)
    def test_defaults_initialized(self, _mock_git, tmp_path):
        w = HeartbeatWorker("/my/project", _make_config(), state_dir=tmp_path)
        assert w._dedup_memory == {}
        assert w._candidates == []
        assert w._inflight_task_ids == []
        assert w._tier2_completions_since_tier1 == 0
        assert w._tier1_owed is False
        assert w._category_failures == {}
        assert w._category_cooldown_until == {}


class TestWorkerStatePersistence:
    def test_save_load_round_trip(self, tmp_path):
        from datetime import datetime, timezone

        w = _make_worker(tmp_path)
        w._dedup_memory["test:1"] = {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "verdict": "candidate",
        }
        w._tier2_completions_since_tier1 = 2
        w.save_state()

        w2 = _make_worker(tmp_path)
        w2.load_state()
        assert "test:1" in w2._dedup_memory
        assert w2._tier2_completions_since_tier1 == 2

    def test_load_missing_file_is_empty(self, tmp_path):
        w = _make_worker(tmp_path)
        w.load_state()
        assert w._dedup_memory == {}

    def test_state_file_uses_path_hash(self, tmp_path):
        w = _make_worker(tmp_path, repo_path="/home/user/projects/foo")
        w.save_state()
        files = list(tmp_path.glob("*.json"))
        assert len(files) == 1
        assert files[0].name != "heartbeat_state.json"

    def test_load_corrupt_json(self, tmp_path):
        w = _make_worker(tmp_path)
        w._state_file.parent.mkdir(parents=True, exist_ok=True)
        w._state_file.write_text("not json{{{")
        w.load_state()
        assert w._dedup_memory == {}

    def test_save_creates_directory(self, tmp_path):
        nested = tmp_path / "nested" / "dir"
        w = _make_worker(nested)
        w.save_state()
        assert w._state_file.exists()

    def test_load_persists_category_state(self, tmp_path):
        w = _make_worker(tmp_path)
        w._category_failures["coverage"] = 2
        w._category_cooldown_until["coverage"] = "2026-06-01T00:00:00+00:00"
        w._tier1_owed = True
        w.save_state()

        w2 = _make_worker(tmp_path)
        w2.load_state()
        assert w2._category_failures["coverage"] == 2
        assert w2._category_cooldown_until["coverage"] == "2026-06-01T00:00:00+00:00"
        assert w2._tier1_owed is True

    def test_load_invalid_dedup_entry_skipped(self, tmp_path):
        from datetime import datetime, timezone

        w = _make_worker(tmp_path)
        state_data = {
            "dedup_memory": {
                "good:key": {
                    "evaluated_at": datetime.now(timezone.utc).isoformat(),
                    "verdict": "candidate",
                },
                "bad:key": {"no_required_fields": True},
            },
        }
        w._state_file.parent.mkdir(parents=True, exist_ok=True)
        w._state_file.write_text(json.dumps(state_data))
        w.load_state()
        assert "good:key" in w._dedup_memory
        assert "bad:key" not in w._dedup_memory

    def test_load_invalid_dedup_not_dict(self, tmp_path):
        w = _make_worker(tmp_path)
        state_data = {"dedup_memory": "not_a_dict"}
        w._state_file.parent.mkdir(parents=True, exist_ok=True)
        w._state_file.write_text(json.dumps(state_data))
        w.load_state()
        assert w._dedup_memory == {}

    def test_load_invalid_coverage_cache(self, tmp_path):
        w = _make_worker(tmp_path)
        state_data = {"coverage_cache": {"bad": "data"}}
        w._state_file.parent.mkdir(parents=True, exist_ok=True)
        w._state_file.write_text(json.dumps(state_data))
        w.load_state()
        assert w._coverage_cache is None

    def test_load_valid_coverage_cache(self, tmp_path):
        w = _make_worker(tmp_path)
        state_data = {
            "coverage_cache": {
                "commit_hash": "abc123",
                "ran_at": "2026-01-01T00:00:00+00:00",
                "uncovered_modules": ["golem/foo.py"],
            }
        }
        w._state_file.parent.mkdir(parents=True, exist_ok=True)
        w._state_file.write_text(json.dumps(state_data))
        w.load_state()
        assert w._coverage_cache is not None
        assert w._coverage_cache["commit_hash"] == "abc123"

    def test_load_prunes_expired_dedup(self, tmp_path):
        w = _make_worker(tmp_path)
        state_data = {
            "dedup_memory": {
                "old:key": {
                    "evaluated_at": "2000-01-01T00:00:00+00:00",
                    "verdict": "candidate",
                },
            }
        }
        w._state_file.parent.mkdir(parents=True, exist_ok=True)
        w._state_file.write_text(json.dumps(state_data))
        w.load_state()
        # old entry should be pruned by TTL
        assert "old:key" not in w._dedup_memory


class TestWorkerDedup:
    def test_is_deduped(self, tmp_path):
        w = _make_worker(tmp_path)
        assert w.is_deduped("test:1") is False
        w.record_dedup("test:1", "candidate")
        assert w.is_deduped("test:1") is True

    def test_record_dedup_with_task_id(self, tmp_path):
        w = _make_worker(tmp_path)
        w.record_dedup("test:1", "submitted", task_id=42)
        assert w._dedup_memory["test:1"]["task_id"] == 42

    def test_record_dedup_without_task_id(self, tmp_path):
        w = _make_worker(tmp_path)
        w.record_dedup("test:1", "candidate")
        assert "task_id" not in w._dedup_memory["test:1"]
        assert w._dedup_memory["test:1"]["verdict"] == "candidate"

    def test_get_claimed_issue_ids(self, tmp_path):
        w = _make_worker(tmp_path)
        w.record_dedup("github:42", "submitted", task_id=100)
        w.record_dedup("github:43", "not_automatable")
        w.record_dedup("improvement:coverage:foo", "submitted", task_id=101)
        ids = w.get_claimed_issue_ids()
        assert 42 in ids
        assert 43 not in ids
        # improvement: prefix is excluded
        assert 101 not in ids

    def test_get_claimed_issue_ids_non_numeric(self, tmp_path):
        w = _make_worker(tmp_path)
        w._dedup_memory["github:not-a-number"] = {
            "evaluated_at": "2026-01-01T00:00:00+00:00",
            "verdict": "submitted",
        }
        ids = w.get_claimed_issue_ids()
        assert len(ids) == 0

    def test_prune_dedup_removes_expired(self, tmp_path):
        w = _make_worker(tmp_path)
        w._dedup_memory["old:key"] = {
            "evaluated_at": "2000-01-01T00:00:00+00:00",
            "verdict": "candidate",
        }
        w._dedup_memory["new:key"] = {
            "evaluated_at": "2026-06-01T00:00:00+00:00",
            "verdict": "candidate",
        }
        w._prune_dedup()
        assert "old:key" not in w._dedup_memory
        assert "new:key" in w._dedup_memory

    def test_prune_dedup_invalid_entry(self, tmp_path):
        w = _make_worker(tmp_path)
        w._dedup_memory["bad:key"] = {"verdict": "candidate"}  # missing evaluated_at
        w._prune_dedup()
        assert "bad:key" not in w._dedup_memory


class TestWorkerCategoryCooldown:
    def test_not_cooled_down_by_default(self, tmp_path):
        w = _make_worker(tmp_path)
        assert w.is_category_cooled_down("error-handling") is False

    def test_on_task_completed_tracks_failures(self, tmp_path):
        from datetime import datetime, timezone

        w = _make_worker(tmp_path)
        w._dedup_memory["improvement:error-handling:fix1"] = {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "verdict": "submitted",
            "task_id": 100,
        }
        w._inflight_task_ids.append(100)
        w.on_task_completed(100, success=False)
        assert w._category_failures.get("error-handling", 0) == 1

    def test_on_task_completed_increments_tier2(self, tmp_path):
        from datetime import datetime, timezone

        w = _make_worker(tmp_path)
        w._dedup_memory["improvement:coverage:mod1"] = {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "verdict": "submitted",
            "task_id": 200,
        }
        w._inflight_task_ids.append(200)
        w.on_task_completed(200, success=True)
        assert w._tier2_completions_since_tier1 == 1

    def test_on_task_completed_ignores_unknown_id(self, tmp_path):
        w = _make_worker(tmp_path)
        # Should not raise, just return early
        w.on_task_completed(999, success=True)
        assert w._tier2_completions_since_tier1 == 0

    def test_on_task_completed_coerces_string_id(self, tmp_path):
        w = _make_worker(tmp_path)
        w._inflight_task_ids.append(100)
        w._dedup_memory["improvement:coverage:mod1"] = {
            "evaluated_at": "2026-01-01T00:00:00+00:00",
            "verdict": "submitted",
            "task_id": 100,
        }
        w.on_task_completed("100", success=True)  # type: ignore[arg-type]
        assert 100 not in w._inflight_task_ids

    def test_on_task_completed_rejects_bool(self, tmp_path):
        w = _make_worker(tmp_path)
        w._inflight_task_ids.append(1)
        w.on_task_completed(True, success=True)  # type: ignore[arg-type]
        # bool rejected by _coerce_task_id, inflight unchanged
        assert 1 in w._inflight_task_ids

    def test_cooldown_triggers_at_threshold(self, tmp_path):
        w = _make_worker(tmp_path, heartbeat_category_failure_threshold=2)
        for i in range(2):
            task_id = 100 + i
            w._inflight_task_ids.append(task_id)
            w._dedup_memory[f"improvement:coverage:mod{i}"] = {
                "evaluated_at": "2026-01-01T00:00:00+00:00",
                "verdict": "submitted",
                "task_id": task_id,
            }
            w.on_task_completed(task_id, success=False)
        assert w.is_category_cooled_down("coverage") is True

    def test_success_clears_failures(self, tmp_path):
        w = _make_worker(tmp_path)
        w._category_failures["coverage"] = 2
        w._dedup_memory["improvement:coverage:mod1"] = {
            "evaluated_at": "2026-01-01T00:00:00+00:00",
            "verdict": "submitted",
            "task_id": 200,
        }
        w._inflight_task_ids.append(200)
        w.on_task_completed(200, success=True)
        assert "coverage" not in w._category_failures

    def test_expired_cooldown_clears(self, tmp_path):
        w = _make_worker(tmp_path)
        # Set expired cooldown
        w._category_cooldown_until["coverage"] = "2000-01-01T00:00:00+00:00"
        w._category_failures["coverage"] = 5
        assert w.is_category_cooled_down("coverage") is False
        # State should be cleared after expiry check
        assert "coverage" not in w._category_cooldown_until

    def test_is_category_cooled_down_invalid_timestamp(self, tmp_path):
        w = _make_worker(tmp_path)
        w._category_cooldown_until["coverage"] = "not-a-timestamp"
        assert w.is_category_cooled_down("coverage") is False

    def test_on_task_completed_sets_tier1_owed(self, tmp_path):
        w = _make_worker(tmp_path, heartbeat_tier1_every_n=2)
        for i in range(2):
            task_id = 300 + i
            w._inflight_task_ids.append(task_id)
            w._dedup_memory[f"improvement:coverage:mod{i}"] = {
                "evaluated_at": "2026-01-01T00:00:00+00:00",
                "verdict": "submitted",
                "task_id": task_id,
            }
            w.on_task_completed(task_id, success=True)
        assert w._tier1_owed is True


class TestWorkerScanTodos:
    def test_non_git_returns_empty(self, tmp_path):
        w = _make_worker(tmp_path)
        assert w._scan_todos() == []

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_git_repo_scans(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="file1.py\nfile2.py\n"
            )
            results = w._scan_todos()
            assert len(results) == 2
            assert all(r[0].startswith("todo:") for r in results)
            assert mock_run.call_args[1]["cwd"] == "/fake/repo"

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_git_scan_nonzero_returncode(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            results = w._scan_todos()
            assert results == []

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_git_scan_oserror(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        with patch(
            "golem.heartbeat_worker.subprocess.run", side_effect=OSError("no git")
        ):
            results = w._scan_todos()
            assert results == []

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_deduped_todos_filtered(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        # Pre-register one finding as deduped
        key = "todo:" + HeartbeatWorker._content_hash("file1.py")
        w.record_dedup(key, "submitted")
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0, stdout="file1.py\nfile2.py\n"
            )
            results = w._scan_todos()
            assert len(results) == 1
            assert "file2.py" in results[0][1]


class TestWorkerScanCoverage:
    def test_runs_pytest_in_repo(self, tmp_path):
        w = _make_worker(tmp_path)
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc123\n"),  # git rev-parse HEAD
                MagicMock(
                    returncode=0,
                    stdout="golem/foo.py  50  10  80%\ngolem/bar.py  30  0  100%\n",
                ),
            ]
            results = w._scan_coverage()
            assert len(results) == 1
            assert "foo.py" in results[0][1]

    def test_uses_cache_when_current(self, tmp_path):
        from datetime import datetime, timezone

        w = _make_worker(tmp_path)
        w._coverage_cache = {
            "commit_hash": "abc123",
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "uncovered_modules": ["golem/cached.py"],
        }
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="abc123\n")
            results = w._scan_coverage()
            # pytest should NOT be called (cache hit)
            assert mock_run.call_count == 1
            assert len(results) == 1
            assert "cached.py" in results[0][1]

    def test_git_rev_parse_fails(self, tmp_path):
        w = _make_worker(tmp_path)
        with patch(
            "golem.heartbeat_worker.subprocess.run", side_effect=OSError("no git")
        ):
            results = w._scan_coverage()
            assert results == []

    def test_coverage_timeout(self, tmp_path):
        import subprocess

        w = _make_worker(tmp_path)
        call_count = 0

        def side_effect(*_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return MagicMock(returncode=0, stdout="abc123\n")
            raise subprocess.TimeoutExpired("pytest", 300)

        with patch("golem.heartbeat_worker.subprocess.run", side_effect=side_effect):
            results = w._scan_coverage()
            assert results == []

    def test_cache_commit_mismatch_reruns(self, tmp_path):
        from datetime import datetime, timezone

        w = _make_worker(tmp_path)
        w._coverage_cache = {
            "commit_hash": "old-hash",
            "ran_at": datetime.now(timezone.utc).isoformat(),
            "uncovered_modules": ["golem/stale.py"],
        }
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="new-hash\n"),
                MagicMock(returncode=0, stdout="golem/fresh.py  50  10  80%\n"),
            ]
            results = w._scan_coverage()
            assert mock_run.call_count == 2
            assert "fresh.py" in results[0][1]


class TestWorkerScanPitfalls:
    def test_missing_agents_md(self, tmp_path):
        w = _make_worker(tmp_path)
        assert w._scan_pitfalls() == []

    def test_finds_pitfalls(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        agents_md = repo / "AGENTS.md"
        agents_md.write_text(
            "# Agents\n\n## Recurring Antipatterns\n\n"
            "- **Foo**: bar baz <!-- seen:2 last:2026-01-01 -->\n"
            "\n## Other Section\n"
        )
        w = _make_worker(tmp_path, repo_path=str(repo))
        results = w._scan_pitfalls()
        assert len(results) == 1
        assert results[0][0].startswith("pitfall:")

    def test_deduped_pitfall_filtered(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        agents_md = repo / "AGENTS.md"
        line = "- **Foo**: bar baz <!-- seen:2 last:2026-01-01 -->"
        agents_md.write_text("# Agents\n\n## Recurring Antipatterns\n\n" + line + "\n")
        w = _make_worker(tmp_path, repo_path=str(repo))
        # The pitfall clean text after stripping HTML comment
        import re

        clean = re.sub(r"\s*<!--.*?-->", "", line.strip())
        from golem.heartbeat_worker import _content_hash

        key = f"pitfall:{_content_hash(clean)}"
        w.record_dedup(key, "submitted")
        results = w._scan_pitfalls()
        assert results == []

    def test_oserror_reading_agents_md(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        agents_md = repo / "AGENTS.md"
        agents_md.write_text("# dummy")
        w = _make_worker(tmp_path, repo_path=str(repo))
        with patch(
            "golem.heartbeat_worker.Path.read_text", side_effect=OSError("no read")
        ):
            results = w._scan_pitfalls()
            assert results == []

    def test_no_section_returns_empty(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        agents_md = repo / "AGENTS.md"
        agents_md.write_text(
            "# Agents\n\n## Some Other Section\n\n- no pitfalls here\n"
        )
        w = _make_worker(tmp_path, repo_path=str(repo))
        results = w._scan_pitfalls()
        assert results == []


class TestWorkerGroupCandidates:
    def test_groups_by_category(self, tmp_path):
        w = _make_worker(tmp_path)
        candidates = [
            {
                "id": "improvement:coverage:a",
                "category": "coverage",
                "confidence": 0.8,
                "subject": "a",
                "body": "a",
                "automatable": True,
                "complexity": "small",
                "reason": "a",
                "tier": 2,
            },
            {
                "id": "improvement:coverage:b",
                "category": "coverage",
                "confidence": 0.9,
                "subject": "b",
                "body": "b",
                "automatable": True,
                "complexity": "small",
                "reason": "b",
                "tier": 2,
            },
            {
                "id": "improvement:dead-code:c",
                "category": "dead-code",
                "confidence": 0.7,
                "subject": "c",
                "body": "c",
                "automatable": True,
                "complexity": "small",
                "reason": "c",
                "tier": 2,
            },
        ]
        batch = w._group_candidates(candidates)
        assert len(batch) == 2
        assert all(c["category"] == "coverage" for c in batch)

    def test_empty_returns_empty(self, tmp_path):
        w = _make_worker(tmp_path)
        assert w._group_candidates([]) == []

    def test_no_category_falls_back_to_id(self, tmp_path):
        w = _make_worker(tmp_path)
        candidates = [
            {
                "id": "improvement:reliability:fix1",
                "category": "",
                "confidence": 0.8,
                "subject": "fix1",
                "body": "body",
                "automatable": True,
                "complexity": "small",
                "reason": "r",
                "tier": 2,
            },
        ]
        batch = w._group_candidates(candidates)
        assert len(batch) == 1

    def test_candidate_with_no_category_and_no_parseable_id_skipped(self, tmp_path):
        w = _make_worker(tmp_path)
        candidates = [
            {
                "id": "no-colon",
                "category": "",
                "confidence": 0.8,
                "subject": "x",
                "body": "x",
                "automatable": True,
                "complexity": "small",
                "reason": "r",
                "tier": 2,
            },
        ]
        batch = w._group_candidates(candidates)
        assert batch == []


class TestPathHash:
    def test_deterministic(self):
        assert _path_hash("/foo/bar") == _path_hash("/foo/bar")

    def test_different_paths_differ(self):
        assert _path_hash("/foo/bar") != _path_hash("/foo/baz")

    def test_length_12(self):
        assert len(_path_hash("/some/path")) == 12


class TestContentHash:
    def test_deterministic(self):
        assert HeartbeatWorker._content_hash("hello") == HeartbeatWorker._content_hash(
            "hello"
        )

    def test_different_content_differs(self):
        assert HeartbeatWorker._content_hash("hello") != HeartbeatWorker._content_hash(
            "world"
        )

    def test_length_12(self):
        assert len(HeartbeatWorker._content_hash("test")) == 12


class TestWorkerValidateCandidates:
    def test_valid_candidate(self, tmp_path):
        w = _make_worker(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:42",
                    "automatable": True,
                    "confidence": 0.8,
                    "complexity": "small",
                    "reason": "easy fix",
                    "category": "coverage",
                }
            ]
        }
        result = w._validate_candidates(raw, tier=1)
        assert len(result) == 1
        assert result[0]["id"] == "github:42"

    def test_invalid_structure(self, tmp_path):
        w = _make_worker(tmp_path)
        assert w._validate_candidates("not a dict", tier=1) == []
        assert w._validate_candidates({"no_candidates": []}, tier=1) == []

    def test_filters_low_confidence(self, tmp_path):
        w = _make_worker(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:42",
                    "automatable": True,
                    "confidence": 0.1,
                    "complexity": "large",
                    "reason": "hard",
                    "category": "coverage",
                }
            ]
        }
        result = w._validate_candidates(raw, tier=1)
        assert len(result) == 0

    def test_filters_non_automatable(self, tmp_path):
        w = _make_worker(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:42",
                    "automatable": False,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "easy",
                    "category": "coverage",
                }
            ]
        }
        result = w._validate_candidates(raw, tier=1)
        assert len(result) == 0

    def test_filters_invalid_complexity(self, tmp_path):
        w = _make_worker(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:42",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "huge",
                    "reason": "easy",
                    "category": "coverage",
                }
            ]
        }
        result = w._validate_candidates(raw, tier=1)
        assert len(result) == 0

    def test_filters_empty_id(self, tmp_path):
        w = _make_worker(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "easy",
                    "category": "coverage",
                }
            ]
        }
        result = w._validate_candidates(raw, tier=1)
        assert len(result) == 0

    def test_filters_no_category_and_no_parseable_id(self, tmp_path):
        w = _make_worker(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "no-colon-id",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "easy",
                    "category": "",
                }
            ]
        }
        result = w._validate_candidates(raw, tier=1)
        assert len(result) == 0

    def test_sorts_by_confidence_desc(self, tmp_path):
        w = _make_worker(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:1",
                    "automatable": True,
                    "confidence": 0.6,
                    "complexity": "small",
                    "reason": "r",
                    "category": "c",
                },
                {
                    "id": "github:2",
                    "automatable": True,
                    "confidence": 0.9,
                    "complexity": "small",
                    "reason": "r",
                    "category": "c",
                },
            ]
        }
        result = w._validate_candidates(raw, tier=1)
        assert result[0]["id"] == "github:2"
        assert result[1]["id"] == "github:1"

    def test_filters_non_dict_candidate(self, tmp_path):
        w = _make_worker(tmp_path)
        raw = {"candidates": ["not-a-dict"]}
        result = w._validate_candidates(raw, tier=1)
        assert result == []

    def test_non_numeric_confidence_filtered(self, tmp_path):
        w = _make_worker(tmp_path)
        raw = {
            "candidates": [
                {
                    "id": "github:42",
                    "automatable": True,
                    "confidence": "high",
                    "complexity": "small",
                    "reason": "r",
                    "category": "c",
                }
            ]
        }
        result = w._validate_candidates(raw, tier=1)
        assert result == []


class TestWorkerExtractCategory:
    @pytest.mark.parametrize(
        "candidate_id,expected",
        [
            ("improvement:coverage:foo", "coverage"),
            ("improvement:dead-code:bar", "dead-code"),
            ("github:42", "github"),
            ("local:99", "local"),
            ("nocolon", ""),
        ],
    )
    def test_extract_category(self, candidate_id, expected):
        assert HeartbeatWorker._extract_category_from_id(candidate_id) == expected


class TestCallHaiku:
    async def test_call_haiku_returns_parsed_json(self, tmp_path):
        w = _make_worker(tmp_path)
        mock_result = MagicMock()
        mock_result.cost_usd = 0.001
        mock_result.output = {"result": '{"candidates": []}'}

        with patch("golem.heartbeat_worker.invoke_cli", return_value=mock_result):
            record_spend = MagicMock()
            result = await w._call_haiku("test prompt", "[]", record_spend)
            assert result == {"candidates": []}
            record_spend.assert_called_once_with(0.001)

    async def test_call_haiku_handles_cli_error(self, tmp_path):
        from golem.core.cli_wrapper import CLIError

        w = _make_worker(tmp_path)
        with patch("golem.heartbeat_worker.invoke_cli", side_effect=CLIError("fail")):
            record_spend = MagicMock()
            result = await w._call_haiku("test prompt", "[]", record_spend)
            assert result == ""
            record_spend.assert_not_called()

    async def test_call_haiku_handles_non_json(self, tmp_path):
        w = _make_worker(tmp_path)
        mock_result = MagicMock()
        mock_result.cost_usd = 0.001
        mock_result.output = {"result": "plain text response"}

        with patch("golem.heartbeat_worker.invoke_cli", return_value=mock_result):
            record_spend = MagicMock()
            result = await w._call_haiku("test prompt", "[]", record_spend)
            assert result == "plain text response"

    async def test_call_haiku_strips_markdown(self, tmp_path):
        w = _make_worker(tmp_path)
        mock_result = MagicMock()
        mock_result.cost_usd = 0.001
        mock_result.output = {"result": '```json\n{"candidates": []}\n```'}

        with patch("golem.heartbeat_worker.invoke_cli", return_value=mock_result):
            record_spend = MagicMock()
            result = await w._call_haiku("test prompt", "[]", record_spend)
            assert result == {"candidates": []}


class TestGetRecentBatchCategories:
    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_parses_categories(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="abc123 [FIX][INFRA] [HEARTBEAT] batch:dead-code (4 items)\n"
                "def456 [FIX][INFRA] [HEARTBEAT] batch:coverage (2 items)\n",
            )
            cats = w._get_recent_batch_categories()
            assert "dead-code" in cats
            assert "coverage" in cats

    @patch("golem.heartbeat_worker.is_git_repo", return_value=False)
    def test_non_git_returns_empty(self, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        cats = w._get_recent_batch_categories()
        assert cats == set()

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_nonzero_returncode(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            cats = w._get_recent_batch_categories()
            assert cats == set()

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_oserror(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        with patch("golem.heartbeat_worker.subprocess.run", side_effect=OSError("err")):
            cats = w._get_recent_batch_categories()
            assert cats == set()


class TestGetRecentlyResolvedIds:
    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_parses_ids(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Fixed [pitfall:abc123def456] and [improvement:coverage:mod1]\n",
            )
            ids = w._get_recently_resolved_ids()
            assert "pitfall:abc123def456" in ids
            assert "improvement:coverage:mod1" in ids

    @patch("golem.heartbeat_worker.is_git_repo", return_value=False)
    def test_non_git_returns_empty(self, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        ids = w._get_recently_resolved_ids()
        assert ids == set()

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_nonzero_returncode(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        with patch("golem.heartbeat_worker.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            ids = w._get_recently_resolved_ids()
            assert ids == set()

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value=None)
    def test_oserror(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        with patch("golem.heartbeat_worker.subprocess.run", side_effect=OSError("err")):
            ids = w._get_recently_resolved_ids()
            assert ids == set()


class TestRunTier1:
    async def test_skips_when_no_github_remote(self, tmp_path):
        w = _make_worker(tmp_path)
        # is_git=False, github_remote=None => skip
        task_source = MagicMock()
        record_spend = MagicMock()
        result = await w._run_tier1(task_source, record_spend)
        assert result == []
        task_source.poll_untagged_tasks.assert_not_called()

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value="owner/repo")
    async def test_returns_candidates(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        task_source = MagicMock()
        task_source.poll_untagged_tasks.return_value = [
            {"id": 1, "subject": "Fix bug", "body": "details"}
        ]
        record_spend = MagicMock()
        haiku_response = {
            "candidates": [
                {
                    "id": "local:1",
                    "automatable": True,
                    "confidence": 0.8,
                    "complexity": "small",
                    "reason": "easy",
                    "category": "bugfix",
                }
            ]
        }
        with patch.object(w, "_call_haiku", new=AsyncMock(return_value=haiku_response)):
            result = await w._run_tier1(task_source, record_spend)
            assert len(result) == 1
            assert result[0]["id"] == "local:1"

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value="owner/repo")
    async def test_poll_exception_returns_empty(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        task_source = MagicMock()
        task_source.poll_untagged_tasks.side_effect = RuntimeError("network error")
        record_spend = MagicMock()
        result = await w._run_tier1(task_source, record_spend)
        assert result == []

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value="owner/repo")
    async def test_all_issues_deduped(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        w.record_dedup("local:1", "submitted")
        task_source = MagicMock()
        task_source.poll_untagged_tasks.return_value = [
            {"id": 1, "subject": "Fix bug", "body": "details"}
        ]
        record_spend = MagicMock()
        result = await w._run_tier1(task_source, record_spend)
        assert result == []


class TestRunTier1Promoted:
    async def test_skips_when_no_github_remote(self, tmp_path):
        w = _make_worker(tmp_path)
        task_source = MagicMock()
        record_spend = MagicMock()
        result = await w._run_tier1_promoted(task_source, record_spend)
        assert result == []

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value="owner/repo")
    async def test_returns_candidates(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        task_source = MagicMock()
        task_source.poll_untagged_tasks.return_value = [
            {"id": 2, "subject": "Improve perf", "body": "details"}
        ]
        record_spend = MagicMock()
        haiku_response = {
            "candidates": [
                {
                    "id": "local:2",
                    "automatable": True,
                    "confidence": 0.75,
                    "complexity": "medium",
                    "reason": "perf",
                    "category": "performance",
                }
            ]
        }
        with patch.object(w, "_call_haiku", new=AsyncMock(return_value=haiku_response)):
            result = await w._run_tier1_promoted(task_source, record_spend)
            assert len(result) == 1

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value="owner/repo")
    async def test_poll_exception_returns_empty(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        task_source = MagicMock()
        task_source.poll_untagged_tasks.side_effect = RuntimeError("err")
        record_spend = MagicMock()
        result = await w._run_tier1_promoted(task_source, record_spend)
        assert result == []

    @patch("golem.heartbeat_worker.is_git_repo", return_value=True)
    @patch("golem.heartbeat_worker.detect_github_remote", return_value="owner/repo")
    async def test_all_issues_deduped(self, _mock_remote, _mock_git, tmp_path):
        w = HeartbeatWorker("/fake/repo", _make_config(), state_dir=tmp_path)
        w.record_dedup("local:2", "submitted")
        task_source = MagicMock()
        task_source.poll_untagged_tasks.return_value = [
            {"id": 2, "subject": "Improve perf", "body": "details"}
        ]
        record_spend = MagicMock()
        result = await w._run_tier1_promoted(task_source, record_spend)
        assert result == []


class TestRunTier2:
    async def test_returns_candidates(self, tmp_path):
        w = _make_worker(tmp_path)
        record_spend = MagicMock()
        with patch.object(
            w, "_scan_todos", return_value=[("todo:abc", "TODO in file.py")]
        ):
            with patch.object(w, "_scan_coverage", return_value=[]):
                with patch.object(w, "_scan_pitfalls", return_value=[]):
                    with patch.object(
                        w, "_get_recently_resolved_ids", return_value=set()
                    ):
                        with patch.object(
                            w, "_get_recent_batch_categories", return_value=set()
                        ):
                            haiku_response = {
                                "candidates": [
                                    {
                                        "id": "todo:abc",
                                        "automatable": True,
                                        "confidence": 0.8,
                                        "complexity": "small",
                                        "reason": "easy",
                                        "category": "todo",
                                    }
                                ]
                            }
                            with patch.object(
                                w,
                                "_call_haiku",
                                new=AsyncMock(return_value=haiku_response),
                            ):
                                result = await w._run_tier2(record_spend)
                                assert len(result) == 1
                                assert result[0]["id"] == "todo:abc"

    async def test_empty_findings_returns_empty(self, tmp_path):
        w = _make_worker(tmp_path)
        record_spend = MagicMock()
        with patch.object(w, "_scan_todos", return_value=[]):
            with patch.object(w, "_scan_coverage", return_value=[]):
                with patch.object(w, "_scan_pitfalls", return_value=[]):
                    result = await w._run_tier2(record_spend)
                    assert result == []

    async def test_filters_resolved_ids(self, tmp_path):
        w = _make_worker(tmp_path)
        record_spend = MagicMock()
        with patch.object(w, "_scan_todos", return_value=[("todo:abc", "TODO")]):
            with patch.object(w, "_scan_coverage", return_value=[]):
                with patch.object(w, "_scan_pitfalls", return_value=[]):
                    with patch.object(
                        w, "_get_recently_resolved_ids", return_value={"todo:abc"}
                    ):
                        result = await w._run_tier2(record_spend)
                        assert result == []

    async def test_filters_by_cooldown(self, tmp_path):
        w = _make_worker(tmp_path)
        w._category_cooldown_until["todo"] = "2099-01-01T00:00:00+00:00"
        record_spend = MagicMock()
        with patch.object(w, "_scan_todos", return_value=[("todo:abc", "TODO")]):
            with patch.object(w, "_scan_coverage", return_value=[]):
                with patch.object(w, "_scan_pitfalls", return_value=[]):
                    with patch.object(
                        w, "_get_recently_resolved_ids", return_value=set()
                    ):
                        with patch.object(
                            w, "_get_recent_batch_categories", return_value=set()
                        ):
                            haiku_response = {
                                "candidates": [
                                    {
                                        "id": "todo:abc",
                                        "automatable": True,
                                        "confidence": 0.8,
                                        "complexity": "small",
                                        "reason": "easy",
                                        "category": "todo",
                                    }
                                ]
                            }
                            with patch.object(
                                w,
                                "_call_haiku",
                                new=AsyncMock(return_value=haiku_response),
                            ):
                                result = await w._run_tier2(record_spend)
                                assert result == []


class TestTickMethod:
    async def test_tick_returns_tier2_candidates(self, tmp_path):
        w = _make_worker(tmp_path)
        task_source = MagicMock()
        record_spend = MagicMock()
        budget_allows = MagicMock(return_value=True)

        candidates = [
            {
                "id": "todo:abc",
                "automatable": True,
                "confidence": 0.8,
                "complexity": "small",
                "reason": "easy",
                "category": "todo",
                "subject": "Fix TODO",
                "body": "body",
                "tier": 2,
            }
        ]

        with patch.object(w, "_run_tier1", new=AsyncMock(return_value=[])):
            with patch.object(w, "_run_tier2", new=AsyncMock(return_value=candidates)):
                result_candidates, tier = await w.tick(
                    task_source, record_spend, budget_allows
                )
                assert tier == 2
                assert len(result_candidates) == 1

    async def test_tick_returns_tier1_candidates(self, tmp_path):
        w = _make_worker(tmp_path)
        task_source = MagicMock()
        record_spend = MagicMock()
        budget_allows = MagicMock(return_value=True)

        candidates = [
            {
                "id": "github:1",
                "automatable": True,
                "confidence": 0.8,
                "complexity": "small",
                "reason": "easy",
                "category": "bugfix",
                "subject": "Fix bug",
                "body": "body",
                "tier": 1,
            }
        ]

        with patch.object(w, "_run_tier1", new=AsyncMock(return_value=candidates)):
            result_candidates, tier = await w.tick(
                task_source, record_spend, budget_allows
            )
            assert tier == 1
            assert len(result_candidates) == 1

    async def test_tick_no_budget(self, tmp_path):
        w = _make_worker(tmp_path)
        task_source = MagicMock()
        record_spend = MagicMock()
        budget_allows = MagicMock(return_value=False)

        result_candidates, tier = await w.tick(task_source, record_spend, budget_allows)
        assert result_candidates == []
        assert tier == 0

    async def test_tick_tier1_owed_promoted(self, tmp_path):
        w = _make_worker(tmp_path)
        w._tier1_owed = True
        task_source = MagicMock()
        record_spend = MagicMock()
        budget_allows = MagicMock(return_value=True)

        promoted = [
            {
                "id": "github:10",
                "automatable": True,
                "confidence": 0.9,
                "complexity": "small",
                "reason": "r",
                "category": "bugfix",
                "subject": "Fix",
                "body": "body",
                "tier": 1,
            }
        ]

        with patch.object(
            w, "_run_tier1_promoted", new=AsyncMock(return_value=promoted)
        ):
            result_candidates, tier = await w.tick(
                task_source, record_spend, budget_allows
            )
            assert tier == 1
            assert len(result_candidates) == 1

    async def test_tick_tier1_owed_no_promoted_falls_to_tier2(self, tmp_path):
        w = _make_worker(tmp_path)
        w._tier1_owed = True
        task_source = MagicMock()
        record_spend = MagicMock()
        budget_allows = MagicMock(return_value=True)

        tier2_candidates = [
            {
                "id": "todo:abc",
                "automatable": True,
                "confidence": 0.8,
                "complexity": "small",
                "reason": "easy",
                "category": "todo",
                "subject": "Fix TODO",
                "body": "body",
                "tier": 2,
            }
        ]

        with patch.object(w, "_run_tier1_promoted", new=AsyncMock(return_value=[])):
            with patch.object(
                w, "_run_tier2", new=AsyncMock(return_value=tier2_candidates)
            ):
                result_candidates, tier = await w.tick(
                    task_source, record_spend, budget_allows
                )
                assert tier == 2
                assert len(result_candidates) > 0

    async def test_tick_tier1_owed_no_budget_after_promoted(self, tmp_path):
        w = _make_worker(tmp_path)
        w._tier1_owed = True
        task_source = MagicMock()
        record_spend = MagicMock()
        # Budget fails on second check
        call_count = 0

        def budget_allows():
            nonlocal call_count
            call_count += 1
            return call_count < 2  # False on second call

        with patch.object(w, "_run_tier1_promoted", new=AsyncMock(return_value=[])):
            result_candidates, tier = await w.tick(
                task_source, record_spend, budget_allows
            )
            assert result_candidates == []
            assert tier == 0

    async def test_tick_resets_tick_caches(self, tmp_path):
        w = _make_worker(tmp_path)
        w._tick_resolved_ids = {"some:id"}
        w._tick_recent_categories = {"coverage"}
        task_source = MagicMock()
        record_spend = MagicMock()
        budget_allows = MagicMock(return_value=False)

        await w.tick(task_source, record_spend, budget_allows)
        assert w._tick_resolved_ids is None
        assert w._tick_recent_categories is None

    async def test_tick_returns_empty_when_both_tiers_empty(self, tmp_path):
        w = _make_worker(tmp_path)
        task_source = MagicMock()
        record_spend = MagicMock()
        budget_allows = MagicMock(return_value=True)

        with patch.object(w, "_run_tier1", new=AsyncMock(return_value=[])):
            with patch.object(w, "_run_tier2", new=AsyncMock(return_value=[])):
                result_candidates, tier = await w.tick(
                    task_source, record_spend, budget_allows
                )
                assert result_candidates == []
                assert tier == 0


class TestStripMarkdownJson:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ('```json\n{"key": "val"}\n```', '{"key": "val"}'),
            ('```\n{"key": "val"}\n```', '{"key": "val"}'),
            ('{"key": "val"}', '{"key": "val"}'),
            ("  some text  ", "some text"),
        ],
    )
    def test_strip(self, text, expected):
        from golem.heartbeat_worker import _strip_markdown_json

        assert _strip_markdown_json(text) == expected


class TestCoerceTaskId:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (42, 42),
            ("99", 99),
            (None, None),
            (3.14, None),
            (True, None),
            (False, None),
            ("not-a-number", None),
        ],
    )
    def test_coerce(self, value, expected):
        from golem.heartbeat_worker import _coerce_task_id

        assert _coerce_task_id(value) == expected
