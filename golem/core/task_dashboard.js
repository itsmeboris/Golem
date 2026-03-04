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
let _showThinking = false;
let _showText = true;
let _prevFingerprints = {};
let _liveSnap = {};
let _allTraceEvents = {};

/* Pipeline view state */
let _pipelineView = 'waterfall'; /* 'waterfall' | 'flow' | 'log' */
let _expandedStages = new Set();
let _stageFingerprint = '';
let _selectedStageId = null;

/* ── Sidebar rendering ─────────────────────────────────────── */
function renderSidebar() {
  const list = $('#task-list');
  const search = ($('#task-search').value || '').toLowerCase();
  const stateFilter = $('#state-filter').value;
  const entries = Object.entries(_sessions);

  if (!entries.length) {
    list.innerHTML = '<div class="sidebar-empty">No sessions yet. Tasks will appear when [AGENT] issues are detected.</div>';
    $('#sidebar-stats').textContent = '0 sessions';
    return;
  }

  /* Sort: active first (by created_at asc), then terminal (by updated_at desc) */
  const active = [];
  const terminal = [];
  for (const [id, s] of entries) {
    if (['completed', 'failed'].includes(s.state)) terminal.push([id, s]);
    else active.push([id, s]);
  }
  active.sort((a, b) => (a[1].created_at || '').localeCompare(b[1].created_at || ''));
  terminal.sort((a, b) => (b[1].updated_at || '').localeCompare(a[1].updated_at || ''));
  const sorted = [...active, ...terminal];

  /* Filter */
  const filtered = sorted.filter(([id, s]) => {
    if (stateFilter && s.state !== stateFilter) return false;
    if (search) {
      const hay = `#${id} ${s.parent_subject || ''} ${s.state || ''}`.toLowerCase();
      if (!hay.includes(search)) return false;
    }
    return true;
  });

  /* Stats */
  const activeCount = active.length;
  const totalCount = entries.length;
  const liveDot = _liveSnap.active_count > 0 ? 'active' : 'idle';
  $('#sidebar-stats').innerHTML =
    `${activeCount} active / ${totalCount} total` +
    `<span class="sidebar-live"><span class="live-dot ${liveDot}"></span>${_liveSnap.active_count || 0} running</span>`;

  /* Result count */
  const countEl = document.getElementById('filter-count');
  if (countEl) {
    if (search || stateFilter) {
      countEl.textContent = `Showing ${filtered.length} of ${entries.length}`;
      countEl.classList.remove('hidden');
    } else {
      countEl.classList.add('hidden');
    }
  }

  if (filtered.length === 0 && (search || stateFilter)) {
    list.innerHTML = '<div class="sidebar-empty">No tasks match your filter.</div>';
    return;
  }

  /* Render cards */
  list.innerHTML = filtered.map(([id, s]) => {
    const sel = id === _selectedId ? ' selected' : '';
    const state = s.state || 'detected';
    const rawSubject = (s.parent_subject || '').replace(/^\[AGENT\]\s*/, '');
    const subject = esc(truncText(rawSubject, 90));
    const fullSubject = esc(rawSubject);
    const cost = s.total_cost_usd ? fmtCost(s.total_cost_usd) : '';
    const dur = s.duration_seconds ? fmtDuration(s.duration_seconds)
      : (state === 'detected' && s.grace_deadline) ? fmtCountdown(s.grace_deadline)
      : fmtAgo(s.created_at);

    const deps = s.depends_on || [];
    const mergeFailed = hasMergeError(s);

    let statusIndicator = '';
    if (mergeFailed) {
      statusIndicator = '<span class="tc-badge" style="background:#450a0a;color:#f87171">merge fail</span>';
    } else if (['running','validating','detected','retrying'].includes(state)) {
      statusIndicator = `<span class="tc-badge">${esc(state)}</span>`;
    }

    let depHtml = '';
    if (deps.length) {
      const allDone = deps.every(d => { const ds = _sessions[d]; return ds && ds.state === 'completed'; });
      const anyFailed = deps.some(d => { const ds = _sessions[d]; return ds && ds.state === 'failed'; });
      const depColor = anyFailed ? 'var(--red)' : allDone ? 'var(--green)' : 'var(--yellow)';
      depHtml = `<span class="tc-deps" style="color:${depColor}">\u26D3 ${deps.length}</span>`;
    }

    return `<div class="task-card state-${state}${sel}" data-id="${id}" onclick="selectTask('${id}')" title="${fullSubject}">
      <div class="tc-top">
        <span class="tc-id">#${id}</span>
        ${statusIndicator}
      </div>
      <div class="tc-subject">${subject}</div>
      <div class="tc-meta">
        ${cost ? `<span>${cost}</span>` : ''}
        ${dur ? `<span>${dur}</span>` : ''}
        ${depHtml}
        ${s.milestone_count ? `<span>${s.milestone_count} steps</span>` : ''}
      </div>
    </div>`;
  }).join('');
}

function hasMergeError(s) {
  return (s.errors || []).some(e => typeof e === 'string' && e.startsWith('merge failed'));
}

function verdictBadgeStyle(v) {
  if (v === 'PASS') return 'background:#064e3b;color:#4ade80';
  if (v === 'FAIL') return 'background:#450a0a;color:#f87171';
  if (v === 'PARTIAL') return 'background:#431407;color:#fb923c';
  return 'background:#422006;color:#fbbf24';
}

/* ── Task selection & routing ──────────────────────────────── */
function selectTask(id) {
  _selectedId = id;
  _expandedStages.clear();
  _stageFingerprint = '';
  _selectedStageId = null;
  _flowSelectedStageId = null;
  location.hash = '/task/' + id;

  $$('.task-card').forEach(el => el.classList.toggle('selected', el.dataset.id === id));
  $('#overview-state').classList.add('hidden');
  $('#task-detail').classList.remove('hidden');

  const s = _sessions[id];
  if (s) renderTaskDetail(id, s);
}

function deselectTask() {
  _selectedId = null;
  _expandedStages.clear();
  _stageFingerprint = '';
  _selectedStageId = null;
  _flowSelectedStageId = null;
  history.pushState(null, '', location.pathname);
  $('#overview-state').classList.remove('hidden');
  $('#task-detail').classList.add('hidden');
  $$('.task-card.selected').forEach(el => el.classList.remove('selected'));
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
  const entries = Object.entries(_sessions);
  const statCards = $('#stat-cards');
  const emptyState = $('#empty-state');

  if (!entries.length) {
    statCards.innerHTML = '';
    emptyState.classList.remove('hidden');
    return;
  }

  emptyState.classList.add('hidden');

  let totalTasks = entries.length;
  let totalCost = 0, totalValCost = 0, totalDuration = 0, durationCount = 0;
  let maxCost = 0, completedCostSum = 0, completedCount = 0;
  const byState = {};

  for (const [id, s] of entries) {
    const state = s.state || 'detected';
    byState[state] = (byState[state] || 0) + 1;
    const taskCost = (s.total_cost_usd || 0) + (s.validation_cost_usd || 0);
    totalCost += s.total_cost_usd || 0;
    totalValCost += s.validation_cost_usd || 0;
    if (taskCost > maxCost) maxCost = taskCost;
    if (state === 'completed') { completedCostSum += taskCost; completedCount++; }
    if (s.duration_seconds) { totalDuration += s.duration_seconds; durationCount++; }
  }

  const avgCost = completedCount > 0 ? completedCostSum / completedCount : 0;
  const avgDuration = durationCount > 0 ? totalDuration / durationCount : 0;
  const completed = byState['completed'] || 0;
  const failed = byState['failed'] || 0;
  const running = byState['running'] || 0;
  const validating = byState['validating'] || 0;
  const detected = byState['detected'] || 0;
  const retrying = byState['retrying'] || 0;

  statCards.innerHTML = `
    <div class="stat-card"><div class="sc-label">Total Tasks</div><div class="sc-value">${totalTasks}</div></div>
    <div class="stat-card"><div class="sc-label">Completed</div><div class="sc-value green">${completed}</div></div>
    <div class="stat-card"><div class="sc-label">Failed</div><div class="sc-value ${failed ? 'red' : ''}">${failed}</div></div>
    <div class="stat-card"><div class="sc-label">Running</div><div class="sc-value ${running ? 'blue' : ''}">${running}</div></div>
    ${validating ? `<div class="stat-card"><div class="sc-label">Validating</div><div class="sc-value">${validating}</div></div>` : ''}
    ${detected ? `<div class="stat-card"><div class="sc-label">Detected</div><div class="sc-value">${detected}</div></div>` : ''}
    ${retrying ? `<div class="stat-card"><div class="sc-label">Retrying</div><div class="sc-value">${retrying}</div></div>` : ''}
    <div class="stat-card"><div class="sc-label">Total Spend</div><div class="sc-value">${fmtCost(totalCost + totalValCost)}</div></div>
    <div class="stat-card"><div class="sc-label">Validation Cost</div><div class="sc-value">${fmtCost(totalValCost)}</div></div>
    <div class="stat-card"><div class="sc-label">Avg Cost</div><div class="sc-value">${fmtCost(avgCost)}</div></div>
    <div class="stat-card"><div class="sc-label">Max Cost</div><div class="sc-value">${fmtCost(maxCost)}</div></div>
    <div class="stat-card"><div class="sc-label">Avg Duration</div><div class="sc-value">${fmtDuration(avgDuration)}</div></div>`;
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

  $('#task-header').innerHTML = `
    <button class="back-btn" onclick="deselectTask()">&larr; Dashboard</button>
    <div class="th-top">
      <span class="th-id">#${id}</span>
      ${mode ? `<span class="th-mode">${esc(mode)}</span>` : ''}
      <span class="th-badge" style="${stateBadgeStyle(state)}">${esc(state)}</span>
      ${sha ? `<span class="th-mode" title="${esc(sha)}">&#10003; ${esc(sha.slice(0, 7))}</span>` : ''}
      ${groupId ? `<span class="th-mode" title="Batch group">\u2B21 ${esc(groupId)}</span>` : ''}
      ${mergeHeaderBadge}
    </div>
    <div class="th-subject">${subject}</div>`;
}

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
    const depItems = deps.map(d => {
      const ds = _sessions[d];
      const dSubject = ds ? truncText((ds.parent_subject || '').replace(/^\[AGENT\]\s*/, ''), 30) : '#' + d;
      const dState = ds ? ds.state : 'unknown';
      return `<a href="#/task/${d}" style="text-decoration:none;color:inherit" title="#${d}">${esc(dSubject)}</a> <span style="font-size:0.65rem;opacity:0.7">(${esc(dState)})</span>`;
    });
    cards.push({ label: 'Dependencies', value: depItems.join('<br>'), cls: '' });
  }

  $('#metrics-row').innerHTML = cards.map(c =>
    `<div class="metric-card"><div class="mc-label">${c.label}</div><div class="mc-value ${c.cls}">${c.value}</div></div>`
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
  execution: '\u25B6', validation: '\uD83D\uDD0D',
  merge: '\u2B06', commit: '\u2714', retry: '\u21BA', failure: '\u2717'
};

/* ── Stage Grouping Engine ─────────────────────────────────── */
function computeStages(s) {
  const stages = [];
  const state = s.state || 'detected';
  const isSubagent = s.execution_mode === 'subagent';
  const allEvents = s.event_log || [];
  const taskId = s.parent_issue_id || _selectedId;

  stages.push({ id: 'task', type: 'task', label: '#' + taskId,
    state: state === 'detected' ? 'pending' : 'completed', events: [],
    meta: { subject: s.parent_subject, mode: s.execution_mode } });

  if (state === 'detected') return stages;

  stages.push({ id: 'preflight', type: 'preflight', label: 'Preflight', state: 'completed', events: [] });

  if (isSubagent) {
    const execState = ['completed','failed','validating','retrying'].includes(state) ? 'completed'
      : state === 'running' ? 'running' : 'pending';
    stages.push({ id: 'orchestration', type: 'orchestration', label: 'Orchestration', state: execState,
      events: allEvents, meta: { milestones: s.milestone_count, cost: s.total_cost_usd } });
  } else {
    const execState = ['completed','failed','validating','retrying'].includes(state) ? 'completed'
      : state === 'running' ? 'running' : 'pending';
    stages.push({ id: 'execution', type: 'execution', label: 'Execution', state: execState,
      events: allEvents, meta: { milestones: s.milestone_count, cost: s.total_cost_usd } });
  }

  if (['validating','completed','failed','retrying'].includes(state) || s.validation_verdict) {
    const valState = state === 'validating' ? 'running'
      : s.validation_verdict === 'PASS' ? 'completed'
      : s.validation_verdict === 'FAIL' ? 'failed'
      : s.validation_verdict === 'PARTIAL' ? 'warning' : 'pending';
    stages.push({ id: 'validation', type: 'validation', label: 'Validation', state: valState, events: [],
      meta: { verdict: s.validation_verdict, confidence: s.validation_confidence,
        summary: s.validation_summary, concerns: s.validation_concerns, cost: s.validation_cost_usd } });
  }

  if (s.validation_verdict === 'PARTIAL' || state === 'retrying') {
    stages.push({ id: 'retry', type: 'retry', label: 'Retry #' + (s.retry_count || 1),
      state: 'warning', events: [], meta: { retryCount: s.retry_count } });
  }

  if (s.validation_verdict === 'PASS' || s.merge_ready || s.commit_sha) {
    stages.push({ id: 'merge', type: 'merge', label: 'Merge Queue',
      state: s.commit_sha ? 'completed' : 'running', events: [] });
  }

  if (s.commit_sha) {
    stages.push({ id: 'commit', type: 'commit', label: 'Committed',
      state: 'completed', events: [], meta: { sha: s.commit_sha } });
  }

  if (s.validation_verdict === 'FAIL' || (state === 'failed' && !s.validation_verdict)) {
    stages.push({ id: 'failure', type: 'failure', label: 'Failed',
      state: 'failed', events: [], meta: { errors: s.errors } });
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
    case 'tool_result': return { icon: isError ? '\u2717' : '\u2192', cls: 'ev-tool-result' + (isError ? ' ev-error' : ''), chip: '', body: truncText(text, 150), ts };
    case 'text': return { icon: '\u2026', cls: 'ev-text', chip: '', body: truncText(text, 250), ts };
    case 'thinking': return { icon: '~', cls: 'ev-thinking', chip: '', body: truncText(text, 120), ts };
    case 'error': return { icon: '\u2717', cls: 'ev-error', chip: '', body: truncText(text, 200), ts };
    case 'result': return { icon: '\u2501', cls: 'ev-result', chip: '', body: text, ts };
    case 'supervisor': return { icon: isError ? '\u2717' : '\u25C6', cls: 'ev-supervisor' + (isError ? ' ev-error' : ''), chip: '', body: text, ts };
    case 'system_init': return { icon: '\u25B6', cls: 'ev-system', chip: '', body: text, ts };
    default: return { icon: '\u00B7', cls: '', chip: '', body: text || kind, ts };
  }
}

function filterEvents(events) {
  return events.filter(e => {
    const kind = e.kind || e.type;
    if (kind === 'thinking' && !_showThinking) return false;
    if (kind === 'text' && !_showText) return false;
    return true;
  });
}

/* ── Pipeline View Main Entry ──────────────────────────────── */
function renderPipelineView(id, s) {
  const section = document.getElementById('pipeline-view');
  section.classList.remove('hidden');

  const stages = computeStages(s);
  const fp = (s.state || '') + '|' + (s.milestone_count || 0) + '|' +
    (s.total_cost_usd || 0) + '|' + ((s.event_log || []).length) + '|' +
    (s.validation_verdict || '') + '|' +
    (s.supervisor_phase || '') + '|' +
    (s.retry_count || 0) + '|' + (s.commit_sha || '') + '|' + (s.merge_ready ? '1' : '0');

  if (fp === _stageFingerprint) return;
  _stageFingerprint = fp;

  document.getElementById('waterfall-view').classList.add('hidden');
  document.getElementById('flow-view').classList.add('hidden');
  document.getElementById('log-view').classList.add('hidden');

  if (_pipelineView === 'waterfall') {
    document.getElementById('waterfall-view').classList.remove('hidden');
    renderWaterfallTable(stages, s);
  } else if (_pipelineView === 'flow') {
    document.getElementById('flow-view').classList.remove('hidden');
    renderFlowGraph(stages, s);
  } else {
    document.getElementById('log-view').classList.remove('hidden');
    renderAccordionView(stages, s);
  }
}

function setPipelineView(view) {
  _pipelineView = view;
  _stageFingerprint = '';
  document.getElementById('btn-waterfall-view').classList.toggle('active', view === 'waterfall');
  document.getElementById('btn-flow-view').classList.toggle('active', view === 'flow');
  document.getElementById('btn-log-view').classList.toggle('active', view === 'log');
  if (_selectedId && _sessions[_selectedId]) renderPipelineView(_selectedId, _sessions[_selectedId]);
}

function toggleThinking() {
  _showThinking = !_showThinking;
  document.getElementById('btn-thinking').classList.toggle('active', _showThinking);
  _stageFingerprint = '';
  if (_selectedId && _sessions[_selectedId]) renderPipelineView(_selectedId, _sessions[_selectedId]);
}

function toggleText() {
  _showText = !_showText;
  document.getElementById('btn-text').classList.toggle('active', _showText);
  _stageFingerprint = '';
  if (_selectedId && _sessions[_selectedId]) renderPipelineView(_selectedId, _sessions[_selectedId]);
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

    html += `<div class="wf-row${selected}" data-stage-id="${st.id}" onclick="selectWaterfallStage('${st.id}')">
      <div class="wf-name">
        <div class="wf-icon st-${st.state}">${icon}</div>
        <span>${esc(st.label)}</span>
        ${costText ? `<span class="wf-cost">${costText}</span>` : ''}
      </div>
      <div class="wf-state st-${st.state}">${stateLabel}</div>
      <div class="wf-bar-container">
        ${barWidth > 0 ? `<div class="wf-bar st-${st.state}" style="left:${barLeft.toFixed(1)}%;width:${barWidth.toFixed(1)}%"></div>` : ''}
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

  if (st.type === 'validation' && m.verdict) {
    html += '<div class="wf-detail-summary">';
    html += `<span class="wf-verdict ${m.verdict}">${esc(m.verdict)}</span>`;
    if (m.confidence != null) html += ` <span style="font-size:0.78rem;color:var(--text-muted)">${(m.confidence * 100).toFixed(0)}%</span>`;
    if (m.cost) html += ` <span style="font-size:0.78rem;color:var(--text-muted)">${fmtCost(m.cost)}</span>`;
    if (m.summary) html += `<div style="margin-top:0.3rem">${esc(m.summary)}</div>`;
    if (m.concerns && m.concerns.length) {
      html += '<ul class="wf-concerns">' + m.concerns.map(c => `<li>${esc(c)}</li>`).join('') + '</ul>';
    }
    html += '</div>';
  }

  if (st.type === 'commit' && m.sha) {
    html += `<div class="wf-detail-summary" style="font-family:var(--font-mono);font-size:0.78rem;word-break:break-all">${esc(m.sha)}</div>`;
  }

  if (st.type === 'failure' && m.errors) {
    html += '<div class="wf-detail-summary">' + (m.errors || []).map(e => `<div style="color:var(--red)">${esc(e)}</div>`).join('') + '</div>';
  }

  if (st.type === 'retry') {
    html += `<div class="wf-detail-summary">Retry count: ${m.retryCount || 1}. Triggered by PARTIAL verdict.</div>`;
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

function renderWfEventRow(ev) {
  const e = enrichEvent(ev);
  if (!e.body && !e.chip) return '';
  return `<div class="wf-event ${e.cls}">
    <span class="ev-icon">${e.icon}</span>
    ${e.chip ? `<span class="ev-chip">${esc(e.chip)}</span>` : '<span></span>'}
    <span class="ev-body">${esc(e.body)}</span>
    ${e.ts ? `<span class="ev-ts">${e.ts}</span>` : '<span></span>'}
  </div>`;
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

/* ═══════════════════════════════════════════════════════════════
   FLOW GRAPH (DAG VISUALIZATION)
   ═══════════════════════════════════════════════════════════════ */

const FLOW_COLORS = {
  completed: { fill: '#064e3b', stroke: '#34D399', text: '#34D399', glow: 'rgba(52,211,153,0.25)' },
  running:   { fill: '#0c1a3d', stroke: '#60A5FA', text: '#60A5FA', glow: 'rgba(96,165,250,0.35)' },
  failed:    { fill: '#2d0f0f', stroke: '#F87171', text: '#F87171', glow: 'rgba(248,113,113,0.25)' },
  warning:   { fill: '#2d1a08', stroke: '#FBBF24', text: '#FBBF24', glow: 'rgba(251,191,36,0.25)' },
  pending:   { fill: '#1a1a2e', stroke: '#4B5563', text: '#6B7280', glow: 'rgba(75,85,99,0.1)' },
};

const FLOW_NODE = { w: 156, h: 64, rx: 10 };
const FLOW_GAP = { x: 56, y: 20 };

function layoutFlowGraph(stages) {
  /* Separate stages into columns:
     col 0: task
     col 1: preflight
     col 2: orchestration/execution
     col 3: validation
     col 4: retry/merge
     col 5: commit/failure
  */
  const colOrder = ['task', 'preflight', 'orchestration', 'execution', 'validation', 'retry', 'merge', 'commit', 'failure'];

  /* Group stages by their column position */
  const colMap = new Map();
  for (const st of stages) {
    const colIdx = colOrder.indexOf(st.type);
    if (colIdx === -1) continue;
    if (!colMap.has(colIdx)) colMap.set(colIdx, []);
    colMap.get(colIdx).push(st);
  }

  /* Build final columns in order */
  const sortedCols = [...colMap.keys()].sort((a, b) => a - b);

  const nodes = [];
  const edges = [];
  let colX = 40;

  const nodeMap = new Map(); /* stageId -> {x, y, w, h, stage} */

  for (const colIdx of sortedCols) {
    const colStages = colMap.get(colIdx);

    for (let i = 0; i < colStages.length; i++) {
      const st = colStages[i];
      const y = 40 + i * (FLOW_NODE.h + FLOW_GAP.y);
      nodeMap.set(st.id, { x: colX, y, w: FLOW_NODE.w, h: FLOW_NODE.h, stage: st });
      nodes.push({ ...st, x: colX, y, w: FLOW_NODE.w, h: FLOW_NODE.h });
    }
    colX += FLOW_NODE.w + FLOW_GAP.x;
  }

  /* Build edges based on pipeline flow */
  for (let i = 1; i < stages.length; i++) {
    const st = stages[i];
    const prev = stages[i - 1];
    if (nodeMap.has(prev.id) && nodeMap.has(st.id)) {
      edges.push({ from: prev.id, to: st.id, state: st.state });
    }
  }

  /* Deduplicate edges */
  const edgeSet = new Set();
  const uniqueEdges = edges.filter(e => {
    const key = e.from + '->' + e.to;
    if (edgeSet.has(key)) return false;
    edgeSet.add(key);
    return true;
  });

  /* Center nodes vertically around the tallest column */
  let maxH = 0;
  const colNodes = new Map();
  for (const n of nodes) {
    if (!colNodes.has(n.x)) colNodes.set(n.x, []);
    colNodes.get(n.x).push(n);
  }
  for (const [, col] of colNodes) {
    const colH = col.length * (FLOW_NODE.h + FLOW_GAP.y) - FLOW_GAP.y;
    if (colH > maxH) maxH = colH;
  }
  for (const [, col] of colNodes) {
    const colH = col.length * (FLOW_NODE.h + FLOW_GAP.y) - FLOW_GAP.y;
    const offset = (maxH - colH) / 2;
    for (const n of col) {
      n.y += offset;
      const nm = nodeMap.get(n.id);
      if (nm) nm.y = n.y;
    }
  }

  const svgW = colX + 40;
  const svgH = maxH + 120;

  return { nodes, edges: uniqueEdges, nodeMap, svgW, svgH };
}

function svgEdgePath(fromNode, toNode) {
  const x1 = fromNode.x + fromNode.w;
  const y1 = fromNode.y + fromNode.h / 2;
  const x2 = toNode.x;
  const y2 = toNode.y + toNode.h / 2;
  const cp = Math.abs(x2 - x1) * 0.45;
  return `M${x1},${y1} C${x1 + cp},${y1} ${x2 - cp},${y2} ${x2},${y2}`;
}

let _flowSelectedStageId = null;

function renderFlowGraph(stages, session) {
  const container = document.getElementById('flow-graph-container');
  const layout = layoutFlowGraph(stages);
  const { nodes, edges, nodeMap, svgW, svgH } = layout;
  const timing = computeTimingInfo(stages, session);

  /* Build SVG */
  let svg = `<svg class="flow-svg" viewBox="0 0 ${svgW} ${svgH}" width="${svgW}" height="${svgH}" xmlns="http://www.w3.org/2000/svg">`;

  /* Defs: filters, gradients, markers */
  svg += `<defs>`;
  for (const [state, colors] of Object.entries(FLOW_COLORS)) {
    svg += `<filter id="glow-${state}" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feFlood flood-color="${colors.glow}" result="color"/>
      <feComposite in="color" in2="blur" operator="in" result="shadow"/>
      <feMerge><feMergeNode in="shadow"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>`;
  }
  /* Animated dash pattern for running edges */
  svg += `<style>
    @keyframes flow-dash { to { stroke-dashoffset: -20; } }
    .flow-edge-running { animation: flow-dash 1s linear infinite; }
    .flow-node-group { cursor: pointer; }
    .flow-node-group:hover .flow-node-rect { filter: brightness(1.3); }
    .flow-node-group.selected .flow-node-rect { stroke-width: 2.5; }
    @keyframes flow-pulse { 0%,100% { opacity: 0.4; } 50% { opacity: 1; } }
    .flow-running-indicator { animation: flow-pulse 2s ease-in-out infinite; }
  </style>`;
  svg += `</defs>`;

  /* Render edges */
  for (const edge of edges) {
    const fromN = nodeMap.get(edge.from);
    const toN = nodeMap.get(edge.to);
    if (!fromN || !toN) continue;
    const colors = FLOW_COLORS[edge.state] || FLOW_COLORS.pending;
    const path = svgEdgePath(fromN, toN);
    const isRunning = edge.state === 'running';
    const dashAttrs = isRunning ? `stroke-dasharray="6 4" class="flow-edge-running"` : '';
    const opacity = edge.state === 'pending' ? 0.3 : 0.5;
    svg += `<path d="${path}" fill="none" stroke="${colors.stroke}" stroke-width="1.5" opacity="${opacity}" ${dashAttrs}/>`;
    /* Glow layer for completed/running */
    if (edge.state === 'completed' || edge.state === 'running') {
      svg += `<path d="${path}" fill="none" stroke="${colors.stroke}" stroke-width="4" opacity="0.1" ${dashAttrs}/>`;
    }
  }

  /* Render nodes */
  for (const node of nodes) {
    const colors = FLOW_COLORS[node.state] || FLOW_COLORS.pending;
    const icon = STAGE_ICONS[node.type] || '\u25CF';
    const isSelected = _flowSelectedStageId === node.id;
    const selClass = isSelected ? ' selected' : '';
    const t = timing.timingMap.get(node.id);
    const durationText = t && t.duration > 0 ? fmtDuration(t.duration) : '';

    let costText = '';
    if (node.meta) {
      if (node.meta.cost) costText = fmtCost(node.meta.cost);
      else if (node.meta.result && node.meta.result.cost_usd) costText = fmtCost(node.meta.result.cost_usd);
    }

    svg += `<g class="flow-node-group${selClass}" data-stage-id="${node.id}" onclick="selectFlowNode('${node.id}')">`;

    /* Node background */
    svg += `<rect class="flow-node-rect" x="${node.x}" y="${node.y}" width="${node.w}" height="${node.h}"
      rx="${FLOW_NODE.rx}" fill="${colors.fill}" stroke="${colors.stroke}" stroke-width="1.5"
      filter="url(#glow-${node.state})"/>`;

    /* Running pulse ring */
    if (node.state === 'running') {
      svg += `<rect class="flow-running-indicator" x="${node.x - 3}" y="${node.y - 3}"
        width="${node.w + 6}" height="${node.h + 6}" rx="${FLOW_NODE.rx + 2}"
        fill="none" stroke="${colors.stroke}" stroke-width="1" opacity="0.4"/>`;
    }

    /* Icon + label */
    const textX = node.x + 12;
    const labelY = node.y + (durationText || costText ? 22 : 28);
    svg += `<text x="${textX}" y="${labelY}" fill="${colors.text}" font-size="11" font-weight="600" font-family="system-ui,sans-serif">`;
    svg += `<tspan>${icon} </tspan>`;
    /* Truncate label */
    const maxChars = 14;
    const label = node.label.length > maxChars ? node.label.slice(0, maxChars - 1) + '\u2026' : node.label;
    svg += `${esc(label)}</text>`;

    /* Status badge */
    const stateLabel = STATE_LABELS[node.state] || node.state;
    svg += `<text x="${node.x + node.w - 10}" y="${labelY}" fill="${colors.text}" font-size="9"
      font-weight="500" text-anchor="end" opacity="0.8" font-family="system-ui,sans-serif">${stateLabel}</text>`;

    /* Duration / cost sub-line */
    if (durationText || costText) {
      const subParts = [durationText, costText].filter(Boolean).join(' \u00B7 ');
      svg += `<text x="${textX}" y="${node.y + 42}" fill="${colors.text}" font-size="9" opacity="0.55"
        font-family="system-ui,sans-serif">${esc(subParts)}</text>`;
    }

    /* Progress bar for running stages */
    if (node.state === 'running' && t && t.duration > 0) {
      const barY = node.y + node.h - 6;
      const barW = node.w - 20;
      svg += `<rect x="${node.x + 10}" y="${barY}" width="${barW}" height="2" rx="1" fill="${colors.stroke}" opacity="0.15"/>`;
      svg += `<rect x="${node.x + 10}" y="${barY}" width="${barW * 0.6}" height="2" rx="1" fill="${colors.stroke}" opacity="0.5">
        <animate attributeName="width" from="0" to="${barW}" dur="3s" repeatCount="indefinite"/>
      </rect>`;
    }

    svg += `</g>`;
  }

  svg += `</svg>`;
  container.innerHTML = svg;

  /* Show detail for selected node */
  if (_flowSelectedStageId) {
    const st = stages.find(s => s.id === _flowSelectedStageId);
    if (st) renderFlowDetail(st, session);
    else closeFlowDetail();
  }
}

function selectFlowNode(stageId) {
  if (_flowSelectedStageId === stageId) {
    closeFlowDetail();
    return;
  }
  _flowSelectedStageId = stageId;
  _stageFingerprint = '';
  if (_selectedId && _sessions[_selectedId]) renderPipelineView(_selectedId, _sessions[_selectedId]);
  setTimeout(() => {
    const panel = document.getElementById('flow-detail-panel');
    if (panel && !panel.classList.contains('hidden')) {
      panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, 50);
}

function closeFlowDetail() {
  _flowSelectedStageId = null;
  document.getElementById('flow-detail-panel').classList.add('hidden');
}

function renderFlowDetail(st, session) {
  const panel = document.getElementById('flow-detail-panel');
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
    <button class="wf-dh-close" onclick="closeFlowDetail()" title="Close">&times;</button>
  </div>`;

  html += renderDetailSummary(st);
  html += '<div class="wf-detail-body" id="flow-detail-body">';
  const events = filterEvents(st.events);
  if (events.length > 0) {
    const MAX = 200;
    const rendered = events.length > MAX ? events.slice(-MAX) : events;
    if (events.length > MAX) html += `<div class="wf-events-empty">${events.length - MAX} older events hidden</div>`;
    html += rendered.map(renderWfEventRow).join('');
  } else {
    html += '<div class="wf-events-empty">No events recorded.</div>';
  }
  html += '</div>';

  panel.innerHTML = html;
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
      return `<a href="#/task/${d}" style="text-decoration:none"><span class="th-badge" style="${style};font-size:0.65rem;cursor:pointer">#${d} ${esc(dState)}</span></a>`;
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
    const el = $('#config-bar');
    if (!cfg || !Object.keys(cfg).length) { el.style.display = 'none'; return; }
    el.style.display = '';
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
  } catch (e) { $('#config-bar').style.display = 'none'; }
}

/* ── Data Fetching ─────────────────────────────────────────── */
async function fetchSessions() {
  try {
    const res = await fetch('/api/sessions');
    const data = await res.json();
    _sessions = data.sessions || {};
    renderSidebar();
    if (!_selectedId) renderOverview();
    if (_selectedId && _sessions[_selectedId]) {
      const s = _sessions[_selectedId];
      const fp = s.state + '|' + (s.milestone_count || 0) + '|' + (s.total_cost_usd || 0) + '|' +
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
$('#task-search').addEventListener('input', renderSidebar);
$('#state-filter').addEventListener('change', renderSidebar);
window.addEventListener('hashchange', handleHash);

async function init() {
  await Promise.all([loadConfig(), fetchSessions(), fetchLive()]);
  if (location.hash) handleHash();
  else renderOverview();
  setInterval(fetchSessions, 3000);
  setInterval(fetchLive, 3000);
}

init();
