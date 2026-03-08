# Warm Mono Dashboard Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Redesign the Golem task dashboard with the "Warm Mono" aesthetic: warm charcoal dark + cream light themes with purple accent, side-by-side DAG + task list layout, colored accent bars, icon-sized metric tiles, theme toggle.

**Architecture:** CSS rewrite with new design tokens using `data-theme` attribute for light/dark switching. HTML restructured for new layout (split DAG+tasks, card-style metrics). JS rendering functions updated to emit new HTML classes; theme toggle added. All existing functionality preserved.

**Tech Stack:** Vanilla JS, CSS custom properties, SVG, FastAPI static serving

**Key files:**
- `golem/core/dashboard_shared.css` (189 lines) — full rewrite with new design system + theme tokens
- `golem/core/task_dashboard.css` (542 lines) — full rewrite with Warm Mono component styles
- `golem/core/task_dashboard.html` (118 lines) — restructure layout: nav tabs, split view, theme toggle
- `golem/core/task_dashboard.js` (1509+ lines) — update render functions for new HTML structure + theme toggle
- `golem/core/dashboard_shared.js` (167 lines) — add theme toggle utility
- `golem/tests/test_dashboard.py` — update structural tests for new layout

**Test command:** `python -m pytest golem/tests/test_dashboard.py -x -q`

**Design doc:** `docs/plans/2026-03-08-dashboard-warmmon-design.md`

---

### Task 1: Update HTML tests for the new layout structure

The existing structural tests check for old class names (`dag-container`, `task-table`, `dag-filter-btn`, `dag-collapse-btn`, etc.) and CSS patterns. Update them to check for new Warm Mono layout elements.

**Files:**
- Modify: `golem/tests/test_dashboard.py:931-969` (5 structural tests)

**Step 1: Replace the 5 structural tests**

Replace lines 931-969 with:

```python
    def test_dashboard_html_has_new_layout(self):
        """Verify the HTML file has the Warm Mono layout structure."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        # Core layout elements
        assert "top-bar" in body, "Missing top-bar"
        assert "split-view" in body, "Missing split-view (DAG + task list)"
        assert "dag-panel" in body, "Missing dag-panel"
        assert "task-list" in body, "Missing task-list"
        assert "metrics-row" in body, "Missing metrics-row"
        # No old sidebar
        assert "sidebar" not in body, "Old sidebar should be removed"

    def test_dashboard_html_has_theme_toggle(self):
        """Theme toggle button should exist in the top bar."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert "theme-toggle" in body, "Missing theme toggle button"

    def test_dashboard_html_has_nav_tabs(self):
        """Navigation tabs should exist in the top bar."""
        html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
        body = html.read_text(encoding="utf-8")
        assert "nav-tab" in body, "Missing nav tabs"

    def test_css_has_theme_tokens(self):
        """Shared CSS should define both dark and light theme tokens."""
        css = Path(__file__).resolve().parent.parent / "core" / "dashboard_shared.css"
        body = css.read_text(encoding="utf-8")
        assert "data-theme" in body, "Missing data-theme attribute selector"
        assert "--accent" in body, "Missing --accent CSS variable"

    def test_css_has_split_view(self):
        """Task dashboard CSS should have the split-view layout."""
        css = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.css"
        body = css.read_text(encoding="utf-8")
        assert "split-view" in body, "Missing split-view CSS"

    def test_shared_js_has_theme_toggle(self):
        """Shared JS should have theme toggle function."""
        js = Path(__file__).resolve().parent.parent / "core" / "dashboard_shared.js"
        body = js.read_text(encoding="utf-8")
        assert "toggleTheme" in body or "setTheme" in body, "Missing theme toggle in shared JS"
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q -k "test_dashboard_html_has_new_layout or test_dashboard_html_has_theme_toggle or test_dashboard_html_has_nav_tabs or test_css_has_theme_tokens or test_css_has_split_view or test_shared_js_has_theme_toggle"`
Expected: FAIL (old HTML doesn't have new elements yet)

**Step 3: Commit the test updates**

```bash
git add golem/tests/test_dashboard.py
git commit -m "test(dashboard): update structural tests for Warm Mono redesign"
```

---

### Task 2: Rewrite dashboard_shared.css with new design system + themes

Complete rewrite of the shared CSS file with Warm Mono dark/light theme tokens and updated base styles.

**Files:**
- Rewrite: `golem/core/dashboard_shared.css`

**Step 1: Write the new shared CSS**

Rewrite the entire `golem/core/dashboard_shared.css` file. Key changes:
- New `:root` / `[data-theme="dark"]` variables with Warm Mono dark palette
- `[data-theme="light"]` variables with warm cream light palette
- Font stack: `'DM Sans'` body, `'JetBrains Mono'` mono
- Radius: `--radius: 8px; --radius-lg: 14px`
- All existing shared component styles (back-btn, metrics-row, info-tabs, config-bar, etc.) updated to use new tokens
- Markdown body, validation card, skeleton, error tooltip styles preserved

The CSS should be structured as:
1. Reset & custom properties (dark + light)
2. Body & layout
3. Scrollbars
4. Back button
5. Metrics row (new: icon-tile style)
6. Info tabs (updated colors)
7. Config bar
8. Empty state
9. Markdown body
10. Error tooltip
11. Validation
12. Skeleton loading

**Step 2: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py::TestDashboardRoutes::test_css_has_theme_tokens -v`
Expected: PASS (CSS now has `data-theme` and `--accent`)

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: Some tests pass, some still fail (HTML/JS not updated yet)

**Step 3: Commit**

```bash
git add golem/core/dashboard_shared.css
git commit -m "feat(dashboard): rewrite shared CSS with Warm Mono design system"
```

---

### Task 3: Rewrite task_dashboard.css with new component styles

Complete rewrite of the task-specific CSS with Warm Mono layout and components.

**Files:**
- Rewrite: `golem/core/task_dashboard.css`

**Step 1: Write the new task CSS**

Key structural changes from current CSS:
- **Top bar**: New `.top-bar` with nav tabs (`.nav-tab` pills with 8px radius), theme toggle button
- **Overview**: New `.overview` with vertical stack: metrics → split view
- **Split view**: New `.split-view` — `display:grid; grid-template-columns: 1fr 1fr; gap: 1rem`
- **DAG panel**: `.dag-panel` with 14px radius, header bar + body. Replaces old `.dag-section`/`.dag-container`
- **Task list**: `.task-list` panel with header + `.tl-row` items that have 3px colored left borders by state
- **Metric cards**: `.mr-card` with colored icon tile (`.mr-dot`) + label/value
- **Task detail**: Keep structure but restyle with new tokens. Sticky header preserved
- **Waterfall/accordion/live terminal**: Restyle with new colors and radii
- **Responsive**: Stack split-view vertically below 900px, metrics 2x2 below 700px

Preserve all existing class names used by JS that aren't being changed (e.g. `.wf-row`, `.acc-group`, `.lt-row`, etc.) OR if renaming, note what JS functions need updating.

**Important**: The CSS should reference only CSS variables from shared.css — no hard-coded hex colors.

**Step 2: Run tests**

Run: `python -m pytest golem/tests/test_dashboard.py::TestDashboardRoutes::test_css_has_split_view -v`
Expected: PASS

**Step 3: Commit**

```bash
git add golem/core/task_dashboard.css
git commit -m "feat(dashboard): rewrite task CSS with Warm Mono layout"
```

---

### Task 4: Restructure task_dashboard.html

Restructure the HTML for the new Warm Mono layout.

**Files:**
- Rewrite: `golem/core/task_dashboard.html`

**Step 1: Write the new HTML structure**

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Golem Dashboard</title>
<link rel="stylesheet" href="/dashboard/shared.css">
<link rel="stylesheet" href="/dashboard/task.css">
</head>
<body>

<!-- Top Bar -->
<header class="top-bar" id="top-bar">
  <div class="top-brand">
    <svg class="top-logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="20" height="20"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>
    <span>Golem</span>
  </div>
  <nav class="top-nav" id="top-nav">
    <button class="nav-tab active" data-view="overview">Overview</button>
    <button class="nav-tab" data-view="tasks">Tasks</button>
    <button class="nav-tab" data-view="config">Config</button>
  </nav>
  <div class="top-stats" id="top-stats"></div>
  <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" title="Toggle theme">
    <span class="theme-icon" id="theme-icon">&#9789;</span>
  </button>
</header>

<div class="config-bar hidden" id="config-bar"></div>

<!-- Level 1: Overview -->
<main class="main-view" id="main-view">
  <div class="overview" id="overview">
    <!-- Metrics Row -->
    <div class="metrics-row" id="overview-metrics"></div>

    <!-- Split View: DAG + Task List -->
    <div class="split-view">
      <!-- DAG Panel (left) -->
      <div class="dag-panel" id="dag-panel">
        <div class="dag-header">
          <span class="dag-title">Task Graph</span>
          <div class="dag-filters" id="dag-filters">
            <button class="dag-pill active" data-filter="" onclick="setDagFilter('')">All</button>
            <button class="dag-pill" data-filter="active" onclick="setDagFilter('active')">Active</button>
            <button class="dag-pill" data-filter="failed" onclick="setDagFilter('failed')">Failed</button>
            <button class="dag-pill" data-filter="completed" onclick="setDagFilter('completed')">Completed</button>
          </div>
          <select class="dag-group-filter hidden" id="dag-group-filter" onchange="setDagGroupFilter(this.value)"></select>
          <div class="dag-controls">
            <button class="dag-ctrl-btn dag-collapse-btn" onclick="toggleDagCollapse()" title="Toggle DAG">&#9650;</button>
            <button class="dag-ctrl-btn" onclick="dagZoomFit()" title="Fit">&#8862;</button>
            <button class="dag-ctrl-btn" onclick="dagZoomIn()" title="Zoom in">+</button>
            <button class="dag-ctrl-btn" onclick="dagZoomOut()" title="Zoom out">&minus;</button>
          </div>
        </div>
        <div class="dag-body" id="dag-body">
          <svg class="dag-svg" id="dag-svg"></svg>
          <button class="dag-minimap-toggle" id="dag-minimap-toggle" onclick="toggleMinimap()" title="Minimap">&#9638;</button>
          <div class="dag-minimap" id="dag-minimap"></div>
          <div class="dag-empty" id="dag-empty">
            <p>No tasks yet.</p>
          </div>
        </div>
      </div>

      <!-- Task List (right) -->
      <div class="task-list-panel" id="task-list-panel">
        <div class="tl-header">
          <span class="tl-title">Tasks</span>
          <input type="search" id="table-search" class="tl-search" placeholder="Filter tasks...">
          <select id="table-state-filter" class="tl-filter">
            <option value="">All</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="validating">Validating</option>
            <option value="detected">Detected</option>
          </select>
        </div>
        <div class="task-list" id="task-list"></div>
      </div>
    </div>
  </div>

  <!-- Level 2: Task Detail (hidden until selected) -->
  <div class="task-detail hidden" id="task-detail">
    <div class="task-back" id="task-back"></div>
    <header class="task-header" id="task-header"></header>
    <div class="metrics-row" id="metrics-row"></div>
    <div id="phase-banner-container"></div>
    <div class="pipeline-view hidden" id="pipeline-view">
      <div class="pv-toolbar">
        <h3>Pipeline</h3>
        <div class="pv-controls">
          <button class="pv-toggle-btn active" id="btn-waterfall-view" onclick="setPipelineView('waterfall')">Waterfall</button>
          <button class="pv-toggle-btn" id="btn-log-view" onclick="setPipelineView('log')">Log</button>
          <button class="pv-toggle-btn" id="btn-live-view" onclick="setPipelineView('live')">Live</button>
          <button class="pv-toggle-btn" id="btn-thinking" onclick="toggleThinking()">Reasoning</button>
          <button class="pv-toggle-btn" id="btn-text" onclick="toggleText()">Agent text</button>
        </div>
      </div>
      <div id="waterfall-view">
        <div class="wf-table" id="wf-table"></div>
        <div class="wf-detail-panel hidden" id="wf-detail-panel"></div>
      </div>
      <div id="log-view" class="hidden">
        <div class="accordion-view" id="accordion-view"></div>
      </div>
      <div id="live-view" class="hidden">
        <div class="live-terminal" id="live-terminal"></div>
      </div>
    </div>
    <div class="info-tabs" id="info-tabs">
      <div class="tab-bar" id="tab-bar">
        <button class="tab-btn active" data-tab="errors" onclick="activateTab('errors')">Errors</button>
        <button class="tab-btn" data-tab="tools" onclick="activateTab('tools')">Tools</button>
        <button class="tab-btn" data-tab="coordination" onclick="activateTab('coordination')">Coordination</button>
        <button class="tab-btn" data-tab="raw" onclick="activateTab('raw')">Raw Session</button>
      </div>
      <div id="tab-errors" class="tab-content active"></div>
      <div id="tab-tools" class="tab-content"></div>
      <div id="tab-coordination" class="tab-content"></div>
      <div id="tab-raw" class="tab-content"></div>
    </div>
  </div>
</main>

<script src="/dashboard/elk.js"></script>
<script src="/dashboard/shared.js"></script>
<script src="/dashboard/task.js"></script>
</body>
</html>
```

Key changes from old HTML:
- `<html data-theme="dark">` for theme switching
- Old `.live-bar` → `.top-bar` with nav tabs + theme toggle
- Old `.dag-section` → `.dag-panel` inside `.split-view`
- Old `.task-table-section` → `.task-list-panel` inside `.split-view`
- Old `#task-table` → `#task-list` (JS reference change)
- Old `.dag-container` → `.dag-body`
- Old `.dag-toolbar` → `.dag-header`
- Old `.dag-filter-btn` → `.dag-pill`
- Old `.tt-toolbar` → `.tl-header`
- New `#overview-metrics` for overview metric cards
- Pipeline view, task detail, info tabs structure **unchanged**

**Step 2: Run HTML structural tests**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q -k "test_dashboard_html"`
Expected: PASS for `test_dashboard_html_has_new_layout`, `test_dashboard_html_has_theme_toggle`, `test_dashboard_html_has_nav_tabs`

**Step 3: Commit**

```bash
git add golem/core/task_dashboard.html
git commit -m "feat(dashboard): restructure HTML for Warm Mono layout"
```

---

### Task 5: Add theme toggle to dashboard_shared.js

Add the theme toggle utility function to the shared JS.

**Files:**
- Modify: `golem/core/dashboard_shared.js` — append theme toggle functions at the end

**Step 1: Add theme toggle code**

Append to the end of `golem/core/dashboard_shared.js`:

```javascript

/* ── Theme Toggle ─────────────────────────────────────────── */
function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('golem-theme', theme); } catch(e) {}
  const icon = document.getElementById('theme-icon');
  if (icon) icon.textContent = theme === 'light' ? '\u2600' : '\u263D';
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  setTheme(current === 'dark' ? 'light' : 'dark');
}

/* Restore saved theme on load */
(function() {
  try {
    const saved = localStorage.getItem('golem-theme');
    if (saved) setTheme(saved);
  } catch(e) {}
})();
```

**Step 2: Run test**

Run: `python -m pytest golem/tests/test_dashboard.py::TestDashboardRoutes::test_shared_js_has_theme_toggle -v`
Expected: PASS

**Step 3: Commit**

```bash
git add golem/core/dashboard_shared.js
git commit -m "feat(dashboard): add theme toggle to shared JS"
```

---

### Task 6: Update task_dashboard.js rendering functions

Update the JS rendering functions to emit new HTML classes matching the restructured layout. This is the largest task.

**Files:**
- Modify: `golem/core/task_dashboard.js`

**Changes needed by function:**

1. **`renderLiveBar()`** (line 50-77): Rename to `renderTopStats()`. Target `#top-stats` instead of `#lb-stats`. Remove `.lb-` class prefixes, use simple spans. Output is stats only (brand/nav/toggle are static HTML).

2. **`renderTaskTable()`** (line 80-160): Rename to `renderTaskList()`. Target `#task-list` instead of `#task-table`. Change output from grid-based `.tt-row` to `.tl-row` items with colored left border (class `st-{state}`). Each row: ID, subject, badge, cost. Remove checkbox column. Update header to use `.tl-col-header` instead of `.tt-header`.

3. **`renderOverview()`** (line 218): Update to also render overview metrics into `#overview-metrics`. Call `renderOverviewMetrics()` (new function) + `renderDagGraph()` + `renderTaskList()`.

4. **New function `renderOverviewMetrics()`**: Renders 4 metric cards into `#overview-metrics` with icon-tile style (`.mr-card` with `.mr-dot`). Shows: active count, completed count, failed count, total cost.

5. **`toggleDagCollapse()`** (line 40-43): Update to toggle on `#dag-panel` instead of `#dag-section`.

6. **DAG functions**: Update `getElementById` references:
   - `dag-container` → `dag-body`
   - `dag-svg` stays the same
   - `dag-minimap`, `dag-minimap-toggle` stay the same
   - `dag-empty` stays the same
   - `.dag-filter-btn` → `.dag-pill` in `setDagFilter()`
   - `dag-group-filter` stays the same

7. **`renderTaskDetail()`** (line 886+): No structural change needed — it writes into `#task-header`, `#metrics-row`, `#pipeline-view` etc. which are preserved. Only `stateBadgeStyle()` colors may need updating to use `var(--accent)` etc.

8. **`stateBadgeStyle()`** (around line 863): Update to Warm Mono colors:
   ```javascript
   function stateBadgeStyle(state) {
     const map = {
       completed: 'background:var(--green-bg);color:var(--green)',
       failed: 'background:var(--red-bg);color:var(--red)',
       running: 'background:var(--accent-bg);color:var(--accent)',
       validating: 'background:var(--blue-bg);color:var(--blue)',
       detected: 'background:var(--yellow-bg);color:var(--yellow)',
       retrying: 'background:var(--red-bg);color:var(--red)',
     };
     return map[state] || 'background:var(--bg-elevated);color:var(--text-secondary)';
   }
   ```

9. **Filter event listeners**: Update `document.getElementById('table-search')` and `table-state-filter` — IDs stay the same, no change needed.

10. **`loadConfig()`**: Reference `#config-bar` stays the same, no change needed.

11. **Poll/init**: `renderLiveBar()` calls → `renderTopStats()`.

**Step 1: Make all the JS changes described above**

Be careful to:
- Update ALL `getElementById` and `querySelector` calls that reference renamed elements
- Search for every reference to old class names (`.tt-row`, `.tt-header`, `#task-table`, `.dag-container`, `.dag-section`, `.dag-filter-btn`, `#lb-stats`) and update them
- Keep all DAG logic (ELK, zoom, pan, drag, hover, minimap) functional — only container IDs change

**Step 2: Run the full test suite**

Run: `python -m pytest golem/tests/test_dashboard.py -x -q`
Expected: All tests PASS

**Step 3: Run broader tests**

Run: `python -m pytest golem/tests/ -x -q --timeout=30`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add golem/core/task_dashboard.js
git commit -m "feat(dashboard): update JS rendering for Warm Mono layout"
```

---

### Task 7: Clean up theme_preview.html

The theme preview file was used during design exploration and is no longer needed in the codebase.

**Files:**
- Delete: `golem/core/theme_preview.html`

**Step 1: Remove the file**

```bash
rm golem/core/theme_preview.html
```

**Step 2: Commit**

```bash
git add -A golem/core/theme_preview.html
git commit -m "chore(dashboard): remove theme preview file"
```

---

### Task 8: Final integration verification

**Step 1: Run the full test suite**

```bash
python -m pytest golem/tests/test_dashboard.py -x -v
```

Expected: All tests pass.

**Step 2: Run broader test suite**

```bash
python -m pytest golem/tests/ -x -q --timeout=30
```

Expected: All tests pass.

**Step 3: Verify no hard-coded colors in CSS**

```bash
grep -n '#[0-9a-fA-F]\{3,8\}' golem/core/task_dashboard.css | head -20
```

Expected: Very few matches — only inside `:root` / `[data-theme]` blocks, not in component rules.

**Step 4: Verify no console.log in JS**

```bash
grep -n 'console\.log\b' golem/core/task_dashboard.js
```

Expected: No matches (only `console.error` or `console.warn`).

**Step 5: Check theme toggle works**

Manually verify by opening the dashboard:
1. Default: dark theme (warm charcoal)
2. Click theme toggle → light theme (warm cream)
3. Refresh page → light theme persists (localStorage)
4. Click toggle again → dark theme

**Step 6: Visual check at key breakpoints**

1. **1920x1080**: Split view shows DAG and task list side by side, spacious
2. **1280x720**: Same layout, slightly more compact
3. **900px**: Split view stacks vertically
4. **320px**: Metrics 2x2, compact single column

---

## Summary of Changes

| # | Task | Files | Description |
|---|------|-------|-------------|
| 1 | Update tests | test_dashboard.py | New structural tests for Warm Mono layout |
| 2 | Shared CSS | dashboard_shared.css | Full rewrite: design tokens, dark+light themes |
| 3 | Task CSS | task_dashboard.css | Full rewrite: Warm Mono components + split layout |
| 4 | HTML | task_dashboard.html | Restructure: nav tabs, split view, theme toggle |
| 5 | Shared JS | dashboard_shared.js | Add theme toggle (setTheme, toggleTheme, localStorage) |
| 6 | Task JS | task_dashboard.js | Update all render functions for new HTML structure |
| 7 | Cleanup | theme_preview.html | Remove design exploration file |
| 8 | Verification | — | Full test suite + visual checks |
