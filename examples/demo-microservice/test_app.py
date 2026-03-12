"""Tests for the demo microservice."""

import io
import json
import logging

import pytest

import app as app_module
from app import app


@pytest.fixture(autouse=True)
def _reset_state():
    """Reset global state between tests so order doesn't matter."""
    app_module._tasks.clear()
    app_module._next_id = 1


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_list_tasks_empty(client):
    resp = client.get("/tasks")
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_create_task(client):
    resp = client.post("/tasks", json={"title": "Buy milk"})
    assert resp.status_code == 201
    data = resp.get_json()
    assert data["title"] == "Buy milk"
    assert data["done"] is False
    assert "id" in data


def test_create_task_empty_title(client):
    resp = client.post("/tasks", json={"title": ""})
    assert resp.status_code == 400


def test_complete_task(client):
    create = client.post("/tasks", json={"title": "Test task"})
    task_id = create.get_json()["id"]
    resp = client.post(f"/tasks/{task_id}/complete")
    assert resp.status_code == 200
    assert resp.get_json()["done"] is True


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok"}


def test_log_output_is_valid_json(client):
    """Each log line is valid JSON with required fields."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(app_module.JsonFormatter())
    demo_logger = logging.getLogger("demo")
    demo_logger.addHandler(handler)
    try:
        resp = client.post("/tasks", json={"title": "Log test task"})
        assert resp.status_code == 201

        output = buf.getvalue()
        entries = [json.loads(line) for line in output.splitlines() if line.strip()]

        # Every entry must have the three base keys
        for entry in entries:
            assert "timestamp" in entry
            assert "level" in entry
            assert "message" in entry

        # At least one entry is the HTTP request log
        request_entries = [
            e
            for e in entries
            if all(k in e for k in ("method", "path", "status", "duration_ms"))
        ]
        assert len(request_entries) >= 1

        # At least one entry is the task-created log
        task_created_entries = [
            e for e in entries if e.get("message") == "Task created" and "task_id" in e
        ]
        assert len(task_created_entries) >= 1
    finally:
        demo_logger.removeHandler(handler)
