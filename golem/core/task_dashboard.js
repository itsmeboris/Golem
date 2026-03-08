/* ── Helpers ───────────────────────────────────────────────── */
function fmtTermTs(ts) {
  if (!ts) return '';
  try {
    /* Handle both Unix epoch seconds (number) and ISO strings */
    const d = typeof ts === 'number' ? new Date(ts * 1000) : new Date(ts);
    return d.toLocaleTimeString(undefined, {hour:'2-digit',minute:'2-digit',second:'2-digit'});
  } catch(e) { return ''; }
}

/* ── State ─────────────────────────────────────────────────── */
let _sessions = {};
let _selectedId = null;
let _traceCache = {};
let _filterPreset = 'all'; /* 'all' | 'agent' | 'tools' | 'errors' */
let _prevFingerprints = {};
let _liveSnap = {};

/* Pipeline view state */
let _pipelineView = 'waterfall'; /* 'waterfall' | 'log' | 'live' */
let _expandedStages = new Set();
let _stageFingerprint = '';
let _selectedStageId = null;
let _liveAutoScroll = true;
let _liveEventCount = 0;

/* DAG state */
let _dagFilter = ''; /* '' | 'active' | 'failed' | 'completed' */
let _dagGroupFilter = ''; /* '' or a group_id string */
let _dagTransform = { x: 0, y: 0, scale: 1 };
let _dagLayout = null; /* cached ELK layout result */
let _dagFingerprint = '';
let _dagRenderFingerprint = '';
let _hoveredNodeId = null;
let _dagSelectedTasks = new Set();
let _dragNode = null; /* { taskId, el, startMX, startMY, origTX, origTY } */
let _dagCollapsed = false;

function toggleDagCollapse() {
  _dagCollapsed = !_dagCollapsed;
  document.getElementById('dag-panel').classList.toggle('collapsed', _dagCollapsed);
}

/* Table state */
let _tableSortCol = 'id';
let _tableSortAsc = false;

/* ── Top Stats ────────────────────────────────────────────── */
function renderTopStats() {
  const el = document.getElementById('top-stats');
  const active = _liveSnap.active_count || 0;
  const queue = _liveSnap.queue_depth || 0;
  const models = _liveSnap.models_active || {};
  const uptime = _liveSnap.uptime_s || 0;

  const entries = Object.entries(_sessions);
  let totalCost = 0, completed = 0, failed = 0;
  for (const [, s] of entries) {
    totalCost += (s.total_cost_usd || 0) + (s.validation_cost_usd || 0);
    if (s.state === 'completed') completed++;
    if (s.state === 'failed') failed++;
  }

  const dotCls = active > 0 ? 'active' : 'idle';
  const modelStr = Object.entries(models).map(([m, c]) => `${m}\u00D7${c}`).join(' ');

  let html = '';
  html += `<span class="lb-stat"><span class="lb-dot ${dotCls}"></span>${active} running</span>`;
  if (queue > 0) html += `<span class="lb-stat">\u25E6 ${queue} queued</span>`;
  if (modelStr) html += `<span class="lb-sep"></span><span class="lb-stat">${esc(modelStr)}</span>`;
  html += `<span class="lb-sep"></span><span class="lb-stat">${fmtCost(totalCost)} spent</span>`;
  html += `<span class="lb-stat">${completed}\u2713 ${failed}\u2717</span>`;
  if (uptime > 0) html += `<span class="lb-sep"></span><span class="lb-stat">\u2191${fmtUptime(uptime)}</span>`;

  el.innerHTML = html;
}

/* ── Task List ────────────────────────────────────────────── */
function renderTaskList() {
  const container = document.getElementById('task-list');
  const search = (document.getElementById('table-search').value || '').toLowerCase();
  const stateFilter = document.getElementById('table-state-filter').value;
  const entries = Object.entries(_sessions);

  if (!entries.length) {
    container.innerHTML = '<div class="wf-events-empty">No sessions yet.</div>';
    return;
  }

  /* Filter */
  let filtered = entries.filter(([id, s]) => {
    if (stateFilter && s.state !== stateFilter) return false;
    if (search) {
      const hay = `#${id} ${s.parent_subject || ''} ${s.state || ''}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });

  /* Sort */
  filtered.sort(([aId, a], [bId, b]) => {
    let va, vb;
    switch (_tableSortCol) {
      case 'id': va = parseInt(aId); vb = parseInt(bId); break;
      case 'subject': va = (a.parent_subject || '').toLowerCase(); vb = (b.parent_subject || '').toLowerCase(); break;
      case 'state': va = a.state || ''; vb = b.state || ''; break;
      case 'cost': va = (a.total_cost_usd || 0) + (a.validation_cost_usd || 0); vb = (b.total_cost_usd || 0) + (b.validation_cost_usd || 0); break;
      case 'duration': va = a.duration_seconds || 0; vb = b.duration_seconds || 0; break;
      case 'deps': va = (a.depends_on || []).length; vb = (b.depends_on || []).length; break;
      default: va = parseInt(aId); vb = parseInt(bId);
    }
    const cmp = va < vb ? -1 : va > vb ? 1 : 0;
    return _tableSortAsc ? cmp : -cmp;
  });

  let html = '';
  for (const [id, s] of filtered) {
    const state = s.state || 'detected';
    const subject = esc(truncText((s.parent_subject || '').replace(/^\[AGENT\]\s*/, ''), 60));
    const cost = s.total_cost_usd ? fmtCost(s.total_cost_usd) : '-';
    const stateLabel = STATE_LABELS[state] || state;

    const dur = fmtDuration(liveDuration(s));

    html += `<div class="tl-row st-${state}" data-id="${id}" onclick="pulseDagNode('${id}');selectTask('${id}')"
      onmouseenter="highlightDagNode('${id}')" onmouseleave="unhighlightDagNode()">
      <span class="tl-id" title="#${id}">#${shortId(id)}</span>
      <span class="tl-subject">${subject}</span>
      <span class="tl-badge" style="${stateBadgeStyle(state)}">${stateLabel}</span>
      <span class="tl-dur">${dur}</span>
      <span class="tl-cost">${cost}</span>
    </div>`;
  }

  container.innerHTML = html;
}

function sortTable(col) {
  if (_tableSortCol === col) _tableSortAsc = !_tableSortAsc;
  else { _tableSortCol = col; _tableSortAsc = col === 'id'; }
  renderTaskList();
}

function hasMergeError(s) {
  return (s.errors || []).some(e => typeof e === 'string' && e.startsWith('merge failed'));
}

function verdictBadgeStyle(v) {
  if (v === 'PASS') return 'background:var(--green-bg);color:var(--green)';
  if (v === 'FAIL') return 'background:var(--red-bg);color:var(--red)';
  if (v === 'PARTIAL') return 'background:var(--yellow-bg);color:var(--orange)';
  return 'background:var(--yellow-bg);color:var(--yellow)';
}

/* ── Task selection & routing ──────────────────────────────── */
function selectTask(id) {
  _selectedId = id;
  _expandedStages.clear();
  _stageFingerprint = '';
  _selectedStageId = null;
  _liveEventCount = 0;
  _traceTerminalCache = {};
  _prevFingerprints = {};
  location.hash = '/task/' + id;

  document.getElementById('overview').classList.add('hidden');
  document.getElementById('task-detail').classList.remove('hidden');

  const s = _sessions[id];
  if (s) renderTaskDetail(id, s);
}

function deselectTask() {
  _selectedId = null;
  _expandedStages.clear();
  _stageFingerprint = '';
  _selectedStageId = null;
  history.pushState(null, '', location.pathname);
  document.getElementById('overview').classList.remove('hidden');
  document.getElementById('task-detail').classList.add('hidden');
  renderOverview();
}

function handleHash() {
  const hash = location.hash;
  const m = hash.match(/^#\/task\/(\d+)/);
  if (!m) { deselectTask(); return; }
  const id = m[1];
  if (_sessions[id]) selectTask(id);
  else _selectedId = id;
}

/* ── Overview rendering ────────────────────────────────────── */
function renderOverview() {
  renderTopStats();
  renderOverviewMetrics();
  renderDagLegend();
  populateGroupFilter();
  renderDagGraph();
  renderTaskList();
}

function renderOverviewMetrics() {
  const el = document.getElementById('overview-metrics');
  if (!el) return;
  const entries = Object.entries(_sessions);
  let active = 0, completed = 0, failed = 0, totalCost = 0;
  for (const [, s] of entries) {
    totalCost += (s.total_cost_usd || 0) + (s.validation_cost_usd || 0);
    if (['running', 'validating', 'retrying'].includes(s.state)) active++;
    else if (s.state === 'completed') completed++;
    else if (s.state === 'failed') failed++;
  }

  el.innerHTML = `
    <div class="mr-card"><span class="mr-dot" style="background:var(--accent-bg);color:var(--accent)">${active}</span><div class="mr-text"><span class="mr-label">Active</span><span class="mr-value">${active}</span></div></div>
    <div class="mr-card"><span class="mr-dot" style="background:var(--green-bg);color:var(--green)">${completed}</span><div class="mr-text"><span class="mr-label">Done</span><span class="mr-value">${completed}</span></div></div>
    <div class="mr-card"><span class="mr-dot" style="background:var(--red-bg);color:var(--red)">${failed}</span><div class="mr-text"><span class="mr-label">Failed</span><span class="mr-value">${failed}</span></div></div>
    <div class="mr-card"><span class="mr-dot" style="background:var(--blue-bg);color:var(--blue)">$</span><div class="mr-text"><span class="mr-label">Cost</span><span class="mr-value">${fmtCost(totalCost)}</span></div></div>
  `;
}

/* ── DAG highlight helpers (used by table hover) ──────────── */
function highlightDagNode(id) {
  _hoveredNodeId = id;
  const ancestors = new Set(), descendants = new Set();
  function findAncestors(nid) {
    const s = _sessions[nid]; if (!s) return;
    for (const dep of (s.depends_on || [])) { ancestors.add(String(dep)); findAncestors(String(dep)); }
  }
  function findDescendants(nid) {
    for (const [oid, os] of Object.entries(_sessions)) {
      if ((os.depends_on || []).includes(parseInt(nid))) { descendants.add(oid); findDescendants(oid); }
    }
  }
  findAncestors(id); findDescendants(id);
  const highlight = new Set([id, ...ancestors, ...descendants]);

  document.querySelectorAll('.dag-node').forEach(el => {
    el.classList.toggle('dimmed', !highlight.has(el.dataset.taskId));
  });
  document.querySelectorAll('.dag-edge').forEach(el => {
    const from = el.dataset.from, to = el.dataset.to;
    el.classList.toggle('dimmed', !highlight.has(from) || !highlight.has(to));
  });
}

function unhighlightDagNode() {
  _hoveredNodeId = null;
  document.querySelectorAll('.dag-node.dimmed').forEach(el => el.classList.remove('dimmed'));
  document.querySelectorAll('.dag-edge.dimmed').forEach(el => el.classList.remove('dimmed'));
}

/* DAG filter buttons */
function setDagFilter(filter) {
  _dagFilter = filter;
  document.querySelectorAll('.dag-pill').forEach(b =>
    b.classList.toggle('active', b.dataset.filter === filter));
  if (_dagLayout) renderDagSvg(_dagLayout);
}

/* DAG zoom controls */
function applyDagTransform() {
  const svg = document.getElementById('dag-svg');
  const g = svg.querySelector('.dag-root');
  if (g) g.setAttribute('transform', `translate(${_dagTransform.x},${_dagTransform.y}) scale(${_dagTransform.scale})`);
}
function dagZoomFit() {
  const svg = document.getElementById('dag-svg');
  const container = document.getElementById('dag-body');
  if (!svg || !container) { _dagTransform = { x: 20, y: 20, scale: 1 }; applyDagTransform(); return; }
  const svgW = parseFloat(svg.dataset.contentW) || 400;
  const svgH = parseFloat(svg.dataset.contentH) || 300;
  const cW = container.clientWidth;
  const cH = container.clientHeight;
  const pad = 30;
  const scale = Math.min((cW - pad * 2) / svgW, (cH - pad * 2) / svgH, 1.5);
  _dagTransform = { x: (cW - svgW * scale) / 2, y: (cH - svgH * scale) / 2, scale };
  applyDagTransform();
}
function dagZoomIn() {
  const container = document.getElementById('dag-body');
  const cx = container ? container.clientWidth / 2 : 0;
  const cy = container ? container.clientHeight / 2 : 0;
  const newScale = Math.min(3, _dagTransform.scale * 1.15);
  const ratio = newScale / _dagTransform.scale;
  _dagTransform.x = cx - ratio * (cx - _dagTransform.x);
  _dagTransform.y = cy - ratio * (cy - _dagTransform.y);
  _dagTransform.scale = newScale;
  applyDagTransform();
}
function dagZoomOut() {
  const container = document.getElementById('dag-body');
  const cx = container ? container.clientWidth / 2 : 0;
  const cy = container ? container.clientHeight / 2 : 0;
  const newScale = Math.max(0.2, _dagTransform.scale / 1.15);
  const ratio = newScale / _dagTransform.scale;
  _dagTransform.x = cx - ratio * (cx - _dagTransform.x);
  _dagTransform.y = cy - ratio * (cy - _dagTransform.y);
  _dagTransform.scale = newScale;
  applyDagTransform();
}

/* ═══════════════════════════════════════════════════════════════
   DAG GRAPH (ELK.js layout + SVG rendering)
   ═══════════════════════════════════════════════════════════════ */

/* Shared filter check: returns true if the state is filtered OUT (should dim) */
function toggleDagSelect(id) {
  if (_dagSelectedTasks.has(id)) _dagSelectedTasks.delete(id);
  else _dagSelectedTasks.add(id);
  renderTaskList();
  applyDagDimming();
}
function clearDagSelection() {
  _dagSelectedTasks.clear();
  renderTaskList();
  applyDagDimming();
}
function applyDagDimming() {
  /* Fast path: toggle .dimmed on existing SVG elements without re-rendering */
  const svgEl = document.getElementById('dag-svg');
  if (!svgEl) return;

  svgEl.querySelectorAll('.dag-node').forEach(node => {
    const taskId = node.dataset.taskId;
    const s = _sessions[taskId] || {};
    const state = s.state || 'detected';
    node.classList.toggle('dimmed', isDagFiltered(state, s, taskId));
  });

  svgEl.querySelectorAll('.dag-edge, .dag-edge-running').forEach(path => {
    const fromId = path.dataset.from;
    const toId = path.dataset.to;
    const fromS = _sessions[fromId] || {};
    const toS = _sessions[toId] || {};
    const dimmed = isDagFiltered(fromS.state || 'detected', fromS, fromId) ||
                   isDagFiltered(toS.state || 'detected', toS, toId);
    path.classList.toggle('dimmed', dimmed);
  });
}

function isDagFiltered(state, session, taskId) {
  if (_dagSelectedTasks.size > 0 && taskId && !_dagSelectedTasks.has(taskId)) return true;
  if (_dagGroupFilter && session && (session.group_id || '') !== _dagGroupFilter) return true;
  if (!_dagFilter) return false;
  if (_dagFilter === 'active') return !['running', 'validating', 'retrying', 'detected'].includes(state);
  if (_dagFilter === 'failed') return state !== 'failed';
  if (_dagFilter === 'completed') return state !== 'completed';
  return false;
}

/* Group/batch filter */
function populateGroupFilter() {
  const sel = document.getElementById('dag-group-filter');
  if (!sel) return;
  const groups = new Set();
  for (const s of Object.values(_sessions)) {
    if (s.group_id) groups.add(s.group_id);
  }
  if (groups.size === 0) { sel.classList.add('hidden'); return; }
  sel.classList.remove('hidden');
  let html = '<option value="">All groups</option>';
  for (const g of [...groups].sort()) html += `<option value="${esc(g)}"${g === _dagGroupFilter ? ' selected' : ''}>${esc(g)}</option>`;
  sel.innerHTML = html;
}
function setDagGroupFilter(group) {
  _dagGroupFilter = group;
  if (_dagLayout) renderDagSvg(_dagLayout);
}

/* Color legend */
function renderDagLegend() {
  const el = document.getElementById('dag-legend');
  if (!el) return;
  const skip = new Set(['warning', 'pending']);
  let html = '';
  for (const [state, colors] of Object.entries(DAG_COLORS)) {
    if (skip.has(state)) continue;
    const label = STATE_LABELS[state] || state;
    html += `<span class="dag-legend-item"><span class="dag-legend-dot" style="background:${colors.stroke}"></span>${label}</span>`;
  }
  el.innerHTML = html;
}

const DAG_COLORS = {
  completed: { fill: '#0a2920', stroke: '#34D399', text: '#6ee7b7', glow: 'rgba(52,211,153,0.12)', edge: 'rgba(52,211,153,0.5)' },
  running:   { fill: '#0c1a30', stroke: '#60A5FA', text: '#93bbfd', glow: 'rgba(96,165,250,0.15)', edge: 'rgba(96,165,250,0.6)' },
  failed:    { fill: '#220d0d', stroke: '#F87171', text: '#fca5a5', glow: 'rgba(248,113,113,0.12)', edge: 'rgba(248,113,113,0.5)' },
  warning:   { fill: '#221508', stroke: '#FBBF24', text: '#fde68a', glow: 'rgba(251,191,36,0.12)', edge: 'rgba(251,191,36,0.5)' },
  validating:{ fill: '#1a1040', stroke: '#a78bfa', text: '#c4b5fd', glow: 'rgba(167,139,250,0.12)', edge: 'rgba(167,139,250,0.5)' },
  detected:  { fill: '#1e1508', stroke: '#FBBF24', text: '#fde68a', glow: 'rgba(251,191,36,0.08)', edge: 'rgba(251,191,36,0.35)' },
  retrying:  { fill: '#221508', stroke: '#fb923c', text: '#fdba74', glow: 'rgba(251,146,60,0.12)', edge: 'rgba(251,146,60,0.5)' },
  pending:   { fill: '#151520', stroke: '#3B4553', text: '#6B7280', glow: 'rgba(75,85,99,0.05)', edge: 'rgba(75,85,99,0.3)' },
};

function buildElkGraph(sessions) {
  const entries = Object.entries(sessions);
  const children = [];
  const edges = [];
  const idSet = new Set(entries.map(([id]) => id));

  for (const [id, s] of entries) {
    children.push({
      id: 'n' + id,
      width: 230,
      height: 88,
      labels: [{ text: id }],
      _session: s,
      _taskId: id,
    });

    for (const depId of (s.depends_on || [])) {
      if (idSet.has(String(depId))) {
        edges.push({
          id: 'e' + depId + '-' + id,
          sources: ['n' + depId],
          targets: ['n' + id],
          _fromState: (sessions[depId] || {}).state || 'pending',
          _toState: s.state || 'detected',
        });
      }
    }
  }

  /* Group by group_id */
  const groups = new Map();
  for (const child of children) {
    const gid = child._session.group_id;
    if (gid) {
      if (!groups.has(gid)) groups.set(gid, []);
      groups.get(gid).push(child);
    }
  }

  /* If groups exist, wrap them in compound nodes */
  const topChildren = [];
  const usedIds = new Set();
  for (const [gid, members] of groups) {
    if (members.length > 1) {
      topChildren.push({
        id: 'g-' + gid,
        children: members,
        labels: [{ text: gid }],
        layoutOptions: { 'elk.padding': '[top=28,left=12,bottom=12,right=12]' },
      });
      for (const m of members) usedIds.add(m.id);
    }
  }
  for (const child of children) {
    if (!usedIds.has(child.id)) topChildren.push(child);
  }

  return {
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.spacing.nodeNode': '30',
      'elk.layered.spacing.nodeNodeBetweenLayers': '60',
      'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
      'elk.edgeRouting': 'SPLINES',
    },
    children: topChildren,
    edges: edges,
  };
}

let _elk = null;

async function layoutDag(graph) {
  if (!_elk && typeof ELK !== 'undefined') _elk = new ELK();
  if (!_elk) return null;
  return await _elk.layout(graph);
}

async function renderDagGraph() {
  const entries = Object.entries(_sessions);
  if (!entries.length) {
    document.getElementById('dag-empty').classList.remove('hidden');
    document.getElementById('dag-svg').innerHTML = '';
    return;
  }
  document.getElementById('dag-empty').classList.add('hidden');

  /* Fingerprint check to avoid re-layout and unnecessary re-render */
  const fp = entries.map(([id, s]) => id + s.state + (s.depends_on || []).join(',')).join('|');
  const renderFp = fp + '|' + _dagFilter + '|' + _dagGroupFilter + '|' + _hoveredNodeId;
  if (fp === _dagFingerprint && _dagLayout && renderFp === _dagRenderFingerprint) {
    return; /* Nothing changed — skip SVG rebuild to preserve pan/zoom state */
  }
  if (fp === _dagFingerprint && _dagLayout) {
    _dagRenderFingerprint = renderFp;
    renderDagSvg(_dagLayout);
    return;
  }

  const graph = buildElkGraph(_sessions);
  try {
    _dagLayout = await layoutDag(graph);
    if (!_dagLayout) return;
    _dagFingerprint = fp;
    _dagRenderFingerprint = renderFp;
    renderDagSvg(_dagLayout);
    dagZoomFit();
  } catch (e) {
    console.error('ELK layout failed:', e);
  }
}

function collectElkNodes(node, offsetX, offsetY, result) {
  /* Recursively collect all leaf nodes from ELK layout, applying parent offsets */
  if (node.children) {
    for (const child of node.children) {
      if (child.children) {
        /* Compound node — recurse with accumulated offset */
        collectElkNodes(child, offsetX + (child.x || 0), offsetY + (child.y || 0), result);
      } else {
        result.push({
          id: child.id,
          x: offsetX + (child.x || 0),
          y: offsetY + (child.y || 0),
          w: child.width || 180,
          h: child.height || 72,
          _session: child._session,
          _taskId: child._taskId,
        });
      }
    }
  }
}

function collectElkEdges(layout, result) {
  if (layout.edges) {
    for (const e of layout.edges) result.push(e);
  }
  if (layout.children) {
    for (const child of layout.children) collectElkEdges(child, result);
  }
}

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

function renderDagSvg(layout) {
  const svgEl = document.getElementById('dag-svg');

  /* Collect all leaf nodes */
  const nodes = [];
  collectElkNodes(layout, 0, 0, nodes);
  const edges = [];
  collectElkEdges(layout, edges);

  /* Build node lookup */
  const nodeMap = new Map();
  for (const n of nodes) nodeMap.set(n.id, n);

  /* Compute SVG dimensions */
  let maxX = 0, maxY = 0;
  for (const n of nodes) {
    if (n.x + n.w > maxX) maxX = n.x + n.w;
    if (n.y + n.h > maxY) maxY = n.y + n.h;
  }
  const svgW = maxX + 40;
  const svgH = maxY + 40;
  /* SVG fills the container; pan/zoom is handled by the dag-root transform.
     Store content dimensions as data attributes for dagZoomFit(). */
  svgEl.setAttribute('width', '100%');
  svgEl.setAttribute('height', '100%');
  svgEl.removeAttribute('viewBox');
  svgEl.dataset.contentW = svgW;
  svgEl.dataset.contentH = svgH;

  let svg = '';

  /* Defs */
  svg += '<defs>';
  for (const [state, colors] of Object.entries(DAG_COLORS)) {
    svg += `<filter id="dag-glow-${state}" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="6" result="blur"/>
      <feFlood flood-color="${colors.glow}" result="color"/>
      <feComposite in="color" in2="blur" operator="in" result="shadow"/>
      <feMerge><feMergeNode in="shadow"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>`;
    svg += `<marker id="dag-arrow-${state}" viewBox="0 0 8 6" refX="7" refY="3" markerWidth="7" markerHeight="5" orient="auto-start-reverse">
      <path d="M0,0 L8,3 L0,6 Z" fill="${colors.edge || colors.stroke}" opacity="0.8"/>
    </marker>`;
  }
  svg += `<style>
    @keyframes dag-dash { to { stroke-dashoffset: -20; } }
    .dag-edge-running { animation: dag-dash 1s linear infinite; }
    @keyframes dag-pulse { 0%,100% { opacity: 0.4; } 50% { opacity: 1; } }
    .dag-running-ring { animation: dag-pulse 2s ease-in-out infinite; }
  </style>`;
  svg += '</defs>';

  svg += '<g class="dag-root">';

  /* Render group clusters */
  const groups = collectElkGroups(layout);
  for (const g of groups) {
    svg += `<g class="dag-cluster">
      <rect x="${g.x}" y="${g.y}" width="${g.w}" height="${g.h}"/>
      ${g.label ? `<text x="${g.x + 8}" y="${g.y + 16}">${esc(g.label)}</text>` : ''}
    </g>`;
  }

  /* Render edges */
  for (const edge of edges) {
    const fromId = (edge.sources || [])[0];
    const toId = (edge.targets || [])[0];
    const fromN = nodeMap.get(fromId);
    const toN = nodeMap.get(toId);
    if (!fromN || !toN) continue;

    const toSession = toN._session || {};
    const edgeState = toSession.state || 'pending';
    const colors = DAG_COLORS[edgeState] || DAG_COLORS.pending;
    const isRunning = edgeState === 'running';

    /* Connection points: right edge of source → left edge of target */
    const sx = fromN.x + fromN.w, sy = fromN.y + fromN.h / 2;
    const ex = toN.x, ey = toN.y + toN.h / 2;

    /* Apply drag offsets if nodes were moved */
    const fromOff = _dragOffsets.get(fromN._taskId) || { dx: 0, dy: 0 };
    const toOff = _dragOffsets.get(toN._taskId) || { dx: 0, dy: 0 };
    const pathD = bezierPath(sx + fromOff.dx, sy + fromOff.dy, ex + toOff.dx, ey + toOff.dy);

    /* Check if edge should be dimmed by DAG filter or group filter */
    const fromSession = fromN._session || {};
    const fromState = fromSession.state || 'detected';
    const toState = toSession.state || 'detected';
    const edgeDimmed = isDagFiltered(fromState, fromSession, fromN._taskId) || isDagFiltered(toState, toSession, toN._taskId);
    const dimCls = edgeDimmed ? ' dimmed' : '';
    const dashAttrs = isRunning ? ` stroke-dasharray="6 4" class="dag-edge dag-edge-running${dimCls}"` : ` class="dag-edge${dimCls}"`;
    svg += `<path d="${pathD}" fill="none" stroke="${colors.edge || colors.stroke}" stroke-width="1.2"${dashAttrs}
      marker-end="url(#dag-arrow-${edgeState})"
      data-from="${fromN._taskId}" data-to="${toN._taskId}"
      data-sx="${sx}" data-sy="${sy}" data-ex="${ex}" data-ey="${ey}"><title>#${fromN._taskId} → #${toN._taskId}</title></path>`;
  }

  /* Render nodes */
  for (const node of nodes) {
    const s = node._session || {};
    const state = s.state || 'detected';
    const colors = DAG_COLORS[state] || DAG_COLORS.pending;
    const taskId = node._taskId;

    /* Apply DAG filter */
    const dimmed = isDagFiltered(state, s, taskId);

    const subject = truncText((s.parent_subject || '').replace(/^\[AGENT\]\s*/, ''), 28);
    const cost = s.total_cost_usd ? fmtCost(s.total_cost_usd) : '';
    const dur = fmtDuration(liveDuration(s));
    const stateLabel = STATE_LABELS[state] || state;
    const subInfo = [dur, cost].filter(Boolean).join(' \u00B7 ');

    /* Phase / last activity line */
    const phase = s.supervisor_phase || '';
    const activity = s.last_activity || '';
    const phaseText = phase ? phase : (activity ? truncText(activity, 30) : '');

    const dragOff = _dragOffsets.get(taskId) || { dx: 0, dy: 0 };
    const txAttr = (dragOff.dx || dragOff.dy) ? ` transform="translate(${dragOff.dx},${dragOff.dy})"` : '';
    svg += `<g class="dag-node${dimmed ? ' dimmed' : ''}" data-task-id="${taskId}" data-orig-x="${node.x}" data-orig-y="${node.y}"${txAttr}
      onmouseenter="highlightDagNode('${taskId}')" onmouseleave="unhighlightDagNode()">`;

    /* Node rect — subtle border, soft glow */
    svg += `<rect class="dag-node-rect" x="${node.x}" y="${node.y}" width="${node.w}" height="${node.h}"
      rx="8" fill="${colors.fill}" stroke="${colors.stroke}" stroke-width="1" stroke-opacity="0.5"
      filter="url(#dag-glow-${state})"/>`;

    /* Running pulse ring */
    if (state === 'running') {
      svg += `<rect class="dag-running-ring" x="${node.x - 2}" y="${node.y - 2}"
        width="${node.w + 4}" height="${node.h + 4}" rx="10"
        fill="none" stroke="${colors.stroke}" stroke-width="0.8" opacity="0.35"/>`;
    }

    /* ID + state label */
    const textX = node.x + 12;
    const labelY = node.y + 18;
    svg += `<text x="${textX}" y="${labelY}" fill="${colors.text}" font-size="11" font-weight="700"
      font-family="system-ui,sans-serif">#${shortId(taskId)}</text>`;
    svg += `<text x="${node.x + node.w - 10}" y="${labelY}" fill="${colors.stroke}" font-size="8"
      font-weight="600" text-anchor="end" opacity="0.7" text-transform="uppercase" letter-spacing="0.5"
      font-family="system-ui,sans-serif">${stateLabel}</text>`;

    /* Subject */
    svg += `<text x="${textX}" y="${node.y + 33}" fill="${colors.text}" font-size="10" opacity="0.65"
      font-family="system-ui,sans-serif">${esc(subject)}</text>`;

    /* Phase / last activity */
    if (phaseText) {
      svg += `<text x="${textX}" y="${node.y + 48}" fill="${colors.stroke}" font-size="9" opacity="0.55"
        font-family="system-ui,sans-serif" font-style="italic">${esc(phaseText)}</text>`;
    }

    /* Sub-info (duration · cost) */
    if (subInfo) {
      svg += `<text x="${textX}" y="${node.y + 63}" fill="${colors.text}" font-size="9" opacity="0.4"
        font-family="system-ui,sans-serif">${esc(subInfo)}</text>`;
    }

    /* Progress bar for running */
    if (state === 'running') {
      const barY = node.y + node.h - 5;
      const barW = node.w - 16;
      svg += `<rect x="${node.x + 8}" y="${barY}" width="${barW}" height="1.5" rx="1" fill="${colors.stroke}" opacity="0.12"/>`;
      svg += `<rect x="${node.x + 8}" y="${barY}" width="${barW * 0.6}" height="1.5" rx="1" fill="${colors.stroke}" opacity="0.45">
        <animate attributeName="width" from="0" to="${barW}" dur="3s" repeatCount="indefinite"/>
      </rect>`;
    }

    svg += '</g>';
  }

  svg += '</g>';
  svgEl.innerHTML = svg;

  /* Minimap */
  renderDagMinimap(svgEl, nodes.length);
}

function renderDagMinimap(svgEl, nodeCount) {
  const minimap = document.getElementById('dag-minimap');
  const toggle = document.getElementById('dag-minimap-toggle');
  /* Auto-hide for small DAGs */
  if (nodeCount < 6) {
    if (minimap) minimap.classList.add('hidden');
    if (toggle) toggle.classList.add('hidden');
    return;
  }
  if (toggle) toggle.classList.remove('hidden');
  if (minimap && !minimap.classList.contains('user-hidden')) minimap.classList.remove('hidden');

  const clone = svgEl.cloneNode(true);
  /* Strip animations and interactivity */
  clone.querySelectorAll('animate').forEach(el => el.remove());
  clone.querySelectorAll('[onclick]').forEach(el => el.removeAttribute('onclick'));
  clone.querySelectorAll('[onmouseenter]').forEach(el => el.removeAttribute('onmouseenter'));
  /* Minimap needs a viewBox to show full content, and no pan/zoom transform */
  const cW = svgEl.dataset.contentW || 400;
  const cH = svgEl.dataset.contentH || 300;
  clone.setAttribute('viewBox', `0 0 ${cW} ${cH}`);
  clone.removeAttribute('width');
  clone.removeAttribute('height');
  clone.style.width = '100%';
  clone.style.height = '100%';
  const rootG = clone.querySelector('.dag-root');
  if (rootG) rootG.removeAttribute('transform');
  minimap.innerHTML = '';
  minimap.appendChild(clone);
}

function toggleMinimap() {
  const minimap = document.getElementById('dag-minimap');
  const toggle = document.getElementById('dag-minimap-toggle');
  if (!minimap) return;
  const isHidden = minimap.classList.toggle('hidden');
  minimap.classList.toggle('user-hidden', isHidden);
  if (toggle) toggle.classList.toggle('minimap-off', isHidden);
}

/* ── Drag offset persistence ─────────────────────────────── */
let _dragOffsets = new Map(); /* taskId → { dx, dy } */

function updateEdgesForNode(taskId) {
  /* Recompute all edges connected to this node using stored connection points */
  const svgEl = document.getElementById('dag-svg');
  if (!svgEl) return;

  svgEl.querySelectorAll(`path[data-from="${taskId}"], path[data-to="${taskId}"]`).forEach(path => {
    const fromId = path.dataset.from;
    const toId = path.dataset.to;
    const sx = parseFloat(path.dataset.sx);
    const sy = parseFloat(path.dataset.sy);
    const ex = parseFloat(path.dataset.ex);
    const ey = parseFloat(path.dataset.ey);

    /* Apply drag offsets */
    const fromOff = _dragOffsets.get(fromId) || { dx: 0, dy: 0 };
    const toOff = _dragOffsets.get(toId) || { dx: 0, dy: 0 };
    const x1 = sx + fromOff.dx, y1 = sy + fromOff.dy;
    const x2 = ex + toOff.dx, y2 = ey + toOff.dy;

    path.setAttribute('d', bezierPath(x1, y1, x2, y2));
  });
}

function bezierPath(x1, y1, x2, y2) {
  const cp = Math.max(Math.abs(x2 - x1) * 0.4, 30);
  return `M${x1},${y1} C${x1 + cp},${y1} ${x2 - cp},${y2} ${x2},${y2}`;
}

function initDagPanZoom() {
  const container = document.getElementById('dag-body');
  if (!container) return;
  let isPanning = false, startX, startY;

  container.addEventListener('wheel', (e) => {
    e.preventDefault();
    const direction = e.deltaY > 0 ? -1 : 1;
    const factor = Math.pow(1.002, direction * Math.min(Math.abs(e.deltaY), 60));
    const newScale = Math.max(0.2, Math.min(3, _dagTransform.scale * factor));
    const rect = container.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    const ratio = newScale / _dagTransform.scale;
    _dagTransform.x = cx - ratio * (cx - _dagTransform.x);
    _dagTransform.y = cy - ratio * (cy - _dagTransform.y);
    _dagTransform.scale = newScale;
    applyDagTransform();
  }, { passive: false });

  container.addEventListener('mousedown', (e) => {
    const nodeEl = e.target.closest('.dag-node');
    if (nodeEl) {
      /* Start node drag */
      e.stopPropagation();
      const taskId = nodeEl.dataset.taskId;
      const prev = _dragOffsets.get(taskId) || { dx: 0, dy: 0 };
      _dragNode = { taskId, el: nodeEl, startMX: e.clientX, startMY: e.clientY, baseDX: prev.dx, baseDY: prev.dy };
      nodeEl.classList.add('dragging');
      return;
    }
    isPanning = true; startX = e.clientX - _dagTransform.x; startY = e.clientY - _dagTransform.y;
    container.style.cursor = 'grabbing';
  });

  window.addEventListener('mousemove', (e) => {
    if (_dragNode) {
      const dx = _dragNode.baseDX + (e.clientX - _dragNode.startMX) / _dagTransform.scale;
      const dy = _dragNode.baseDY + (e.clientY - _dragNode.startMY) / _dagTransform.scale;
      _dragNode.el.setAttribute('transform', `translate(${dx},${dy})`);
      _dragOffsets.set(_dragNode.taskId, { dx, dy });
      updateEdgesForNode(_dragNode.taskId);
      return;
    }
    if (!isPanning) return;
    _dagTransform.x = e.clientX - startX;
    _dagTransform.y = e.clientY - startY;
    applyDagTransform();
  });

  window.addEventListener('mouseup', (e) => {
    if (_dragNode) {
      const mx = Math.abs(e.clientX - _dragNode.startMX);
      const my = Math.abs(e.clientY - _dragNode.startMY);
      _dragNode.el.classList.remove('dragging');
      if (mx < 5 && my < 5) {
        /* Treat as click — remove any offset we just stored */
        _dragOffsets.delete(_dragNode.taskId);
        selectTask(_dragNode.taskId);
      }
      _dragNode = null;
      return;
    }
    if (isPanning) { isPanning = false; container.style.cursor = 'grab'; }
  });
}

/* ── Escape key deselects task ─────────────────────────────── */
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _selectedId) deselectTask();
});

/* ── Table-to-DAG pulse sync ──────────────────────────────── */
function pulseDagNode(id) {
  const node = document.querySelector(`.dag-node[data-task-id="${id}"]`);
  if (!node) return;
  node.classList.add('dag-node-pulse');
  setTimeout(() => node.classList.remove('dag-node-pulse'), 800);
}

/* ── Cancel task ───────────────────────────────────────────── */
const _cancelInFlight = new Set();

async function cancelTask(taskId) {
  if (!/^\d+$/.test(String(taskId))) return;
  if (_cancelInFlight.has(taskId)) return;
  if (!confirm(`Cancel task #${shortId(taskId)}? This will stop the running task.`)) return;
  _cancelInFlight.add(taskId);
  if (_selectedId && _sessions[_selectedId]) renderHeader(_selectedId, _sessions[_selectedId]);
  let ok = false;
  try {
    const res = await fetch(`/api/cancel/${encodeURIComponent(taskId)}`, { method: 'POST' });
    if (res.ok) {
      ok = true;
      if (_selectedId && _sessions[_selectedId]) renderTaskDetail(_selectedId, _sessions[_selectedId]);
    } else {
      let detail = `HTTP ${res.status}`;
      try { const body = await res.json(); detail = body.detail || detail; } catch (_) {}
      alert(`Failed to cancel task: ${detail}`);
    }
  } finally {
    _cancelInFlight.delete(taskId);
    if (!ok && _selectedId && _sessions[_selectedId]) renderHeader(_selectedId, _sessions[_selectedId]);
  }
}

/* ── Task Detail rendering ─────────────────────────────────── */
function renderTaskDetail(id, s) {
  renderHeader(id, s);
  renderMetrics(s);
  renderPhaseBanner(s);
  renderPipelineView(id, s);
  renderInfoTabs(s);
}

function renderHeader(id, s) {
  const state = s.state || 'detected';
  const subject = esc(s.parent_subject || '');
  const mode = s.execution_mode || '';
  const sha = s.commit_sha || '';
  const groupId = s.group_id || '';
  const mergeFailed = hasMergeError(s);

  let mergeHeaderBadge = '';
  if (mergeFailed) mergeHeaderBadge = '<span class="th-badge" style="background:#450a0a;color:#f87171">merge failed</span>';
  else if (s.merge_ready) mergeHeaderBadge = '<span class="th-badge" style="background:#172554;color:#60a5fa">merge queued</span>';

  const cancelable = ['detected', 'running', 'validating', 'retrying'].includes(state);
  const cancelBtn = cancelable
    ? `<button class="th-cancel-btn" onclick="cancelTask('${esc(id)}')"${_cancelInFlight.has(id) ? ' disabled' : ''}>&times; Cancel</button>`
    : '';

  $('#task-back').innerHTML = '<button class="back-btn" onclick="deselectTask()">&larr; Dashboard</button>';
  $('#task-header').innerHTML = `
    <div class="th-top">
      <span class="th-id" title="#${id}">#${shortId(id)}</span>
      ${mode ? `<span class="th-mode">${esc(mode)}</span>` : ''}
      <span class="th-badge" style="${stateBadgeStyle(state)}">${esc(state)}</span>
      ${sha ? `<span class="th-mode" title="${esc(sha)}">&#10003; ${esc(sha.slice(0, 7))}</span>` : ''}
      ${groupId ? `<span class="th-mode" title="Batch group">\u2B21 ${esc(groupId)}</span>` : ''}
      ${mergeHeaderBadge}
      ${cancelBtn}
    </div>
    <div class="th-subject">${subject}</div>`;
}

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

function renderMetrics(s) {
  const cards = [];
  if (s.state === 'detected' && s.grace_deadline) {
    cards.push({ label: 'Starts In', value: fmtCountdown(s.grace_deadline), cls: 'blue' });
  }
  cards.push(
    { label: 'Cost', value: s.total_cost_usd ? fmtCost(s.total_cost_usd) : (s.state === 'running' ? '...' : '-'), cls: '' },
    { label: 'Duration', value: fmtDuration(s.duration_seconds), cls: '' },
    { label: 'Milestones', value: s.milestone_count || 0, cls: '' },
    { label: 'Retries', value: s.retry_count || 0, cls: s.retry_count > 0 ? 'red' : '' },
  );
  if (s.infra_retry_count > 0) cards.push({ label: 'Infra Retries', value: s.infra_retry_count, cls: 'red' });
  if (s.validation_cost_usd) cards.push({ label: 'Validation Cost', value: fmtCost(s.validation_cost_usd), cls: '' });
  const deps = s.depends_on || [];
  if (deps.length) {
    const depChips = deps.map(d => {
      const ds = _sessions[d];
      const dSubject = ds ? truncText((ds.parent_subject || '').replace(/^\[AGENT\]\s*/, ''), 25) : 'Task';
      const dState = ds ? ds.state || 'pending' : 'pending';
      const dColors = DAG_COLORS[dState] || DAG_COLORS.pending;
      return `<a href="#/task/${d}" class="dep-chip" style="border-color:${dColors.stroke};color:${dColors.text}" title="#${d} (${dState})"><span class="dep-chip-dot" style="background:${dColors.stroke}"></span>#${shortId(d)} ${esc(dSubject)}</a>`;
    }).join('');
    cards.push({ label: 'Dependencies', value: depChips, cls: '', wide: true });
  }

  $('#metrics-row').innerHTML = cards.map(c =>
    `<div class="metric-card${c.wide ? ' mc-wide' : ''}"><div class="mc-label">${c.label}</div><div class="mc-value ${c.cls}">${c.value}</div></div>`
  ).join('');
}

/* ── Phase Banner ──────────────────────────────────────────── */
function renderPhaseBanner(s) {
  const container = $('#phase-banner-container');
  if (s.execution_mode !== 'subagent' || !s.state || s.state === 'detected') { container.innerHTML = ''; return; }

  const phase = s.supervisor_phase || '';

  let icon, label, detail, cls;
  const isTerminal = ['completed', 'failed'].includes(s.state);

  if (isTerminal) {
    icon = s.state === 'completed' ? '\u2713' : '\u2717';
    label = s.state === 'completed' ? 'Completed' : 'Failed';
    detail = ''; cls = 'done';
  } else {
    switch (phase) {
      case 'orchestrating': icon = '\u2699'; label = 'Orchestrating\u2026'; detail = ''; cls = 'active'; break;
      case 'validating': icon = '\uD83D\uDD0D'; label = 'Validating\u2026'; detail = ''; cls = 'active'; break;
      case 'committing': icon = '\u2B06'; label = 'Committing\u2026'; detail = ''; cls = 'active'; break;
      default: icon = '\u23F3'; label = 'Working\u2026'; detail = ''; cls = 'active';
    }
  }

  container.innerHTML = `<div class="phase-banner ${cls}">
    <span class="phase-icon">${icon}</span>
    <span class="phase-label">${label}</span>
    ${detail ? `<span class="phase-detail">${detail}</span>` : ''}
  </div>`;
}

/* ═══════════════════════════════════════════════════════════════
   PIPELINE VIEW
   ═══════════════════════════════════════════════════════════════ */

const STAGE_ICONS = {
  task: '\uD83D\uDCCB', preflight: '\u2714', orchestration: '\u2699',
  scout: '\uD83D\uDD0D', build: '\u2699', review: '\uD83D\uDCDD',
  verify: '\u2611', execution: '\u25B6', validation: '\uD83D\uDD0D',
  merge: '\u2B06', commit: '\u2714', retry: '\u21BA', failure: '\u2717'
};

/* ── Orchestration Sub-Phase Detection ───────────────────── */
function detectOrchestraPhases(events) {
  const phases = [];
  let current = { phase: 'setup', events: [], startTs: 0, endTs: 0 };
  let buildCount = 0;

  for (const ev of events) {
    const kind = ev.kind || ev.type || '';
    const summary = ev.summary || ev.text || '';
    const ts = ev.timestamp || 0;

    /* Detect phase transitions from supervisor events */
    if (kind === 'supervisor') {
      const newPhase = detectPhaseTransition(summary);
      if (newPhase) {
        if (current.events.length > 0) {
          current.endTs = ts;
          phases.push(current);
        }
        let phaseName = newPhase;
        if (phaseName === 'build' || phaseName === 'retry') {
          buildCount++;
          phaseName = buildCount > 1 ? 'build_' + buildCount : 'build';
        }
        current = { phase: phaseName, events: [ev], startTs: ts, endTs: ts };
        continue;
      }
    }

    /* Detect phase transitions from Agent subagent_type in tool_call events */
    if (kind === 'tool_call' && ev.tool_name === 'Agent') {
      const agentPhase = detectAgentPhase(summary);
      if (agentPhase && agentPhase !== current.phase) {
        if (current.events.length > 0) {
          current.endTs = ts;
          phases.push(current);
        }
        current = { phase: agentPhase, events: [ev], startTs: ts, endTs: ts };
        continue;
      }
    }

    current.events.push(ev);
    if (ts > 0 && current.startTs === 0) current.startTs = ts;
    if (ts > 0) current.endTs = ts;
  }

  if (current.events.length > 0) phases.push(current);
  return phases;
}

function detectAgentPhase(summary) {
  /* Agent summaries follow: "Agent: [subagent_type] description" */
  const m = summary.match(/^Agent:\s*\[(\w+)\]/);
  if (!m) return null;
  const sub = m[1].toLowerCase();
  if (sub === 'scout' || sub === 'explore') return 'scout';
  if (sub === 'builder' || sub === 'implementer') return 'build_agent';
  if (sub === 'reviewer') return 'review';
  if (sub === 'verifier' || sub === 'tester') return 'verify';
  return null;
}

function detectPhaseTransition(summary) {
  if (/Starting single-session orchestration/i.test(summary)) return 'build';
  if (/Warm retry|Cold retry/i.test(summary)) return 'retry';
  if (/Running external validation/i.test(summary)) return 'ext_validation';
  if (/Committing and merging/i.test(summary)) return 'committing';
  if (/Queued for merge/i.test(summary)) return 'merge_queue';
  if (/Orchestrator finished/i.test(summary)) return '_end_build';
  if (/Task completed/i.test(summary)) return '_end';
  return null;
}

/* ── Stage Grouping Engine ─────────────────────────────────── */
function computeStages(s) {
  const stages = [];
  const state = s.state || 'detected';
  const allEvents = s.event_log || [];
  const taskId = s.parent_issue_id || _selectedId;

  stages.push({ id: 'task', type: 'task', label: '#' + shortId(taskId),
    state: state === 'detected' ? 'pending' : 'completed', events: [],
    meta: { subject: s.parent_subject, mode: s.execution_mode } });

  if (state === 'detected') return stages;

  stages.push({ id: 'preflight', type: 'preflight', label: 'Preflight',
    state: 'completed', events: [],
    meta: { worktree: s.worktree_path, depends: s.depends_on } });

  const isOrchestrated = s.execution_mode === 'subagent';
  const supervisorEvents = allEvents.filter(e => (e.kind || e.type) === 'supervisor');

  if (isOrchestrated && supervisorEvents.length > 1) {
    const phases = detectOrchestraPhases(allEvents);
    let buildIdx = 0;

    let phaseIdx = 0;
    for (const ph of phases) {
      if (ph.phase.startsWith('_end') || ph.phase === 'setup') continue;
      if (ph.phase === 'ext_validation' || ph.phase === 'committing' || ph.phase === 'merge_queue') continue;

      const execState = ['completed','failed','validating','retrying'].includes(state) ? 'completed'
        : state === 'running' ? 'running' : 'pending';

      if (ph.phase === 'scout') {
        phaseIdx++;
        stages.push({
          id: 'scout_' + phaseIdx, type: 'scout', label: 'Scout',
          state: execState, events: ph.events,
          meta: { milestones: ph.events.length, startTs: ph.startTs, endTs: ph.endTs }
        });
      } else if (ph.phase === 'review') {
        phaseIdx++;
        stages.push({
          id: 'review_' + phaseIdx, type: 'review', label: 'Review',
          state: execState, events: ph.events,
          meta: { milestones: ph.events.length, startTs: ph.startTs, endTs: ph.endTs }
        });
      } else if (ph.phase === 'verify') {
        phaseIdx++;
        stages.push({
          id: 'verify_' + phaseIdx, type: 'verify', label: 'Verify',
          state: execState, events: ph.events,
          meta: { milestones: ph.events.length, startTs: ph.startTs, endTs: ph.endTs }
        });
      } else if (ph.phase.startsWith('build') || ph.phase === 'build_agent') {
        buildIdx++;
        const isRetry = buildIdx > 1;
        const phaseType = isRetry ? 'retry' : 'build';
        const suffix = buildIdx > 1 ? '\u2082' : '';
        const label = 'Build' + suffix;
        stages.push({
          id: 'build_' + buildIdx, type: phaseType, label: label,
          state: execState, events: ph.events,
          meta: { milestones: ph.events.length, cost: buildIdx === 1 ? s.total_cost_usd : 0,
            startTs: ph.startTs, endTs: ph.endTs, isRetry }
        });
      }
    }

    if (buildIdx === 0) {
      const execState = ['completed','failed','validating','retrying'].includes(state) ? 'completed'
        : state === 'running' ? 'running' : 'pending';
      stages.push({ id: 'execution', type: 'execution', label: 'Orchestrating',
        state: execState, events: allEvents,
        meta: { milestones: s.milestone_count, cost: s.total_cost_usd } });
    }
  } else {
    const execLabel = isOrchestrated ? 'Orchestrating' : 'Execution';
    const execState = ['completed','failed','validating','retrying'].includes(state) ? 'completed'
      : state === 'running' ? 'running' : 'pending';
    stages.push({ id: 'execution', type: 'execution', label: execLabel,
      state: execState, events: allEvents,
      meta: { milestones: s.milestone_count, cost: s.total_cost_usd } });
  }

  if (['validating','completed','failed','retrying'].includes(state) || s.validation_verdict) {
    const valState = state === 'validating' ? 'running'
      : s.validation_verdict === 'PASS' ? 'completed'
      : s.validation_verdict === 'FAIL' ? 'failed'
      : s.validation_verdict === 'PARTIAL' ? 'warning' : 'pending';
    stages.push({ id: 'validation', type: 'validation', label: 'Validation',
      state: valState, events: [],
      meta: { verdict: s.validation_verdict, confidence: s.validation_confidence,
        summary: s.validation_summary, concerns: s.validation_concerns,
        cost: s.validation_cost_usd } });
  }

  if (s.validation_verdict === 'PASS' || s.merge_ready || s.commit_sha) {
    stages.push({ id: 'merge', type: 'merge', label: 'Merge Queue',
      state: s.commit_sha ? 'completed' : 'running', events: [] });
  }

  if (s.commit_sha) {
    stages.push({ id: 'commit', type: 'commit', label: 'Committed',
      state: 'completed', events: [],
      meta: { sha: s.commit_sha } });
  }

  if (s.validation_verdict === 'FAIL' || (state === 'failed' && !s.validation_verdict)) {
    stages.push({ id: 'failure', type: 'failure', label: 'Failed',
      state: 'failed', events: [],
      meta: { errors: s.errors } });
  }

  return stages;
}

function enrichEvent(ev) {
  const kind = ev.kind || ev.type || '';
  const toolName = ev.tool_name || '';
  const text = ev.summary || ev.text || '';
  const isError = ev.is_error;
  const ts = ev.timestamp ? fmtTermTs(ev.timestamp) : '';

  switch (kind) {
    case 'tool_call': return { icon: '\u2699', cls: 'ev-tool-call', chip: toolName, body: text || toolName, ts };
    case 'tool_result': return { icon: isError ? '\u2717' : '\u2192', cls: 'ev-tool-result' + (isError ? ' ev-error' : ''), chip: '', body: text, ts };
    case 'text': return { icon: '\u2026', cls: 'ev-text', chip: '', body: text, ts };
    case 'thinking': return { icon: '~', cls: 'ev-thinking', chip: '', body: text, ts };
    case 'error': return { icon: '\u2717', cls: 'ev-error', chip: '', body: text, ts };
    case 'result': return { icon: '\u2501', cls: 'ev-result', chip: '', body: text, ts };
    case 'supervisor': return { icon: isError ? '\u2717' : '\u25C6', cls: 'ev-supervisor' + (isError ? ' ev-error' : ''), chip: '', body: text, ts };
    case 'system_init': return { icon: '\u25B6', cls: 'ev-system', chip: '', body: text, ts };
    default: return { icon: '\u00B7', cls: '', chip: '', body: text || kind, ts };
  }
}

function filterEvents(events) {
  if (_filterPreset === 'all') return events;
  return events.filter(e => {
    const kind = e.kind || e.type;
    switch (_filterPreset) {
      case 'agent': return ['text', 'thinking', 'supervisor'].includes(kind);
      case 'tools': return ['tool_call', 'tool_result'].includes(kind);
      case 'errors': return e.is_error || kind === 'error';
      default: return true;
    }
  });
}

/* ── Pipeline View Main Entry ──────────────────────────────── */
function renderPipelineView(id, s) {
  const section = document.getElementById('pipeline-view');
  section.classList.remove('hidden');

  const stages = computeStages(s);
  const fp = id + '|' + (s.state || '') + '|' + (s.milestone_count || 0) + '|' +
    (s.total_cost_usd || 0) + '|' + ((s.event_log || []).length) + '|' +
    (s.validation_verdict || '') + '|' +
    (s.supervisor_phase || '') + '|' +
    (s.retry_count || 0) + '|' + (s.commit_sha || '') + '|' + (s.merge_ready ? '1' : '0');

  if (fp === _stageFingerprint) return;
  _stageFingerprint = fp;

  document.getElementById('waterfall-view').classList.add('hidden');
  document.getElementById('log-view').classList.add('hidden');
  document.getElementById('live-view').classList.add('hidden');

  if (_pipelineView === 'waterfall') {
    document.getElementById('waterfall-view').classList.remove('hidden');
    renderWaterfallTable(stages, s);
  } else if (_pipelineView === 'live') {
    document.getElementById('live-view').classList.remove('hidden');
    renderLiveTerminal(s);
  } else {
    document.getElementById('log-view').classList.remove('hidden');
    renderAccordionView(stages, s);
  }
}

function setPipelineView(view) {
  _pipelineView = view;
  _stageFingerprint = '';
  _liveEventCount = 0;
  document.getElementById('btn-waterfall-view').classList.toggle('active', view === 'waterfall');
  document.getElementById('btn-log-view').classList.toggle('active', view === 'log');
  document.getElementById('btn-live-view').classList.toggle('active', view === 'live');
  if (_selectedId && _sessions[_selectedId]) renderPipelineView(_selectedId, _sessions[_selectedId]);
}

function setFilterPreset(preset) {
  _filterPreset = preset;
  _stageFingerprint = '';
  _liveEventCount = 0;
  document.querySelectorAll('.lt-filter-pill').forEach(b =>
    b.classList.toggle('active', b.dataset.filter === preset));
  if (_selectedId && _sessions[_selectedId]) renderPipelineView(_selectedId, _sessions[_selectedId]);
}

/* ── Stage Summary Text ────────────────────────────────────── */
function stageSummaryText(st) {
  const m = st.meta || {};
  switch (st.type) {
    case 'task': {
      const parts = [];
      if (m.mode) parts.push(m.mode);
      return parts.join(' \u00B7 ');
    }
    case 'preflight': {
      const parts = [];
      if (m.worktree) parts.push(m.worktree.split('/').pop());
      if (m.depends && m.depends.length) parts.push(m.depends.length + ' deps');
      return parts.join(' \u00B7 ');
    }
    case 'scout':
    case 'review':
    case 'verify': {
      const parts = [];
      if (m.milestones) parts.push(m.milestones + ' events');
      return parts.join(' \u00B7 ');
    }
    case 'build':
    case 'execution': {
      const parts = [];
      if (m.cost) parts.push(fmtCost(m.cost));
      if (m.milestones) parts.push(m.milestones + ' events');
      if (m.isRetry) parts.push('retry');
      return parts.join(' \u00B7 ');
    }
    case 'retry': {
      const parts = ['retry'];
      if (m.milestones) parts.push(m.milestones + ' events');
      return parts.join(' \u00B7 ');
    }
    case 'validation': {
      const parts = [];
      if (m.verdict) parts.push(m.verdict);
      if (m.confidence) parts.push(Math.round(m.confidence * 100) + '%');
      if (m.cost) parts.push(fmtCost(m.cost));
      if (m.concerns && m.concerns.length) parts.push(m.concerns.length + ' concerns');
      return parts.join(' \u00B7 ');
    }
    case 'commit':
      return m.sha ? m.sha.slice(0, 7) : '';
    default:
      return '';
  }
}

/* ── Waterfall Table ───────────────────────────────────────── */
function computeTimingInfo(stages, session) {
  const globalStart = session.created_at ? new Date(session.created_at).getTime() / 1000 : 0;
  const now = Date.now() / 1000;
  const globalEnd = session.updated_at
    ? Math.max(new Date(session.updated_at).getTime() / 1000, now)
    : globalStart + (session.duration_seconds || 1);
  const globalDuration = Math.max(globalEnd - globalStart, 1);
  const timingMap = new Map();
  let prevEnd = globalStart;

  for (const st of stages) {
    let start = 0, end = 0, duration = 0;
    const timestamps = st.events.map(e => e.timestamp).filter(t => t > 0);
    if (timestamps.length > 0) {
      start = Math.min(...timestamps);
      end = Math.max(...timestamps);
      duration = end - start;
    } else if (st.meta && st.meta.result && st.meta.result.duration_seconds) {
      duration = st.meta.result.duration_seconds;
      start = prevEnd;
      end = start + duration;
    } else if (st.state === 'completed') {
      start = prevEnd;
      duration = globalDuration * 0.02;
      end = start + duration;
    } else if (st.state === 'running') {
      start = prevEnd;
      end = now;
      duration = end - start;
    }
    timingMap.set(st.id, { start, end, duration });
    if (end > 0) prevEnd = end;
  }
  return { globalStart, globalEnd, globalDuration, timingMap };
}

const STATE_LABELS = {
  completed: '\u2713 done', running: '\u25CF run', failed: '\u2717 fail',
  warning: '\u26A0 warn', pending: '\u00B7 wait'
};

function renderWaterfallTable(stages, session) {
  const container = document.getElementById('wf-table');
  const timing = computeTimingInfo(stages, session);

  let html = `<div class="wf-header"><span>Stage</span><span>Status</span><span style="text-align:right">Duration</span></div>`;

  for (const st of stages) {
    const icon = STAGE_ICONS[st.type] || '\u25CF';
    const stateLabel = STATE_LABELS[st.state] || st.state;
    const selected = st.id === _selectedStageId ? ' selected' : '';

    const t = timing.timingMap.get(st.id);
    const barLeft = t && t.start > 0 ? ((t.start - timing.globalStart) / timing.globalDuration * 100) : 0;
    const barWidth = t && t.duration > 0 ? Math.max(t.duration / timing.globalDuration * 100, 1.5) : 0;
    const durationText = t && t.duration > 0 ? fmtDuration(t.duration) : '';

    let costText = '';
    if (st.meta) {
      if (st.meta.cost) costText = fmtCost(st.meta.cost);
      else if (st.meta.result && st.meta.result.cost_usd) costText = fmtCost(st.meta.result.cost_usd);
    }
    const summaryText = stageSummaryText(st);

    html += `<div class="wf-row${selected}" data-stage-id="${st.id}" data-stage-type="${st.type}" onclick="selectWaterfallStage('${st.id}')">
      <div class="wf-name">
        <div class="wf-icon st-${st.state}">${icon}</div>
        <span>${esc(st.label)}</span>
        ${costText ? `<span class="wf-cost">${costText}</span>` : ''}
        ${summaryText ? `<span class="wf-summary">${esc(summaryText)}</span>` : ''}
      </div>
      <div class="wf-state st-${st.state}">${stateLabel}</div>
      <div class="wf-bar-container">
        ${barWidth > 0 ? `<div class="wf-bar st-${st.state}" data-phase="${st.type}" style="left:${barLeft.toFixed(1)}%;width:${barWidth.toFixed(1)}%"></div>` : ''}
        ${durationText ? `<div class="wf-bar-label">${durationText}</div>` : ''}
      </div>
    </div>`;
  }

  container.innerHTML = html;

  /* Render detail panel for selected stage */
  if (_selectedStageId) {
    const st = stages.find(s => s.id === _selectedStageId);
    if (st) renderWaterfallDetail(st, session);
    else closeWaterfallDetail();
  }
}

function selectWaterfallStage(stageId) {
  if (_selectedStageId === stageId) {
    closeWaterfallDetail();
    return;
  }
  _selectedStageId = stageId;
  _stageFingerprint = '';
  if (_selectedId && _sessions[_selectedId]) renderPipelineView(_selectedId, _sessions[_selectedId]);
  setTimeout(() => {
    const panel = document.getElementById('wf-detail-panel');
    if (panel && !panel.classList.contains('hidden')) {
      panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, 50);
}

function closeWaterfallDetail() {
  _selectedStageId = null;
  document.getElementById('wf-detail-panel').classList.add('hidden');
  document.querySelectorAll('.wf-row.selected').forEach(el => el.classList.remove('selected'));
}

function renderWaterfallDetail(st, session) {
  const panel = document.getElementById('wf-detail-panel');
  panel.classList.remove('hidden');

  const icon = STAGE_ICONS[st.type] || '\u25CF';
  const metaParts = [];
  if (st.meta && st.meta.cost) metaParts.push(fmtCost(st.meta.cost));
  if (st.meta && st.meta.result && st.meta.result.cost_usd) metaParts.push(fmtCost(st.meta.result.cost_usd));
  if (st.meta && st.meta.result && st.meta.result.duration_seconds) metaParts.push(fmtDuration(st.meta.result.duration_seconds));

  let html = `<div class="wf-detail-header">
    <div class="wf-icon st-${st.state}" style="width:24px;height:24px;font-size:0.85rem">${icon}</div>
    <span class="wf-dh-name">${esc(st.label)}</span>
    <span class="wf-state st-${st.state}" style="font-size:0.78rem">${st.state}</span>
    <div class="wf-dh-meta">${metaParts.map(m => `<span>${m}</span>`).join('')}</div>
    <button class="wf-dh-close" onclick="closeWaterfallDetail()" title="Close">&times;</button>
  </div>`;

  html += renderDetailSummary(st);
  html += '<div class="wf-detail-body" id="wf-detail-body">';
  html += renderDetailEvents(st, session);
  html += '</div>';

  panel.innerHTML = html;
}

function renderDetailSummary(st) {
  let html = '';
  const m = st.meta || {};

  switch (st.type) {
    case 'preflight': {
      if (m.worktree) html += `<div class="wf-detail-line"><span class="wf-dl-label">Worktree</span><code>${esc(m.worktree)}</code></div>`;
      if (m.depends && m.depends.length) {
        html += `<div class="wf-detail-line"><span class="wf-dl-label">Dependencies</span>${m.depends.map(d => `<span class="dep-chip" style="border-color:var(--text-muted);color:var(--text-secondary)">#${shortId(d)}</span>`).join(' ')}</div>`;
      }
      if (m.mode) html += `<div class="wf-detail-line"><span class="wf-dl-label">Mode</span><span>${esc(m.mode)}</span></div>`;
      if (!m.worktree && !(m.depends && m.depends.length)) {
        html += '<div class="wf-detail-line" style="color:var(--text-muted)">Environment ready.</div>';
      }
      break;
    }
    case 'validation': {
      if (m.verdict) {
        html += '<div class="wf-detail-summary">';
        html += `<span class="wf-verdict ${m.verdict}">${esc(m.verdict)}</span>`;
        if (m.confidence != null) html += ` <span style="font-size:0.78rem;color:var(--text-muted)">${(m.confidence * 100).toFixed(0)}%</span>`;
        if (m.cost) html += ` <span style="font-size:0.78rem;color:var(--text-muted)">${fmtCost(m.cost)}</span>`;
        if (m.summary) html += `<div style="margin-top:0.3rem">${esc(m.summary)}</div>`;
        if (m.concerns && m.concerns.length) {
          html += '<ul class="wf-concerns">' + m.concerns.map(c => `<li>${esc(c)}</li>`).join('') + '</ul>';
        }
        html += '</div>';
      } else {
        html += '<div class="wf-detail-line" style="color:var(--text-muted)">Validation in progress\u2026</div>';
      }
      break;
    }
    case 'merge': {
      html += '<div class="wf-detail-line" style="color:var(--text-muted)">Queued for sequential merge via MergeQueue.</div>';
      break;
    }
    case 'commit': {
      if (m.sha) html += `<div class="wf-detail-line"><span class="wf-dl-label">Commit</span><code>${esc(m.sha)}</code></div>`;
      break;
    }
    case 'failure': {
      if (m.errors) {
        html += '<div class="wf-detail-summary">' + (m.errors || []).map(e => `<div style="color:var(--red)">${esc(e)}</div>`).join('') + '</div>';
      }
      break;
    }
    case 'retry': {
      html += `<div class="wf-detail-summary">Retry count: ${m.retryCount || 1}. Triggered by PARTIAL verdict.</div>`;
      break;
    }
  }

  return html;
}

function renderDetailEvents(st, session) {
  const events = filterEvents(st.events);
  let html = '';

  if (events.length > 0) {
    const MAX = 200;
    const rendered = events.length > MAX ? events.slice(-MAX) : events;
    if (events.length > MAX) {
      html += `<div class="wf-events-empty">${events.length - MAX} older events hidden</div>`;
    }
    html += rendered.map(renderWfEventRow).join('');
  } else {
    html += '<div class="wf-events-empty">No events recorded.</div>';
  }

  return html;
}

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

/* ── Expandable Event Detail ──────────────────────────────── */
let _traceTerminalCache = {};

async function loadTraceTerminal(eventId) {
  if (_traceTerminalCache[eventId]) return _traceTerminalCache[eventId];
  try {
    const res = await fetch('/api/trace-terminal/' + eventId);
    if (!res.ok) return null;
    const data = await res.json();
    _traceTerminalCache[eventId] = data.events || [];
    return _traceTerminalCache[eventId];
  } catch (e) { return null; }
}

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

function expandStage(stageId) {
  _expandedStages.add(stageId);
  _stageFingerprint = '';
  if (_selectedId && _sessions[_selectedId]) renderPipelineView(_selectedId, _sessions[_selectedId]);
}

function toggleStage(stageId) {
  if (_expandedStages.has(stageId)) _expandedStages.delete(stageId);
  else _expandedStages.add(stageId);
  _stageFingerprint = '';
  if (_selectedId && _sessions[_selectedId]) renderPipelineView(_selectedId, _sessions[_selectedId]);
}

function renderStageBody(st, s) {
  let html = renderDetailSummary(st);
  const events = filterEvents(st.events);

  if (events.length > 0) {
    const MAX = 200;
    const rendered = events.length > MAX ? events.slice(-MAX) : events;
    if (events.length > MAX) html += `<div class="wf-events-empty">${events.length - MAX} older events hidden</div>`;
    html += rendered.map(renderWfEventRow).join('');
  } else if (!html) {
    html += '<div class="wf-events-empty">No events recorded.</div>';
  }

  return html;
}

/* ── Live Terminal View ───────────────────────────────────── */
function renderLiveTerminal(s) {
  const container = document.getElementById('live-terminal');
  const events = s.event_log || [];
  const filtered = filterEvents(events);

  /* Only do incremental append if new events arrived */
  if (filtered.length === _liveEventCount && container.querySelector('.lt-row')) return;

  const isNewRender = _liveEventCount === 0 || !container.querySelector('.lt-row');
  const startIdx = isNewRender ? 0 : _liveEventCount;
  _liveEventCount = filtered.length;

  if (!filtered.length) {
    container.innerHTML = '<div class="lt-empty">Waiting for events\u2026 Events will stream here as the agent works.</div>';
    return;
  }

  /* Stats bar (always re-render) */
  const toolCalls = filtered.filter(e => (e.kind || e.type) === 'tool_call').length;
  const errors = filtered.filter(e => e.is_error).length;
  const textMsgs = filtered.filter(e => (e.kind || e.type) === 'text').length;
  const supervisorMsgs = filtered.filter(e => (e.kind || e.type) === 'supervisor').length;
  const isRunning = s.state === 'running';

  let statsHtml = '<div class="lt-stats-bar">';
  statsHtml += `<span>\u2699 Tools:<span class="lt-stat-val">${toolCalls}</span></span>`;
  statsHtml += `<span>\u2709 Messages:<span class="lt-stat-val">${textMsgs}</span></span>`;
  if (supervisorMsgs) statsHtml += `<span>\u25C6 Supervisor:<span class="lt-stat-val">${supervisorMsgs}</span></span>`;
  if (errors) statsHtml += `<span style="color:var(--red)">\u2717 Errors:<span class="lt-stat-val">${errors}</span></span>`;
  statsHtml += `<span>Total:<span class="lt-stat-val">${filtered.length}</span></span>`;
  if (isRunning) statsHtml += '<span style="color:var(--blue)">\u25CF live</span>';
  statsHtml += `<label class="lt-auto-scroll"><input type="checkbox" ${_liveAutoScroll ? 'checked' : ''} onchange="_liveAutoScroll=this.checked"> Auto-scroll</label>`;
  statsHtml += '<div class="lt-filter-pills">';
  for (const f of ['all', 'agent', 'tools', 'errors']) {
    statsHtml += `<button class="lt-filter-pill${_filterPreset === f ? ' active' : ''}" data-filter="${f}" onclick="setFilterPreset('${f}')">${f}</button>`;
  }
  statsHtml += '</div>';
  statsHtml += '</div>';

  if (isNewRender) {
    let html = statsHtml;
    html += '<div class="lt-events">';
    html += filtered.map(renderLiveRow).join('');
    html += '<div class="lt-scroll-anchor"></div>';
    html += '</div>';
    container.innerHTML = html;
  } else {
    /* Update stats bar */
    const existingStats = container.querySelector('.lt-stats-bar');
    if (existingStats) {
      const temp = document.createElement('div');
      temp.innerHTML = statsHtml;
      existingStats.replaceWith(temp.firstElementChild);
    }
    /* Append new events */
    const anchor = container.querySelector('.lt-scroll-anchor');
    const newEvents = filtered.slice(startIdx);
    for (const ev of newEvents) {
      const temp = document.createElement('div');
      temp.innerHTML = renderLiveRow(ev);
      if (temp.firstElementChild && anchor) anchor.before(temp.firstElementChild);
    }
  }

  /* Auto-scroll to bottom */
  if (_liveAutoScroll) {
    const anchor = container.querySelector('.lt-scroll-anchor');
    if (anchor) anchor.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }
}

function classifyTool(name) {
  if (/^(Read|Glob|Grep|LS)$/i.test(name)) return 'read';
  if (/^(Edit|Write|NotebookEdit)$/i.test(name)) return 'edit';
  if (/^Bash$/i.test(name)) return 'bash';
  if (/^(Agent|WebSearch|WebFetch)$/i.test(name)) return 'search';
  return '';
}

function renderLiveRow(ev) {
  const kind = ev.kind || ev.type || '';
  const toolName = ev.tool_name || '';
  const text = ev.summary || ev.text || '';
  const isError = ev.is_error;
  const ts = ev.timestamp ? fmtTermTs(ev.timestamp) : '';

  let icon, label, cls, body;

  switch (kind) {
    case 'tool_call':
      icon = '\u2699';
      /* Detect Agent calls and render them specially */
      if (toolName === 'Agent') {
        cls = 'lt-agent';
        label = 'AGENT';
        body = text || 'Agent subagent spawned';
      } else {
        cls = 'lt-tool-call';
        label = toolName || 'TOOL';
        body = text || toolName;
      }
      break;
    case 'tool_result':
    case 'result':
      if (kind === 'result' && !isError) {
        icon = '\u2501';
        cls = 'lt-result';
        label = 'DONE';
        body = text;
        break;
      }
      icon = isError ? '\u2717' : '\u2192';
      cls = isError ? 'lt-error' : 'lt-tool-result';
      label = isError ? 'ERROR' : 'RESULT';
      body = text;
      break;
    case 'text':
      icon = '\u2026';
      cls = 'lt-text';
      label = 'TEXT';
      body = text;
      break;
    case 'thinking':
      icon = '~';
      cls = 'lt-thinking';
      label = 'THINK';
      body = text;
      break;
    case 'error':
      icon = '\u2717';
      cls = 'lt-error';
      label = 'ERROR';
      body = text;
      break;
    case 'supervisor':
      icon = isError ? '\u2717' : '\u25C6';
      cls = isError ? 'lt-error' : 'lt-supervisor';
      label = 'SUPER';
      body = text;
      break;
    default:
      icon = '\u00B7';
      cls = '';
      label = kind.toUpperCase() || 'EVENT';
      body = text || kind;
  }

  const evTs = ev.timestamp || 0;
  const evKind = ev.kind || ev.type || '';
  const evTool = ev.tool_name || '';

  const toolType = (kind === 'tool_call' && toolName !== 'Agent') ? classifyTool(toolName) : '';
  const labelAttr = toolType ? ` data-tool-type="${toolType}"` : '';

  return `<div class="lt-row ${cls}" data-event-ts="${evTs}" data-event-kind="${evKind}" data-event-tool="${esc(evTool)}" onclick="toggleLiveDetail(this)">
    <span class="lt-icon">${icon}</span>
    <span class="lt-label"${labelAttr}>${esc(label)}</span>
    <span class="lt-body">${esc(body)}</span>
    ${ts ? `<span class="lt-ts">${ts}</span>` : '<span></span>'}
  </div>`;
}

async function toggleLiveDetail(rowEl) {
  /* Collapse if already expanded */
  const existing = rowEl.nextElementSibling;
  if (existing && existing.classList.contains('lt-detail-panel')) {
    existing.remove();
    rowEl.classList.remove('lt-expanded');
    return;
  }

  /* Collapse any other expanded row */
  document.querySelectorAll('.lt-detail-panel').forEach(el => el.remove());
  document.querySelectorAll('.lt-expanded').forEach(el => el.classList.remove('lt-expanded'));

  rowEl.classList.add('lt-expanded');

  const s = _sessions[_selectedId];
  if (!s) return;
  const eventId = 'golem-' + _selectedId;

  /* Show loading */
  const panel = document.createElement('div');
  panel.className = 'lt-detail-panel';
  panel.innerHTML = '<div class="lt-detail-loading">Loading\u2026</div>';
  rowEl.after(panel);

  const events = await loadTraceTerminal(eventId);
  if (!events || !events.length) {
    panel.innerHTML = '<div class="lt-detail-loading">Trace not available.</div>';
    return;
  }

  /* Match by timestamp + kind + tool_name */
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

    if (teKind !== targetKind && targetKind) continue;
    score += 1;
    if (targetTool && teTool === targetTool) score += 2;
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
    panel.innerHTML = '<div class="lt-detail-loading">Event details not found in trace.</div>';
    return;
  }

  const text = fullEvent.text || fullEvent.summary || '';
  if (!text) {
    panel.innerHTML = '<div class="lt-detail-loading">No additional detail.</div>';
    return;
  }

  /* Render full text with basic code block handling */
  let rendered = esc(text);
  /* Convert ```...``` fenced code blocks to styled sections */
  rendered = rendered.replace(/```(\w*)\n([\s\S]*?)```/g,
    '<code style="background:var(--bg-surface);padding:2px 6px;border-radius:3px;display:block;margin:0.5em 0">$2</code>');
  panel.innerHTML = `<pre>${rendered}</pre>`;
}

/* ── Accordion / Tree View ─────────────────────────────────── */
function renderAccordionView(stages, s) {
  const container = document.getElementById('accordion-view');
  for (const st of stages) { if (st.state === 'running') _expandedStages.add(st.id); }

  container.innerHTML = stages.map(st => {
    const expanded = _expandedStages.has(st.id);
    const icon = STAGE_ICONS[st.type] || '\u25CF';

    return `<div class="acc-group st-${st.state}${expanded ? ' expanded' : ''}" data-stage="${st.id}">
      <div class="acc-group-header" onclick="toggleStage('${st.id}')">
        <span class="ag-chevron">\u25B6</span>
        <span class="ag-icon">${icon}</span>
        <span class="ag-name">${esc(st.label)}</span>
        <span class="ag-badge sc-badge st-${st.state}">${st.state}</span>
        ${st.events.length ? `<span class="ag-meta">${st.events.length} events</span>` : ''}
      </div>
      ${expanded ? `<div class="acc-group-body">${renderStageBody(st, s)}</div>` : ''}
    </div>`;
  }).join('');
}


/* ── Info Tabs ─────────────────────────────────────────────── */
function activateTab(name) {
  $$('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  $$('.tab-content').forEach(c => c.classList.toggle('active', c.id === 'tab-' + name));
}

function renderInfoTabs(s) {
  const errors = s.errors || [];
  $('#tab-errors').innerHTML = errors.length
    ? errors.map(e => `<div class="error-item">${esc(e)}</div>`).join('')
    : '<div class="terminal-empty">No errors recorded.</div>';

  const tools = s.tools_called || [], mcpTools = s.mcp_tools_called || [];
  let toolsHtml = '';
  if (tools.length || mcpTools.length) {
    const tc = {};
    for (const ev of (s.event_log || [])) { if ((ev.kind || ev.type) === 'tool_call') { const n = ev.tool_name || ev.summary || ''; if (n) tc[n] = (tc[n] || 0) + 1; } }
    toolsHtml = `<div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:0.5rem">${tools.length + mcpTools.length} tools, ${Object.values(tc).reduce((a,b)=>a+b,0) || '?'} calls</div><div style="margin-bottom:0.5rem">`;
    toolsHtml += tools.map(t => `<span class="tool-chip">${esc(t)}${tc[t] ? ' <small style="opacity:0.6">\u00D7'+tc[t]+'</small>' : ''}</span>`).join('');
    toolsHtml += mcpTools.map(t => `<span class="tool-chip mcp">${esc(t)}${tc[t] ? ' <small style="opacity:0.6">\u00D7'+tc[t]+'</small>' : ''}</span>`).join('');
    toolsHtml += '</div>';
  } else { toolsHtml = '<div class="terminal-empty">No tools recorded.</div>'; }
  $('#tab-tools').innerHTML = toolsHtml;

  const coordRows = [];
  if (s.group_id) coordRows.push(['Group', esc(s.group_id)]);
  const depIds = s.depends_on || [];
  if (depIds.length) {
    coordRows.push(['Dependencies', depIds.map(d => {
      const ds = _sessions[d], dState = ds ? ds.state : 'unknown';
      const style = ds ? stateBadgeStyle(dState) : 'background:#1e293b;color:#94a3b8';
      return `<a href="#/task/${d}" style="text-decoration:none" title="#${d}"><span class="th-badge" style="${style};font-size:0.65rem;cursor:pointer">#${shortId(d)} ${esc(dState)}</span></a>`;
    }).join(' ')]);
  }
  if (s.merge_ready) coordRows.push(['Merge Status', '<span style="color:var(--blue);font-weight:600">Queued for merge</span>']);
  else if (s.commit_sha) coordRows.push(['Merge Status', 'Merged']);
  if (s.worktree_path) coordRows.push(['Worktree', `<code>${esc(s.worktree_path)}</code>`]);
  if (s.base_work_dir) coordRows.push(['Base Work Dir', `<code>${esc(s.base_work_dir)}</code>`]);
  if (s.infra_retry_count > 0) coordRows.push(['Infra Retries', `<span style="color:var(--red);font-weight:600">${s.infra_retry_count}</span>`]);

  $('#tab-coordination').innerHTML = coordRows.length
    ? '<table style="width:100%;font-size:0.82rem;border-collapse:collapse">' +
      coordRows.map(([k, v]) => `<tr><td style="padding:0.4rem 0.75rem;color:var(--text-muted);white-space:nowrap;vertical-align:top">${k}</td><td style="padding:0.4rem 0.75rem;color:var(--text-primary)">${v}</td></tr>`).join('') + '</table>'
    : '<div class="terminal-empty">No coordination data for this session.</div>';

  $('#tab-raw').innerHTML = `<pre class="json">${highlightJson(esc(JSON.stringify(s, null, 2)))}</pre>`;
}

/* ── Config Bar ────────────────────────────────────────────── */
async function loadConfig() {
  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();
    const el = document.getElementById('config-bar');
    if (!el) return;
    if (!cfg || !Object.keys(cfg).length) { el.classList.add('hidden'); return; }
    el.classList.remove('hidden');
    let html = '';
    if (cfg.model) html += `<span><span class="cfg-label">Model</span><span class="cfg-val">${esc(cfg.model)}</span></span>`;
    if (cfg.max_concurrent) html += `<span><span class="cfg-label">Concurrency</span><span class="cfg-val">${cfg.max_concurrent}</span></span>`;
    if (cfg.budget) html += `<span><span class="cfg-label">Budget</span><span class="cfg-val">$${cfg.budget}</span></span>`;
    if (cfg.flows) {
      html += '<span><span class="cfg-label">Flows</span>';
      for (const [name, on] of Object.entries(cfg.flows)) html += `<span class="cfg-chip ${on ? 'on' : 'off'}">${esc(name)}</span>`;
      html += '</span>';
    }
    el.innerHTML = html;
  } catch (e) { const bar = document.getElementById('config-bar'); if (bar) bar.classList.add('hidden'); }
}

/* ── Data Fetching ─────────────────────────────────────────── */
async function fetchSessions() {
  try {
    const res = await fetch('/api/sessions');
    const data = await res.json();
    _sessions = data.sessions || {};
    renderTopStats();
    if (!_selectedId) renderOverview();
    if (_selectedId && _sessions[_selectedId]) {
      const s = _sessions[_selectedId];
      const fp = _selectedId + '|' + s.state + '|' + (s.milestone_count || 0) + '|' + (s.total_cost_usd || 0) + '|' +
        ((s.event_log || []).length) + '|' + (s.validation_verdict || '');
      if (s.state === 'detected' || _prevFingerprints[_selectedId] !== fp) {
        _prevFingerprints[_selectedId] = fp;
        renderTaskDetail(_selectedId, s);
      }
    }
  } catch (e) { console.error('Failed to fetch sessions:', e); }
}

async function fetchLive() {
  try { const res = await fetch('/api/live'); _liveSnap = await res.json(); } catch (e) {}
}

/* ── Initialization ────────────────────────────────────────── */
const _tableSearchEl = document.getElementById('table-search');
const _tableFilterEl = document.getElementById('table-state-filter');
if (_tableSearchEl) _tableSearchEl.addEventListener('input', renderTaskList);
if (_tableFilterEl) _tableFilterEl.addEventListener('change', renderTaskList);
window.addEventListener('hashchange', handleHash);

/* ── Split View Resize ──────────────────────────────────────── */
function initSplitResize() {
  const handle = document.getElementById('split-handle');
  const splitView = handle ? handle.parentElement : null;
  if (!handle || !splitView) return;

  /* Restore saved ratio */
  try {
    const saved = localStorage.getItem('golem-split-ratio');
    if (saved) {
      const ratio = parseFloat(saved);
      if (ratio > 0.15 && ratio < 0.85) {
        splitView.style.gridTemplateColumns = `${ratio}fr 6px ${1 - ratio}fr`;
      }
    }
  } catch(e) {}

  let dragging = false;

  handle.addEventListener('mousedown', (e) => {
    e.preventDefault();
    dragging = true;
    handle.classList.add('active');
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  });

  window.addEventListener('mousemove', (e) => {
    if (!dragging) return;
    const rect = splitView.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const ratio = Math.max(0.15, Math.min(0.85, x / rect.width));
    splitView.style.gridTemplateColumns = `${ratio}fr 6px ${1 - ratio}fr`;
  });

  window.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    handle.classList.remove('active');
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    /* Persist ratio */
    const cols = splitView.style.gridTemplateColumns;
    const match = cols.match(/([\d.]+)fr/);
    if (match) {
      try { localStorage.setItem('golem-split-ratio', match[1]); } catch(e) {}
    }
  });

  /* Double-click resets to 50/50 */
  handle.addEventListener('dblclick', () => {
    splitView.style.gridTemplateColumns = '';
    try { localStorage.removeItem('golem-split-ratio'); } catch(e) {}
  });
}

/* ── Vertical Panel Resize ───────────────────────────────────── */
function initVerticalResize() {
  document.querySelectorAll('.v-resize-handle').forEach(handle => {
    const wrapper = handle.parentElement;
    const key = handle.dataset.key;
    if (!wrapper || !key) return;

    /* Restore saved height */
    try {
      const saved = localStorage.getItem(key);
      if (saved) {
        const h = parseInt(saved, 10);
        if (h >= 120 && h <= 2000) wrapper.style.height = h + 'px';
      }
    } catch(e) {}

    let dragging = false;
    let startY = 0;
    let startH = 0;

    handle.addEventListener('mousedown', (e) => {
      e.preventDefault();
      dragging = true;
      startY = e.clientY;
      startH = wrapper.getBoundingClientRect().height;
      handle.classList.add('active');
      document.body.style.cursor = 'row-resize';
      document.body.style.userSelect = 'none';
    });

    window.addEventListener('mousemove', (e) => {
      if (!dragging) return;
      const delta = e.clientY - startY;
      const newH = Math.max(120, startH + delta);
      wrapper.style.height = newH + 'px';
    });

    window.addEventListener('mouseup', () => {
      if (!dragging) return;
      dragging = false;
      handle.classList.remove('active');
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      try { localStorage.setItem(key, Math.round(wrapper.getBoundingClientRect().height)); } catch(e) {}
    });

    /* Double-click resets to default height */
    handle.addEventListener('dblclick', () => {
      wrapper.style.height = '';
      try { localStorage.removeItem(key); } catch(e) {}
    });
  });
}

async function init() {
  initDagPanZoom();
  initSplitResize();
  initVerticalResize();
  await Promise.all([loadConfig(), fetchSessions(), fetchLive()]);
  if (location.hash) handleHash();
  else renderOverview();
  setInterval(fetchSessions, 3000);
  setInterval(fetchLive, 3000);
}

init();
