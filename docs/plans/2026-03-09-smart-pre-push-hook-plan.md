# Smart Pre-Push Hook Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the pre-push hook skip irrelevant checks based on which files actually changed, so pushing docs-only or config-only changes is instant.

**Architecture:** Rewrite `.githooks/pre-push` to: (1) read pushed refs from stdin, (2) compute changed files via `git diff --name-only`, (3) classify files into categories using regex matching, (4) run only the checks relevant to those categories.

**Tech Stack:** Bash (same as current hook), Python one-liners for YAML validation.

---

### Task 1: Rewrite pre-push hook with smart change detection

**Files:**
- Modify: `.githooks/pre-push` (full rewrite)

**Step 1: Write the new hook**

Replace `.githooks/pre-push` entirely with:

```bash
#!/bin/bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BOLD='\033[1m'
NC='\033[0m'

# ── Collect changed files from all refs being pushed ────────────────
ZERO="0000000000000000000000000000000000000000"
changed_files=""

while read -r local_ref local_sha remote_ref remote_sha; do
    if [[ "$local_sha" == "$ZERO" ]]; then
        continue  # deleting a branch — nothing to check
    fi
    if [[ "$remote_sha" == "$ZERO" ]]; then
        # New branch — compare against merge-base with master
        base=$(git merge-base origin/master "$local_sha" 2>/dev/null || echo "origin/master")
    else
        base="$remote_sha"
    fi
    changed_files+=$'\n'"$(git diff --name-only "$base".."$local_sha" 2>/dev/null || true)"
done

# Deduplicate and remove blanks
changed_files=$(echo "$changed_files" | sort -u | sed '/^$/d')

if [[ -z "$changed_files" ]]; then
    printf "${GREEN}${BOLD}No changed files detected. Push allowed.${NC}\n"
    exit 0
fi

# ── Classify changed files ──────────────────────────────────────────
py_source=()
py_test=()
py_all=()
yaml_files=()
has_toolconfig=false

while IFS= read -r f; do
    if [[ "$f" =~ \.py$ ]]; then
        py_all+=("$f")
        if [[ "$f" =~ ^golem/tests/ ]]; then
            py_test+=("$f")
        elif [[ "$f" =~ ^golem/ ]]; then
            py_source+=("$f")
        fi
    elif [[ "$f" == pyproject.toml || "$f" == .pylintrc || "$f" == pyrightconfig.json \
         || "$f" =~ ^\.github/workflows/ ]]; then
        has_toolconfig=true
    elif [[ "$f" =~ \.(yaml|yml)$ ]]; then
        yaml_files+=("$f")
    fi
done <<< "$changed_files"

# ── Determine which checks to run ──────────────────────────────────
run_black=false
run_pylint=false
run_pytest=false
run_pytest_full=false
run_yaml=false

if [[ ${#py_all[@]} -gt 0 ]] || [[ "$has_toolconfig" == true ]]; then
    run_black=true
    run_pylint=true
fi

if [[ ${#py_source[@]} -gt 0 ]] || [[ "$has_toolconfig" == true ]]; then
    run_pytest=true
    run_pytest_full=true
elif [[ ${#py_test[@]} -gt 0 ]]; then
    run_pytest=true
    run_pytest_full=false
fi

if [[ ${#yaml_files[@]} -gt 0 ]]; then
    run_yaml=true
fi

# ── Print change summary ────────────────────────────────────────────
total=$(echo "$changed_files" | wc -l | tr -d ' ')
summary=()
[[ ${#py_source[@]} -gt 0 ]] && summary+=("${#py_source[@]} source .py")
[[ ${#py_test[@]} -gt 0 ]] && summary+=("${#py_test[@]} test .py")
[[ "$has_toolconfig" == true ]] && summary+=("toolconfig")
[[ ${#yaml_files[@]} -gt 0 ]] && summary+=("${#yaml_files[@]} yaml")
if [[ ${#summary[@]} -eq 0 ]]; then
    summary+=("docs/other only")
fi
printf "${BOLD}Changed files: ${total} (%s)${NC}\n\n" "$(IFS=', '; echo "${summary[*]}")"

# ── Run checks ──────────────────────────────────────────────────────
failed=0

# --- black ---
printf "${BOLD}=== pre-push: black ===${NC}\n"
if [[ "$run_black" == true ]]; then
    if ! python -m black --check "${py_all[@]}" --quiet 2>/dev/null; then
        printf "${RED}black: formatting issues found. Run 'python -m black golem/' to fix.${NC}\n"
        failed=1
    else
        printf "${GREEN}black: ok${NC}\n"
    fi
else
    printf "${YELLOW}black: skipped (no Python changes)${NC}\n"
fi

# --- pylint ---
printf "${BOLD}=== pre-push: pylint ===${NC}\n"
if [[ "$run_pylint" == true ]]; then
    if ! python -m pylint --errors-only "${py_all[@]}" 2>/dev/null; then
        printf "${RED}pylint: errors found${NC}\n"
        failed=1
    else
        printf "${GREEN}pylint: ok${NC}\n"
    fi
else
    printf "${YELLOW}pylint: skipped (no Python changes)${NC}\n"
fi

# --- pytest ---
printf "${BOLD}=== pre-push: pytest + coverage ===${NC}\n"
if [[ "${AGENT_WORKTREE:-}" == "1" ]]; then
    printf "${YELLOW}pytest: skipped (agent worktree — validated by supervisor)${NC}\n"
elif [[ "$run_pytest" == true ]]; then
    if [[ "$run_pytest_full" == true ]]; then
        if ! python -m pytest golem/tests/ -m "" -x -q --tb=line \
                --cov=golem --cov-report=term-missing:skip-covered --cov-fail-under=100 2>/dev/null; then
            printf "${RED}pytest: test failures or coverage below 100%%${NC}\n"
            failed=1
        else
            printf "${GREEN}pytest + coverage: ok${NC}\n"
        fi
    else
        if ! python -m pytest "${py_test[@]}" -m "" -x -q --tb=line \
                --cov=golem --cov-report=term-missing:skip-covered --cov-fail-under=100 2>/dev/null; then
            printf "${RED}pytest: test failures or coverage below 100%%${NC}\n"
            failed=1
        else
            printf "${GREEN}pytest (changed tests) + coverage: ok${NC}\n"
        fi
    fi
else
    printf "${YELLOW}pytest: skipped (no Python changes)${NC}\n"
fi

# --- yaml ---
if [[ "$run_yaml" == true ]]; then
    printf "${BOLD}=== pre-push: yaml syntax ===${NC}\n"
    yaml_ok=true
    for yf in "${yaml_files[@]}"; do
        if [[ -f "$yf" ]] && ! python -c "import yaml, sys; yaml.safe_load(open(sys.argv[1]))" "$yf" 2>/dev/null; then
            printf "${RED}yaml: syntax error in ${yf}${NC}\n"
            yaml_ok=false
            failed=1
        fi
    done
    if [[ "$yaml_ok" == true ]]; then
        printf "${GREEN}yaml: ok${NC}\n"
    fi
fi

# ── Result ──────────────────────────────────────────────────────────
if [[ $failed -ne 0 ]]; then
    printf "\n${RED}${BOLD}Pre-push checks failed. Push aborted.${NC}\n"
    printf "Use ${YELLOW}git push --no-verify${NC} to bypass (not recommended).\n"
    exit 1
fi

printf "\n${GREEN}${BOLD}All pre-push checks passed.${NC}\n"
```

**Step 2: Verify syntax**

Run: `bash -n .githooks/pre-push`
Expected: no output (valid syntax)

**Step 3: Smoke-test with a real diff**

Pick two recent commits and simulate a push:
```bash
echo "refs/heads/master $(git rev-parse HEAD) refs/heads/master $(git rev-parse HEAD~1)" | bash .githooks/pre-push
```

Verify the summary line and check selection make sense for what changed between those commits.

**Step 4: Commit**

```bash
git add .githooks/pre-push
git commit -m "feat(hooks): smart pre-push — skip checks based on changed file types

Classifies changed files into source .py, test .py, toolconfig, yaml,
and other. Only runs black/pylint/pytest/yaml-check when the relevant
category has changes. Docs-only pushes skip all checks."
```
