---
description: Smart task delegation to Golem — evaluates complexity, delegates if warranted, handles inline if too small
argument-hint: '[--background|--wait] [--delegate-all] [task description]'
context: fork
allowed-tools: Bash(python3:*), Bash(golem:*), Read, Glob, Grep, AskUserQuestion
---

Smart task delegation to Golem's autonomous pipeline.

Raw slash-command arguments:
`$ARGUMENTS`

## Step 1: Parse arguments

- Extract `--background`, `--wait`, `--delegate-all` flags
- Remaining text is the task description
- If no task description provided, use `AskUserQuestion` once: "What task should Golem work on?"

## Step 2: Evaluate delegation heuristic

Unless `--delegate-all` is present, evaluate the task using the `delegation-heuristics` skill.

Evaluate based on the task description text alone, applying the signals from the delegation-heuristics skill.

Apply the delegation heuristic:
- If **delegate**: proceed to Step 3
- If **too small**: tell the user why and suggest handling inline. Mention `--delegate-all` as override. Stop here.
- If **uncertain**: use `AskUserQuestion` with options: `Delegate to Golem (Recommended)` / `Handle inline`

## Step 3: Shape the prompt

If `golem.md` exists in the repo root, read it to understand the project context. Use that context to enrich the task prompt — add relevant conventions, test commands, or architectural notes that will help Golem work effectively.

## Step 4: Choose execution mode

- If `--wait` was passed: foreground
- If `--background` was passed: background
- Otherwise: estimate task complexity. For multi-file changes, recommend background. For focused tasks, recommend foreground.
- Use `AskUserQuestion` with two options, recommended first:
  - `Wait for results` or `Run in background (Recommended)`

## Step 5: Delegate

Route to the `golem:golem-delegate` subagent with:
- The shaped prompt
- The execution mode flag (`--background` or `--wait`)
- Strip `--delegate-all` — it is not forwarded

If running in background:
- Tell the user: "Golem task started in the background. Check `/golem:status` for progress."

If running in foreground:
- Return the delegate agent's output, formatted per `golem-result-handling` skill.
