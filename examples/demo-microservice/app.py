"""A tiny task-tracker microservice — demo project for Golem."""

import json
import logging
import time

from flask import Flask, g, jsonify, request

app = Flask(__name__)

# In-memory task store
_tasks: dict[int, dict] = {}
_next_id = 1

# ---------- Logging setup ----------

EXTRA_FIELDS = {"method", "path", "status", "duration_ms", "task_id"}


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for field in EXTRA_FIELDS:
            if hasattr(record, field):
                entry[field] = getattr(record, field)
        return json.dumps(entry)


_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())

logger = logging.getLogger("demo")
logger.setLevel(logging.INFO)
logger.addHandler(_handler)

# ---------- Request lifecycle hooks ----------


@app.before_request
def _before_request():
    g.start_time = time.time()


@app.after_request
def _after_request(response):
    duration_ms = (time.time() - g.start_time) * 1000.0
    logger.info(
        "Request",
        extra={
            "method": request.method,
            "path": request.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    return response


# ---------- Routes ----------


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
        logger.warning("Missing title in task creation")
        return jsonify({"error": "title is required"}), 400
    task = {"id": _next_id, "title": title, "done": False}
    _tasks[_next_id] = task
    _next_id += 1
    logger.info("Task created", extra={"task_id": task["id"]})
    return jsonify(task), 201


@app.post("/tasks/<int:task_id>/complete")
def complete_task(task_id):
    """Mark a task as done."""
    # BUG: returns 200 with empty body instead of 404 when task doesn't exist
    task = _tasks.get(task_id)
    if task:
        task["done"] = True
        logger.info("Task completed", extra={"task_id": task_id})
    return jsonify(task)
