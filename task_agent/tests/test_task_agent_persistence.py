"""Tests for atomic session persistence and Redmine status verification."""

# pylint: disable=missing-class-docstring,missing-function-docstring
# pylint: disable=protected-access

from unittest.mock import MagicMock


# -- Atomic session persistence tests ----------------------------------------


class TestAtomicSessionPersistence:
    """Tests for the atomic save_sessions / load_sessions roundtrip."""

    def test_full_roundtrip_all_fields(self, tmp_path):
        """Every TaskSession field should survive save → load."""
        from task_agent.orchestrator import (
            TaskSession,
            TaskSessionState,
            load_sessions,
            save_sessions,
        )

        sessions = {
            1: TaskSession(
                parent_issue_id=1,
                state=TaskSessionState.RUNNING,
                trace_file="traces/task_agent/test.jsonl",
                total_cost_usd=1.50,
            ),
            2: TaskSession(
                parent_issue_id=2,
                state=TaskSessionState.COMPLETED,
            ),
        }

        path = tmp_path / "sessions.json"
        save_sessions(sessions, path)

        loaded = load_sessions(path)
        assert set(loaded.keys()) == {1, 2}

        s1 = loaded[1]
        assert s1.parent_issue_id == 1
        assert s1.state == TaskSessionState.RUNNING
        assert s1.total_cost_usd == 1.50
        assert s1.trace_file == "traces/task_agent/test.jsonl"

        s2 = loaded[2]
        assert s2.parent_issue_id == 2
        assert s2.state == TaskSessionState.COMPLETED

    def test_atomic_save_produces_valid_json(self, tmp_path):
        """Verify that the saved file is always valid JSON."""
        import json

        from task_agent.orchestrator import (
            TaskSession,
            TaskSessionState,
            save_sessions,
        )

        path = tmp_path / "sessions.json"
        sessions = {
            1: TaskSession(parent_issue_id=1, state=TaskSessionState.RUNNING),
        }
        save_sessions(sessions, path)

        # File should be valid JSON
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "sessions" in data
        assert "1" in data["sessions"]

    def test_atomic_save_no_temp_files_left(self, tmp_path):
        """Verify no temp files are left behind after successful save."""
        from task_agent.orchestrator import (
            TaskSession,
            TaskSessionState,
            save_sessions,
        )

        save_dir = tmp_path / "save_dir"
        save_dir.mkdir()
        path = save_dir / "sessions.json"
        sessions = {
            1: TaskSession(parent_issue_id=1, state=TaskSessionState.DETECTED),
        }
        save_sessions(sessions, path)

        # Only the target file should exist (no .tmp files)
        files = list(save_dir.iterdir())
        assert len(files) == 1
        assert files[0].name == "sessions.json"


# -- Redmine status verification tests ----------------------------------------


class TestRedmineStatusVerification:
    """Tests for the Redmine status verification after PUT."""

    def test_successful_status_update(self, monkeypatch):
        from task_agent.backends.redmine import _update_redmine_issue

        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()
        get_resp = MagicMock()
        get_resp.raise_for_status = MagicMock()
        get_resp.json.return_value = {"issue": {"status": {"id": 2}}}

        call_count = {"put": 0, "get": 0}

        def mock_retry(method, *_args, **_kwargs):
            if method.__name__ == "put":
                call_count["put"] += 1
                return put_resp
            call_count["get"] += 1
            return get_resp

        monkeypatch.setattr(
            "task_agent.backends.redmine._request_with_retry", mock_retry
        )
        monkeypatch.setattr(
            "task_agent.backends.redmine.get_redmine_headers",
            lambda: {"X-Redmine-API-Key": "test"},
        )

        result = _update_redmine_issue(123, status_id=2, notes="Starting work")
        assert result is True

    def test_silent_status_transition_failure(self, monkeypatch):
        """PUT succeeds but GET shows status didn't change (workflow violation)."""
        from task_agent.backends.redmine import _update_redmine_issue

        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()
        get_resp = MagicMock()
        get_resp.raise_for_status = MagicMock()
        # Requested status_id=16 but actual is still 2
        get_resp.json.return_value = {"issue": {"status": {"id": 2}}}

        def mock_retry(method, *_args, **_kwargs):
            if method.__name__ == "put":
                return put_resp
            return get_resp

        monkeypatch.setattr(
            "task_agent.backends.redmine._request_with_retry", mock_retry
        )
        monkeypatch.setattr(
            "task_agent.backends.redmine.get_redmine_headers",
            lambda: {"X-Redmine-API-Key": "test"},
        )

        result = _update_redmine_issue(123, status_id=16)
        assert result is False

    def test_notes_only_update_skips_verification(self, monkeypatch):
        """When no status_id is passed, skip the verification GET."""
        from task_agent.backends.redmine import _update_redmine_issue

        put_resp = MagicMock()
        put_resp.raise_for_status = MagicMock()

        get_called = {"value": False}

        def mock_retry(method, *_args, **_kwargs):
            if method.__name__ == "get":
                get_called["value"] = True
            return put_resp

        monkeypatch.setattr(
            "task_agent.backends.redmine._request_with_retry", mock_retry
        )
        monkeypatch.setattr(
            "task_agent.backends.redmine.get_redmine_headers",
            lambda: {"X-Redmine-API-Key": "test"},
        )

        result = _update_redmine_issue(123, notes="Just a comment")
        assert result is True
        assert get_called["value"] is False
