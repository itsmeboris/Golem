"""A tiny task-tracker microservice — demo project for Golem."""

from flask import Flask, jsonify, request

app = Flask(__name__)

# In-memory task store
_tasks: dict[int, dict] = {}
_next_id = 1


@app.get("/tasks")
def list_tasks():
    """Return all tasks."""
    return jsonify(list(_tasks.values()))


@app.post("/tasks")
def create_task():
    """Create a new task. Expects JSON: {"title": "..."}"""
    global _next_id
    data = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error": "title is required"}), 400
    task = {"id": _next_id, "title": title, "done": False}
    _tasks[_next_id] = task
    _next_id += 1
    return jsonify(task), 201


@app.post("/tasks/<int:task_id>/complete")
def complete_task(task_id):
    """Mark a task as done."""
    # BUG: returns 200 with empty body instead of 404 when task doesn't exist
    task = _tasks.get(task_id)
    if task:
        task["done"] = True
    return jsonify(task)
