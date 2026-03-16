# Testing Rules

## Requirements
- 100% test coverage required (`--cov-fail-under=100`)
- Tests go in `golem/tests/` mirroring source structure
- Use `@pytest.mark.parametrize` for test cases with repeated logic
- Every bug fix must include a reproduction test
- Run full suite before claiming completion: `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100`
- `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` decorators needed

## Test Quality Standards

Coverage alone doesn't ensure quality. Every test must be able to **catch a real bug**.

### Forbidden: Tautological Tests
Never construct a value and assert the same value back. These tests cannot fail.

```python
# BAD — tests Python's dict, not your code
entry: MilestoneDict = {"kind": "tool_call", ...}
assert entry["kind"] == "tool_call"

# BAD — tests dataclass construction, not behavior
r = VerificationResult(passed=True, ...)
assert r.passed is True

# GOOD — call the real producer, verify output matches contract
result = run_verification("/tmp/test")
assert result.passed is True
assert result.test_count == 64

# GOOD — verify producer output matches TypedDict keys
tracker = TaskEventTracker(session_id="test-1")
tracker.handle_event(event)
export = tracker.to_dict()
for key in MilestoneDict.__required_keys__:
    assert key in export["event_log"][0]
```

### Forbidden: Shallow Assertions
Assert on specific values, not types or existence.

```python
# BAD
assert result is not None
assert isinstance(x, list)
assert len(output) < 200  # threshold is 120, test allows 200

# GOOD
assert result.score == 4
assert len(result.items) == 3
assert result.items[0]["name"] == "expected"
```

### Forbidden: str() Substring Matching on Structured Data
When testing structured output (cards, dicts, JSON), assert on structure.

```python
# BAD — can't catch structural bugs (value in wrong field)
body_text = str(card["body"])
assert "123" in body_text

# GOOD — verify specific elements
assert "123" in card["body"][0]["text"]  # header
facts = {f["title"]: f["value"] for f in card["body"][2]["facts"]}
assert facts["Cost"] == "$1.50"
```

### Mock at Boundaries, Not Internals
Mock external I/O (subprocess, HTTP, filesystem at edges). Don't mock so many
internals that you're testing mock wiring instead of behavior.

```python
# BAD — 12 patches, testing mock call ordering
with patch("mod.a"), patch("mod.b"), patch("mod.c"), ...:
    result = function()
    mock_a.assert_called_once()  # tests wiring, not behavior

# GOOD — mock only the external boundary
@patch("golem.verifier.subprocess.run")
def test_all_pass(self, mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="64 passed\n...")
    result = run_verification("/tmp/test")
    assert result.passed is True  # tests computed behavior
```

### Verify Specific Values, Not Call Counts
Call counts are fragile — they break on refactor without catching bugs.

```python
# BAD — breaks if implementation adds a git call
assert mock_run.call_count == 3

# GOOD — order-independent, allows additional calls
called_tools = {call.args[0][0] for call in mock_run.call_args_list}
assert called_tools >= {"black", "pylint", "pytest"}
```

### Assert Specific States, Not Negations

```python
# BAD — any state except DETECTED passes (FAILED, CRASHED, etc.)
assert session.state != TaskSessionState.DETECTED

# GOOD — verifies the exact expected transition
assert session.state == TaskSessionState.RUNNING
```

### Deterministic Async Tests
Never use `asyncio.sleep(N)` and hope the work finished. Use event-based
synchronization.

```python
# BAD — timing-dependent, flaky under CI load
mgr.start(mock_flow)
await asyncio.sleep(0.05)
mock_tick.assert_called()

# GOOD — deterministic, waits for actual completion
tick_called = asyncio.Event()

async def _tick_signal():
    tick_called.set()

with patch.object(mgr, "_run_heartbeat_tick", new=_tick_signal):
    mgr.start(mock_flow)
    try:
        await asyncio.wait_for(tick_called.wait(), timeout=2.0)
    finally:
        mgr.stop()

assert tick_called.is_set()
```

### Every Test Must Have an Assertion
A test with no assertion always passes. "Doesn't crash" is not a test.

### Parametrize Regex/Parser Tests
Regex parsers and format functions must have parametrized tests covering
normal, edge, empty, and malformed inputs.

### No Duplicate Tests Across Files
If the same test class exists in two files, delete the duplicate. Verify
coverage is maintained first with `--ignore=<file>`.

## Protocol / Interface Tests
- **Dummy class tests** verify the protocol contract is stable (catches protocol changes)
- **Real implementation tests** verify backends conform to the protocol (catches implementation drift)
- Both are needed — they test different things. Don't replace one with the other.
