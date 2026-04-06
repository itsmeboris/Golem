# Dashboard

The Golem web dashboard provides live monitoring of tasks, merge queue management, and configuration editing — all in a browser.

---

## Launching

The dashboard is auto-started alongside the daemon on the configured port (default: 8081):

```bash
# Start the daemon (dashboard starts automatically)
golem daemon

# Or start a standalone dashboard without the daemon
golem dashboard --port 8081
```

Access it at `http://localhost:8081/dashboard`.

The daemon and dashboard share the same port — the REST API is served at `http://localhost:8081/api/` on the same process. There is no need to run them separately.

---

## Overview Tab

The main landing view shows your entire task history at a glance:

- **Search bar** — filter tasks by ID, subject, or state (client-side, instant)
- **State filter dropdown** — show only Running, Completed, Failed, or Detected tasks
- **Task list** (left panel) — paginated (25 per page) with status badge, cost, and elapsed time
- **Preview panel** (right panel) — summary of the selected task including validation verdict, concerns, and commit SHA
- **Status color legend** for merge queue states (pending, merging, deferred, conflicts, completed, failed)

Status badges:
| Badge | Meaning |
|-------|---------|
| RUNNING | Task is executing in an active Claude session |
| VERIFYING | Running `black`, `pylint`, `pytest` checks |
| VALIDATING | Validation agent reviewing the work |
| RETRYING | Retrying after PARTIAL verdict |
| COMPLETED | Validated, merged, and committed |
| FAILED | Budget exceeded, timeout, or validation failed |
| HUMAN_REVIEW | Awaiting human feedback on a failed task |

---

## Task Detail Tab

Click any task to open a detailed view:

- **Header** — task ID, subject, profile, model, creation time
- **Metrics strip** — cost, duration, milestone count, tools called
- **Phase-aware timeline** with sidebar navigation — jump directly to any phase:
  - UNDERSTAND
  - PLAN
  - BUILD
  - REVIEW
  - VERIFY
- **Live strip** — for running tasks, shows current phase and elapsed time with a pulsing indicator
- **Subagent grouping** — builder and reviewer subagent turns are grouped and collapsible
- **Per-tool usage visualization** — shows which tools were called and how often in each phase

The timeline is built from parsed JSONL traces. The dashboard uses the `?since_event=N` parameter on the trace endpoint to avoid re-parsing the full trace on every poll — only new events since the last render are fetched.

---

## Merge Queue Tab

Real-time view of the merge pipeline:

**Metrics bar** at the top shows:
- Pending (validated, waiting to merge)
- Merging (currently rebasing/merging)
- Deferred (merge blocked — dirty working tree or transient failure)
- Conflicts (merge agent active resolving conflicts)
- Merged today
- Failed today

**Collapsible sections:**
- **Active** — currently executing merge
- **Pending** — validated tasks queued for merge
- **Deferred** — blocked merges (expand to see reason and retry count)
- **Conflicts** — tasks where the merge agent is resolving conflicts
- **Recent** — completed merges (last 24 hours)

Each entry is expandable to show the full task details. **One-click retry** is available for failed or deferred entries — this re-enqueues the merge without re-running the task.

When the deferred count exceeds the `merge_deferred_threshold` (default: 5), the health monitor fires `ALERT_MERGE_QUEUE_BLOCKED`. The dashboard highlights this condition in red.

---

## Config Tab

A live config editor organized by category:

- **Categories** — profile, budget, models, heartbeat, self-update, health, integrations, dashboard, daemon, logging, polling
- **Field metadata** — each field shows its type, default value, and description
- **Validation** — values are validated before saving (type checking, range constraints)
- **Optional daemon reload** — a toggle lets you trigger `SIGHUP` after saving so changes take effect immediately without restart

This is equivalent to `golem config` but in the browser. Changes write atomically to `config.yaml`.

---

## Additional Features

**JSONL trace parsing** — raw agent JSONL traces are parsed server-side into structured timelines with:
- Phase detection (`## Phase: UNDERSTAND`, etc.)
- Subagent grouping (builder, reviewer, verifier turns)
- Per-tool usage counts and timing
- Error highlighting

**Dark / light theme** — toggle in the dashboard header. Preference persists via `localStorage`.

**Polling with cache bypass** — the timeline endpoint accepts `?since_event=N`. When the trace has not grown since the last poll, the server returns the cached result and avoids a full re-parse. This makes the live strip efficient even during long-running tasks.

**Toast notifications** — styled snackbar (success/error/info) replaces native `alert()`; auto-dismiss after 4s; button loading states during async operations.

**Loading states** — CSS skeleton cards on initial task list load; loading spinners on fetch calls with parsed-trace cache guard to avoid redundant spinners.

**Keyboard shortcuts** — Escape (close detail/modal), Arrow keys (task list navigation), Ctrl/Cmd+K (focus search), 1-5 (switch tabs). Suppressed when an input field is focused.

**Copy-to-clipboard** — click-to-copy for task IDs, prompt hashes, commit SHAs, and error text with toast confirmation (`navigator.clipboard.writeText`).

**Deep linking / URL sharing** — hash-based routing (`#overview`, `#merge-queue`, `#prompts`, `#task/<id>`). `hashchange` event listener for browser back/forward; `DOMContentLoaded` handler restores view from bookmarked URLs.

**Data visualizations** — inline SVG sparklines for success-rate trends; CSS horizontal bar charts for cost-by-model and average phase duration; rendered in the overview stats panel.

**Mobile-responsive layout** — `@media` queries at 1024px and 600px breakpoints; 44px touch targets; vertical stacking; scrollable tab bars.

**Empty states** — first-time guidance when no tasks exist; filter-no-match feedback when search/state filter returns nothing.

---

## API Endpoints

The REST API is served on the same port as the dashboard. All endpoints return JSON.

**Security:** All `/api/*` endpoints (except `/api/health` and `/api/flow/status`)
require an `X-Api-Key` header when `api_key` is configured. Mutation endpoints
(`/api/submit`, `/api/submit/batch`, `/api/cancel`) are additionally rate-limited
to 10 requests/minute per client IP (sliding window). CORS is restricted to
`localhost`/`127.0.0.1` origins only. File reads in `/api/submit` validate paths
against CWD and registered repos, and open with `O_NOFOLLOW` to prevent symlink
attacks.

`Admin*` = requires the `Authorization: Bearer <token>` header if `admin_token` is configured in `config.yaml`; open access otherwise.

### Health

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/health` | GET | None | Readiness probe — returns `{"ok": true, "pid": ..., "uptime_seconds": ...}` |

### Submission

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/submit` | POST | API key | Submit a task — accepts `{"prompt": "..."}` or `{"file": "/path/to/file.md"}` with optional `subject` and `work_dir` |
| `/api/submit/batch` | POST | API key | Submit multiple tasks — accepts `{"tasks": [...], "group_id": "..."}` with per-task `depends_on` for ordering |
| `/api/cancel/{task_id}` | POST | API key | Cancel a running task |

### Analytics

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/analytics` | GET | API key | Quality metrics — pass/fail rates, avg cost, retry effectiveness, top failure reasons |
| `/api/cost-analytics` | GET | API key | Cost analytics — spend per task, totals, budget remaining |
| `/api/live` | GET | API key | Live dashboard state — active tasks, queue depth, uptime, recently completed tasks |

### Sessions

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/sessions` | GET | API key | All session metadata |
| `/api/sessions/{task_id}` | GET | API key | Session details for a specific task |
| `/api/sessions/clear-failed` | POST | API key | Clear all failed sessions from history |
| `/api/batch/{group_id}` | GET | API key | Status of a submitted batch by group ID |
| `/api/batches` | GET | API key | List all known batches |

### Merge Queue

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/merge-queue` | GET | API key | Merge queue snapshot — pending, active, deferred, conflicts, recent history |
| `/api/merge-queue/retry/{session_id}` | POST | API key | Re-enqueue a failed or deferred merge entry |

### Config

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/config` | GET | Admin* | Current config grouped by category with field metadata |
| `/api/config/update` | POST | Admin* | Validate and apply config updates; triggers daemon reload |

### Traces

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/trace-parsed/{event_id}` | GET | API key | Structured trace with phase detection, subagent grouping, tool timelines; accepts `?since_event=N` |
| `/api/trace/{event_id}` | GET | API key | Raw JSONL trace parsed into sections |
| `/api/trace-terminal/{event_id}` | GET | API key | Terminal-renderable event list |
| `/api/prompt/{event_id}` | GET | API key | Prompt text for a task |
| `/api/report/{event_id}` | GET | API key | Report markdown for a completed task |
| `/api/self-update` | GET | API key | Self-update status — branch, last check, verdict, update history |
| `/api/logs` | GET | API key | Tail of the daemon log file |

### Flow Control

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/flow/status` | GET | None | Status of all configured flows |
| `/api/flow/start` | POST | Admin | Start flows by name |
| `/api/flow/stop` | POST | Admin | Stop flows by name |

---

## curl Example

Submit a task via the API:

```bash
curl -X POST http://localhost:8081/api/submit \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add retry logic to the HTTP client with exponential backoff"}'
```

Response:

```json
{"task_id": 1042, "status": "submitted"}
```

Submit with an API key (when `api_key` is configured):

```bash
curl -X POST http://localhost:8081/api/submit \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{"prompt": "Fix the login bug", "subject": "Auth fix"}'
```

Check merge queue:

```bash
curl http://localhost:8081/api/merge-queue | python3 -m json.tool
```

Retry a deferred merge:

```bash
curl -X POST http://localhost:8081/api/merge-queue/retry/1042
```
