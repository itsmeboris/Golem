# Add Structured Logging

Add structured JSON logging to the demo microservice.

## Requirements

1. Add Python's built-in `logging` module with a JSON formatter
2. Log every incoming request (method, path, status code, duration in ms)
3. Log task creation and completion events at INFO level
4. Log errors (e.g., missing title) at WARNING level
5. Add a test that verifies log output is valid JSON and contains expected fields

Do not add any external dependencies — use only the Python standard library for logging.
