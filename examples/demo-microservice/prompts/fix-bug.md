# Fix: complete_task Returns 200 for Nonexistent Tasks

The `POST /tasks/<id>/complete` endpoint returns HTTP 200 with a `null` JSON body when the task ID doesn't exist. It should return 404.

## Requirements

1. Write a failing test first that demonstrates the bug:
   - `POST /tasks/9999/complete` should return 404
   - The response body should include an error message
2. Fix the `complete_task` function to return 404 when the task is not found
3. All existing tests must continue to pass
