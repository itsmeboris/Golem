# Add DELETE /tasks/<id> Endpoint

Add an endpoint to delete a task by ID.

## Requirements

1. `DELETE /tasks/<id>` removes the task and returns `{"deleted": true}`
2. Return 404 if the task doesn't exist
3. After deletion, the task should no longer appear in `GET /tasks`
4. Write tests covering: successful deletion, deletion of nonexistent task, and verifying the task is gone from the list after deletion
