"""Tests for the demo microservice."""

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
