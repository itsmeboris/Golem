---
name: test-driven-development
description: Test-driven development for Golem Python code. Use when writing new features, fixing bugs, adding tests, or when 100% coverage is required. Enforces red-green-refactor with pytest patterns specific to this codebase.
---

# Test-Driven Development

Adapted from [obra/superpowers](https://github.com/obra/superpowers) for Python/pytest in the Golem codebase.

Write the test first. Watch it fail. Write minimal code to pass.

## The Iron Law

```
NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST
```

Write code before the test? Delete it. Start over. Implement fresh from tests.

## Red-Green-Refactor

### RED — Write a failing test

```python
def test_rejects_empty_input(self):
    with pytest.raises(ValidationError, match="cannot be empty"):
        validate_input("")
```

Requirements: real behavior (not mocking for the sake of it), clear name, one thing.

Run it:
```bash
pytest golem/tests/test_<module>.py::TestClass::test_name -x
```

Confirm it fails because the feature is missing (not because of typos or import errors).

### GREEN — Write minimal code to pass

Simplest code that makes the test pass. Don't add features beyond what the test requires.

### REFACTOR — Clean up

After green only. Extract helpers, improve names, remove duplication. Keep tests green.

### Repeat

Next failing test for the next piece of behavior.

## Project Patterns

Test files: `golem/tests/test_<module>.py`

```python
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

class TestFeatureName:
    def test_happy_path(self):
        result = function(valid_input)
        assert result == expected

    def test_edge_case(self):
        with pytest.raises(SpecificError):
            function(bad_input)

    @pytest.mark.parametrize("input,expected", [
        ("a", 1),
        ("b", 2),
    ])
    def test_variations(self, input, expected):
        assert function(input) == expected

    async def test_async_operation(self):
        mock_dep = AsyncMock(return_value="result")
        output = await async_function(mock_dep)
        assert output == "result"
```

## Coverage Requirement

100% coverage is enforced. Every new function/method needs a test.

```bash
pytest --cov=golem --cov-fail-under=100
```

If coverage drops below 100%, find the uncovered lines and add tests.

## Bug Fix Pattern

1. Write a failing test that reproduces the bug
2. Verify it fails for the right reason
3. Fix the code
4. Verify the test passes
5. Run full suite to check for regressions

## When Stuck

| Problem | Solution |
|---|---|
| Don't know how to test | Write the assertion first — what should the result be? |
| Test too complicated | Design too complicated — simplify the interface |
| Must mock everything | Code too coupled — use dependency injection |
| Test setup huge | Extract fixtures. Still complex? Simplify design |
