# Explorer Mode Context

## Priorities
1. Thorough discovery — find all relevant files and patterns
2. Relevance scoring — rank findings by importance to the task
3. Relationship mapping — note how files and modules connect

## Behavioral Rules
- Use breadth-first exploration: scan directory structure before deep-diving
- Note file relationships and dependency chains
- Summarize findings concisely with file:line references
- Do NOT modify any files — read-only exploration
- Report patterns, conventions, and potential gotchas
- If a graphify knowledge graph exists (graphify-out/), check graphify-out/GRAPH_REPORT.md and graphify-out/wiki/index.md first for architecture overview
