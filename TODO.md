# Open Items and Future Work

Consolidated list of TODOs, stubs, and planned features across the codebase.

## Teams Direct Messages (Graph API)

Send Adaptive Cards as 1:1 DMs to users (build author on failure, issue author on validation).
Requires Azure AD app registration with `Chat.Create` and `ChatMessage.Send` permissions.

| Item | Location | Status |
|------|----------|--------|
| `send_direct_message()` stub | `core/teams.py:72-81` | `NotImplementedError` — implement with Graph API |
| Config fields | `core/config.py:119-121` | `graph_client_id`, `graph_client_secret`, `graph_tenant_id` ready |
| Config YAML placeholders | `config_flows.yaml` | Commented env var references |
| DM on Jenkins failure | `flows/jenkins/flow.py:324-327` | Commented callsite — extract author email, send card |
| DM on Redmine validation | `flows/redmine/flow.py:140-143` | Commented callsite — extract author email, send card |

### Implementation steps

1. Register Azure AD app with `Chat.Create` + `ChatMessage.Send` application permissions
2. Uncomment and populate `graph_*` fields in `config_flows.yaml`
3. Implement `TeamsClient.send_direct_message()` using `POST /v1.0/chats` + `POST /v1.0/chats/{id}/messages`
4. Uncomment callsites in Jenkins and Redmine flows
5. Map Gerrit/Redmine usernames to Azure AD user emails (may need a lookup table)

## Teams Thread Replies via Graph API

Task-agent notifications currently send standalone cards (one per event via PA webhook). With multiple concurrent tasks, this floods the channel. Graph API threading would group all updates for a single task under one parent message.

| Item | Location | Status |
|------|----------|--------|
| Config fields | `core/config.py:119-121` | `graph_client_id`, `graph_client_secret`, `graph_tenant_id` ready |
| Activity card builder | `agents/task_agent/notifications.py` | `build_task_activity_card()` redesigned for thread replies |
| `last_text` on TrackerState | `agents/task_agent/event_tracker.py` | Human-readable agent status captured |
| Thread message ID storage | `agents/task_agent/orchestrator.py` | Add `thread_message_id` to `TaskSession` |

### Implementation steps

1. Register Azure AD app with `ChannelMessage.Send` application permission
2. On task start: `POST /teams/{team-id}/channels/{channel-id}/messages` with the Started card — capture the returned `id` as `thread_message_id`
3. Store `thread_message_id` in `TaskSession` for reply correlation
4. Mid-run updates: `POST .../messages/{thread_message_id}/replies` with the Activity card (using `status_text` from `last_text`)
5. On completion/failure: post final card as a thread reply
6. Fallback: if Graph API call fails, fall back to standalone webhook card

### Prerequisites

- Azure AD app registration with `ChannelMessage.Send` permission (delegated or application)
- Team ID and Channel ID for the task_agent channel (discoverable via Graph API)
- `graph_client_id`, `graph_client_secret`, `graph_tenant_id` populated in config_flows.yaml / config_agent.yaml

## RERUN Button UX

The RERUN button on Jenkins failure cards uses `Action.OpenUrl`, which opens a browser tab. The user sees an HTML confirmation page. This works but is not seamless.

| Item | Location | Notes |
|------|----------|-------|
| `Action.OpenUrl` for RERUN | `core/teams.py:126` | Only card action type that reaches our server without bot/premium |
| Browser RERUN endpoint | `core/triggers/webhook.py` `_handle_rerun_browser()` | Returns styled HTML page |

### Alternative: Azure Bot Framework

Registering an Azure Bot would allow `Action.Execute` / `Action.Submit` on cards, which POSTs directly to our webhook server without opening a browser. The card could update inline with the result.

Steps:
1. Register Azure Bot Channel in Azure Portal (free tier)
2. Add bot messaging endpoint to our FastAPI server
3. Replace `Action.OpenUrl` with `Action.Execute` in card builders
4. Handle `invoke` activities from Teams Bot Framework

## Teams Command Registry — Future Commands

The command registry (`flows/teams/registry.py`) is extensible. Adding a command = one `CommandHandler` subclass + `registry.register()`. Planned commands:

| Command | Type | Description |
|---------|------|-------------|
| `REVIEW <change_id>` | async | Trigger Gerrit code review |
| `FIX <build_number>` | async | Suggest fix for failed build |
| `PLAN <issue_id>` | async | Generate implementation plan |
| `ASSIGN <issue_id>` | sync | Assign issue to yourself |
| `CLOSE <issue_id>` | sync | Close a resolved issue |

If future commands need AI reasoning (e.g., natural language queries), they can dispatch through the standard Claude pipeline inside their `execute()` method — see `AnalyzeCommand` for the pattern.


## Power Automate Flows (not code)

The following PA flows need to be created to complete the integration:

| Flow | Trigger | Action |
|------|---------|--------|
| Command Handler | @mention in Teams | POST to `/api/command`, branch on `status` field to show result or "Processing..." |
| Async Callback Receiver | PA HTTP trigger | Receive result card from server, post to conversation |
| Proactive Notifications | Incoming webhook from server | Post failure/validation cards (existing pattern, enhanced with PA) |

## MCP-Driven Flow Improvements

| Item | Notes |
|------|-------|
| Jenkins-Gerrit cross-reference | When a build has `GERRIT_CHANGE_NUMBER` in its parameters, fetch the Gerrit change details for richer failure context |
| Gerrit flow: fetch CI results | When reviewing a change, pull its CI build results to inform the review |

## Worktree Merge — Agent-Assisted Conflict Resolution

When multiple task-agent sessions run concurrently on the same repo, their worktree branches may conflict at merge time. The current `merge_and_cleanup()` in `agents/task_agent/worktree_manager.py` already rebases the agent branch onto the current target before merging, which handles the common case (non-overlapping file changes). When rebase fails due to genuine conflicts, the branch is preserved and the merge is skipped — requiring manual human intervention.

| Item | Location | Status |
|------|----------|--------|
| Rebase-before-merge | `agents/task_agent/worktree_manager.py:merge_and_cleanup()` | Implemented — handles non-conflicting concurrent merges |
| Stash/unstash helpers | `agents/task_agent/worktree_manager.py:_stash_if_dirty()` | Implemented — protects dirty working tree during merge |
| Agent-assisted conflict resolution | `agents/task_agent/worktree_manager.py` | **Not implemented** — spawn Claude to resolve conflicts |

### Implementation steps

1. On rebase failure (before `--abort`), parse `git diff --name-only --diff-filter=U` to list conflicted files
2. For each conflicted file, read the conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`)
3. Gather context: the subtask description, the changes from both sides (ours = target branch, theirs = agent branch), and the original file before the conflict
4. Spawn a Claude agent (via `claude_runner`) with a prompt containing the conflict markers, both sides' intent, and instructions to produce a clean merged file
5. Apply the agent's resolution: write the resolved file, `git add` it, `git rebase --continue`
6. If the agent's resolution fails validation (e.g., syntax errors, test failures), abort the rebase and fall back to human escalation with a detailed error message
7. Track resolution attempts in the session's event_log as supervisor events

### Design considerations

- **Scope limit**: Only attempt auto-resolution for conflicts in ≤5 files. Larger conflicts likely indicate architectural issues that need human judgment.
- **Validation**: After resolution, run a quick syntax check (e.g., `python -c "compile(open(f).read(), f, 'exec')"` for Python files) before continuing the rebase.
- **Cost tracking**: Resolution attempts consume API tokens. Log cost in the session and include it in the final summary.
- **Timeout**: Cap the resolution agent at 60 seconds per file. Abort and escalate on timeout.
- **Idempotency**: If merge_and_cleanup is retried after a failed resolution attempt, ensure stale rebase state is cleaned up first.

## Verify: MCP Cache File Read Failures in Bash Sandbox

The `analyze_failure.txt` prompt previously instructed agents to read MCP-cached files with `Bash` (`cat`/`python3 -c`). The Bash sandbox intermittently blocks access to `/mtrsysgwork/mcp-cache/`, causing `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`. Fixed by switching the instruction to use the `Read` tool instead.

**Follow-up**: After the fix is deployed, monitor new Jenkins trace files for the same error pattern to confirm the fix is effective. Check whether any other flow prompts (Gerrit, Redmine, task-agent) have similar `cat`-based instructions for cached files.

Known affected builds (pre-fix):
- `chipsim_ci-27472`, `chipsim_ci-27471`, `chipsim_ci-27431`, `chipsim_nightly_regression-959`

Search command: `grep -r "Expecting value.*char 0" data/flows/traces/`

## Gerrit Review — Dedicated Service Account

The agent currently shares the user's Gerrit credentials, so any review it posts overwrites the user's manual vote. A workaround skips the review entirely when an existing vote is detected on the patchset (`flows/gerrit/flow.py:_post_gerrit_review()`).

Once a dedicated Gerrit service account is provisioned, remove the `get_my_code_review_vote` early-return guard and restore the previous logic (agent votes independently of the user).

## Other

| Item | Notes |
|------|-------|
| Power Automate "wait for response" | Could replace OpenUrl if Premium license becomes available |
| Teams channel monitoring | Could poll a channel for text commands as alternative to webhook inbound |
| Rate limiting on `/api/rerun` and `/api/command` | Currently no protection against repeated requests |
| Authentication on `/api/command` | Currently open — consider shared secret or API key |
| Authentication on `/api/rerun` | Currently open — anyone with the URL can trigger a rerun |

## Architecture — Future Separation Options

The task agent (`agents/`, `main_agent.py`) and regular flows (`flows/`, `main_flows.py`) now have independent entry points with separate config files and data directories, but they live in the same git repository and share `core/`. Two further options if full separation becomes necessary:

### Option B: Monorepo with installable packages

Keep a single git repo but define three installable packages via `pyproject.toml`:

```
pyproject.toml          # defines [project.optional-dependencies] for core, flows, agent
packages/
  core/                 # shared infrastructure (config, dispatcher, teams, etc.)
  flows/                # regular flows package — depends on core
  agents/               # task agent package — depends on core
```

Each package can be installed independently (`pip install -e ".[flows]"` or `pip install -e ".[agent]"`). CI builds each in isolation. Shared `core/` code is vendored or installed as a dependency.

**Pros**: single repo, shared CI, easy cross-cutting changes.
**Cons**: version coupling, monorepo tooling overhead.

### Option C: Separate git repos

Split into three repositories:

```
agent-automation-core    # pip-installable shared library
agent-automation-flows   # depends on core via pip
agent-automation-agent   # depends on core via pip
```

`core` publishes versioned releases. Flows and agent pin to a specific core version. Each repo has independent CI/CD, deployment, and release cycles.

**Pros**: full isolation, independent deployment, clean dependency graph.
**Cons**: cross-cutting changes require multiple PRs, version coordination overhead.
