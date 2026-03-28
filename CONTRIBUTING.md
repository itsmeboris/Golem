# Contributing to Golem

Welcome! Golem is an autonomous AI coding agent daemon. For a comprehensive contributor guide, see the **[Development Guide](https://github.com/itsmeboris/Golem/wiki/Development-Guide)** on the wiki. This page covers the essentials to get started.

---

## Getting Started

```bash
git clone https://github.com/itsmeboris/golem.git && cd golem
pip install -e ".[dev,dashboard]"
git config core.hooksPath .githooks
```

Verify everything works:

```bash
make lint && make test
```

---

## Development Workflow

1. Branch from `master`
2. Write tests first (TDD — red, green, refactor)
3. Implement until tests pass
4. Run checks: `make lint && make test`
5. Push — the pre-push hook blocks on failure
6. Open a pull request

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

## Quick Reference

| Topic | Rule |
|-------|------|
| **Coverage** | 100% enforced (`--cov-fail-under=100`) |
| **Formatter** | black (line length 88) |
| **Linter** | pylint errors-only |
| **Logging** | `logger.info("msg %s", val)` — never f-strings |
| **TypedDicts** | Define in `golem/types.py`, never inline |
| **Mutable defaults** | `field(default_factory=list)`, never `[]` |
| **Tests** | Mirror source structure, `@pytest.mark.parametrize` for repeated logic |
| **Bug fixes** | Must include a reproduction test |
| **Git hooks** | Never skip with `--no-verify` |

For full coding conventions, testing rules, and extension guides (adding backends, skills, prompts), see the **[Development Guide](https://github.com/itsmeboris/Golem/wiki/Development-Guide)** and **[Backends](https://github.com/itsmeboris/Golem/wiki/Backends)** wiki pages.

---

## Reporting Issues

Use the [bug report](https://github.com/itsmeboris/golem/issues/new?template=bug_report.yml) or [feature request](https://github.com/itsmeboris/golem/issues/new?template=feature_request.yml) templates.

## Questions & Discussion

For questions, ideas, and general discussion, use [GitHub Discussions](https://github.com/itsmeboris/golem/discussions) rather than opening an issue.
