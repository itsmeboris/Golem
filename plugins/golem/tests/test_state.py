"""Tests for plugins/golem/scripts/lib/state.py."""

import json
import os
from unittest.mock import patch

import state


class TestStateFile:
    def test_uses_session_id_when_set(self, tmp_path):
        with patch.dict(
            os.environ,
            {"CLAUDE_SESSION_ID": "abc123", "GOLEM_PLUGIN_STATE_DIR": str(tmp_path)},
        ):
            # Reload state module constants
            with patch("state._STATE_DIR", tmp_path):
                result = state._state_file()
            assert result == tmp_path / "session-abc123.json"

    def test_uses_repo_hash_when_no_session_id(self, tmp_path):
        with patch.dict(
            os.environ, {"GOLEM_PLUGIN_STATE_DIR": str(tmp_path)}, clear=False
        ):
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_SESSION_ID"}
            env["GOLEM_PLUGIN_STATE_DIR"] = str(tmp_path)
            with patch.dict(os.environ, env, clear=True):
                with patch("state._STATE_DIR", tmp_path):
                    result = state._state_file()
                # Should be a repo-<hash>.json file
                assert result.name.startswith("repo-")
                assert result.name.endswith(".json")
                assert len(result.stem) == len("repo-") + 12  # 12-char hash

    def test_creates_state_dir_if_missing(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "sess1"}):
            with patch("state._STATE_DIR", nested):
                state._state_file()
        assert nested.exists()


class TestLoadState:
    def test_returns_default_state_when_file_missing(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "nosuchsession"}):
                result = state._load_state()
        assert result == {
            "jobs": [],
            "stats": {"delegated": 0, "completed": 0, "failed": 0},
        }

    def test_loads_existing_state(self, tmp_path):
        existing = {
            "jobs": [{"task_id": 42, "status": "running"}],
            "stats": {"delegated": 1, "completed": 0, "failed": 0},
        }
        state_file = tmp_path / "session-testsess.json"
        state_file.write_text(json.dumps(existing))

        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "testsess"}):
                result = state._load_state()

        assert result["jobs"][0]["task_id"] == 42
        assert result["stats"]["delegated"] == 1

    def test_returns_default_state_when_json_invalid(self, tmp_path):
        state_file = tmp_path / "session-badsess.json"
        state_file.write_text("{not valid json}")

        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "badsess"}):
                result = state._load_state()

        assert result == {
            "jobs": [],
            "stats": {"delegated": 0, "completed": 0, "failed": 0},
        }


class TestSaveState:
    def test_saves_state_as_json(self, tmp_path):
        data = {
            "jobs": [{"task_id": 99}],
            "stats": {"delegated": 1, "completed": 1, "failed": 0},
        }
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "savesess"}):
                state._save_state(data)
                state_file = tmp_path / "session-savesess.json"
                loaded = json.loads(state_file.read_text())

        assert loaded["jobs"][0]["task_id"] == 99
        assert loaded["stats"]["delegated"] == 1


class TestRecordDelegation:
    def test_records_job_and_increments_delegated(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "deleg1"}):
                state.record_delegation(7, "Fix the login bug", "background")
                result = state._load_state()

        assert len(result["jobs"]) == 1
        job = result["jobs"][0]
        assert job["task_id"] == 7
        assert job["mode"] == "background"
        assert job["status"] == "running"
        assert result["stats"]["delegated"] == 1

    def test_truncates_prompt_to_200_chars(self, tmp_path):
        long_prompt = "x" * 500
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "deleg2"}):
                state.record_delegation(1, long_prompt, "wait")
                result = state._load_state()

        assert len(result["jobs"][0]["prompt"]) == 200

    def test_multiple_delegations_accumulate(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "deleg3"}):
                state.record_delegation(1, "task one", "background")
                state.record_delegation(2, "task two", "wait")
                result = state._load_state()

        assert len(result["jobs"]) == 2
        assert result["stats"]["delegated"] == 2


class TestUpdateJobStatus:
    def test_updates_existing_job_to_completed(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "upd1"}):
                state.record_delegation(10, "some task", "background")
                state.update_job_status(10, "completed")
                result = state._load_state()

        job = result["jobs"][0]
        assert job["status"] == "completed"
        assert result["stats"]["completed"] == 1
        assert result["stats"]["failed"] == 0

    def test_updates_existing_job_to_failed(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "upd2"}):
                state.record_delegation(11, "another task", "wait")
                state.update_job_status(11, "failed")
                result = state._load_state()

        job = result["jobs"][0]
        assert job["status"] == "failed"
        assert result["stats"]["failed"] == 1
        assert result["stats"]["completed"] == 0

    def test_noop_when_task_id_not_found(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "upd3"}):
                state.record_delegation(20, "something", "background")
                state.update_job_status(999, "completed")  # unknown task_id
                result = state._load_state()

        # The original job should still be "running"
        assert result["jobs"][0]["status"] == "running"
        assert result["stats"]["completed"] == 0


class TestGetSessionJobs:
    def test_returns_empty_list_when_no_jobs(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "getjobs1"}):
                result = state.get_session_jobs()
        assert result == []

    def test_returns_recorded_jobs(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "getjobs2"}):
                state.record_delegation(5, "task", "background")
                result = state.get_session_jobs()
        assert len(result) == 1
        assert result[0]["task_id"] == 5


class TestGetSessionStats:
    def test_returns_zeroed_stats_initially(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "stats1"}):
                result = state.get_session_stats()
        assert result == {"delegated": 0, "completed": 0, "failed": 0}

    def test_returns_accumulated_stats(self, tmp_path):
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "stats2"}):
                state.record_delegation(1, "t1", "background")
                state.record_delegation(2, "t2", "wait")
                state.update_job_status(1, "completed")
                result = state.get_session_stats()
        assert result == {"delegated": 2, "completed": 1, "failed": 0}


class TestFlushStatsToGlobal:
    def test_does_nothing_when_no_delegations(self, tmp_path):
        global_path = tmp_path / ".golem" / "data" / "plugin-stats.json"
        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "flush1"}):
                with patch("state.Path.home", return_value=tmp_path):
                    state.flush_stats_to_global()
        assert not global_path.exists()

    def test_appends_to_global_stats_file(self, tmp_path):
        global_dir = tmp_path / ".golem" / "data"
        global_dir.mkdir(parents=True)
        global_path = global_dir / "plugin-stats.json"

        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "flush2"}):
                with patch("state.Path.home", return_value=tmp_path):
                    state.record_delegation(1, "task", "background")
                    state.flush_stats_to_global()
                    entries = json.loads(global_path.read_text())

        assert len(entries) == 1
        assert entries[0]["delegated"] == 1
        assert entries[0]["completed"] == 0
        assert "timestamp" in entries[0]
        assert "pid" in entries[0]

    def test_appends_to_existing_global_stats(self, tmp_path):
        global_dir = tmp_path / ".golem" / "data"
        global_dir.mkdir(parents=True)
        global_path = global_dir / "plugin-stats.json"
        global_path.write_text(
            json.dumps(
                [
                    {
                        "delegated": 5,
                        "completed": 5,
                        "failed": 0,
                        "timestamp": 1.0,
                        "pid": 1,
                    }
                ]
            )
        )

        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "flush3"}):
                with patch("state.Path.home", return_value=tmp_path):
                    state.record_delegation(3, "task", "wait")
                    state.flush_stats_to_global()
                    entries = json.loads(global_path.read_text())

        assert len(entries) == 2
        assert entries[0]["delegated"] == 5
        assert entries[1]["delegated"] == 1

    def test_handles_corrupt_global_stats_gracefully(self, tmp_path):
        global_dir = tmp_path / ".golem" / "data"
        global_dir.mkdir(parents=True)
        global_path = global_dir / "plugin-stats.json"
        global_path.write_text("not json")

        with patch("state._STATE_DIR", tmp_path):
            with patch.dict(os.environ, {"CLAUDE_SESSION_ID": "flush4"}):
                with patch("state.Path.home", return_value=tmp_path):
                    state.record_delegation(1, "task", "background")
                    state.flush_stats_to_global()
                    entries = json.loads(global_path.read_text())

        # Starts fresh from empty list
        assert len(entries) == 1
        assert entries[0]["delegated"] == 1
