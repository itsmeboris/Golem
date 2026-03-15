# Documentation Splitting Guide

When a single README tries to serve every audience, it fails all of them.
This guide covers when to split, what goes where, and how to cross-link.

## When to Split

Split documentation when any of these are true:

- **README exceeds ~300 lines** — length is a symptom of scope creep
- **Multiple distinct audiences** — a first-time visitor and an active operator
  need different things; serving both in one doc means the visitor scrolls past
  config tables and the operator can't find the reference material
- **Reference material mixed with marketing** — a 40-row config table buried
  inside a README kills the pitch
- **Contributor workflow mixed with user content** — dev setup instructions
  belong in CONTRIBUTING.md, not in the README

When in doubt, write the README first. If it grows past 300 lines, identify
what audience the excess content serves and move it.

## Standard Split Pattern

Four documents cover almost every project:

| Document | Primary audience | Job |
|----------|-----------------|-----|
| `README.md` | First-time visitors | Hook, pitch, quick start, link out |
| `docs/architecture.md` | Curious users, contributors | How it works internally |
| `docs/operations.md` | Active operators | Config, env vars, monitoring |
| `CONTRIBUTING.md` | Contributors | Dev setup, testing, extending |

Each document has one primary audience and one job. When a section doesn't
fit any of these cleanly, that is a signal to reconsider whether it belongs
in the docs at all.

## What Goes Where

| Content | Where it goes |
|---------|--------------|
| Hero, pitch, why, quick start | `README.md` |
| Diagrams, state machines, data flow | `docs/architecture.md` |
| Module/component reference | `docs/architecture.md` |
| Config tables, env vars | `docs/operations.md` |
| Deployment, monitoring, alerting | `docs/operations.md` |
| Local dev setup | `CONTRIBUTING.md` |
| Testing instructions | `CONTRIBUTING.md` |
| How to add a new backend/plugin | `CONTRIBUTING.md` |
| License, credits | `README.md` |

When content fits two categories, prefer the one that serves the primary
reader of that content. A config table that operators look up daily belongs
in operations.md even if it is also mentioned briefly in the README quick start.

## How to Cross-Link

### README links out

End the README with a Links section pointing to every split doc:

```markdown
## Links

- [Architecture](docs/architecture.md) — technical deep dive
- [Operations Guide](docs/operations.md) — configuration and monitoring
- [Contributing](CONTRIBUTING.md) — development setup and workflow
```

### Split docs link back

Every split doc should open with a breadcrumb or back-link so a reader who
lands on it directly can orient themselves:

```markdown
<!-- top of docs/architecture.md -->
[← README](../README.md)
```

### Cross-linking between split docs

When one split doc references content owned by another, link — don't copy:

```markdown
<!-- in docs/architecture.md -->
For the full config reference, see the [Operations Guide](operations.md).
```

Copying creates drift. If the config changes, only the operations doc needs
updating.

## Keeping Splits Maintained

- When adding a new config key, update operations.md — not the README
- When adding a new component, update architecture.md
- When changing the dev setup, update CONTRIBUTING.md
- Periodically review the README to confirm the quick start still works and
  all links resolve
