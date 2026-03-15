---
name: writing-docs
description: >-
  Write and improve project documentation — READMEs, contributor guides,
  architecture docs, and ops references. Use when asked to write a README,
  update documentation, improve docs, create a getting-started guide,
  document a feature, or review existing docs for accuracy and completeness.
---

# Writing Documentation

## Iron Law

Every factual claim must be verified against the code. Never document what you
assume — grep for it.

## Before Writing

### 1. Read existing docs

Read every doc in the repo before writing. Understand current structure, tone,
coverage, and what's stale. When updating, make surgical edits — preserve
existing structure unless there is a clear reason to restructure.

### 2. Read the code

Documentation must match what the code actually does, not what it was supposed
to do.

Before stating any fact:
- Grep for default values: `grep -r "default_timeout" golem/`
- Grep for class and component names before mentioning them
- Check CLI flags against the actual argument parser
- Verify config keys against the actual config schema

If the README says the default is X, verify X in the code.

### 3. Identify and rank audiences

Identify every audience this doc serves. Then rank them. Write for the primary
audience — secondary audiences belong in separate docs (see Document Sizing).

Default recommendation for pre-launch projects:

1. **Users first** — README. Converts visitors to users.
2. **Contributors second** — CONTRIBUTING.md. Onboards developers.
3. **Ops/admins third** — docs/. Config reference, monitoring, deployment.

Challenge each section: would this audience care? If they would not, either
cut it or link to a doc that targets the audience that would.

## Document Sizing

README is a landing page, not an encyclopedia. Target 150-250 lines.

**If a README exceeds ~300 lines, content needs splitting.**

Signals that a doc needs splitting:
- Multiple distinct audiences (visitors AND active operators)
- Reference material mixed with marketing copy
- Sections that only apply to contributors buried after user content
- Configuration tables with 10+ rows

### Standard split pattern

| Document | Primary audience | Job |
|----------|-----------------|-----|
| `README.md` | First-time visitors | Hook them, show quick start, link out |
| `docs/architecture.md` | Curious users, contributors | Technical deep dive |
| `docs/operations.md` | Active operators | Config, env vars, monitoring |
| `CONTRIBUTING.md` | Contributors | Dev setup, testing, workflow |

Each doc has one primary audience and one job. Overlap is a sign that content
belongs somewhere else.

When splitting, add a Links section to the README pointing to the other docs.
Each split doc should reference back to README and cross-link siblings where
relevant. See `references/splitting-guide.md` for detailed guidance.

## README Pattern

A slim README converts visitors to users. Structure:

1. **Hero** — logo, title, tagline, badges
2. **Pitch** — 2-3 sentences: what it does, for whom, and the key benefit
3. **Why** — 5 bullets max, bold lead-ins (`**Feature** — description`)
4. **Who Is This For** — one short paragraph naming the target user
5. **Quick Start** — prerequisites, install, configure, run
6. **How It Works** — one diagram, brief prose (3-5 sentences)
7. **Links** — pointers to architecture, ops, contributing docs
8. **License**

Skip sections that don't apply. Keep the order stable when sections are
present. See `references/readme-template.md` for the full template.

## Writing Rules

**Accuracy.** Every claim is verifiable in the code. If a diagram shows
component names, they must match the actual class or file names. If a table
lists defaults, they must match the actual defaults in config.

**Scannable.** Readers skim. Use short paragraphs (2-3 sentences max), bold
lead-ins for key concepts, tables for structured data, and code blocks for
anything the user types or reads.

**Show, don't tell.** A code example beats a paragraph of explanation.

```
# Weak
The submit endpoint accepts a JSON payload with a prompt field.

# Strong
curl -X POST http://localhost:8081/api/submit \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Add retry logic"}'
```

**DRY.** Don't repeat information across sections or docs. Reference instead:
"See [Operations Guide](docs/operations.md) for all config options."

**No filler.** Cut "It should be noted that", "In order to", "As mentioned
above". Say the thing directly.

**Challenge each section.** Would the primary audience for this doc care about
this section? If not, link to the doc that targets the audience who would, or
cut it.

**Consistent terminology.** Pick one term for each concept and use it
everywhere. If the code calls it a "worktree", the docs call it a "worktree" —
not "workspace", "checkout", or "branch directory".

**Avoid time-sensitive information.** Don't write "as of version 2.1" or "we
recently added". Write as if the doc will be read two years from now.

## Verification Loop

After writing, before finishing:

1. Grep for every default value claimed — confirm it matches code
2. Grep for every component, class, or module name referenced — confirm it exists
3. Check all code examples: correct flags, correct paths, correct syntax
4. Verify all internal links resolve to real anchors or files
5. Confirm that each doc section addresses the stated primary audience
6. Run through the checklist below

## Checklist

- [ ] Every default verified against code
- [ ] Every component name matches actual code
- [ ] Code examples are runnable
- [ ] No orphan sections (all sections linked from TOC or nav if doc has one)
- [ ] Consistent formatting throughout (heading levels, bold patterns, list styles)
