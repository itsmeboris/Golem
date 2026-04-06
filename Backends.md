# Backends

All external integrations in Golem are pluggable via **profiles** — bundles of five backends you can mix and match. Switching from GitHub Issues to Redmine, or from Slack to Teams, is a one-line change in `config.yaml`. The daemon picks up the new profile on next start or `SIGHUP`.

---

## Overview

A profile packages five interfaces together:

```yaml
profile: local     # file-based submissions, no external services
profile: redmine   # Redmine issue tracking + Slack/Teams + MCP
profile: github    # GitHub Issues via gh CLI + Slack/Teams
```

When the daemon starts, it loads the named profile and wires every subsystem — task polling, status updates, notifications, MCP tools, and prompt templates — through that profile's backends. There are no per-subsystem config flags to keep in sync.

---

## Profile Interfaces

Five `typing.Protocol` interfaces define the contract between Golem's engine and external services. Any class that implements the required methods satisfies the protocol (structural subtyping — no inheritance required).

| Interface | Key Methods | Purpose |
|-----------|-------------|---------|
| `TaskSource` | `poll_tasks`, `get_task_subject`, `get_task_description`, `get_child_tasks`, `create_child_task`, `get_task_comments`, `poll_untagged_tasks` | Discovers and reads task definitions from an external system |
| `StateBackend` | `update_status`, `post_comment`, `update_progress` | Updates task state in an external tracking system |
| `Notifier` | `notify_started`, `notify_completed`, `notify_failed`, `notify_escalated`, `notify_batch_submitted`, `notify_batch_completed`, `notify_health_alert` | Sends lifecycle notifications to an external channel |
| `ToolProvider` | `base_servers`, `servers_for_subject` | Determines which MCP tool servers are available for a given task |
| `PromptProvider` | `format` | Loads and formats prompt templates by name |

All interfaces are defined in `golem/interfaces.py` and decorated with `@runtime_checkable`, so you can use `isinstance()` checks in tests without requiring inheritance.

### TaskSource

```python
class TaskSource(Protocol):
    def poll_tasks(self, projects, detection_tag, timeout=30) -> list[dict]: ...
    def get_task_subject(self, task_id) -> str: ...
    def get_task_description(self, task_id) -> str: ...
    def get_child_tasks(self, parent_id) -> list[dict]: ...
    def create_child_task(self, parent_id, subject, description) -> int | str | None: ...
    def get_task_comments(self, task_id, *, since="") -> list[dict]: ...
    def poll_untagged_tasks(self, projects, exclude_tag, limit=20, timeout=30) -> list[dict]: ...
```

`poll_tasks` returns dicts with at minimum `{"id": ..., "subject": ...}`. `get_task_comments` returns dicts with keys `author`, `body`, `created_at`. Backends that don't support `poll_untagged_tasks` return an empty list — it's used by the heartbeat for Tier 1 triage.

### StateBackend

```python
class StateBackend(Protocol):
    def update_status(self, task_id, status: str) -> bool: ...
    def post_comment(self, task_id, text: str) -> bool: ...
    def update_progress(self, task_id, percent: int) -> bool: ...
```

`update_status` uses canonical values from `TaskStatus`: `in_progress`, `fixed`, `closed`. Each backend maps these to its system-specific IDs or names.

### Notifier

```python
class Notifier(Protocol):
    def notify_started(self, task_id, subject) -> None: ...
    def notify_completed(self, task_id, subject, *, cost_usd, duration_s, steps, verdict, ...) -> None: ...
    def notify_failed(self, task_id, subject, reason, *, cost_usd, duration_s) -> None: ...
    def notify_escalated(self, task_id, subject, verdict, summary, ...) -> None: ...
    def notify_batch_submitted(self, group_id, task_count) -> None: ...
    def notify_batch_completed(self, group_id, status, ...) -> None: ...
    def notify_health_alert(self, alert_type, message, *, details) -> None: ...
```

### ToolProvider

```python
class ToolProvider(Protocol):
    def base_servers(self) -> list[str]: ...
    def servers_for_subject(self, subject: str, *, role: str = "") -> list[str]: ...
```

`servers_for_subject` returns the full list of MCP servers for a given task subject. When `role` is provided, implementations may filter to servers appropriate for that role.

### PromptProvider

```python
class PromptProvider(Protocol):
    def format(self, template_name: str, **kwargs) -> str: ...
```

Loads a template by name from the `prompts/` directory and fills placeholders.

---

## Built-in Profiles

### Local (`profile: local`)

File-based task source backed by `data/submissions/`. Null state backend (no external tracker updates). Log notifier (writes to stdout). Zero external dependencies.

**Best for:** local development, testing, and running Golem without any issue tracker.

```yaml
profile: local
```

Tasks are picked up from JSON files dropped in `data/submissions/`. Each file has `{"prompt": "...", "subject": "..."}`. The CLI (`golem run -p "..."`) and HTTP API write to this directory automatically.

MCP tools are disabled by default for the local profile. Enable with `mcp_enabled: true` in the flow config.

### GitHub (`profile: github`)

Uses the `gh` CLI for issue polling, status updates, comments, and PR creation. Labels control which issues Golem picks up. After closing an issue, Golem verifies the close state with `gh issue view --json state`.

**Requires:** `gh auth login` (GitHub CLI authenticated).

```yaml
profile: github

flows:
  golem:
    projects:
      - "owner/repo"
    detection_tag: "golem"
```

`detection_tag` is the GitHub label Golem watches. Issues with that label are picked up as tasks. Set `mcp_enabled: true` to enable keyword-based MCP server scoping.

### Redmine (`profile: redmine`)

REST API integration for Redmine issue tracking. Reads tasks from configured projects, updates custom fields, posts journal notes as comments.

**Requires:** `REDMINE_URL` and `REDMINE_API_KEY` environment variables.

```yaml
profile: redmine

flows:
  golem:
    projects:
      - "123"   # Redmine project ID
    detection_tag: "golem"
```

---

## Notification Adapters

Both Slack and Teams are configured independently of the profile. Any profile uses whichever notifier is enabled. If both are enabled, Slack takes priority. If neither is enabled, the daemon logs to stdout.

### Slack

Block Kit messages via incoming webhook.

```yaml
slack:
  enabled: true
  webhooks:
    - "https://hooks.slack.com/services/..."
```

### Teams

Adaptive Cards via incoming webhook.

```yaml
teams:
  enabled: true
  webhooks:
    - "https://your-org.webhook.office.com/..."
```

---

## MCP Tools

Golem can scope MCP tool servers per task using keyword matching. This lets different tasks get access to different tools — a database task gets the database MCP server, a deployment task gets the cloud MCP server.

```yaml
flows:
  golem:
    mcp_enabled: true
    mcp_servers:
      - name: "database"
        keywords: ["sql", "migration", "schema"]
      - name: "deploy"
        keywords: ["deploy", "kubernetes", "helm"]
```

When `mcp_enabled` is false (the default for local and github profiles), a `NullToolProvider` is used and no MCP servers are injected into agent sessions.

---

## Adding a Custom Backend

To add a new integration — Jira, Linear, Azure DevOps, or anything else — implement the five interfaces and register a profile factory. The daemon will load it like any built-in profile.

**Step 1 — Create a new file in `golem/backends/`:**

```bash
touch golem/backends/jira.py
```

**Step 2 — Implement the interfaces:**

Start with the minimum viable surface. `get_task_subject` and `get_task_description` can share the same API call. Use `backends/local.py` as a reference for a minimal no-dependency implementation.

**Step 3 — Register with `register_profile()`:**

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

**Step 4 — Import the module so registration runs:**

Add `import golem.backends.jira` in `golem/backends/__init__.py` alongside the existing backend imports.

**Step 5 — Set `profile: your_name` in config:**

```yaml
profile: jira
```

The daemon picks it up on next start or `SIGHUP`.

You only need to implement the methods you actually use. Methods with default implementations in the protocol (like `get_task_comments` returning `[]`, or `poll_untagged_tasks` returning `[]`) can be omitted if the default behavior is acceptable.

For a full CLI-based example with auth, polling, and post-close state verification, see `golem/backends/github.py`.

---

See also: [[Development-Guide]] for coding conventions and testing requirements when writing a new backend.
