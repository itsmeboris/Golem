---
description: Bootstrap Golem for the current repo — start daemon, attach, generate golem.md with verified commands
argument-hint: '[--regenerate] [--update] [--skip-verify]'
allowed-tools: Bash(python3:*), Bash(golem:*), Read, Glob, Grep, Write, AskUserQuestion
---

Bootstrap Golem for the current repository.

Raw slash-command arguments:
`$ARGUMENTS`

## Step 1: Run companion setup

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/golem-companion.py" setup --json
```

If the result says golem is not installed:
- Use `AskUserQuestion` once to ask whether to install: "Golem CLI not found. Install with `pip install golem-agent`?"
- Options: `Install Golem (Recommended)` / `Skip for now`
- If install chosen, run `pip install golem-agent` then re-run setup.

If setup succeeds, proceed to Step 2.

## Step 2: Generate golem.md

Read the prompt template:
```bash
cat "${CLAUDE_PLUGIN_ROOT}/prompts/golem-md-template.md"
```

Follow the template instructions:
1. Use your Read, Glob, Grep tools to explore the repo
2. Identify the stack, test/lint/format commands, architecture, and conventions
3. Generate `golem.md` at the repo root following the exact template structure
4. Write it using the Write tool

If `--update` was passed, read the existing `golem.md` first and use it as context for regeneration.
If `--regenerate` was passed, ignore any existing `golem.md`.

## Step 3: Verify commands

Unless `--skip-verify` was passed, run the companion's internal verification:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/golem-companion.py" setup --verify --json
```

This runs all `verify` commands from golem.md internally with proper timeouts. It auto-detects python/python3 and fixes the executable path. Only failures are reported with error output.

If all commands pass: proceed to Step 4.

If any command fails:
1. Read the failure details from the JSON output (`failed` array with `stdout`, `stderr`, `error`)
2. Revise the failing command in golem.md (fix the command, adjust timeout, or remove it)
3. Re-run `setup --verify --json` (up to 2 retries total)
4. If a command still fails after retries, remove it from golem.md and warn the user

Do NOT run verify commands yourself via Bash — always use the companion's `--verify` flag.

## Step 4: Finalize

If verification passed:
```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/golem-companion.py" setup --finalize --json
```

This derives `.golem/verify.yaml` from golem.md and adds `golem.md` to `.gitignore`.

If `--skip-verify` was passed:
- Do NOT run the finalize step — no verify.yaml generation from unverified commands
- Tell the user: "golem.md written but verify.yaml was not generated. Run `/golem:setup` without `--skip-verify` to create a verified config."
- Stop here.

## Step 5: Report

Tell the user what was set up:
- Daemon status
- Repo attachment status
- golem.md location and what it contains
- verify.yaml commands (if generated)
- Any commands that failed verification
