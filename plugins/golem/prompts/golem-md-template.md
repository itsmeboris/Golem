# golem.md Generation Template

You are generating a `golem.md` file for a software project. This file tells Golem everything it needs to know to work autonomously on this repo.

## Instructions

1. Explore the repo using your tools (Read, Glob, Grep) to understand:
   - What language(s) and framework(s) are used
   - How tests are run
   - How linting/formatting works
   - How to build the project
   - How to start dev servers
   - Key architectural patterns
   - Coding conventions

2. **Find ALL verification commands.** Check these sources — do not rely on just one:
   - `Makefile` — look for `test`, `lint`, `check`, `verify` targets
   - `package.json` scripts — `test`, `lint`, `typecheck`, `format`
   - CI config — `.github/workflows/*.yml`, `.gitlab-ci.yml`, `Jenkinsfile`
   - Git hooks — `.githooks/`, `.husky/`, `.pre-commit-config.yaml`
   - `pyproject.toml` — `[tool.pytest]`, `[tool.black]`, `[tool.ruff]`, `[tool.mypy]`
   - `CLAUDE.md` or `AGENTS.md` — may document required checks
   - `tox.ini`, `noxfile.py` — test runners with multiple environments
   
   Every check that runs in CI or pre-push should appear in the verify section.
   Missing a check means Golem could commit code that fails CI.

3. Generate `golem.md` following this EXACT structure:

~~~
# Project: <repo-name>

## Stack
- Language: <primary language and version>
- Framework: <main framework(s)>
- Package manager: <tool> (<config file>)
- CI: <CI system if detected>

## Commands

### verify
Commands that validate code correctness. Safe to run, idempotent, no side effects.
Each entry MUST use this exact format (one per line):
- **role:** `test` | **cmd:** `["executable", "arg1", "arg2"]` | **timeout:** <seconds>
- **role:** `lint` | **cmd:** `["executable", "arg1"]` | **timeout:** <seconds>
- **role:** `format` | **cmd:** `["executable", "--check", "arg1"]` | **timeout:** <seconds>

Valid roles: test, lint, format, typecheck
Cmd MUST be a JSON array of strings (tokenized argv, not a shell string).

### build
Commands that produce artifacts. Informational only.
- **<label>:** `<shell command>`

### serve
Long-running processes. Informational only.
- **<label>:** `<shell command>`

## Architecture
<2-5 sentences about key modules and their roles>

## Conventions
<Bullet list of coding style rules, commit conventions, branch strategy>

## Notes
<Anything unusual the AI noticed that Golem should know>
~~~

4. IMPORTANT:
   - The `verify` section commands will be tested during setup. Only include commands that actually work.
   - Use `format` role for format-check commands (e.g., `black --check`, `ruff format --check`)
   - Use `lint` role for linting (e.g., `pylint`, `ruff check`, `eslint`, `pyflakes`, `vulture`)
   - Use `test` role for test runners (e.g., `pytest`, `jest`, `go test`)
   - Use `typecheck` role for type checking (e.g., `mypy`, `tsc --noEmit`)
   - Set realistic timeouts (30s for lint/format, 300s for tests). Test suites often take 1-2 minutes — use 300s to avoid flaky timeouts under load
   - If a Makefile `lint` target runs 5 tools, list ALL 5 as separate verify entries — not just one
   - If a pre-push hook runs checks, include those too
