# Smart Pre-Push Hook

## Problem

The current pre-push hook runs black, pylint, and pytest unconditionally on every push. This wastes time when the push only modifies non-Python files (docs, configs, markdown).

## Design

### Change detection

1. Read refs from stdin — git provides `local_ref local_sha remote_ref remote_sha` per ref being pushed.
2. Compute changed files: `git diff --name-only <remote_sha>..<local_sha>`.
3. For new branches (remote SHA is all zeros), use `git merge-base origin/master HEAD` as the base.

### File categorization

Each changed file is classified into one or more categories:

- **python_source** — `golem/**/*.py` excluding `golem/tests/`
- **python_test** — `golem/tests/**/*.py`
- **toolconfig** — `pyproject.toml`, `.pylintrc`, `pyrightconfig.json`, CI workflow files
- **yaml** — `*.yaml`, `*.yml`
- **other** — everything else (`.md`, `.json`, docs, assets, etc.)

### Check matrix

| Condition | Check |
|---|---|
| Any `.py` changed OR toolconfig changed | `black --check` on all changed `.py` files |
| Any `.py` changed OR toolconfig changed | `pylint --errors-only` on changed `.py` files only |
| Source `.py` or toolconfig changed | Full `pytest` + coverage |
| Only test `.py` changed (no source) | Run changed test files + full coverage check |
| `.yaml`/`.yml` changed | `python -c "yaml.safe_load()"` syntax check |
| Only "other" files | Skip everything, push through |

### Preserved behavior

- `AGENT_WORKTREE=1` continues to skip pytest (validated by supervisor).
- Colored output with pass/fail/skip status per check.
- Exit 1 on any failure; suggest `--no-verify` to bypass.

### Output changes

Checks that don't apply print a "skipped (no relevant changes)" message in yellow instead of running.
