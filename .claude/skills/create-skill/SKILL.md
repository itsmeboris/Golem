---
name: create-skill
description: Create new Agent Skills following the agentskills.io specification and Anthropic best practices. Use when asked to create a new skill, add a skill, or make a new rule/cookbook. Generates proper directory structure, SKILL.md with valid frontmatter, and validates against the specification.
---

# Create New Agent Skill

Guide for creating skills that conform to the [Agent Skills specification](https://agentskills.io/specification) and the [Anthropic skill-building guide](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf).

---

## Core Principles

### Conciseness

The context window is a shared resource. **Default assumption: Claude is already very smart.** Only include information Claude doesn't already have. Challenge each paragraph: "Does this justify its token cost?"

Prefer concise examples over verbose explanations.

### Degrees of Freedom

Match specificity to the task's fragility:

| Freedom Level | When | Example |
|---------------|------|---------|
| **High** (text instructions) | Multiple valid approaches, context-dependent | "Choose appropriate data structure" |
| **Medium** (pseudocode/parameterized scripts) | Preferred pattern exists, some variation OK | "Use RegWrapper with path from Deview" |
| **Low** (exact scripts, few parameters) | Fragile operations, consistency critical | "Run exact compile command sequence" |

### Progressive Disclosure

Three loading levels minimize token usage:

1. **Metadata** (~100 tokens) — `name` + `description`, always in context
2. **SKILL.md body** (<5000 tokens) — loaded when skill triggers
3. **Bundled resources** (unlimited) — loaded only when needed

### What NOT to Include

A skill should only contain files an AI agent needs to do the job. Do NOT create:
- README.md, CHANGELOG.md, INSTALLATION_GUIDE.md
- Setup/testing documentation for humans
- Information Claude already knows (standard library APIs, common patterns)

---

## Quick Start

1. Create skill directory: `skills/<skill-name>/`
2. Create `SKILL.md` with frontmatter and instructions
3. Add `references/`, `scripts/`, `assets/` only if needed
4. Validate with `./validate_skills.py skills/<skill-name>`
5. Install with `./install.sh --link`

---

## Step 1: Naming

| Requirement | Example |
|-------------|---------|
| Lowercase only | `code-review` ✓, `Code-Review` ✗ |
| Letters, numbers, hyphens | `pdf-v2` ✓, `pdf_v2` ✗ |
| No start/end/consecutive hyphens | `my-skill` ✓, `-my--skill` ✗ |
| Max 64 characters | Keep it concise |
| Must match folder name | `skills/my-skill/` → `name: my-skill` |

Use descriptive, action-oriented names: `ci-debug`, `code-review`, `hw-unit-integration`.

---

## Step 2: Directory Structure

```
skills/<skill-name>/
├── SKILL.md          # Required - main instructions
├── scripts/          # Optional - deterministic/reusable code
├── references/       # Optional - detailed docs loaded on demand
└── assets/           # Optional - templates, images used in output
```

**scripts/** — Include when the same code gets rewritten repeatedly or deterministic reliability is needed. Scripts can be executed without loading into context.

**references/** — Include for documentation Claude should reference while working. Keeps SKILL.md lean. For large reference files (>100 lines), include a table of contents at top. Keep references one level deep from SKILL.md.

**assets/** — Files used in output (templates, icons, fonts), not loaded into context.

---

## Step 3: Write SKILL.md

### Frontmatter

```yaml
---
name: <skill-name>
description: <What it does>. Use when <triggers>. <Key capabilities>.
---
```

| Field | Required | Constraints |
|-------|----------|-------------|
| `name` | Yes | 1-64 chars, lowercase, kebab-case |
| `description` | Yes | 1-1024 chars, must include "Use when" |
| `license` | No | License identifier |
| `compatibility` | No | 1-500 chars, environment requirements |
| `metadata` | No | Custom key-value pairs |

**No other fields.** No `globs`, `alwaysApply`, `allowed-tools` (experimental only). No XML angle brackets (`<` `>`) in frontmatter.

### Writing Descriptions

The description is the **primary triggering mechanism**. Structure:

```
[What it does] + [Use when triggers] + [Key capabilities/scope]
```

Include specific phrases users would say. Mention file types if relevant.

```yaml
# Good (specific triggers, clear scope)
description: Perform adversarial code reviews for Python code. Use when asked to review code, review a commit, or perform code review. Produces structured review reports with issues, recommendations, and suggested fixes.

# Bad (no triggers, too vague)
description: Reviews code.
```

**All "when to use" information goes in the description**, not in the body. The body is only loaded after triggering — a "When to Use" section in the body doesn't help Claude decide to trigger the skill.

### Body Content

Keep SKILL.md under 500 lines. Write imperative/infinitive form.

Recommended structure for the body:

```markdown
# Skill Title

[Brief statement of purpose]

## [Core workflow / instructions]

Step-by-step procedures, patterns, examples.

## [Additional sections as needed]

Error handling, common issues, checklists.
```

When supporting multiple variants/frameworks/options, keep only the core workflow and selection guidance in SKILL.md. Move variant-specific details to `references/`.

---

## Step 4: Choose a Skill Pattern

Match your skill to one of these established patterns:

### Pattern 1: Sequential Workflow
Multi-step processes in specific order. Use explicit step ordering, validation at each stage, rollback instructions for failures.

### Pattern 2: Multi-Tool Coordination
Workflows spanning multiple tools/services. Use clear phase separation, data passing between phases, centralized error handling.

### Pattern 3: Iterative Refinement
Output improves with iteration. Include quality criteria, validation scripts/checks, and know-when-to-stop conditions.

### Pattern 4: Context-Aware Selection
Same outcome but different approaches depending on context. Include decision tree, fallback options, transparency about choices made.

### Pattern 5: Domain-Specific Intelligence
Specialized knowledge beyond tool access. Embed domain expertise, compliance/governance rules, and comprehensive audit trails.

---

## Step 5: Validate

```bash
./validate_skills.py skills/<skill-name>
```

| Common Error | Fix |
|--------------|-----|
| `name contains uppercase` | Lowercase only |
| `name doesn't match folder` | Rename folder or fix name |
| `Missing 'description' field` | Add description to frontmatter |
| `description exceeds 1024 chars` | Shorten description |
| `Unknown frontmatter field` | Remove non-spec fields |
| `missing 'Use when' trigger` | Add "Use when..." to description |

---

## Step 6: Install and Test

```bash
./install.sh --link
```

Test in Cursor by mentioning trigger phrases. Iterate based on:

- **Under-triggering** (skill doesn't load): Add more trigger phrases and keywords to description
- **Over-triggering** (loads for unrelated queries): Be more specific, add scope boundaries
- **Execution issues** (inconsistent results): Improve instructions, add error handling, add examples

---

## Progressive Disclosure — When to Split

| Keep in SKILL.md | Move to references/ | Move to scripts/ | Move to assets/ |
|-------------------|---------------------|-------------------|-----------------|
| Core workflow | Detailed API docs | Executable helpers | Templates |
| Decision logic | Exhaustive examples | Automation code | Images, fonts |
| Quick reference | Edge cases | Validation scripts | Boilerplate |
| Error handling | Domain schemas | Build scripts | Sample data |

**Important:** When splitting content out, reference the files from SKILL.md and describe clearly when to read them. Otherwise the agent won't know they exist.

---

## Checklist

- [ ] `name`: lowercase, valid chars, matches folder
- [ ] `description`: includes "Use when", under 1024 chars, specific triggers
- [ ] Body: concise, under 500 lines, imperative form
- [ ] No unnecessary files (README.md, CHANGELOG.md, etc.)
- [ ] No information Claude already knows
- [ ] References clearly linked from SKILL.md with usage guidance
- [ ] Validation passes
- [ ] Tested in Cursor with trigger phrases

