# README Template

Reference template for project READMEs. Adapt sections to fit the project.

## Header Block

```html
<p align="center">
  <img src="assets/logo.png" alt="Project Name" width="200" />
</p>

<h1 align="center">Project Name</h1>

<p align="center">
  <strong>One sentence that explains what this does and why you'd use it.</strong>
</p>

<p align="center">
  <a href="..."><img src="https://img.shields.io/badge/..." alt="badge"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#how-it-works">How It Works</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#configuration">Configuration</a>
</p>
```

## Table of Contents (collapsible)

```html
<details>
<summary><strong>Table of Contents</strong></summary>

- [Why Project](#why-project)
- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Configuration](#configuration)
- [API Reference](#api-reference)
- [Development](#development)
- [License](#license)

</details>
```

## Why Section Pattern

```markdown
## Why Project Name

One sentence framing the problem.

**Feature 1** — 2-3 sentence description of what it does and why it matters.

**Feature 2** — Same pattern. Bold lead-in, dash, description.
```

## Quick Start Pattern

```markdown
## Quick Start

### Prerequisites

- **Tool 1** — purpose and install link
- **Tool 2** — purpose and install link

### 1. Install

\`\`\`bash
git clone ... && cd ...
pip install -e .
\`\`\`

### 2. Configure

\`\`\`bash
cp config.example config    # annotate what to edit
\`\`\`

### 3. Run

\`\`\`bash
project-name run             # most common command first
project-name status          # then monitoring
\`\`\`
```

## Configuration Table Pattern

```markdown
| Setting | Default | Description |
|---------|---------|-------------|
| `setting_name` | `value` | What it controls |
```

## API Table Pattern

```markdown
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/health` | GET | Readiness probe |
| `/api/resource` | POST | Create a resource — accepts `{"field": "..."}` |
```

## Architecture Diagram Pattern

```
\`\`\`mermaid
flowchart TB
    input["Input Source"] --> engine["Processing Engine"]
    engine --> output["Output"]
\`\`\`
```

## Collapsible Detail Pattern

```html
<details>
<summary><strong>Section Title</strong></summary>

Content that most readers don't need but some want access to.

</details>
```
