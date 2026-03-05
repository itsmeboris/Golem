# Dashboard Polish V2 — Playwright-Found Issues

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix three visual/UX issues found during Playwright visual testing.

**Architecture:** Pure CSS/JS fixes in task_dashboard.css and task_dashboard.js. No backend changes.

**Tech Stack:** CSS, vanilla JS, pytest for unit tests.

---

### Task 1: Make DAG section collapsible to give task table more space

The DAG at `max-height: 45vh` dominates the viewport at 1280x720. The first table row is barely visible without scrolling. Add a collapse/expand toggle to the DAG toolbar so users can hide the DAG when they want to focus on the table.

**Files:**
- Modify: `golem/core/task_dashboard.html:24` (add collapse button)
- Modify: `golem/core/task_dashboard.css:24-53` (add collapsed state)
- Modify: `golem/core/task_dashboard.js` (add toggle function)
- Test: `golem/tests/test_dashboard.py`

**Step 1: Write the failing test**

Add to `golem/tests/test_dashboard.py` after the `test_dashboard_html_has_dag_filters` test:

```python
def test_dashboard_html_has_dag_collapse(self):
    html = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.html"
    body = html.read_text(encoding="utf-8")
    assert "dag-collapse-btn" in body
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest golem/tests/test_dashboard.py::TestDashboardRoutes::test_dashboard_html_has_dag_collapse -v`
Expected: FAIL with `AssertionError`

**Step 3: Add the collapse button to HTML**

In `golem/core/task_dashboard.html`, inside `<div class="dag-controls">` (line ~33), add as the first child:

```html
<button class="dag-ctrl-btn dag-collapse-btn" onclick="toggleDagCollapse()" title="Toggle DAG">&#9650;</button>
```

**Step 4: Add CSS for collapsed state**

In `golem/core/task_dashboard.css`, after the `.dag-container:active` rule (line ~54), add:

```css
.dag-section.collapsed .dag-container{max-height:0;min-height:0;border:none;margin:0;overflow:hidden;transition:max-height 0.2s ease}
.dag-section.collapsed .dag-minimap,.dag-section.collapsed .dag-minimap-toggle{display:none}
.dag-collapse-btn{transition:transform 0.2s}
.dag-section.collapsed .dag-collapse-btn{transform:rotate(180deg)}
```

Also wrap the DAG toolbar + container in a section div. In `task_dashboard.html`, wrap lines 23-48 (from `<!-- DAG Filter Bar -->` through closing `</div>` of dag-container) in:

```html
<div class="dag-section" id="dag-section">
  <!-- existing dag-toolbar and dag-container here -->
</div>
```

**Step 5: Add JS toggle function**

In `golem/core/task_dashboard.js`, add near the DAG state variables (after line ~34):

```javascript
let _dagCollapsed = false;

function toggleDagCollapse() {
  _dagCollapsed = !_dagCollapsed;
  document.getElementById('dag-section').classList.toggle('collapsed', _dagCollapsed);
}
```

**Step 6: Run test to verify it passes**

Run: `python -m pytest golem/tests/test_dashboard.py::TestDashboardRoutes::test_dashboard_html_has_dag_collapse -v`
Expected: PASS

**Step 7: Run full dashboard test suite**

Run: `python -m pytest golem/tests/test_dashboard.py -v`
Expected: All tests PASS

**Step 8: Commit**

```bash
git add golem/core/task_dashboard.html golem/core/task_dashboard.css golem/core/task_dashboard.js golem/tests/test_dashboard.py
git commit -m "feat(dashboard): add collapsible DAG section toggle"
```

---

### Task 2: Fix table header overflow at narrow mobile widths

At 320px viewport width, the "DURATION" column header text overflows. The existing `@media(max-width:700px)` rule hides `.tt-duration` cells in rows but doesn't hide the corresponding header column. Also the header grid still has 7 columns while rows switch to 5.

**Files:**
- Modify: `golem/core/task_dashboard.css:456-466` (fix responsive media query)
- Test: `golem/tests/test_dashboard.py`

**Step 1: Write the failing test**

Add to `golem/tests/test_dashboard.py`:

```python
def test_css_responsive_hides_header_columns(self):
    """Mobile responsive rule hides duration/deps in both header and rows."""
    css = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.css"
    body = css.read_text(encoding="utf-8")
    assert ".tt-header .tt-duration" in body or ".tt-header span:nth-child(6)" in body or ".tt-header .hide-mobile" in body, \
        "Responsive CSS should hide duration column in header too"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest golem/tests/test_dashboard.py::TestDashboardRoutes::test_css_responsive_hides_header_columns -v`
Expected: FAIL

**Step 3: Fix the responsive media query**

In `golem/core/task_dashboard.css`, replace the `@media(max-width:700px)` block (lines 457-466) with:

```css
@media(max-width:700px){
  .dag-container{max-height:35vh;margin:0 0.5rem}
  .live-bar{padding:0.5rem 0.75rem;font-size:0.75rem}
  .tt-header,.tt-row{grid-template-columns:28px 40px 1fr 70px 60px}
  .tt-header span:nth-child(6),.tt-header span:nth-child(7),
  .tt-row .tt-duration,.tt-row .tt-deps{display:none}
  .wf-table{font-size:0.78rem}
  .wf-row{min-height:36px;padding:0.35rem 0.5rem}
  .wf-row.wf-child{padding-left:1.5rem}
  .wf-event{padding:0.3rem 0.75rem;font-size:0.78rem}
}
```

The key fix is adding `.tt-header span:nth-child(6),.tt-header span:nth-child(7)` to hide the Duration and Deps header labels on mobile.

**Step 4: Run test to verify it passes**

Run: `python -m pytest golem/tests/test_dashboard.py::TestDashboardRoutes::test_css_responsive_hides_header_columns -v`
Expected: PASS

**Step 5: Run full dashboard test suite**

Run: `python -m pytest golem/tests/test_dashboard.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add golem/core/task_dashboard.css golem/tests/test_dashboard.py
git commit -m "fix(dashboard): hide duration/deps header columns on mobile"
```

---

### Task 3: Ensure task detail header is visible above the fold

When clicking into a task detail at 1280x720, the waterfall takes focus and the task header (ID, state badge, subject) plus metrics row may not be prominently visible. Make the back button and header sticky so they're always visible as you scroll through the waterfall.

**Files:**
- Modify: `golem/core/task_dashboard.css:164-184` (make header sticky)
- Test: `golem/tests/test_dashboard.py`

**Step 1: Write the failing test**

Add to `golem/tests/test_dashboard.py`:

```python
def test_css_task_header_sticky(self):
    """Task detail header should be sticky so it's always visible."""
    css = Path(__file__).resolve().parent.parent / "core" / "task_dashboard.css"
    body = css.read_text(encoding="utf-8")
    assert "task-header" in body and "sticky" in body, \
        "Task header should use sticky positioning"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest golem/tests/test_dashboard.py::TestDashboardRoutes::test_css_task_header_sticky -v`
Expected: FAIL (the `.task-back` is sticky but `.task-header` is not)

**Step 3: Make the task header sticky**

In `golem/core/task_dashboard.css`, replace the `.task-back` and `.task-header` rules (lines 166-173):

```css
.task-back{
  position:sticky;top:0;z-index:10;
  background:var(--bg-base);
  padding:0.5rem 0 0
}
.task-header{
  position:sticky;top:2rem;z-index:9;
  background:var(--bg-base);
  margin-bottom:1.25rem;padding-bottom:0.5rem;
  border-bottom:1px solid var(--border)
}
```

This makes the back button stick at top:0 and the header stick just below it at top:2rem. The header gets a bottom border to visually separate it from the scrolling content below.

**Step 4: Run test to verify it passes**

Run: `python -m pytest golem/tests/test_dashboard.py::TestDashboardRoutes::test_css_task_header_sticky -v`
Expected: PASS

**Step 5: Run full dashboard test suite**

Run: `python -m pytest golem/tests/test_dashboard.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add golem/core/task_dashboard.css golem/tests/test_dashboard.py
git commit -m "fix(dashboard): make task detail header sticky for visibility"
```

---

### Final Verification

Run the full test suite:

```bash
python -m pytest golem/tests/ -v --tb=short
```

All tests should pass. Then start the dashboard server and visually verify:

1. **DAG collapse**: Click the ▲ button in DAG controls → DAG section collapses with animation. Click again → expands. Table should fill the freed space.
2. **Mobile 320px**: Duration and Deps columns fully hidden in both header and rows. No text overflow.
3. **Task detail header**: Click any task → header (#ID, badge, subject) stays pinned at top as you scroll through waterfall/accordion content.
