# Task Agent

An autonomous AI task execution system powered by Claude CLI. Picks up tasks from a tracker (e.g. Redmine), executes them via Claude, validates the results, and reports back -- all without human intervention.

## How It Works

```
                         +-----------+
                         |  Tracker  |  (Redmine, local files, ...)
                         +-----+-----+
                               |
                         poll / detect
                               |
                    +----------v-----------+
                    |     Flow (flow.py)    |  tick loop, session management
                    +----------+-----------+
                               |
              +----------------+----------------+
              |                                 |
     simple task                        complex task
              |                                 |
   +----------v-----------+        +------------v-----------+
   | Orchestrator         |        | Supervisor             |
   | (orchestrator.py)    |        | (supervisor.py)        |
   |                      |        |                        |
   | DETECTED             |        | decompose into         |
   |  -> RUNNING          |        |   subtasks             |
   |  -> VALIDATING       |        | execute each           |
   |  -> COMPLETED/FAILED |        | validate each          |
   +----------+-----------+        | summarize              |
              |                    +------------+-----------+
              |                                 |
              +----------------+----------------+
                               |
                    +----------v-----------+
                    |   Claude CLI         |
                    |   (cli_wrapper.py)   |
                    |                      |
                    |   - stream events    |
                    |   - track progress   |
                    |   - extract results  |
                    +----------+-----------+
                               |
              +----------------+----------------+
              |                |                 |
     +--------v------+  +-----v------+  +-------v--------+
     | Validation     |  | Committer  |  | Notifications  |
     | (validation.py)|  |(committer) |  | (Teams, log)   |
     |                |  |            |  |                 |
     | PASS/PARTIAL/  |  | git add    |  | started card   |
     |   FAIL verdict |  | git commit |  | completed card |
     +----------------+  +------------+  | failure card   |
                                         +----------------+
```

### State Machine

Each task session goes through these states:

```
DETECTED -> RUNNING -> VALIDATING -> COMPLETED
                |           |
                v           v
             FAILED      FAILED (retry -> RUNNING)
```

### Profile System

All external integrations are pluggable via **profiles**. A profile bundles five backends:

| Interface        | What it does                           | Redmine profile        | Local profile       |
|------------------|----------------------------------------|------------------------|---------------------|
| `TaskSource`     | Discover and read tasks                | Redmine REST API       | YAML files          |
| `StateBackend`   | Update status, post comments           | Redmine REST API       | Log to stdout       |
| `Notifier`       | Send lifecycle notifications           | Teams Adaptive Cards   | Log to stdout       |
| `ToolProvider`   | Select MCP servers for each task       | Keyword-based scoping  | No MCP servers      |
| `PromptProvider` | Load and format prompt templates       | `prompts/` directory   | `prompts/`          |

Switch profiles with a single config field: `profile: redmine` or `profile: local`.

## Quick Start

### 1. Install

```bash
# Clone and install
git clone <repo-url> task-agent && cd task-agent
pip install -e .

# For dashboard support:
pip install -e ".[dashboard]"
```

### 2. Configure

```bash
cp .env.example .env          # fill in API keys
cp config.yaml.example config.yaml   # customize settings
```

### 3. Run

```bash
# Execute a single task by Redmine issue ID
python -m task_agent run 12345

# Execute a task from a prompt (no tracker needed)
python -m task_agent run -p "Refactor the logging module to use structured JSON"

# Poll for tasks continuously (daemon mode)
python -m task_agent daemon --foreground

# Launch the web dashboard
python -m task_agent dashboard --port 8082
```

## Project Structure

```
task_agent/
|-- cli.py                  # CLI entry point (run, daemon, dashboard, poll, status)
|-- flow.py                 # Tick-driven flow: poll -> detect -> orchestrate
|-- orchestrator.py         # State-machine session lifecycle (6 states)
|-- supervisor.py           # Decompose -> execute subtasks -> validate -> summarize
|-- validation.py           # Lightweight validation agent (PASS/PARTIAL/FAIL)
|-- committer.py            # Git commit with structured message format
|-- event_tracker.py        # Stream-json event processing and milestone tracking
|-- poller.py               # Task detection (Redmine polling)
|-- prompts.py              # Template loader for prompt files
|-- notifications.py        # Teams Adaptive Card builders
|-- mcp_scope.py            # Dynamic MCP server selection by task keywords
|-- workdir.py              # Per-task working directory resolution
|-- worktree_manager.py     # Git worktree isolation for concurrent tasks
|
|-- interfaces.py           # 5 Protocol definitions (TaskSource, StateBackend, ...)
|-- profile.py              # Profile registry and bundle dataclass
|
|-- backends/
|   |-- redmine.py          # Redmine TaskSource + StateBackend
|   |-- teams_notifier.py   # Teams Notifier
|   |-- mcp_tools.py        # Keyword-based ToolProvider
|   |-- local.py            # Null/log backends for local testing
|   |-- profiles.py         # Profile factory registration
|
|-- prompts/                # Service-agnostic prompt templates (6 files)
|
|-- core/                   # Vendored utilities
|   |-- config.py           # YAML config loader with env expansion
|   |-- cli_wrapper.py      # Claude CLI subprocess wrapper
|   |-- flow_base.py        # BaseFlow / PollableFlow abstractions
|   |-- dashboard.py        # Web dashboard routes and API
|   |-- run_log.py          # JSONL run history
|   |-- live_state.py       # Real-time session state
|   |-- json_extract.py     # JSON extraction from LLM output
|   |-- stream_printer.py   # CLI event pretty-printer
|   |-- report.py           # Report file writer
|   |-- daemon_utils.py     # PID management, daemonize
|   |-- teams.py            # Teams webhook client
|   |-- service_clients.py  # Redmine HTTP helpers
|   |-- defaults.py         # Service URLs and timeouts
|   |-- commit_format.py    # Commit tag loader
|   |-- control_api.py      # Flow control REST API
|   |-- triggers/           # Trigger base classes
|
|-- tests/                  # 139 tests
|-- data/                   # Runtime data (sessions, traces, logs)
|
|-- config.yaml.example     # Annotated config template
|-- .env.example            # Environment variable template
|-- commit_format.yaml      # Commit message tag definitions
|-- pyproject.toml          # Package metadata
|-- .claude/                # Claude Code settings and skills
```

## Configuration

### config.yaml

See [config.yaml.example](config.yaml.example) for all options. Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `profile` | `redmine` | Backend profile (`redmine` or `local`) |
| `task_model` | `sonnet` | Claude model for task execution |
| `budget_per_task_usd` | `10.0` | Max spend per task (0 = unlimited) |
| `supervisor_mode` | `true` | Decompose tasks into subtasks |
| `max_retries` | `1` | Retry count on PARTIAL validation verdict |
| `auto_commit` | `true` | Git commit on PASS verdict |
| `use_worktrees` | `true` | Isolate tasks in git worktrees |

### .env

Service credentials loaded at startup:

```bash
REDMINE_URL=https://redmine.example.com
REDMINE_API_KEY=your-api-key
TEAMS_TASK_AGENT_WEBHOOK_URL=https://...   # optional
```

## Writing a Custom Profile

Implement the five protocols from `interfaces.py` and register your profile:

```python
# my_backends.py
from task_agent.profile import register_profile, TaskAgentProfile
from task_agent.backends.local import LogNotifier, NullToolProvider
from task_agent.prompts import FilePromptProvider

class GitHubTaskSource:
    def poll_tasks(self, projects, detection_tag, timeout=30):
        # fetch GitHub issues with the detection_tag label
        ...
    def get_task_description(self, task_id):
        ...
    # ... implement remaining TaskSource methods

class GitHubStateBackend:
    def update_status(self, task_id, status):
        # close/reopen GitHub issue
        ...
    def post_comment(self, task_id, text):
        # post issue comment
        ...
    def update_progress(self, task_id, percent):
        ...

def _build_github_profile(config):
    return TaskAgentProfile(
        name="github",
        task_source=GitHubTaskSource(),
        state_backend=GitHubStateBackend(),
        notifier=LogNotifier(),
        tool_provider=NullToolProvider(),
        prompt_provider=FilePromptProvider(),
    )

register_profile("github", _build_github_profile)
```

Then in `config.yaml`:
```yaml
flows:
  task_agent:
    profile: github
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dashboard]"
pip install pytest black pylint

# Run tests
pytest tests/ -x -q

# Lint
black --check .
pylint --errors-only task_agent/
```

## License

MIT
