# Git Workflow Rules

## Branch Naming
- Agent work branches: `agent/{session_id}` (created by worktree_manager)
- Feature branches: `feat/{short-description}` (human-initiated)
- Fix branches: `fix/{issue-id}-{short-description}`
- Never commit directly to `master` from agent sessions

## Commit Messages
- Imperative mood: "fix X", "add Y", not "fixed X" or "adds Y"
- First line: type + concise description under 72 chars
- Types: `feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `security:`
- Body (optional): explain *why*, not *what* (the diff shows what)
- Reference issue: `Closes ITEM-ID (GH #N)` at the end

```
fix: wire sandbox config through to preexec_fn

GolemFlowConfig sandbox settings were dead code. Added _sandbox_preexec
helper and threaded config through all CLIConfig construction sites.

Closes SEC-007b (GH #133)
```

## Merge Strategy
- **Fast-forward only** (`--ff-only`) for merging agent branches to master
- Agent branches are rebased onto master before merge attempt
- No merge commits in master history
- If rebase conflicts arise, the merge agent gets a second chance

## Fixup Conventions
- Use `fixup!` prefix for commits that should be squashed later
- The merge queue handles squashing automatically during fast-forward
- Never rewrite history on master after push

## Pre-push Checks
The `.githooks/pre-push` hook runs:
1. `black --check` on changed Python files
2. `pylint --errors-only` on changed Python files
3. `pylint --enable=W0611,W0612,W0101` dead-code check
4. State management audit (JS, non-blocking)
5. Contract linting (non-blocking)
6. `pytest` with 100% coverage requirement
7. YAML syntax check on changed YAML files

All blocking checks must pass before push is allowed.

## Worktree Isolation
- Each agent session works in its own git worktree under `/tmp/`
- Worktrees are cleaned up after successful merge or on crash recovery
- `AGENT_WORKTREE=1` env var skips pre-push pytest in agent worktrees
