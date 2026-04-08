# Development Guide

This guide covers how to contribute to Golem itself — setup, workflow, coding conventions, testing requirements, and how to extend the system with new backends or skills.

For the runtime pipeline and architecture internals, see `docs/architecture.md`. For configuration and deployment, see `docs/operations.md`.

---

## Dev Setup

```bash
git clone https://github.com/itsmeboris/golem.git && cd golem
pip install -e ".[dev,dashboard]"
git config core.hooksPath .githooks
make lint && make test
```

Verify everything works end-to-end:

```bash
golem run -p "Add a one-line docstring to golem/types.py"
```

This starts the daemon, submits a small task, and lets you watch the full pipeline — agent execution, verification, validation, and commit.

---

## Development Workflow

1. **Branch from `master`**
2. **Write tests first** — see [TDD Workflow](#tdd-workflow) below
3. **Implement** until tests pass
4. **Run checks** — `make lint && make test` runs all three in one shot:
   ```bash
   black golem/
   pylint --errors-only golem/
   pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100
   ```
5. **Push** — the pre-push hook reruns all three and blocks on failure
6. **Open a pull request**

Never skip the pre-push hook (`--no-verify`). If it fails, fix the issue before pushing.

---

## Project Layout

```
golem/
├── orchestrator.py      — durable state machine, checkpoints every tick
├── flow.py              — agent invocation and event-streaming pipeline
├── validation.py        — validation agent dispatch and verdict parsing
├── verifier.py          — deterministic checks; config-driven or Python fallback
├── worktree_manager.py  — git worktree lifecycle for parallel isolation
├── event_tracker.py     — stream-json events → Milestone objects
├── types.py             — shared TypedDict contracts (import from here)
├── backends/            — issue-tracker adapters (GitHub, Redmine, local)
├── prompts/             — prompt templates for each agent role
├── core/                — FastAPI web UI + config management
└── tests/               — mirrors source structure, 100% coverage required
```

---

## TDD Workflow

Golem uses strict test-driven development. **No production code without a failing test first.**

### Red — Write a failing test

```python
def test_rejects_empty_input(self):
    with pytest.raises(ValidationError, match="cannot be empty"):
        validate_input("")
```

Run it and confirm it fails because the feature is missing — not because of import errors or typos:

```bash
pytest golem/tests/test_mymodule.py::TestClass::test_name -x
```

### Green — Write minimal code to pass

Write the simplest code that makes the test pass. Don't add features beyond what the test requires.

### Refactor — Clean up

Only after the test is green. Extract helpers, improve names, remove duplication. Keep tests green throughout.

Repeat the cycle for each new piece of behavior.

### Bug Fix Pattern

1. Write a failing test that reproduces the bug
2. Verify it fails for the right reason
3. Fix the code
4. Verify the test now passes
5. Run the full suite to check for regressions

---

## Coding Conventions

### Formatting

Black is enforced with default settings (line length 88). The pre-push hook blocks commits that don't pass `black --check`.

```bash
black golem/    # fix formatting
black --check golem/    # check only
```

### Linting

Pylint is run in errors-only mode. No `# pylint: disable` comments unless strictly necessary and accompanied by a comment explaining why.

```bash
pylint --errors-only golem/
```

### Logging

Never use f-strings in log calls. Use `%`-style lazy formatting:

```python
# Wrong
logger.info(f"Processing task {task_id}")

# Correct
logger.info("Processing task %s", task_id)
```

This prevents string interpolation overhead when the log level is not active, and avoids accidentally logging sensitive data.

### TypedDicts

All shared dict shapes go in `golem/types.py`. Never define inline TypedDicts in individual modules — scattered definitions lead to key-mismatch bugs that are hard to track down.

```python
# Wrong — inline TypedDict in a module
class MyDict(TypedDict):
    task_id: str
    status: str

# Correct — defined in golem/types.py, imported everywhere
from golem.types import MyDict
```

### Dataclass Mutable Defaults

Always use `field(default_factory=...)` for mutable defaults:

```python
# Wrong
@dataclass
class Foo:
    items: list = []

# Correct
from dataclasses import dataclass, field

@dataclass
class Foo:
    items: list = field(default_factory=list)
```

### Imports

Organize imports in three groups, separated by blank lines:

1. Standard library
2. Third-party
3. Local (`golem.*`)

No circular imports. `golem/types.py` has no local imports.

---

## Testing Requirements

Coverage is enforced at 100%. If you add a function, you add a test.

```bash
# Full suite — same as CI
pytest golem/tests/ --cov=golem --cov-fail-under=100 -q

# Fast iteration — stop at first failure
pytest golem/tests/ -x -q

# Target a single file
pytest golem/tests/test_flow.py -x
```

### Structure

- Tests live in `golem/tests/` mirroring source structure: `flow.py` → `tests/test_flow.py`
- Use `@pytest.mark.parametrize` for any test with repeated logic — don't write near-duplicate test methods
- Every bug fix must include a reproduction test that fails before the fix and passes after
- Tests must be deterministic — mock all external I/O and time-dependent calls

### asyncio

`asyncio_mode = "auto"` is set in `pytest.ini`. No `@pytest.mark.asyncio` decorators are needed on async test methods.

### Mock at Boundaries

Mock external I/O (subprocess, HTTP, filesystem at edges). Don't mock so many internals that you're testing mock wiring instead of behavior:

```python
# Wrong — tests mock call ordering, not behavior
with patch("mod.a"), patch("mod.b"), patch("mod.c"):
    result = function()
    mock_a.assert_called_once()

# Correct — mock only the external boundary
@patch("golem.verifier.subprocess.run")
def test_all_pass(self, mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="64 passed\n")
    result = run_verification("/tmp/test")
    assert result.passed is True
```

### Test Pattern

```python
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

class TestFeatureName:
    def test_happy_path(self):
        result = function(valid_input)
        assert result == expected

    def test_rejects_bad_input(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            function("")

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

---

## Adding a Backend

Golem's profile system decouples it from any specific tracker or notifier. To add a new integration, implement five interfaces from `golem/interfaces.py` and register a profile factory.

See [[Backends]] for the full step-by-step guide, including the complete Jira example.

---

## Adding a Skill

Skills are reusable packages of domain knowledge and structured workflows that agent sessions load automatically. When you add a skill, all future agent sessions pick it up without any prompt changes.

**Directory structure:**

```
.claude/skills/
└── my-skill/
    └── SKILL.md      — the skill content (markdown)
```

The skill name is the directory name. `SKILL.md` is the only required file. Write it as a concise, actionable guide — agents read it before starting work on relevant tasks.

Skills auto-propagate to child agent sessions via the `skills` frontmatter in agent definitions under `.claude/agents/`.

**Existing skills for reference:**

- `.claude/skills/test-driven-development/` — red/green/refactor cycle with project-specific patterns
- `.claude/skills/systematic-debugging/` — root cause investigation before any fix attempt
- `.claude/skills/verification-before-completion/` — verification checklist before reporting done

---

## CI Pipeline

The CI pipeline runs on every push to `master` and every pull request.

### Lint Job

Runs on Python 3.11:

- `black --check golem/` — formatting
- `pylint --errors-only golem/` — error-level lint
- `pylint --disable=all --enable=W0611,W0612,W0101,W0613 golem/` — unused imports, variables, unreachable code
- `python3 scripts/pyflakes_noqa.py golem/` — pyflakes with noqa handling
- `python3 -m vulture golem/ vulture_whitelist.py --min-confidence 80` — dead code detection

### Test Matrix

Runs on Python 3.11, 3.12, and 3.13:

```bash
pytest golem/tests/ -x -q --tb=short --cov=golem --cov-report=term-missing:skip-covered --cov-fail-under=100
```

All three Python versions must pass with 100% coverage before a PR can merge.
