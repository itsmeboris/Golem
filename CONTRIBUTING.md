# Contributing to Golem

## Getting Started

```bash
git clone https://github.com/itsmeboris/golem.git && cd golem
pip install -e ".[dev,dashboard]"
git config core.hooksPath .githooks
```

## Development Workflow

1. Create a branch for your change
2. Make your changes
3. Run the checks locally:

```bash
black golem/
pylint --errors-only golem/
pytest golem/tests/ -x -q
```

4. Push — the pre-push hook runs all three automatically
5. Open a pull request

## Code Style

- **Formatter**: black (enforced by pre-push hook)
- **Linting**: pylint errors-only
- **Line length**: 99 characters
- **Python**: 3.11+, type hints encouraged
- **Comments**: only where code can't speak for itself

## Adding a Backend

Implement the five protocols in `interfaces.py` (`TaskSource`, `StateBackend`, `Notifier`, `ToolProvider`, `PromptProvider`) and register via `register_profile()`. See `backends/local.py` for a minimal example and `backends/github.py` for a full CLI-based implementation.

## Tests

Tests live in `golem/tests/`. Run the full suite with:

```bash
pytest golem/tests/ -x -q
```

Mark slow or integration tests appropriately:

```python
@pytest.mark.slow
@pytest.mark.integration
```

## Reporting Issues

Use the [bug report](https://github.com/itsmeboris/golem/issues/new?template=bug_report.yml) or [feature request](https://github.com/itsmeboris/golem/issues/new?template=feature_request.yml) templates.

## Questions & Discussion

For questions, ideas, and general discussion, use [GitHub Discussions](https://github.com/itsmeboris/golem/discussions) rather than opening an issue.
