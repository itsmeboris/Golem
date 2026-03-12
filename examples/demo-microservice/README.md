# Golem Demo — Task Tracker Microservice

A tiny Flask API that Golem can modify autonomously. Use this to see Golem in action before trying it on your own codebase.

## Prerequisites

- [Golem](../../README.md#quick-start) installed (`pip install -e .` from the repo root)
- [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- Python 3.11+

## Cost

Each demo task typically costs **$0.50–$3.00** in Claude API usage. The included `config.yaml` caps spend at $5 per task.

## Quick Start

```bash
# From the repo root:
cd examples/demo-microservice
pip install -r requirements.txt

# Run a demo task — Golem handles everything:
golem run -f prompts/add-logging.md

# Or try fixing the seeded bug:
golem run -f prompts/fix-bug.md

# Or add a new endpoint:
golem run -f prompts/add-endpoint.md
```

## What to Expect

1. Golem starts the daemon (if not already running)
2. Creates an isolated git worktree for the task
3. A Claude agent reads the codebase, writes code and tests
4. Deterministic verification runs (black, pylint, pytest)
5. A validation agent reviews the work
6. On PASS — changes are committed and merged back

Typical runtime: **2–5 minutes** per task.

## Available Prompts

| Prompt | What it does | Difficulty |
|--------|-------------|-----------|
| `prompts/add-logging.md` | Add structured JSON logging | Easy |
| `prompts/fix-bug.md` | Fix a 200-instead-of-404 bug | Easy |
| `prompts/add-endpoint.md` | Add a DELETE endpoint | Easy |

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `claude: command not found` | Install [Claude CLI](https://docs.anthropic.com/en/docs/claude-code) and verify `claude --version` works |
| Task fails with budget exceeded | The `config.yaml` caps at $5/task — increase `budget_per_task_usd` if needed |
| Verification fails on import | Run `pip install -r requirements.txt` in the demo directory first |
| `ModuleNotFoundError: No module named 'app'` | Run pytest from inside the `examples/demo-microservice/` directory, not from the repo root |
