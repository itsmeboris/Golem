# FAQ

Frequently asked questions about Golem — both for operators running the daemon and developers extending it.

---

### How is Golem different from running Claude Code directly?

Claude Code is interactive — you drive every step, reviewing output and deciding what comes next. Golem is autonomous — submit a prompt and walk away. It manages the full pipeline on your behalf: agent execution in isolated git worktrees, deterministic verification (config-driven via .golem/verify.yaml, with a Python fallback to black/pylint/pytest), a separate validation agent that reviews the evidence, automatic retries with structured feedback on partial results, a sequential merge queue that never touches your working tree, and per-task budget caps.

Golem is built on top of the Claude Code CLI. It adds orchestration, quality gates, and operational controls that make Claude Code safe to run unattended.

---

### What does it cost to run?

Golem requires the Claude CLI with a paid Anthropic plan. Typical task costs vary by complexity:

| Task type | Typical cost |
|-----------|-------------|
| Simple bug fix | $0.50 – $1.00 |
| New feature | $1.00 – $3.00 |
| Complex refactor | $3.00 – $8.00 |

The `budget_per_task_usd` setting (default `$10`) caps spend per task — the daemon terminates the session if it would exceed the budget. For autonomous heartbeat work, `heartbeat_daily_budget_usd` caps total daily spend. See [[Configuration]] for the full budget configuration reference.

---

### Can I use it with my own issue tracker?

Yes. Golem's profile system is fully pluggable. Built-in profiles cover:

- **Local** — file-based task source, no external dependencies
- **GitHub Issues** — via the `gh` CLI
- **Redmine** — via REST API

To integrate any other tracker (Jira, Linear, Azure DevOps, etc.), implement five interfaces from `golem/interfaces.py` and register a profile factory. The daemon loads custom profiles the same way as built-in ones. See [[Backends]] for the full guide and a complete Jira example.

---

### What happens if a task fails?

Golem handles failures differently depending on the type:

- **Verification failure**: the agent retries immediately with structured feedback pointing to the specific failures. This does not consume the task's retry budget.
- **PARTIAL validation verdict**: the validation agent identified issues but the work is salvageable. Golem retries with the validator's feedback, up to `max_retries` (default `1`).
- **FAIL validation verdict**: the task is marked `FAILED` and your team is notified with the reason.
- **Budget or timeout exceeded**: the task moves to `FAILED`.

After a failure, you can provide human feedback on the task to trigger a re-attempt from the `HUMAN_REVIEW` state — the agent retries with your guidance included in its context.

See [[Task-Lifecycle]] for the full state machine diagram and transition descriptions.

---

### How do I limit spending?

Three config settings control spending:

```yaml
budget_per_task_usd: 10        # cap per task (default 10)
heartbeat_daily_budget_usd: 1  # cap for autonomous heartbeat work (default 1)
```

Task timeouts prevent runaway sessions from accumulating cost even if the budget cap hasn't been hit yet. When a task exceeds its budget, the session is terminated and the task moves to `FAILED`.

See [[Configuration]] for all budget and timeout settings.

---

### Can I review work before it merges?

Yes. Set `auto_commit: false` in your config to skip automatic commits. Validated work stays in its git worktree for manual inspection and merging.

You can also use the `HUMAN_REVIEW` state: when a task fails, post feedback on the issue (or via the HTTP API) and Golem will re-attempt with your guidance. The retry picks up where the previous attempt left off, incorporating your feedback into the agent's context.

---

### Does it work with models other than Claude?

Currently Golem is built specifically for the Claude Code CLI. Two config settings control which Claude model is used:

```yaml
task_model: sonnet       # model for building tasks
orchestrate_model: opus  # model for orchestration and review
```

Sonnet is used for code generation; Opus is used for orchestration, validation, and review phases where reasoning quality matters more than speed.

Support for other models or AI providers is not currently planned.

---

### What Python versions are supported?

Python **3.11, 3.12, and 3.13**. The CI test matrix runs against all three versions on every pull request. All three must pass with 100% coverage before a PR can merge.

---

### How do I run it in CI/CD?

Golem is designed as a long-running daemon, not a CI step. However, you can use it in CI with the one-shot command:

```bash
golem run -p "Fix the flaky test in test_flow.py"
```

`golem run` auto-starts the daemon if it isn't running, submits the task, waits for completion, and exits with the task's result code. This is suitable for CI pipelines where you have the Claude CLI configured and authenticated in your CI environment.

For parallel workloads, use the batch API (`POST /api/submit/batch`) and poll `GET /api/batch/{group_id}` for completion.

---

### How do I use Golem from inside Claude Code?

Install the plugin with `golem install-plugins`, then use `/golem:run <task>` to delegate work. The plugin evaluates task complexity and delegates to Golem when the task is large enough. Use `/golem:setup` first to bootstrap a repo with `golem.md` project context. See [[Claude Code Plugin]] for the full guide.

---

### What does `/golem:setup` generate?

It creates two files:
1. **`golem.md`** — AI-generated project context (stack, test commands, architecture, conventions). Machine-owned, gitignored.
2. **`.golem/verify.yaml`** — Structured verification config derived from the verified commands in `golem.md`. Used by Golem's verifier during the VERIFY phase.

The setup flow verifies each command actually works before writing `verify.yaml`.
