# Dashboard Redesign: "Warm Mono" with Light/Dark Toggle

## Overview

Redesign the Golem task dashboard with the "Warm Mono" aesthetic — warm charcoal dark theme with purple accent, side-by-side DAG + task list layout, colored accent bars, icon-sized metric tiles. Include a light theme variant with a toggle button persisted to localStorage.

## Audience & Context

- **Primary users**: Engineers on the team using the dashboard daily to monitor Golem agent runs
- **Secondary**: Demos and presentations (light theme)
- **Tech stack**: Vanilla JS/CSS/HTML, no build step, served by FastAPI

## Layout Structure

```
┌─────────────────────────────────────────────────────┐
│ [Logo] Golem   Overview  Tasks  Config    stats  ☀  │  ← top bar with nav tabs + theme toggle
├─────────────────────────────────────────────────────┤
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐│
│ │ [3] Act  │ │ [8] Done │ │ [1] Fail │ │ [$] Cost ││  ← metrics row: icon tile + label/value
│ └──────────┘ └──────────┘ └──────────┘ └──────────┘│
│ ┌────────────────────┐ ┌───────────────────────────┐│
│ │                    │ │ Tasks          [filter...] ││
│ │   DAG Graph        │ │ ▎ #042  Fix auth...  done ││  ← split view: DAG left, task list right
│ │                    │ │ ▎ #043  Refactor...  run  ││     task rows have colored left accent
│ │                    │ │ ▎ #044  Fix NPE...   fail ││
│ │                    │ │ ▎ #045  Update CO... run  ││
│ └────────────────────┘ └───────────────────────────┘│
└─────────────────────────────────────────────────────┘
```

### Task Detail View (when a task is selected)

Same structure as current: back button → sticky header → metrics → pipeline view (waterfall/log/live) → info tabs (errors/tools/coordination/raw). Restyled with Warm Mono aesthetic.

## Design System

### Typography

- **Body**: DM Sans (weights: 400, 500, 600, 700)
- **Data/monospace**: JetBrains Mono (weights: 400, 500)
- **Base size**: 0.82rem for body text
- **Scale**: Labels 0.62rem, small data 0.72rem, body 0.82rem, headings 0.95rem, big numbers 1.15rem

### Border Radius

- Panels/cards: 14px
- Nav items/badges/pills: 8px
- Metric icon tiles: 10px
- Inputs/search: 8px

### Spacing

- Top bar padding: 0.65rem 1.5rem
- Content area padding: 1.25rem 1.5rem
- Card internal padding: 0.85rem 1rem
- Gap between panels: 1rem
- Gap between metric cards: 0.75rem

### Color Tokens (CSS Custom Properties)

**Dark theme (default):**

```css
:root, [data-theme="dark"] {
  --bg-base: #19181c;
  --bg-surface: #1f1e24;
  --bg-elevated: #2a2930;
  --bg-hover: #302f38;
  --bg-terminal: #14131a;
  --border: #2a2930;
  --border-active: #3a3944;
  --border-focus: #c084fc;
  --text-primary: #e4e2eb;
  --text-secondary: #a09ca8;
  --text-muted: #6e6a78;
  --text-dim: #5a5664;
  --accent: #c084fc;         /* purple — primary accent */
  --accent-bg: rgba(192,132,252,0.12);
  --green: #6ee7b7;
  --green-bg: rgba(110,231,183,0.10);
  --red: #fca5a5;
  --red-bg: rgba(252,165,165,0.10);
  --yellow: #fbbf24;
  --yellow-bg: rgba(251,191,36,0.10);
  --blue: #93c5fd;
  --blue-bg: rgba(147,197,253,0.12);
}
```

**Light theme:**

```css
[data-theme="light"] {
  --bg-base: #f5f3ef;
  --bg-surface: #fff;
  --bg-elevated: #f0ede8;
  --bg-hover: #eae7e1;
  --bg-terminal: #faf9f6;
  --border: #e0ddd6;
  --border-active: #d0ccc4;
  --border-focus: #7c3aed;
  --text-primary: #1e1e24;
  --text-secondary: #5a5664;
  --text-muted: #8a8690;
  --text-dim: #a09ca8;
  --accent: #7c3aed;
  --accent-bg: rgba(124,58,237,0.08);
  --green: #059669;
  --green-bg: rgba(5,150,105,0.08);
  --red: #dc2626;
  --red-bg: rgba(220,38,38,0.08);
  --yellow: #d97706;
  --yellow-bg: rgba(217,119,6,0.08);
  --blue: #2563eb;
  --blue-bg: rgba(37,99,235,0.08);
}
```

### Component Styles

**Top bar**: Solid surface background, bottom border, contains brand + nav tabs + stats + theme toggle. Nav tabs are rounded pills (8px radius), active tab gets elevated background.

**Metric cards**: Horizontal row of 4 cards. Each has a colored icon tile (36x36px, 10px radius) with the count/symbol inside, plus label and value text beside it.

**DAG panel**: Bordered panel (14px radius) with header bar (title + filter pills) and SVG body. Dot-grid background pattern using the accent color at low opacity. Collapsible.

**Task list**: Bordered panel (14px radius) with header (title + search input) and rows. Each row has a 3px left border colored by state (accent for running, green for completed, red for failed). Contains: ID, subject, badge, cost.

**Badges**: Rounded (8px), background uses state color at low opacity, text uses state color.

**Theme toggle**: Small button in top bar. Shows sun icon for light, moon for dark. Toggles `data-theme` attribute on `<html>` and saves to localStorage.

## Files to Modify

| File | Changes |
|------|---------|
| `golem/core/dashboard_shared.css` | Rewrite: new CSS custom properties, base styles, theme switching, shared components |
| `golem/core/task_dashboard.css` | Rewrite: all component styles with new design system |
| `golem/core/task_dashboard.html` | Restructure: add nav tabs, theme toggle, restructure metrics/task layout |
| `golem/core/task_dashboard.js` | Update rendering functions to emit new HTML; add theme toggle JS |
| `golem/core/dashboard_shared.js` | Add theme toggle utility |

## Files NOT Changed

- `golem/core/dashboard.py` — backend unchanged
- `golem/core/elk.min.js` — ELK layout library unchanged
- `golem/core/admin.html` — admin panel separate

## Responsive Behavior

- **> 900px**: Side-by-side DAG + task list (current design)
- **700-900px**: DAG and task list stack vertically, DAG height reduced
- **< 700px**: Metrics stack 2x2, nav tabs collapse, duration/deps columns hidden

## Functionality Preserved

All existing features work identically:
- DAG rendering, filtering, zooming, collapsing, minimap, node hover highlighting
- Task table sorting, filtering, selection
- Task detail: waterfall, log, live terminal, event expansion
- Pipeline views, phase banners, validation cards
- Config bar, live bar stats, auto-refresh polling

## Testing

- Existing `golem/tests/test_dashboard.py` must continue to pass
- Add tests for: theme toggle HTML presence, light/dark CSS variable definitions
- Manual visual testing at 1280x720, 1920x1080, and 320px mobile
