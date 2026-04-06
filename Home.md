# Golem Wiki

**Golem** is an autonomous AI agent daemon that picks up tasks from issue trackers or direct prompts, spins up Claude agents, validates results through a deep quality pipeline, and commits passing work — all without human intervention.

```mermaid
flowchart LR
    submit["Submit Task"] --> daemon["Golem Daemon"]
    daemon --> orchestrate["Orchestrate\n5-phase pipeline"]
    orchestrate --> verify["Verify\nblack · pylint · pytest"]
    verify --> validate["Validate\nAI review agent"]
    validate --> merge["Merge Queue\nrebase + commit"]
    merge --> notify["Notify Team"]
```

---

## Where to Start

### I want to use Golem

1. **[[Getting Started]]** — install, configure, run your first task
2. **[[Configuration]]** — all settings, environment variables, config methods
3. **[[CLI Reference|CLI-Reference]]** — every command with flags and examples
4. **[[Dashboard]]** — web UI for monitoring tasks, merge queue, and config
5. **[[Troubleshooting]]** — common issues and how to fix them

### I want to understand or extend Golem

1. **[[Architecture]]** — system overview, module map, data flow diagrams
2. **[[Task Lifecycle|Task-Lifecycle]]** — state machine, retries, crash recovery
3. **[[Sub-Agents]]** — the 5-phase orchestrated pipeline
4. **[[Backends]]** — profile system, built-in integrations, adding your own
5. **[[Development Guide|Development-Guide]]** — contribute to Golem itself

---

## Key Concepts

| Concept | What it is |
|---------|-----------|
| **Orchestrator** | Durable state machine that drives each task through phases and checkpoints every tick |
| **Sub-Agent** | A Claude instance spawned for a specific phase — builder, reviewer, or verifier |
| **Worktree** | Isolated git copy where each task runs independently, keeping your working tree untouched |
| **Merge Queue** | Sequential pipeline that rebases validated work onto HEAD and commits |
| **Profile** | Pluggable bundle of backends (task source, state, notifier, tools, prompts) |
| **Heartbeat** | Autonomous self-improvement system that finds work when the daemon is idle |

---

> For installation and quick start, see the [README](https://github.com/itsmeboris/Golem#readme). For questions, check the **[[FAQ]]**.
