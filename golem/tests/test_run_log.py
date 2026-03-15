# pylint: disable=too-few-public-methods
"""Tests for golem.core.run_log — run logging and duration formatting."""

import json
from datetime import datetime, timezone

import pytest

from golem.core.run_log import (
    RunRecord,
    purge_flow,
    read_runs,
    record_run,
)


class TestRunRecordPromptHash:
    def test_prompt_hash_default_is_empty(self):
        record = RunRecord(event_id="x", flow="golem", task_id="1")
        assert record.prompt_hash == ""

    def test_prompt_hash_serialized(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        record = RunRecord(
            event_id="ph1",
            flow="golem",
            task_id="1",
            prompt_hash="abc123def456",
        )
        record_run(record, log_file)
        data = json.loads(log_file.read_text().strip())
        assert data["prompt_hash"] == "abc123def456"

    @pytest.mark.parametrize(
        "ph",
        ["", "000000000000", "abcdef012345"],
    )
    def test_prompt_hash_roundtrip(self, tmp_path, ph):
        log_file = tmp_path / "runs.jsonl"
        record = RunRecord(event_id="rnd", flow="golem", task_id="1", prompt_hash=ph)
        record_run(record, log_file)
        data = json.loads(log_file.read_text().strip())
        assert data["prompt_hash"] == ph


class TestRecordRun:
    def test_creates_file_and_appends(self, tmp_path):
        log_file = tmp_path / "sub" / "runs.jsonl"
        record = RunRecord(
            event_id="test-1",
            flow="golem",
            task_id="42",
            success=True,
            cost_usd=0.50,
        )
        record_run(record, log_file)

        assert log_file.exists()
        data = json.loads(log_file.read_text().strip())
        assert data["event_id"] == "test-1"
        assert data["flow"] == "golem"
        assert data["success"] is True

    def test_appends_multiple(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        for i in range(3):
            record_run(
                RunRecord(event_id=f"e{i}", flow="golem", task_id=str(i)),
                log_file,
            )
        lines = [l for l in log_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 3


class TestReadRuns:
    def test_empty_file(self, tmp_path):
        assert not read_runs(tmp_path / "nonexistent.jsonl")

    def test_reads_and_reverses(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        for i in range(5):
            record_run(
                RunRecord(event_id=f"e{i}", flow="golem", task_id=str(i)),
                log_file,
            )
        runs = read_runs(log_file)
        assert len(runs) == 5
        assert runs[0]["event_id"] == "e4"

    def test_flow_filter(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        record_run(RunRecord(event_id="g1", flow="golem", task_id="1"), log_file)
        record_run(RunRecord(event_id="o1", flow="other", task_id="2"), log_file)
        record_run(RunRecord(event_id="g2", flow="golem", task_id="3"), log_file)

        runs = read_runs(log_file, flow="golem")
        assert len(runs) == 2
        assert all(r["flow"] == "golem" for r in runs)

    def test_limit(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        for i in range(10):
            record_run(
                RunRecord(event_id=f"e{i}", flow="golem", task_id=str(i)),
                log_file,
            )
        runs = read_runs(log_file, limit=3)
        assert len(runs) == 3

    def test_since_filter(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        old = RunRecord(
            event_id="old",
            flow="golem",
            task_id="1",
            started_at="2020-01-01T00:00:00+00:00",
        )
        new = RunRecord(
            event_id="new",
            flow="golem",
            task_id="2",
            started_at="2026-02-01T00:00:00+00:00",
        )
        record_run(old, log_file)
        record_run(new, log_file)

        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        runs = read_runs(log_file, since=cutoff)
        assert len(runs) == 1
        assert runs[0]["event_id"] == "new"

    def test_handles_corrupt_lines(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        record_run(RunRecord(event_id="ok", flow="golem", task_id="1"), log_file)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write("not json\n")
        record_run(RunRecord(event_id="ok2", flow="golem", task_id="2"), log_file)

        runs = read_runs(log_file)
        assert len(runs) == 2


class TestPurgeFlow:
    def test_purge_specific_flow(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        record_run(RunRecord(event_id="g1", flow="golem", task_id="1"), log_file)
        record_run(RunRecord(event_id="o1", flow="other", task_id="2"), log_file)
        record_run(RunRecord(event_id="g2", flow="golem", task_id="3"), log_file)

        removed = purge_flow("golem", log_file)
        assert removed == 2

        remaining = read_runs(log_file)
        assert len(remaining) == 1
        assert remaining[0]["flow"] == "other"

    def test_purge_nonexistent_flow(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        record_run(RunRecord(event_id="g1", flow="golem", task_id="1"), log_file)
        assert purge_flow("nope", log_file) == 0

    def test_purge_missing_file(self, tmp_path):
        assert purge_flow("any", tmp_path / "missing.jsonl") == 0

    def test_purge_skips_blank_lines(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        record_run(RunRecord(event_id="g1", flow="golem", task_id="1"), log_file)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write("\n\n")
        record_run(RunRecord(event_id="o1", flow="other", task_id="2"), log_file)

        removed = purge_flow("golem", log_file)
        assert removed == 1
        remaining = read_runs(log_file)
        assert len(remaining) == 1

    def test_purge_keeps_corrupt_json_lines(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        record_run(RunRecord(event_id="g1", flow="golem", task_id="1"), log_file)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write("not valid json\n")

        removed = purge_flow("golem", log_file)
        assert removed == 1
        content = log_file.read_text()
        assert "not valid json" in content


class TestReadRunsEdgeCases:
    def test_blank_lines_skipped(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        record_run(RunRecord(event_id="e1", flow="golem", task_id="1"), log_file)
        with open(log_file, "a", encoding="utf-8") as fh:
            fh.write("\n\n")
        record_run(RunRecord(event_id="e2", flow="golem", task_id="2"), log_file)

        runs = read_runs(log_file)
        assert len(runs) == 2

    def test_invalid_date_in_since_filter(self, tmp_path):
        log_file = tmp_path / "runs.jsonl"
        rec = RunRecord(
            event_id="bad-date",
            flow="golem",
            task_id="1",
            started_at="not-a-date",
        )
        record_run(rec, log_file)

        cutoff = datetime(2025, 1, 1, tzinfo=timezone.utc)
        runs = read_runs(log_file, since=cutoff)
        assert len(runs) == 1
        assert runs[0]["event_id"] == "bad-date"


class TestDefaultLogFile:
    def test_record_run_default_path(self, monkeypatch, tmp_path):
        default = tmp_path / "runs" / "runs.jsonl"
        monkeypatch.setattr("golem.core.run_log.DEFAULT_RUN_LOG", default)
        record_run(RunRecord(event_id="d1", flow="golem", task_id="1"))
        assert default.exists()

    def test_read_runs_default_path(self, monkeypatch, tmp_path):
        default = tmp_path / "runs" / "runs.jsonl"
        monkeypatch.setattr("golem.core.run_log.DEFAULT_RUN_LOG", default)
        record_run(RunRecord(event_id="d1", flow="golem", task_id="1"), default)
        runs = read_runs()
        assert len(runs) == 1

    def test_purge_flow_default_path(self, monkeypatch, tmp_path):
        default = tmp_path / "runs" / "runs.jsonl"
        monkeypatch.setattr("golem.core.run_log.DEFAULT_RUN_LOG", default)
        record_run(RunRecord(event_id="d1", flow="golem", task_id="1"), default)
        removed = purge_flow("golem")
        assert removed == 1
