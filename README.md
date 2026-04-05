<p align="center">
  <img src="assets/golem-logo.png" alt="Golem" width="240" />
</p>

<h1 align="center">Golem</h1>

<p align="center">
  <strong>An autonomous AI agent that picks up tasks, executes them, and delivers results — no human in the loop.</strong>
</p>

<p align="center">
  <img src="assets/demo-dashboard.gif" alt="Dashboard overview — task lifecycle" width="720" />
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-3776AB?logo=python&logoColor=white" alt="Python 3.11+"></a>
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License"></a>
  <a href="https://github.com/itsmeboris/Golem/wiki"><img src="https://img.shields.io/badge/docs-wiki-blue?logo=github" alt="Wiki"></a>
  <a href="https://star-history.com/#itsmeboris/golem&Date"><img src="https://img.shields.io/badge/star_history-→-yellow" alt="Star History"></a>
  <a href="https://docs.anthropic.com/en/docs/claude-code"><img src="https://img.shields.io/badge/made_with-Claude-blueviolet?logo=anthropic" alt="Made with Claude"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#why-golem">Why Golem</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#how-it-works">How It Works</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="https://github.com/itsmeboris/Golem/wiki">Wiki</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

---

Golem runs as a daemon, picks up work from your issue tracker or direct prompts, spins up Claude agents, validates the output, commits the results, and notifies your team — in a continuous loop.

Submit a prompt. Walk away. It's done.

---

## Why Golem

Most AI coding tools wait for you to invoke them. Golem runs the other way around.

**Work anywhere** — Submit a prompt from any directory and Golem runs it there. Register multiple repos with `golem attach` — the heartbeat scans all of them with fair round-robin scheduling.

**Daemon-centric** — Everything runs through the daemon. Submit a prompt from the CLI, drop a file, or hit the HTTP API — the daemon picks it up, executes it in the background, and reports back.

**Parallel execution** — Multiple Claude instances run simultaneously, each in its own git worktree. Validated work merges cleanly through a sequential merge queue.

**Deep quality pipeline** — Every task passes through deterministic verification (`black`, `pylint`, `pytest`), AST-based structural analysis, coverage delta on changed files, documentation relevance checks, and a separate validation agent review. Only fully validated work gets committed.

**Budget guardrails** — Per-task dollar limits and timeouts. A one-liner fix won't accidentally burn $50 in API calls.

**Pluggable everything** — Swap Redmine for GitHub Issues, Teams for Slack, or write your own backend — without touching core logic.

---

## Who Is This For?

**Developers using Claude Code who want it to work autonomously.** If you're already paying for Claude and find yourself running the same "edit → test → fix → commit" loop, Golem automates that entire cycle.

- **Solo developers** — submit a prompt, work on something else, come back to committed code
- **Small teams** — assign tasks to Golem via your issue tracker, get notified when they're done
- **AI/LLM enthusiasts** — a real-world autonomous agent with validation, not a demo or benchmark

## How Is This Different?

| | Interactive tools | Autonomous agents | **Golem** |
|---|---|---|---|
| **Mode** | You drive | Agent drives | **Agent drives** |
| **Execution** | Single session | Single session or cloud | **Daemon + parallel worktrees** |
| **Validation** | Manual review | Internal / benchmarks | **black + pylint + pytest + AST + review agent** |
| **Budget control** | None | Varies | **Per-task dollar limit** |
| **Merge workflow** | Manual | Patch / internal | **Rebase + merge queue + integrity check** |

---

## Quick Start

### Prerequisites

- **Python 3.11+**
- **[Claude CLI](https://docs.anthropic.com/en/docs/claude-code)** — Golem wraps Claude Code as a subprocess. Install it first and verify `claude --version` works.
- **Git** — for worktree isolation and merge operations.

### Cost

Golem requires **Claude CLI** with a paid Anthropic plan (Claude Pro or API access). Typical task costs:

| Task type | Typical cost | Typical time |
|-----------|-------------|-------------|
| Simple bug fix | $0.50–$1.00 | 1–3 min |
| New feature / endpoint | $1.00–$3.00 | 2–5 min |
| Complex refactor | $3.00–$8.00 | 5–15 min |

The `budget_per_task_usd` setting (default: $10) caps spend per task.

### 1. Install

```bash
git clone https://github.com/itsmeboris/golem.git && cd golem
pip install -e .
```

### 2. Configure

```bash
golem init                             # interactive wizard → writes ~/.golem/config.yaml
```

Golem auto-creates `~/.golem/config.yaml` with sensible defaults on first run.
To customize, run `golem init` or edit the file directly.

<details>
<summary><strong>GitHub Issues setup</strong></summary>

```bash
gh auth login                          # authenticate the gh CLI
golem init                             # select "github" profile, enter owner/repo
```

Or set it manually in `config.yaml`:

```yaml
profile: github
projects:
  - owner/repo
detection_tag: golem                   # label on issues Golem should pick up
```

Golem assigns issues to itself on pickup, closes them on completion, and creates a PR for each committed task.

</details>

### 3. Run

```bash
# Submit a prompt — runs in your current directory, daemon auto-starts
golem run -p "Refactor the logging module to use structured JSON"

# Submit from a file
golem run -f plan.md

# Register repos for background heartbeat work
golem attach                           # attach current directory
golem attach /path/to/other/project    # attach any directory
golem detach                           # unregister current directory

# Check what's running
golem status

# Launch the web dashboard
golem dashboard --port 8081
```

For a full walkthrough with expected output, see the **[Getting Started](https://github.com/itsmeboris/Golem/wiki/Getting-Started)** wiki page.

### 4. Recommended Plugins (Optional)

Golem embeds its core methodologies (TDD, debugging, verification) directly in its prompts — no plugins required. However, these Claude Code plugins enhance the experience for interactive development alongside Golem:

| Plugin | What it adds | Install |
|--------|-------------|---------|
| **superpowers** | Structured planning, parallel agents, code review workflows | `claude plugins install superpowers` |
| **code-review** | PR review automation with confidence scoring | `claude plugins install code-review` |
| **code-simplifier** | Code clarity and reuse analysis | `claude plugins install code-simplifier` |

Run `golem setup` to check your environment and see which plugins are installed.

---

## How It Works

```mermaid
flowchart TB
    cli["golem run -p / -f<br/>(defaults to cwd)"] -- submit --> api_gw
    api["HTTP API"] -- submit --> api_gw
    tracker["Issue Tracker<br/>(plugin)"] -. poll .-> flow

    subgraph daemon ["Golem Daemon"]
        direction TB
        api_gw["API Gateway<br/>CORS · Auth · Rate Limit"] --> flow["Flow Engine"]
        flow --> orch["Orchestrator"]
        orch --> vfy["Verifier<br/>black · pylint · pytest"]
        vfy -- pass --> val["Validation Agent"]
        vfy -- fail --> retry["Retry w/ Feedback"]
        retry --> orch
        val -- PASS --> mq["Merge Queue"]
        val -- PARTIAL --> retry
        hb["Heartbeat<br/>(multi-repo)"] -. "idle → submit" .-> flow
    end

    mq -- "rebase + merge" --> commit["Commit"]
    commit --> notify["Notify"]
    val -- FAIL --> report["Report Failure"]
```

Tasks flow through a state machine: **DETECTED → RUNNING → VERIFYING → VALIDATING → COMPLETED** (or RETRYING / FAILED). Each task runs in an isolated git worktree with its own Claude instance. Validated work enters a sequential merge queue that rebases onto HEAD — your working tree is never touched. The heartbeat scans all attached repos with fair round-robin scheduling when the daemon is idle.

---

## Documentation

| Resource | Description |
|----------|-------------|
| **[Wiki](https://github.com/itsmeboris/Golem/wiki)** | Comprehensive guides — getting started, configuration, CLI, dashboard, troubleshooting, architecture, and more |
| **[Architecture](docs/architecture.md)** | Technical deep-dive — task lifecycle, agent pipeline, profiles, HTTP API |
| **[Operations](docs/operations.md)** | Operational reference — heartbeat, self-update, health monitoring, config management |
| **[Contributing](CONTRIBUTING.md)** | Development setup, project layout, coding conventions, testing |
| **[Changelog](CHANGELOG.md)** | Release history |

---

## License

MIT
