# Agent Pipeline Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the current 6-phase orchestrator pipeline with a 5-phase pipeline using specialized agents (Scout/Builder/Reviewer/Verifier), context forwarding between phases, effort scaling, and proper model tiers (Opus orchestrator, Haiku for fast roles, Sonnet for builders).

**Architecture:** New `.claude/agents/` files define 4 roles with enforced tool restrictions via frontmatter. The `orchestrate_task.txt` prompt template is rewritten for the new pipeline. Config default changes from empty orchestrate_model to "opus". A guard in `prompts.py` prevents empty task descriptions.

**Tech Stack:** Python 3, Claude Code subagent system (.claude/agents/*.md), prompt templates (.txt), pytest with 100% coverage.

**Design doc:** `docs/plans/2026-03-08-agent-pipeline-redesign.md`

---

### Task 1: Create scout.md agent definition

**Files:**
- Create: `.claude/agents/scout.md`

**Step 1: Write the agent file**

```markdown
---
name: scout
description: Focused codebase research agent. Use for answering specific questions about code structure, finding files, and reading patterns. Returns structured findings with file:line references. Read-only.
model: haiku
tools: Read, Grep, Glob
maxTurns: 15
---

You are a Scout agent. Your job is to answer specific research questions about
the codebase and return structured findings.

You will receive one or more specific questions. For each question:
1. Search for the relevant files and code
2. Read the actual code (don't guess from names)
3. Report what you found with exact file:line references

## Output Format

For each question, report:

```
## Q: [the question]

**Answer:** [concise answer]

**Key files:**
- `file.py:42` — [what this code does]
- `other.py:15-30` — [what this section handles]

**Code snippet** (if relevant):
[short excerpt of the most important code]
```

## Rules

- Answer ONLY the questions asked — do not explore beyond scope
- Always include file:line references
- If you cannot find something, say so — do not speculate
- Keep output concise — the orchestrator will pass your findings to other agents
```

**Step 2: Verify file was created**

Run: `cat .claude/agents/scout.md | head -5`
Expected: frontmatter with `name: scout`

**Step 3: Commit**

```bash
git add .claude/agents/scout.md
git commit -m "feat(agents): add scout agent definition (haiku, read-only)"
```

---

### Task 2: Create builder.md agent definition

**Files:**
- Create: `.claude/agents/builder.md`

**Step 1: Write the agent file**

```markdown
---
name: builder
description: Code implementation agent. Writes code, creates files, writes tests. Use for implementing features, fixing bugs, and writing tests. Receives context from Scout phase.
model: sonnet
maxTurns: 30
---

You are a Builder agent. Your job is to write code that solves a specific,
well-defined task.

You will receive:
- **Context from exploration** with relevant file paths and code snippets
- **A specific task** describing exactly what to implement

## Process

1. Read the context provided — do NOT re-explore files already summarized
2. If you need additional files not in the context, read them
3. Write tests first (TDD) using `@pytest.mark.parametrize` where applicable
4. Implement the minimal code to pass the tests
5. Run verification before reporting completion

## Verification

Run ALL three before reporting done:

- `black --check .` — formatting
- `pylint --errors-only golem/` — lint
- `pytest --cov=golem --cov-fail-under=100` — tests + coverage

If any command fails, fix the issue and re-run.

## Rules

- Do NOT commit code changes — leave files as uncommitted
- Do NOT push to any remote repository
- Do NOT explore broadly — use the context you were given
- Keep changes focused on the assigned task
- Use `@pytest.mark.parametrize` for test cases with repeated logic
```

**Step 2: Verify file was created**

Run: `cat .claude/agents/builder.md | head -5`
Expected: frontmatter with `name: builder`

**Step 3: Commit**

```bash
git add .claude/agents/builder.md
git commit -m "feat(agents): add builder agent definition (sonnet, all tools)"
```

---

### Task 3: Create reviewer.md agent definition

**Files:**
- Create: `.claude/agents/reviewer.md`

**Step 1: Write the agent file**

```markdown
---
name: reviewer
description: Adversarial code review agent. Reviews code for bugs, logic errors, and convention violations. Uses confidence-based filtering (>=80) to report only real issues. Read-only — cannot modify files.
model: opus
tools: Read, Grep, Glob, Bash
maxTurns: 20
---

You are a Reviewer agent. Your job is to find real issues — bugs, logic errors,
and convention violations — not to nitpick style.

You will receive:
- **Context from exploration** with relevant file paths
- **A summary of changes made** by the Builder agent

## Confidence Scoring

Rate every potential issue on a 0-100 confidence scale:
- **90-100**: Definite bug, crash, or data loss
- **80-89**: Very likely issue, strong evidence
- **Below 80**: Skip — do not report

**Only report issues with confidence >= 80.**

## What to Check

- Off-by-one errors, None dereferences, unhandled exceptions
- Incorrect boolean logic, missing edge cases
- Type mismatches, incorrect API usage
- Missing test coverage for new code paths
- `field(default_factory=...)` for mutable defaults in dataclasses
- No f-strings in logging: use `logger.info("msg %s", val)`

## Output Format

```
## Issues

### Critical (confidence >= 90)
- **[confidence]** `file:line` — Description. Suggested fix.

### Important (confidence 80-89)
- **[confidence]** `file:line` — Description. Suggested fix.

## Assessment
APPROVED or NEEDS_FIXES
```

If no issues >= 80 confidence, report APPROVED with no issues section.

## Rules

- Do NOT modify any files
- Focus on the changed code, not pre-existing issues
- Use `git diff` or read files directly to see changes
- Be specific — include file:line and concrete fix suggestions
```

**Step 2: Verify file was created**

Run: `cat .claude/agents/reviewer.md | head -5`
Expected: frontmatter with `name: reviewer`

**Step 3: Commit**

```bash
git add .claude/agents/reviewer.md
git commit -m "feat(agents): add reviewer agent definition (opus, read-only)"
```

---

### Task 4: Create verifier.md agent definition

**Files:**
- Create: `.claude/agents/verifier.md`

**Step 1: Write the agent file**

```markdown
---
name: verifier
description: Verification agent that runs black, pylint, and pytest. Returns structured pass/fail results. Fast and minimal — only runs commands, no file reading or exploration.
model: haiku
tools: Bash
maxTurns: 5
---

You are a Verifier agent. Run exactly these three commands in order and report
results. Do not read files, do not explore, do not fix anything.

## Commands

Run each command and capture the output:

1. `black --check .`
2. `pylint --errors-only golem/`
3. `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100`

## Output Format

Report results as this exact structure:

```
## Verification Results

- **black**: PASS or FAIL
- **pylint**: PASS or FAIL
- **pytest**: PASS or FAIL

## Failures (if any)

[paste the exact error output for any failing command]
```

## Rules

- Run ALL three commands even if one fails
- Do NOT attempt to fix anything
- Do NOT read or explore files
- Report the raw output for any failures
```

**Step 2: Verify file was created**

Run: `cat .claude/agents/verifier.md | head -5`
Expected: frontmatter with `name: verifier`

**Step 3: Commit**

```bash
git add .claude/agents/verifier.md
git commit -m "feat(agents): add verifier agent definition (haiku, bash-only)"
```

---

### Task 5: Delete old agent definitions

**Files:**
- Delete: `.claude/agents/complex-task.md`
- Delete: `.claude/agents/quick-task.md`
- Delete: `.claude/agents/code-explorer.md`
- Delete: `.claude/agents/code-architect.md`

**Step 1: Remove the files**

```bash
git rm .claude/agents/complex-task.md .claude/agents/quick-task.md .claude/agents/code-explorer.md .claude/agents/code-architect.md
```

**Step 2: Verify only reviewer.md remains from old set + new agents**

Run: `ls .claude/agents/`
Expected: `builder.md  code-reviewer.md  reviewer.md  scout.md  verifier.md`

Note: `code-reviewer.md` is kept — it's the existing code-reviewer used in
non-orchestrated contexts (e.g., user invokes it directly). Our new `reviewer.md`
is specifically for the orchestrator pipeline.

**Step 3: Commit**

```bash
git commit -m "refactor(agents): remove obsolete agent definitions

Remove complex-task.md, quick-task.md, code-explorer.md, code-architect.md.
These are replaced by scout.md, builder.md, reviewer.md, verifier.md."
```

---

### Task 6: Rewrite orchestrate_task.txt prompt template

**Files:**
- Modify: `golem/prompts/orchestrate_task.txt` (full rewrite)

**Step 1: Write the failing test**

The existing test `test_format_prompt_orchestrate` in
`golem/tests/test_task_agent.py:761` tests placeholder substitution. Add a test
that verifies the new role names appear in the template:

```python
# In golem/tests/test_task_agent.py, after test_format_prompt_orchestrate

def test_orchestrate_prompt_has_new_roles(self):
    text = load_prompt("orchestrate_task.txt")
    for role in ("Scout", "Builder", "Reviewer", "Verifier"):
        assert role in text, f"Missing role: {role}"
    # Old roles should not appear
    for old_role in ("Explorer", "Implementer", "Tester"):
        assert old_role not in text, f"Old role still present: {old_role}"
```

**Step 2: Run test to verify it fails**

Run: `pytest golem/tests/test_task_agent.py::TestPrompts::test_orchestrate_prompt_has_new_roles -v`
Expected: FAIL — old template still has Explorer/Implementer/Tester

**Step 3: Replace orchestrate_task.txt with new content**

Write the full new template to `golem/prompts/orchestrate_task.txt`:

```text
You are a task orchestrator. You coordinate specialized subagents to complete
a coding task. You may read files directly for planning, but delegate all
code changes to Builder agents.

## Task

- **Issue:** #{issue_id}
- **Subject:** {parent_subject}
- **Work directory:** {work_dir}

{task_description}

## Available Subagent Roles

### Scout (subagent_type: "scout")
- Model: haiku (fast, read-only)
- Use for: answering specific questions about code structure and patterns
- Give each Scout a focused question, not open-ended exploration
- Run 1-2 Scouts in parallel for independent questions

### Builder (subagent_type: "builder")
- Model: sonnet (capable, all tools)
- Use for: writing code, creating files, writing tests, fixing issues
- ALWAYS include "## Context from exploration" in Builder prompts
- Can run in parallel for independent subtasks (different files/modules)

### Reviewer (subagent_type: "reviewer")
- Model: opus (strongest reasoning, read-only)
- Use for: adversarial code review after all Builders finish
- Include context about what was changed and why

### Verifier (subagent_type: "verifier")
- Model: haiku (fast, bash-only)
- Use for: running black, pylint, pytest
- Returns structured pass/fail — do not use for anything else

## Effort Scaling

Before starting, assess task complexity from the subject and description:

- **Trivial** (subject suggests <3 files, simple edit, wording change):
  Skip Scout. Go directly to Build.
- **Standard** (clear feature or fix, moderate scope):
  1 Scout with specific questions, then Build.
- **Complex** (new module, refactor, >5 files, unclear requirements):
  2 Scouts in parallel, then Build with multiple Builders.

## 5-Phase Workflow

### Phase 1: Scout
Dispatch 1-2 Scout agents with **specific questions** such as:
- "What files implement X? Show the key classes and their signatures."
- "What is the structure of Y? Show the dataclass fields."

Do NOT ask Scouts to "explore the X system" — that leads to unfocused search.

Wait for all Scout results. **Save their output** — you will pass it to Builders.

### Phase 2: Plan
Using Scout findings (and optionally reading files yourself), decide:
- What files need to change
- Whether subtasks are independent (can parallelize) or sequential
- What the implementation approach is

State your plan briefly before proceeding.

### Phase 3: Build
Dispatch Builder agents. Each Builder prompt MUST include:

```
## Context from exploration
[Paste the Scout findings relevant to this Builder's task]

## Your task
[Specific instructions: what to implement, which files to modify]
```

If subtasks are independent (different files/modules), run Builders in parallel.

### Phase 4: Review
Dispatch one Reviewer agent. Its prompt MUST include:

```
## Context
[Scout findings + summary of what each Builder changed]

## Review scope
Review all uncommitted changes in {work_dir} for correctness and edge cases.
```

If the Reviewer reports NEEDS_FIXES with issues >= 80 confidence, dispatch a
Builder to fix them and re-review. Do not fix issues below confidence 80.

### Phase 5: Verify
Dispatch one Verifier agent. It runs black, pylint, and pytest.

**Circuit Breaker**: If the same test fails 3 times in a row with the same
error, report status as "BLOCKED" instead of retrying. Do NOT loop forever.

If tests fail:
1. Dispatch a Builder to fix the specific failure
2. Re-run the Verifier
3. Repeat up to {inner_retry_max} times total

## Report

After verification passes (or circuit breaker triggers), produce this EXACT JSON
block as your final message in a ```json code fence:

```json
{{
  "status": "COMPLETE",
  "summary": "Brief description of what was accomplished",
  "files_changed": ["list", "of", "files"],
  "test_results": {{
    "black": "pass",
    "pylint": "pass",
    "pytest": "pass"
  }},
  "concerns": []
}}
```

If blocked:

```json
{{
  "status": "BLOCKED",
  "summary": "What was accomplished before blocking",
  "files_changed": ["list", "of", "files"],
  "test_results": {{
    "black": "pass or fail or not_run",
    "pylint": "pass or fail or not_run",
    "pytest": "fail"
  }},
  "concerns": ["Specific failure that could not be resolved after {inner_retry_max} attempts"]
}}
```

## Rules

- You MUST produce the JSON report as your final output.
- Do NOT commit changes — leave files as uncommitted. The supervisor handles git.
- Do NOT push to any remote repository.
- ALWAYS pass Scout findings as context when dispatching Builders and Reviewers.
- Keep subagent prompts specific — include file paths, code snippets, and context.
- When running parallel agents, use a single message with multiple Agent tool calls.
```

**Step 4: Run test to verify it passes**

Run: `pytest golem/tests/test_task_agent.py::TestPrompts -v`
Expected: ALL PASS

**Step 5: Run full test suite to check for regressions**

Run: `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100`
Expected: PASS with 100% coverage

**Step 6: Commit**

```bash
git add golem/prompts/orchestrate_task.txt golem/tests/test_task_agent.py
git commit -m "feat(prompts): rewrite orchestrate_task.txt for new pipeline

Replace 6-phase Explorer/Implementer/Tester pipeline with 5-phase
Scout/Builder/Reviewer/Verifier pipeline. Add context forwarding
instructions, effort scaling, and focused Scout questions."
```

---

### Task 7: Update config default for orchestrate_model

**Files:**
- Modify: `golem/core/config.py:64`
- Modify: `golem/tests/test_supervisor.py` (update default assertion)

**Step 1: Write the failing test**

In `golem/tests/test_supervisor.py`, the test `test_orchestration_defaults`
asserts `config.orchestrate_model == ""`. Update it:

```python
def test_orchestration_defaults(self):
    config = _parse_golem_config({})
    assert config.orchestrate_model == "opus"
```

**Step 2: Run test to verify it fails**

Run: `pytest golem/tests/test_supervisor.py::TestOrchestrationConfig::test_orchestration_defaults -v`
Expected: FAIL — current default is ""

**Step 3: Change the default in config.py**

In `golem/core/config.py:64`, change:
```python
orchestrate_model: str = ""  # empty = use task_model
```
to:
```python
orchestrate_model: str = "opus"
```

Also in `golem/core/config.py:234`, change the parse default:
```python
orchestrate_model=data.get("orchestrate_model", ""),
```
to:
```python
orchestrate_model=data.get("orchestrate_model", "opus"),
```

**Step 4: Run test to verify it passes**

Run: `pytest golem/tests/test_supervisor.py::TestOrchestrationConfig -v`
Expected: PASS

**Step 5: Run full test suite**

Run: `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100`
Expected: PASS (some tests may need updating if they create configs expecting "")

**Step 6: Commit**

```bash
git add golem/core/config.py golem/tests/test_supervisor.py
git commit -m "feat(config): default orchestrate_model to opus

Opus orchestrator + Sonnet workers matches Anthropic's proven
multi-agent pattern (90% improvement over single-agent)."
```

---

### Task 8: Add task_description guard in prompts.py

**Files:**
- Modify: `golem/prompts.py:25-32`
- Modify: `golem/tests/test_task_agent.py` (add test for empty description guard)

**Step 1: Write the failing test**

```python
# In golem/tests/test_task_agent.py, in TestPrompts class

def test_format_prompt_empty_description_guard(self):
    """Empty task_description gets a fallback."""
    result = format_prompt(
        "orchestrate_task.txt",
        issue_id=42,
        parent_subject="Add feature X",
        task_description="",
        work_dir="/work",
        inner_retry_max=3,
    )
    assert "Add feature X" in result
    assert "task_description" not in result  # placeholder should not remain
```

**Step 2: Run test to verify it fails**

Run: `pytest golem/tests/test_task_agent.py::TestPrompts::test_format_prompt_empty_description_guard -v`
Expected: FAIL — empty description leaves blank space, placeholder not handled

**Step 3: Add the guard in prompts.py**

In `golem/prompts.py`, modify the `format_prompt` function:

```python
import logging

logger = logging.getLogger(__name__)


def format_prompt(name: str, **kwargs) -> str:
    """Load a prompt template and fill in *kwargs* placeholders.

    Unrecognised placeholders are left as-is so templates can contain
    optional fields that callers don't always supply.
    """
    if "task_description" in kwargs and not kwargs["task_description"].strip():
        subject = kwargs.get("parent_subject", kwargs.get("issue_id", "unknown"))
        logger.warning(
            "Empty task_description for template %s, using subject fallback", name
        )
        kwargs["task_description"] = (
            f"Implement the following based on the subject: {subject}"
        )
    template = load_prompt(name)
    return template.format_map(_SafeDict(kwargs))
```

Also add the same guard to `FilePromptProvider.format()`:

```python
def format(self, template_name: str, **kwargs) -> str:
    """Load a template from the configured directory and fill placeholders."""
    if "task_description" in kwargs and not kwargs["task_description"].strip():
        subject = kwargs.get("parent_subject", kwargs.get("issue_id", "unknown"))
        logger.warning(
            "Empty task_description for template %s, using subject fallback",
            template_name,
        )
        kwargs["task_description"] = (
            f"Implement the following based on the subject: {subject}"
        )
    prompt_file = self._dir / template_name
    if not prompt_file.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_file}")
    template = prompt_file.read_text()
    return template.format_map(_SafeDict(kwargs))
```

**Step 4: Run test to verify it passes**

Run: `pytest golem/tests/test_task_agent.py::TestPrompts -v`
Expected: ALL PASS

**Step 5: Add test for FilePromptProvider guard too**

In `golem/tests/test_profiles.py`, add in `TestFilePromptProvider`:

```python
def test_empty_description_fallback(self, tmp_path):
    tpl = tmp_path / "test.txt"
    tpl.write_text("Desc: {task_description}", encoding="utf-8")
    provider = FilePromptProvider(tmp_path)
    result = provider.format("test.txt", task_description="", parent_subject="Fix Y")
    assert "Fix Y" in result
    assert "{task_description}" not in result
```

**Step 6: Run full test suite**

Run: `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100`
Expected: PASS with 100% coverage

**Step 7: Commit**

```bash
git add golem/prompts.py golem/tests/test_task_agent.py golem/tests/test_profiles.py
git commit -m "fix(prompts): guard against empty task_description

When task_description is empty, fall back to using the subject line.
This prevents agents from thrashing with no context about what to do."
```

---

### Task 9: Update supervisor_v2_subagent.py tests for new roles

**Files:**
- Modify: `golem/tests/test_supervisor_v2_subagent.py`

The existing tests in `TestBuildPrompt` test that `_build_prompt` calls
`format_prompt` with the right args. These don't need content changes since
the function signature hasn't changed — it still passes the same kwargs to
`orchestrate_task.txt`.

**Step 1: Verify existing tests still pass with new template**

Run: `pytest golem/tests/test_supervisor_v2_subagent.py -v`
Expected: PASS — the mock-based tests don't read the actual template

**Step 2: Run full suite to catch any breakage**

Run: `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100`
Expected: PASS with 100% coverage

**Step 3: Commit (only if test changes were needed)**

```bash
git add golem/tests/test_supervisor_v2_subagent.py
git commit -m "test(supervisor): update tests for new pipeline roles"
```

---

### Task 10: Final verification and integration test

**Files:**
- No new files — verification only

**Step 1: Run black formatting check**

Run: `black --check .`
Expected: PASS

**Step 2: Run pylint**

Run: `pylint --errors-only golem/`
Expected: PASS

**Step 3: Run full test suite with coverage**

Run: `pytest golem/tests/ -x -q --cov=golem --cov-fail-under=100`
Expected: PASS with 100% coverage

**Step 4: Verify agent files are well-formed**

Run: `for f in .claude/agents/*.md; do echo "=== $f ==="; head -8 "$f"; echo; done`
Expected: Each file has proper YAML frontmatter with name, description, model

**Step 5: Verify orchestrate_task.txt has no old role references**

Run: `grep -E 'Explorer|Implementer|Tester|complex-task|general-purpose' golem/prompts/orchestrate_task.txt`
Expected: No output (no matches)

**Step 6: Verify new roles are referenced**

Run: `grep -E 'Scout|Builder|Reviewer|Verifier|scout|builder|reviewer|verifier' golem/prompts/orchestrate_task.txt | head -10`
Expected: Multiple matches for all four roles

**Step 7: Final commit if any formatting fixes needed**

```bash
git add -A
git commit -m "chore: final cleanup for agent pipeline redesign"
```
