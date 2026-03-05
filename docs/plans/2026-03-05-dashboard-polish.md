# Dashboard Polish & Bug Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 19 layout, visual, functional, and consistency issues identified in the post-redesign dashboard review.

**Architecture:** All changes are to existing vanilla JS/CSS/HTML dashboard files. No new files, no new dependencies. Changes are grouped by concern: layout fixes first, then visual consistency, then functional bugs, then cleanup.

**Tech Stack:** Vanilla JS, CSS custom properties, SVG, FastAPI static serving

**Key files:**
- `golem/core/task_dashboard.css` (458 lines) — layout and style fixes
- `golem/core/task_dashboard.js` (1509 lines) — functional fixes and color consistency
- `golem/core/task_dashboard.html` (108 lines) — add missing config bar container
- `golem/core/dashboard_shared.css` (181 lines) — remove dead sidebar code, add DAG CSS vars
- `golem/core/dashboard_shared.js` (165 lines) — remove dead toggleSidebar()

**Test command:** `python -m pytest golem/tests/test_dashboard.py -x -q` (run after every change to ensure no regressions)

**Important context:** The dashboard was recently redesigned from a sidebar+detail layout to a DAG graph + task table overview. The sidebar was removed but some shared CSS/JS still references it. The design doc is at `docs/plans/2026-03-04-dashboard-redesign.md` — refer to its "Color Palette" section for the canonical variable names.

---

### Task 1: Fix scroll clipping — body overflow and main-view height

The `body` has `overflow:hidden; height:100vh` and `.main-view` also has `height:100vh`. The live bar (sticky, ~40px) eats into the scroll area, so the bottom of the task table gets clipped.

**Files:**
- Modify: `golem/core/dashboard_shared.css:39` (body rule)
- Modify: `golem/core/dashboard_shared.css:42` (`.main-view` rule)

**Step 1: Read the current rules**

Read `golem/core/dashboard_shared.css` lines 39 and 42.

Current (line 39):
```css
body{font-family:var(--font-sans);background:var(--bg-base);color:var(--text-primary);line-height:1.5;overflow:hidden;height:100vh}
```

Current (line 42):
```css
.main-view{height:100vh;overflow-y:auto;display:flex;flex-direction:column}
```

**Step 2: Fix the overflow chain**

The body should stay `overflow:hidden; height:100vh` (prevents double scrollbars). The main-view needs to account for the live bar height using `calc()`:

Change line 42 from:
```css
.main-view{height:100vh;overflow-y:auto;display:flex;flex-direction:column}
```
to:
```css
.main-view{height:calc(100vh - 42px);overflow-y:auto;display:flex;flex-direction:column;margin-top:0}
```

**Why 42px?** The live bar has `padding:0.6rem 1.25rem` ≈ 9.6px top+bottom + ~22px text = ~42px. Using a fixed value is fine since the live bar is fixed-height. Alternatively we could use `flex:1;min-height:0` if the body was a flex container — but that would be a bigger change.

Actually, the better fix: make body a flex column so the main-view fills remaining space naturally:

Change line 39 from:
```css
body{font-family:var(--font-sans);background:var(--bg-base);color:var(--text-primary);line-height:1.5;overflow:hidden;height:100vh}
```
to:
```css
body{font-family:var(--font-sans);background:var(--bg-base);color:var(--text-primary);line-height:1.5;overflow:hidden;height:100vh;display:flex;flex-direction:column}
```

Change line 42 from:
```css
.main-view{height:100vh;overflow-y:auto;display:flex;flex-direction:column}
```
to:
```css
.main-view{flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column}
```

**Step 3: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass (CSS changes don't affect Python tests, but verify nothing breaks the test suite's HTML parsing)

**Step 4: Commit**

```bash
git add golem/core/dashboard_shared.css
git commit -m "fix(dashboard): fix scroll clipping with flex body layout"
```

---

### Task 2: Fix DAG container height and add adaptive sizing

The DAG container is capped at `max-height:55vh` which leaves little room for the task table on small screens. With few tasks, the 200px `min-height` wastes space.

**Files:**
- Modify: `golem/core/task_dashboard.css:46-53` (`.dag-container` rule)

**Step 1: Read the current rule**

Current `.dag-container` (line 46-53):
```css
.dag-container{
  position:relative;margin:0 1.25rem;
  border:1px solid var(--border);border-radius:var(--radius-lg);
  background:var(--bg-surface);overflow:hidden;min-height:200px;max-height:55vh;
  background-image:radial-gradient(circle at 1px 1px,rgba(255,255,255,0.025) 1px,transparent 0);
  background-size:20px 20px;background-color:var(--bg-surface);
  cursor:grab
}
```

**Step 2: Change to adaptive height**

Replace with:
```css
.dag-container{
  position:relative;margin:0 1.25rem;
  border:1px solid var(--border);border-radius:var(--radius-lg);
  background:var(--bg-surface);overflow:hidden;min-height:120px;max-height:45vh;
  background-image:radial-gradient(circle at 1px 1px,rgba(255,255,255,0.025) 1px,transparent 0);
  background-size:20px 20px;background-color:var(--bg-surface);
  cursor:grab
}
```

Changes: `min-height:200px` → `min-height:120px`, `max-height:55vh` → `max-height:45vh`.

Also update the mobile breakpoint (line 450):
```css
.dag-container{max-height:35vh;margin:0 0.5rem}
```
(was `40vh`)

**Step 3: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 4: Commit**

```bash
git add golem/core/task_dashboard.css
git commit -m "fix(dashboard): reduce DAG container height to leave room for task table"
```

---

### Task 3: Center task detail view

The task detail has `max-width:1100px` but no centering — it left-aligns awkwardly on wide screens.

**Files:**
- Modify: `golem/core/task_dashboard.css:161`

**Step 1: Read and fix**

Current (line 161):
```css
.task-detail{padding:1.5rem 2rem 3rem;max-width:1100px}
```

Change to:
```css
.task-detail{padding:1.5rem 2rem 3rem;max-width:1100px;margin:0 auto;width:100%}
```

**Step 2: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 3: Commit**

```bash
git add golem/core/task_dashboard.css
git commit -m "fix(dashboard): center task detail view on wide screens"
```

---

### Task 4: Add DAG CSS custom properties from design doc

The design doc defines `--dag-*` derived variables but they were never created. Instead, raw hex colors live in JS.

**Files:**
- Modify: `golem/core/dashboard_shared.css:9-38` (`:root` block)

**Step 1: Read the current :root block**

Lines 9-38 of `golem/core/dashboard_shared.css`.

**Step 2: Add DAG variables after the existing variables**

Add these lines inside the `:root` block, before the closing `}`. Insert them after line 37 (`--responsive-sidebar:300px;`):

```css
  /* DAG graph derived tokens */
  --dag-node-bg:var(--bg-elevated);
  --dag-edge-default:var(--text-dim);
  --dag-edge-active:var(--blue);
  --dag-edge-done:color-mix(in srgb, var(--green) 40%, transparent);
  --dag-edge-failed:var(--red);
  --dag-cluster-border:color-mix(in srgb, var(--border) 50%, transparent);
  --dag-minimap-bg:var(--bg-base);
  --dag-minimap-viewport:color-mix(in srgb, var(--blue) 20%, transparent);
```

**Step 3: Use the cluster border variable in CSS**

In `golem/core/task_dashboard.css`, add a new rule after the `.dag-node-pulse` rule (after line 96):

```css
/* DAG group cluster borders */
.dag-cluster rect{fill:none;stroke:var(--dag-cluster-border);stroke-width:1;stroke-dasharray:4 3;rx:12}
.dag-cluster text{fill:var(--text-muted);font-size:10px;font-weight:600}
```

**Step 4: Use the minimap variables**

Change the `.dag-minimap` rule (line 60-64) background from `rgba(11,15,25,0.85)` to `var(--dag-minimap-bg)`:
```css
.dag-minimap{
  position:absolute;bottom:8px;right:8px;width:120px;height:80px;
  background:var(--dag-minimap-bg);border:1px solid var(--border);border-radius:4px;
  overflow:hidden;opacity:0.85
}
```

**Step 5: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 6: Commit**

```bash
git add golem/core/dashboard_shared.css golem/core/task_dashboard.css
git commit -m "feat(dashboard): add DAG CSS custom properties from design doc"
```

---

### Task 5: Replace hard-coded hex colors in JS with CSS variable references

`stateBadgeStyle()` and `verdictBadgeStyle()` in task_dashboard.js use raw hex values. The `DAG_COLORS` object also uses raw hex. While we can't use CSS vars directly in SVG attributes, we should at least make the badge functions consistent.

**Files:**
- Modify: `golem/core/task_dashboard.js` — `stateBadgeStyle()` function (~line 863) and `verdictBadgeStyle()` function (~line 164)

**Step 1: Refactor stateBadgeStyle to use CSS variables**

Current `stateBadgeStyle` (around line 863):
```javascript
function stateBadgeStyle(state) {
  const map = {
    completed: 'background:#064e3b;color:#4ade80',
    failed: 'background:#450a0a;color:#f87171',
    running: 'background:#172554;color:#60a5fa',
    validating: 'background:#1e1b4b;color:#a78bfa',
    detected: 'background:#422006;color:#fbbf24',
    retrying: 'background:#431407;color:#fb923c',
  };
  return map[state] || 'background:#1e293b;color:#94a3b8';
}
```

Replace with:
```javascript
function stateBadgeStyle(state) {
  const map = {
    completed: 'background:#064e3b;color:var(--green)',
    failed: 'background:#450a0a;color:var(--red)',
    running: 'background:#172554;color:var(--blue)',
    validating: 'background:#1e1b4b;color:var(--purple)',
    detected: 'background:#422006;color:var(--yellow)',
    retrying: 'background:#431407;color:var(--orange)',
  };
  return map[state] || 'background:var(--bg-elevated);color:var(--text-secondary)';
}
```

**Step 2: Refactor verdictBadgeStyle similarly**

Current `verdictBadgeStyle` (around line 164):
```javascript
function verdictBadgeStyle(v) {
  if (v === 'PASS') return 'background:#064e3b;color:#4ade80';
  if (v === 'FAIL') return 'background:#450a0a;color:#f87171';
  if (v === 'PARTIAL') return 'background:#431407;color:#fb923c';
  return 'background:#422006;color:#fbbf24';
}
```

Replace with:
```javascript
function verdictBadgeStyle(v) {
  if (v === 'PASS') return 'background:#064e3b;color:var(--green)';
  if (v === 'FAIL') return 'background:#450a0a;color:var(--red)';
  if (v === 'PARTIAL') return 'background:#431407;color:var(--orange)';
  return 'background:#422006;color:var(--yellow)';
}
```

Note: The background hex values stay because they are intentionally darker shades not in the variable system (they're 10% opacity versions of the accent colors). Only the foreground text colors get variables.

**Step 3: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 4: Commit**

```bash
git add golem/core/task_dashboard.js
git commit -m "fix(dashboard): use CSS variables for badge foreground colors"
```

---

### Task 6: Add DAG node hover → highlight dependency chain

Currently hovering a node in the DAG itself does nothing — the highlight logic only works from table row hover. The design spec requires: "Hover node: highlight full dependency chain, dim everything else."

**Files:**
- Modify: `golem/core/task_dashboard.js` — `renderDagSvg()` function (around line 631)

**Step 1: Add mouse event attributes to DAG node `<g>` elements**

In `renderDagSvg()`, find the line that creates the node group (around line 631):
```javascript
svg += `<g class="dag-node${dimmed ? ' dimmed' : ''}" data-task-id="${taskId}" data-orig-x="${node.x}" data-orig-y="${node.y}"${txAttr}>`;
```

Replace with:
```javascript
svg += `<g class="dag-node${dimmed ? ' dimmed' : ''}" data-task-id="${taskId}" data-orig-x="${node.x}" data-orig-y="${node.y}"${txAttr}
  onmouseenter="highlightDagNode('${taskId}')" onmouseleave="unhighlightDagNode()">`;
```

**Step 2: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 3: Commit**

```bash
git add golem/core/task_dashboard.js
git commit -m "feat(dashboard): add dependency chain highlight on DAG node hover"
```

---

### Task 7: Draw group cluster bounding boxes in DAG

The design spec requires grouped tasks (same `group_id`) to have a "subtle bounding box." The ELK layout already creates compound nodes for groups, but `renderDagSvg()` doesn't draw them.

**Files:**
- Modify: `golem/core/task_dashboard.js` — `renderDagSvg()` function, and `collectElkNodes()` needs to also return group info

**Step 1: Add a function to collect compound (group) nodes**

Add this new function after `collectElkEdges()` (after line 526):

```javascript
function collectElkGroups(layout) {
  const groups = [];
  if (layout.children) {
    for (const child of layout.children) {
      if (child.children && child.id && child.id.startsWith('g-')) {
        groups.push({
          id: child.id,
          label: (child.labels && child.labels[0] && child.labels[0].text) || '',
          x: child.x || 0,
          y: child.y || 0,
          w: child.width || 0,
          h: child.height || 0,
        });
      }
    }
  }
  return groups;
}
```

**Step 2: Render group boxes in renderDagSvg()**

In `renderDagSvg()`, after `svg += '<g class="dag-root">';` (line 576) and before the edge rendering loop, add:

```javascript
  /* Render group clusters */
  const groups = collectElkGroups(layout);
  for (const g of groups) {
    svg += `<g class="dag-cluster">
      <rect x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}"/>
      ${g.label ? `<text x="${g.x + 8}" y="${g.y + 16}">${esc(g.label)}</text>` : ''}
    </g>`;
  }
```

The CSS for `.dag-cluster rect` and `.dag-cluster text` was already added in Task 4.

**Step 3: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 4: Commit**

```bash
git add golem/core/task_dashboard.js
git commit -m "feat(dashboard): draw group cluster bounding boxes in DAG"
```

---

### Task 8: Fix event detail expansion index mismatch

The expandable event detail uses `events[index]` to match between the filtered `event_log` (200-char summaries, capped at 500 entries) and the full trace from `/api/trace-terminal/`. These indices won't align because:
- The event_log is a curated summary (may skip events, caps at 500)
- The trace has every raw event

The fix: instead of index-based matching, pass the event's timestamp and kind to find the closest match in the full trace.

**Files:**
- Modify: `golem/core/task_dashboard.js` — `renderWfEventRow()`, `toggleEventDetail()`

**Step 1: Pass event metadata instead of raw index**

Change `renderWfEventRow` (around line 1263):

From:
```javascript
function renderWfEventRow(ev, index) {
  const e = enrichEvent(ev);
  if (!e.body && !e.chip) return '';
  return `<div class="wf-event ${e.cls}" data-event-idx="${index}" onclick="toggleEventDetail(this, ${index})">
    <span class="ev-icon">${e.icon}</span>
    ${e.chip ? `<span class="ev-chip">${esc(e.chip)}</span>` : '<span></span>'}
    <span class="ev-body">${esc(e.body)}</span>
    ${e.ts ? `<span class="ev-ts">${e.ts}</span>` : '<span></span>'}
  </div>`;
}
```

To:
```javascript
function renderWfEventRow(ev, index) {
  const e = enrichEvent(ev);
  if (!e.body && !e.chip) return '';
  const evTs = ev.timestamp || 0;
  const evKind = ev.kind || ev.type || '';
  const evTool = ev.tool_name || '';
  return `<div class="wf-event ${e.cls}" data-event-ts="${evTs}" data-event-kind="${evKind}" data-event-tool="${evTool}" onclick="toggleEventDetail(this)">
    <span class="ev-icon">${e.icon}</span>
    ${e.chip ? `<span class="ev-chip">${esc(e.chip)}</span>` : '<span></span>'}
    <span class="ev-body">${esc(e.body)}</span>
    ${e.ts ? `<span class="ev-ts">${e.ts}</span>` : '<span></span>'}
  </div>`;
}
```

**Step 2: Rewrite toggleEventDetail to use fuzzy matching**

Replace the `toggleEventDetail` function (around line 1288-1345):

```javascript
async function toggleEventDetail(rowEl) {
  /* If already expanded, collapse */
  const existing = rowEl.nextElementSibling;
  if (existing && existing.classList.contains('ev-detail-panel')) {
    existing.remove();
    rowEl.classList.remove('ev-expanded');
    return;
  }

  /* Collapse any other expanded row */
  document.querySelectorAll('.ev-detail-panel').forEach(el => el.remove());
  document.querySelectorAll('.ev-expanded').forEach(el => el.classList.remove('ev-expanded'));

  rowEl.classList.add('ev-expanded');

  const s = _sessions[_selectedId];
  if (!s) return;
  const eventId = 'golem-' + _selectedId;

  /* Show loading state */
  const panel = document.createElement('div');
  panel.className = 'ev-detail-panel';
  panel.innerHTML = '<div class="ev-detail-loading">Loading full trace\u2026</div>';
  rowEl.after(panel);

  const events = await loadTraceTerminal(eventId);
  if (!events || !events.length) {
    panel.innerHTML = '<div class="ev-detail-loading">Trace not available.</div>';
    return;
  }

  /* Match by timestamp + kind + tool_name instead of index */
  const targetTs = parseFloat(rowEl.dataset.eventTs) || 0;
  const targetKind = rowEl.dataset.eventKind || '';
  const targetTool = rowEl.dataset.eventTool || '';

  let fullEvent = null;
  let bestScore = -1;

  for (const te of events) {
    let score = 0;
    const teKind = te.type || te.kind || '';
    const teTool = te.tool_name || '';
    const teTs = te.timestamp || 0;

    /* Kind must match */
    if (teKind !== targetKind && targetKind) continue;
    score += 1;

    /* Tool name match */
    if (targetTool && teTool === targetTool) score += 2;

    /* Timestamp proximity (within 2 seconds is a strong match) */
    if (targetTs > 0 && teTs > 0) {
      const diff = Math.abs(teTs - targetTs);
      if (diff < 2) score += 5;
      else if (diff < 10) score += 3;
      else if (diff < 60) score += 1;
    }

    if (score > bestScore) {
      bestScore = score;
      fullEvent = te;
    }
  }

  if (!fullEvent) {
    panel.innerHTML = '<div class="ev-detail-loading">Event details not found in trace.</div>';
    return;
  }

  let html = '<div class="ev-detail-content">';

  const evType = fullEvent.type || 'unknown';
  html += `<div class="ev-detail-type">${esc(evType)}</div>`;

  if (fullEvent.tool_name) {
    html += `<div class="ev-detail-tool">${esc(fullEvent.tool_name)}</div>`;
  }

  if (fullEvent.text) {
    const { formatted, isJson } = tryFormatJson(fullEvent.text);
    if (isJson) {
      html += `<pre class="ev-detail-pre json">${highlightJson(esc(formatted))}</pre>`;
    } else {
      html += `<pre class="ev-detail-pre">${esc(fullEvent.text)}</pre>`;
    }
  }

  html += '</div>';
  panel.innerHTML = html;
}
```

**Step 3: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 4: Commit**

```bash
git add golem/core/task_dashboard.js
git commit -m "fix(dashboard): use timestamp+kind matching for event detail expansion"
```

---

### Task 9: Add config bar container to HTML

`loadConfig()` looks for `#config-bar` which doesn't exist in the new HTML — config info is silently lost.

**Files:**
- Modify: `golem/core/task_dashboard.html` — add config bar after the live bar

**Step 1: Read current HTML**

The live bar ends at line 16. The main view starts at line 19.

**Step 2: Add config bar container**

After line 16 (`</header>`) and before line 19 (`<main class="main-view"...`), insert:

```html
<div class="config-bar hidden" id="config-bar"></div>
```

Note: the `.config-bar` styles already exist in `dashboard_shared.css` (line 99). The `hidden` class ensures it stays hidden until `loadConfig()` populates it and shows it.

**Step 3: Check that loadConfig() removes the hidden class**

Read `loadConfig()` in task_dashboard.js (around line 1448). It does `el.style.display = '';` to show and `el.style.display = 'none';` to hide. This conflicts with the `hidden` class. Fix: in `loadConfig()`, change the show/hide to use classList:

From:
```javascript
if (!cfg || !Object.keys(cfg).length) { el.style.display = 'none'; return; }
el.style.display = '';
```

To:
```javascript
if (!cfg || !Object.keys(cfg).length) { el.classList.add('hidden'); return; }
el.classList.remove('hidden');
```

And the catch block from:
```javascript
catch (e) { const bar = document.getElementById('config-bar'); if (bar) bar.style.display = 'none'; }
```
To:
```javascript
catch (e) { const bar = document.getElementById('config-bar'); if (bar) bar.classList.add('hidden'); }
```

**Step 4: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 5: Commit**

```bash
git add golem/core/task_dashboard.html golem/core/task_dashboard.js
git commit -m "fix(dashboard): add config bar container and fix show/hide logic"
```

---

### Task 10: Add smooth view transition between overview and task detail

Currently the overview ↔ task detail switch is an instant `hidden` class toggle. Add a subtle fade transition.

**Files:**
- Modify: `golem/core/task_dashboard.css` — add transition classes

**Step 1: Add transition styles**

Add at the end of the file, before the responsive section (before line 448):

```css
/* ── View Transitions ─────────────────────────────────────── */
.overview,.task-detail{animation:view-fade-in 0.15s ease-out}
@keyframes view-fade-in{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}
```

This is lightweight: when either view becomes visible (hidden class removed), the CSS animation plays automatically.

**Step 2: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 3: Commit**

```bash
git add golem/core/task_dashboard.css
git commit -m "feat(dashboard): add subtle fade transition between views"
```

---

### Task 11: Remove dead sidebar code from shared CSS and JS

**Files:**
- Modify: `golem/core/dashboard_shared.css` — remove `--sidebar-width`, `--responsive-sidebar`, `.layout` grid, and any sidebar-related rules
- Modify: `golem/core/dashboard_shared.js` — remove `toggleSidebar()`

**Step 1: Clean dashboard_shared.css**

Remove these CSS variables from `:root` (lines 34, 37):
```css
  --sidebar-width:360px;
  --responsive-sidebar:300px;
```

Remove the `.layout` rule (line 42 — but check this hasn't already been moved or is used by the flow dashboard):
```css
.layout{display:grid;grid-template-columns:var(--sidebar-width) 1fr;height:100vh;overflow:hidden}
```

**IMPORTANT CHECK:** Before removing `.layout`, grep for its usage. Run:
```bash
grep -r "class=\"layout" golem/core/ --include="*.html"
```

If the flow dashboard (`flow_dashboard.html`) uses `.layout`, do NOT remove it. Only remove it if nothing references it.

**Step 2: Clean dashboard_shared.js**

Remove the `toggleSidebar()` function (around line 163-165). Read the file first to find the exact location:

```javascript
function toggleSidebar(){...}
```

**IMPORTANT CHECK:** Before removing, grep for usage:
```bash
grep -r "toggleSidebar" golem/core/ --include="*.html" --include="*.js"
```

Only remove if no HTML/JS references it.

**Step 3: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 4: Commit**

```bash
git add golem/core/dashboard_shared.css golem/core/dashboard_shared.js
git commit -m "chore(dashboard): remove dead sidebar CSS/JS from shared files"
```

---

### Task 12: Increase accordion body max-height

The accordion log view limits stage bodies to `max-height:350px` — too cramped for stages with many events.

**Files:**
- Modify: `golem/core/task_dashboard.css:392`

**Step 1: Change max-height**

From:
```css
.acc-group-body{
  display:none;border-top:1px solid var(--border);
  padding:0.4rem 0 0.4rem 0;max-height:350px;overflow-y:auto;
  background:var(--bg-base)
}
```

To:
```css
.acc-group-body{
  display:none;border-top:1px solid var(--border);
  padding:0.4rem 0 0.4rem 0;max-height:60vh;overflow-y:auto;
  background:var(--bg-base)
}
```

**Step 2: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All pass

**Step 3: Commit**

```bash
git add golem/core/task_dashboard.css
git commit -m "fix(dashboard): increase accordion body max-height to 60vh"
```

---

### Task 13: Final integration verification

After all changes are made, do a final comprehensive check.

**Step 1: Run the full test suite**

```bash
python -m pytest golem/tests/test_dashboard.py -x -v
```

Expected: All tests pass.

**Step 2: Run broader test suite to check for regressions**

```bash
python -m pytest golem/tests/ -x -q --timeout=30
```

Expected: All tests pass.

**Step 3: Verify no leftover debug code**

```bash
grep -rn "console\.log\b" golem/core/task_dashboard.js
```

Expected: No matches (only `console.error` for legitimate error handling).

**Step 4: Commit if any final tweaks were needed**

If no tweaks needed, skip this step.

---

## Summary of Changes

| # | Issue | Fix | File(s) |
|---|-------|-----|---------|
| 1 | Scroll clipping | Flex body + `flex:1` main-view | shared.css |
| 2 | DAG too tall | Reduce min/max height | task.css |
| 3 | Detail not centered | Add `margin:0 auto` | task.css |
| 4 | Missing CSS vars | Add `--dag-*` tokens + cluster CSS | shared.css, task.css |
| 5 | Hard-coded badge hex | Use CSS vars for foreground colors | task.js |
| 6 | No DAG node hover highlight | Add mouse events to SVG nodes | task.js |
| 7 | No group cluster boxes | Draw compound node borders | task.js |
| 8 | Event detail index mismatch | Fuzzy match by timestamp+kind | task.js |
| 9 | Missing config bar | Add HTML container + fix show/hide | task.html, task.js |
| 10 | No view transition | CSS fade animation | task.css |
| 11 | Dead sidebar code | Remove from shared CSS/JS | shared.css, shared.js |
| 12 | Accordion too cramped | Increase max-height to 60vh | task.css |
| 13 | Final verification | Run full test suite | — |
