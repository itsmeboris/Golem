# Error Handling & Async Patterns

## Exception Handling

### Do
- Catch specific exception types, not bare `except:` or `except Exception:`
- Always log or re-raise — never silently swallow exceptions
- Use `logger.error` for unexpected failures, `logger.warning` for expected-but-unusual
- Wrap external I/O (subprocess, HTTP, filesystem) in try/except at system boundaries

### Don't
```python
# BAD — silently swallows everything
try:
    do_work()
except:
    pass

# BAD — catches too broadly, hides bugs
try:
    compute_result()
except Exception:
    return None
```

### Correct
```python
# GOOD — specific type, logged, re-raised or handled
try:
    result = subprocess.run(cmd, timeout=30, capture_output=True)
except subprocess.TimeoutExpired:
    logger.warning("Command timed out after 30s: %s", cmd[0])
    return VerificationResult(passed=False, error="timeout")
except OSError as exc:
    logger.error("Failed to run command %s: %s", cmd[0], exc)
    raise
```

## Retry Patterns

### When to Retry
- Transient failures: network timeouts, rate limits (HTTP 429), temporary file locks
- NOT deterministic failures: syntax errors, missing files, permission denied

### Pattern
```python
for attempt in range(max_retries + 1):
    try:
        return await do_work()
    except TransientError as exc:
        if attempt == max_retries:
            raise
        delay = min(2 ** attempt, 30)  # exponential backoff, capped
        logger.warning("Attempt %d failed, retrying in %ds: %s", attempt + 1, delay, exc)
        await asyncio.sleep(delay)
```

### Circuit Breaker
After N consecutive failures for the same operation, stop retrying and fail fast:
```python
if consecutive_failures >= threshold:
    logger.error("Circuit breaker open after %d failures", threshold)
    return SKIP
```

## Asyncio Best Practices

### Task Management
```python
# Gather with return_exceptions for cleanup (don't let one failure cancel others)
results = await asyncio.gather(*tasks, return_exceptions=True)

# wait with timeout for draining
done, pending = await asyncio.wait(tasks, timeout=30)
for task in pending:
    task.cancel()

# wait_for with timeout on individual operations
result = await asyncio.wait_for(operation(), timeout=60)
```

### Blocking I/O
```python
# Always use to_thread for blocking subprocess calls in async functions
result = await asyncio.to_thread(subprocess.run, cmd, timeout=30)

# NEVER call subprocess.run directly in async context
# subprocess.run(cmd)  # BAD — blocks the event loop
```

### Lock Discipline
```python
# Keep lock scope minimal
async with lock:
    data = shared_state.copy()  # fast read under lock
# Process outside lock
result = process(data)

# NEVER hold lock across long awaits
# async with lock:
#     await long_running_operation()  # BAD — starves other coroutines
```

### Test Synchronization
```python
# NEVER use asyncio.sleep in tests
# await asyncio.sleep(0.1)  # BAD — flaky

# GOOD — event-based synchronization
event = asyncio.Event()
async def signal_when_done():
    event.set()

await asyncio.wait_for(event.wait(), timeout=2.0)
```

## Fallback Patterns

When retries are exhausted:
1. Log at ERROR level with full context
2. Return a typed failure result (not None or empty)
3. Propagate the failure to the caller's decision point
4. Never leave the system in an inconsistent state

```python
# GOOD — typed failure with context
return VerificationResult(
    passed=False,
    error=f"All {max_retries} retries exhausted: {last_error}",
)
```
