# Contributing to Golem

Welcome! Golem is an autonomous AI coding agent daemon — it picks up tasks, runs Claude agents, validates results, and commits passing work without human intervention. Before hacking on it, read [docs/architecture.md](docs/architecture.md) for the runtime pipeline and [docs/operations.md](docs/operations.md) for deployment and config reference.

---

## Getting Started

```bash
git clone https://github.com/itsmeboris/golem.git && cd golem
pip install -e ".[dev,dashboard]"
git config core.hooksPath .githooks
```

Verify everything works end-to-end:

```bash
make lint && make test
golem run -p "Add a one-line docstring to golem/types.py"
```

The second command starts the daemon, submits a small task, and lets you watch the full pipeline — agent execution, verification, validation, and commit.

---

## Project Layout

```
golem/
├── orchestrator.py      — durable state machine, checkpoints every tick
├── flow.py              — agent invocation and event-streaming pipeline
├── validation.py        — validation agent dispatch and verdict parsing
├── verifier.py          — deterministic checks (black / pylint / pytest)
├── worktree_manager.py  — git worktree lifecycle for parallel isolation
├── event_tracker.py     — stream-json events → Milestone objects
├── types.py             — shared TypedDict contracts (import from here)
├── backends/            — issue-tracker adapters (GitHub, Redmine, local)
├── prompts/             — prompt templates for each agent role
├── core/                — FastAPI dashboard + config management
└── tests/               — mirrors source structure, 100% coverage required
```

---

## Development Workflow

1. Branch from `master`
2. Write tests first (see [Testing](#testing) below)
3. Implement until tests pass
4. Run checks — `make lint && make test` runs all three in one shot:

```bash
black golem/              # fix formatting
pylint --errors-only golem/
pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100
```

5. Push — the pre-push hook reruns all three and blocks on failure
6. Open a pull request

Never skip the hook (`--no-verify`). If it fails, fix the issue.

---

## Testing

Coverage is enforced at 100%. If you add a function, you add a test.

```bash
# Full suite — same as CI
pytest golem/tests/ --cov=golem --cov-fail-under=100 -q

# Fast iteration during development — stop at first failure
pytest golem/tests/ -x -q

# Target a single file
pytest golem/tests/test_flow.py -x
```

**Rules:**

- Tests live in `golem/tests/` mirroring source structure (`flow.py` → `tests/test_flow.py`)
- Use `@pytest.mark.parametrize` for any test with repeated logic — don't write near-duplicate test methods
- Mock at boundaries, not internals: mock the HTTP call, not the function that parses the response
- Every bug fix must include a reproduction test that fails before the fix and passes after
- Tests must be deterministic — mock all external I/O and time-dependent calls

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
```

Mark slow or integration tests appropriately:

```python
@pytest.mark.slow
@pytest.mark.integration
```

---

## Adding a Backend

Golem's profile system decouples it from any specific tracker or notifier. To add a new integration, implement five interfaces from `interfaces.py` and register with `register_profile()`.

**Interfaces to implement:**

| Interface | Responsibility |
|-----------|---------------|
| `TaskSource` | `poll_tasks()` + `get_task_description()` |
| `StateBackend` | `update_status()` + `post_comment()` |
| `Notifier` | `notify()` lifecycle events |
| `ToolProvider` | Select MCP servers per task |
| `PromptProvider` | Load prompt templates |

Start with `backends/local.py` as a minimal reference (file-based, no external services). See `backends/github.py` for a full CLI-based implementation with auth, polling, and PR creation.

**Example — adding a Jira backend:**

```python
from golem.profile import register_profile, GolemProfile
from golem.backends.local import LogNotifier, NullToolProvider
from golem.prompts import FilePromptProvider

class JiraTaskSource:
    def poll_tasks(self, projects, detection_tag, timeout=30): ...
    def get_task_description(self, task_id): ...

class JiraStateBackend:
    def update_status(self, task_id, status): ...
    def post_comment(self, task_id, text): ...

def _build_jira_profile(config):
    return GolemProfile(
        name="jira",
        task_source=JiraTaskSource(),
        state_backend=JiraStateBackend(),
        notifier=LogNotifier(),
        tool_provider=NullToolProvider(),
        prompt_provider=FilePromptProvider(),
    )

register_profile("jira", _build_jira_profile)
```

Then set `profile: jira` in `config.yaml`. The daemon picks it up on next start or `SIGHUP`.

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

Look at existing skills for examples:

- `.claude/skills/test-driven-development/` — red/green/refactor cycle with project-specific patterns
- `.claude/skills/systematic-debugging/` — root cause investigation before any fix attempt
- `.claude/skills/verification-before-completion/` — verification checklist before reporting done

Skills auto-propagate to child agent sessions via the `skills` frontmatter in agent definitions under `.claude/agents/`.

---

## Code Style

- **Formatter**: black (enforced by pre-push hook)
- **Linting**: pylint errors-only
- **Line length**: 88 (black default); pylint allows up to 99
- **Python**: 3.11+, type hints encouraged
- **Comments**: only where code can't speak for itself

No f-strings in log calls — use `%`-style formatting:

```python
# Wrong
logger.info(f"Processing task {task_id}")

# Correct
logger.info("Processing task %s", task_id)
```

Mutable defaults in dataclasses must use `field(default_factory=...)`:

```python
# Wrong
@dataclass
class Foo:
    items: list = []

# Correct
@dataclass
class Foo:
    items: list = field(default_factory=list)
```

Shared dict shapes go in `golem/types.py` — never define inline TypedDicts in individual modules.

---

## Reporting Issues

Use the [bug report](https://github.com/itsmeboris/golem/issues/new?template=bug_report.yml) or [feature request](https://github.com/itsmeboris/golem/issues/new?template=feature_request.yml) templates.

## Questions & Discussion

For questions, ideas, and general discussion, use [GitHub Discussions](https://github.com/itsmeboris/golem/discussions) rather than opening an issue.
