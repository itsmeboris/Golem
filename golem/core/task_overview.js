/* golem/core/task_overview.js — Overview tab: task list + preview panel.
 * Depends on: task_api.js (S, fetchSessions, fetchParsedTrace, PHASE_COLORS, isTaskRunning).
 */
'use strict';

let _ovPage = 0;
const _OV_PAGE_SIZE = 25;
let _ovFilter = '';
let _ovStateFilter = '';  // '' = all, 'running', 'completed', 'failed', 'detected'

async function renderOverview() {
  const listEl = document.getElementById('ov-task-list');
  if (!listEl) return;

  // Show skeleton loaders while data loads
  if (Object.keys(S.sessions).length === 0) {
    listEl.innerHTML = [1, 2, 3].map(() =>
      '<div class="skeleton skeleton-card"></div>'
    ).join('');
  }

  const sessions = await fetchSessions();
  S.sessions = sessions;

  listEl.innerHTML = '';

  if (Object.keys(sessions).length === 0) {
    listEl.innerHTML = `<div class="ov-empty-state">
      <div class="ov-empty-icon">🚀</div>
      <h3>No tasks yet</h3>
      <p>Submit a task to get started:</p>
      <code>golem run "Fix the bug in auth.py"</code>
      <p>or use the API:</p>
      <code>curl -X POST /api/submit -d '{"prompt":"..."}'</code>
    </div>`;
    return;
  }

  // Sort: running first, then by updated_at desc
  let sorted = Object.entries(sessions).sort(([, a], [, b]) => {
    const aRun = isTaskRunning(a);
    const bRun = isTaskRunning(b);
    if (aRun !== bRun) return bRun - aRun;
    return (b.updated_at || '').localeCompare(a.updated_at || '');
  });

  // Apply search filter
  if (_ovFilter) {
    const q = _ovFilter.toLowerCase();
    sorted = sorted.filter(([eventId, session]) => {
      const id = String(session.parent_issue_id || session.id || eventId).toLowerCase();
      const subject = (session.parent_subject || '').toLowerCase();
      const state = (session.state || '').toLowerCase();
      return id.includes(q) || subject.includes(q) || state.includes(q);
    });
  }

  // Apply state filter
  if (_ovStateFilter) {
    sorted = sorted.filter(([, session]) => {
      return (session.state || '').toLowerCase() === _ovStateFilter;
    });
  }

  if (sorted.length === 0) {
    listEl.innerHTML = `<div class="ov-empty-state">
      <div class="ov-empty-icon">🔍</div>
      <h3>No matching tasks</h3>
      <p>Try adjusting your search or state filter.</p>
    </div>`;
    // Still render pagination (which will be empty)
    _renderPagination(0, 0);
    return;
  }

  // Pagination
  const totalItems = sorted.length;
  const totalPages = Math.max(1, Math.ceil(totalItems / _OV_PAGE_SIZE));
  _ovPage = Math.min(_ovPage, totalPages - 1);
  const start = _ovPage * _OV_PAGE_SIZE;
  const pageItems = sorted.slice(start, start + _OV_PAGE_SIZE);

  for (const [eventId, session] of pageItems) {
    listEl.appendChild(renderTaskRow(eventId, session));
  }

  // Update count with filter info
  const countEl = document.getElementById('ov-task-count');
  if (countEl) {
    const total = Object.keys(sessions).length;
    if (totalItems < total) {
      countEl.textContent = `${totalItems} of ${total} tasks`;
    } else {
      countEl.textContent = `${total} tasks`;
    }
  }

  // Render pagination controls
  _renderPagination(totalPages, totalItems);

  updateTopStats(sessions);

  if (S.selectedTaskId) {
    renderPreview(S.selectedTaskId);
    // Highlight selected row
    listEl.querySelectorAll('.ov-task').forEach(r => {
      r.classList.toggle('selected', r.dataset.eventId === S.selectedTaskId);
    });
  } else if (pageItems.length > 0) {
    S.selectedTaskId = pageItems[0][0];
    renderPreview(S.selectedTaskId);
    const firstRow = listEl.querySelector('.ov-task');
    if (firstRow) firstRow.classList.add('selected');
  }
}

function _renderPagination(totalPages, totalItems) {
  const el = document.getElementById('ov-pagination');
  if (!el) return;
  if (totalPages <= 1) { el.innerHTML = ''; return; }

  let html = '';
  html += `<button class="ov-page-btn" ${_ovPage === 0 ? 'disabled' : ''} data-page="${_ovPage - 1}">\u2190 Prev</button>`;
  html += `<span class="ov-page-info">Page ${_ovPage + 1} of ${totalPages}</span>`;
  html += `<button class="ov-page-btn" ${_ovPage >= totalPages - 1 ? 'disabled' : ''} data-page="${_ovPage + 1}">Next \u2192</button>`;
  el.innerHTML = html;

  el.querySelectorAll('.ov-page-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      _ovPage = parseInt(btn.dataset.page, 10);
      renderOverview();
    });
  });
}

function _initOverviewControls() {
  const searchEl = document.getElementById('ov-search');
  if (searchEl) {
    searchEl.addEventListener('input', () => {
      _ovFilter = searchEl.value.trim();
      _ovPage = 0;
      renderOverview();
    });
  }
  const stateEl = document.getElementById('ov-state-filter');
  if (stateEl) {
    stateEl.addEventListener('change', () => {
      _ovStateFilter = stateEl.value;
      _ovPage = 0;
      renderOverview();
    });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initOverviewControls);
} else {
  _initOverviewControls();
}

function renderTaskRow(eventId, session) {
  const state = session.state || '';
  const chipClass = _stateToChipClass(state);
  const stateLabel = state.toLowerCase();
  const issueId = session.parent_issue_id || session.id || '';
  const subject = subjectTitle(session) || eventId;
  const cost = session.total_cost_usd != null ? fmtCost(session.total_cost_usd) : '—';
  const phase = session.supervisor_phase || '';
  const activity = phase ? esc(phase) : '';

  // Dependency and dependent chips
  const deps = session.depends_on || [];
  const dependents = Object.entries(S.sessions || {})
    .filter(([, s]) => (s.depends_on || []).includes(eventId))
    .map(([id]) => id);

  let depsHtml = '';
  const renderDepChip = (depId) => {
    const depSess = S.sessions[depId];
    const depClass = depSess ? _stateToChipClass(depSess.state) : 'waiting';
    const depNum = String(depId).replace(/^golem-(\d+).*/, '$1');
    return `<span class="ov-dep-chip ${depClass}"><span class="dep-dot"></span>#${esc(depNum)}</span>`;
  };
  if (deps.length > 0 || dependents.length > 0) {
    let sections = '';
    if (deps.length > 0) {
      sections += `<span class="ov-task-deps">
        <span class="ov-dep-arrow"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 3L6 8l4 5"/></svg></span>
        ${deps.map(renderDepChip).join('')}
      </span>`;
    }
    if (dependents.length > 0) {
      sections += `<span class="ov-task-deps">
        <span class="ov-dep-arrow"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 3l4 5-4 5"/></svg></span>
        ${dependents.map(renderDepChip).join('')}
      </span>`;
    }
    depsHtml = sections;
  }

  const div = document.createElement('div');
  div.className = 'ov-task';
  div.dataset.eventId = eventId;
  div.innerHTML = `
    <span class="ov-task-id copy-target" onclick="event.stopPropagation();copyToClipboard('${esc(String(eventId))}')" title="Click to copy">#${esc(String(issueId))}</span>
    <span class="ov-task-subject">${esc(truncText(subject, 60))}</span>
    ${activity ? `<span class="ov-task-activity">${activity}</span>` : '<span class="ov-task-activity"></span>'}
    <span class="ov-task-badge ${chipClass}">${esc(stateLabel)}</span>
    <span class="ov-task-cost">${cost}</span>
    ${depsHtml}
  `;
  div.addEventListener('click', () => {
    document.querySelectorAll('.ov-task').forEach(r => r.classList.remove('selected'));
    div.classList.add('selected');
    S.selectedTaskId = eventId;
    renderPreview(eventId);
  });
  div.addEventListener('dblclick', () => selectTask(eventId));

  return div;
}

async function renderPreview(eventId) {
  const previewEl = document.getElementById('ov-preview');
  if (!previewEl) return;

  const session = S.sessions[eventId];
  if (!session) {
    previewEl.innerHTML = '<div style="padding:1rem;color:var(--text-muted)">No task selected.</div>';
    return;
  }

  // Show a loading spinner while trace data is fetched
  if (!S.parsedTraces[eventId]) {
    previewEl.innerHTML = '<div class="loading-overlay"><div class="loading-spinner"></div>Loading\u2026</div>';
  }

  const running = isTaskRunning(session);
  const issueId = session.parent_issue_id || session.id || '';
  const subject = subjectTitle(session) || eventId;
  const cost = session.total_cost_usd != null ? fmtCost(session.total_cost_usd) : '—';
  const dur = session.duration_seconds ? fmtDuration(session.duration_seconds) : '';

  const trace = await fetchParsedTrace(eventId);
  let phaseStripHtml = trace ? renderPhaseStrip(trace, running, session) : '';

  // Show a pre-flight-only strip when running with no trace yet
  if (!phaseStripHtml && running && session.event_log && session.event_log.length > 0) {
    const pfColor = PHASE_COLORS.PREFLIGHT || 'var(--cyan, #5eead4)';
    phaseStripHtml = `<div style="display:flex;gap:2px;padding:0.5rem 1rem;border-bottom:1px solid var(--border-subtle);background:var(--bg-surface)">
      <div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:2px">
        <div style="width:100%;height:4px;border-radius:2px;background:var(--bg-elevated);overflow:hidden">
          <div style="width:60%;height:100%;background:${pfColor};border-radius:2px;animation:grow 2s ease-in-out infinite alternate"></div>
        </div>
        <span style="font-size:0.6rem;color:${pfColor};font-weight:600">PRE-FLIGHT</span>
      </div>
    </div>`;
  }

  let headerExtra = '';
  if (running) {
    const phase = session.supervisor_phase || '';
    headerExtra = `<p>running${dur ? ' · ' + dur : ''}${cost !== '—' ? ' · ' + cost : ''}${phase ? ' · ' + esc(phase) + ' phase' : ''}</p>`;
  } else {
    const agents = (trace && trace.totals) ? (trace.totals.total_agents || 0) : 0;
    headerExtra = `<p>completed${dur ? ' · ' + dur : ''}${cost !== '—' ? ' · ' + cost : ''}${agents ? ' · ' + agents + ' agents' : ''}</p>`;
  }

  let bodyHtml = '';
  if (running && trace) {
    bodyHtml = renderLiveTraceStream(trace);
  } else if (running) {
    // Pre-flight: no trace data yet
    const events = (session.event_log || []);
    bodyHtml = '<div style="padding:1rem">';
    bodyHtml += '<div class="tl-live-badge" style="margin-bottom:0.75rem"><span class="live-dot"></span>Starting</div>';
    for (const ev of events) {
      bodyHtml += `<div style="padding:0.2rem 0;font-size:0.75rem;color:var(--text-secondary)">${esc(ev.summary || '')}</div>`;
    }
    if (!events.length) bodyHtml += '<div style="color:var(--text-muted);font-size:0.75rem">Waiting for trace data\u2026</div>';
    bodyHtml += '<div class="ov-trace-cursor" style="margin-top:0.5rem"></div>';
    bodyHtml += '</div>';
  } else if (trace) {
    bodyHtml = renderSummaryCard(trace, session);
  }

  const liveBadge = running
    ? `<div class="tl-live-badge"><span class="live-dot"></span>Live</div>`
    : '';

  previewEl.innerHTML = `
    <div class="ov-preview-header" style="display:flex;align-items:center;gap:0.6rem">
      <div style="flex:1">
        <h3>#${esc(String(issueId))} — ${esc(truncText(subject, 50))}</h3>
        ${headerExtra}
      </div>
      ${liveBadge}
      <button data-select-task="${esc(eventId)}"
        style="background:var(--bg-elevated);border:1px solid var(--border-subtle);color:var(--text-secondary);padding:0.25rem 0.6rem;border-radius:var(--radius-sm);cursor:pointer;font:inherit;font-size:0.72rem">
        Full trace →
      </button>
    </div>
    <div class="ov-preview-body" style="padding:0">
      ${phaseStripHtml}
      ${bodyHtml}
    </div>
  `;

  // Attach click handler via data-attribute (avoids inline onclick XSS surface)
  const selectBtn = previewEl.querySelector('[data-select-task]');
  if (selectBtn) {
    selectBtn.addEventListener('click', () => selectTask(selectBtn.dataset.selectTask));
  }
}

function renderPhaseStrip(trace, isRunning, session) {
  const phases = trace.phases || [];
  if (phases.length === 0) return '';

  // Pre-flight bar
  const pfEvents = typeof _getPreflightEvents === 'function'
    ? _getPreflightEvents((session && session.event_log) || [])
    : [];
  const pfFirst = pfEvents.length > 0 ? pfEvents[0].timestamp : 0;
  const pfLast = pfEvents.length > 0 ? pfEvents[pfEvents.length - 1].timestamp : 0;
  const pfDurMs = pfFirst && pfLast ? (pfLast - pfFirst) * 1000 : 0;

  // Calculate proportional flex values based on duration
  const totalMs = phases.reduce((sum, p) => sum + (p.duration_ms || 0), 0) + pfDurMs;
  const phaseNames = ['UNDERSTAND', 'PLAN', 'BUILD', 'REVIEW', 'VERIFY'];

  // Build a set of phase names present in the trace (case-insensitive)
  const tracePhaseNames = new Set(phases.map(p => (p.name || '').toUpperCase()));
  // The active phase during runtime is the last phase present in the trace
  const lastTracePhase = phases.length > 0 ? (phases[phases.length - 1].name || '').toUpperCase() : null;
  const activePhase = isRunning ? lastTracePhase : null;

  // Pre-flight item
  let pfItem = '';
  if (pfEvents.length > 0) {
    const pfColor = PHASE_COLORS.PREFLIGHT || 'var(--cyan, #5eead4)';
    const MIN_FLEX = 14;
    const rawFlex = totalMs > 0 && pfDurMs > 0 ? Math.round(pfDurMs / totalMs * 100) : 0;
    const flexVal = Math.max(rawFlex, MIN_FLEX);
    const durText = pfDurMs > 0 ? ` ${fmtDurationMs(pfDurMs)}` : '';
    pfItem = `<div style="flex:${flexVal};display:flex;flex-direction:column;align-items:center;gap:2px">
      <div style="width:100%;height:4px;border-radius:2px;background:${pfColor}"></div>
      <span style="font-size:0.6rem;color:${pfColor};font-weight:600">PRE-FLIGHT${esc(durText)}</span>
    </div>`;
  }

  const items = phaseNames.map(name => {
    const phase = phases.find(p => (p.name || '').toUpperCase() === name);
    const color = PHASE_COLORS[name] || 'var(--text-muted)';
    const dur = phase && phase.duration_ms ? phase.duration_ms : 0;
    const isActive = name === activePhase;
    // Phase is "reached" if it exists in the trace data (even without duration_ms)
    const phaseReached = tracePhaseNames.has(name);
    const hasData = phase && phase.duration_ms > 0;

    // Skip phases that haven't been reached yet (whether running or completed)
    if (!phaseReached) return '';

    // Ensure every reached phase gets at least 8% of the bar width so short
    // phases (UNDERSTAND, PLAN) remain readable. Unreached phases get flex:1.
    const MIN_FLEX = 14;
    const rawFlex = totalMs > 0 && dur > 0 ? Math.round(dur / totalMs * 100) : 0;
    const flexVal = phaseReached ? Math.max(rawFlex, MIN_FLEX) : 1;

    let barStyle = `width:100%;height:4px;border-radius:2px;`;
    let label = '';
    if (!phaseReached) {
      // Phase not yet reached — gray bar (only shown for running tasks)
      barStyle += `background:var(--bg-elevated)`;
      label = `<span style="font-size:0.6rem;color:var(--text-muted)">${name}</span>`;
    } else if (isActive && isRunning) {
      // Currently active phase — animated bar
      barStyle += `background:var(--bg-elevated);overflow:hidden`;
      const innerBar = `<div style="width:60%;height:100%;background:${color};border-radius:2px;animation:grow 2s ease-in-out infinite alternate"></div>`;
      label = `<span style="font-size:0.6rem;color:${color};font-weight:600">${name}</span>`;
      return `<div style="flex:${flexVal};display:flex;flex-direction:column;align-items:center;gap:2px">
        <div style="${barStyle}">${innerBar}</div>
        ${label}
      </div>`;
    } else {
      // Completed phase — solid bar with name and duration
      barStyle += `background:${color}`;
      const durText = hasData && !isActive ? ` ${fmtDurationMs(phase.duration_ms)}` : '';
      label = `<span style="font-size:0.6rem;color:${color};font-weight:600">${name}${esc(durText)}</span>`;
    }

    return `<div style="flex:${flexVal};display:flex;flex-direction:column;align-items:center;gap:2px">
      <div style="${barStyle}"></div>
      ${label}
    </div>`;
  });

  return `<div style="display:flex;gap:2px;padding:0.5rem 1rem;border-bottom:1px solid var(--border-subtle);background:var(--bg-surface)">
    ${pfItem}${items.join('')}
  </div>`;
}

function renderLiveTraceStream(trace) {
  const phases = trace.phases || [];
  let html = '<div class="ov-live-trace">';

  for (const phase of phases) {
    const name = phase.name || '';
    const color = PHASE_COLORS[name.toUpperCase()] || 'var(--text-muted)';
    const dur = phase.duration_ms ? fmtDurationMs(phase.duration_ms) : '';
    html += `<div class="ov-trace-ev phase" style="color:${color}">── ${esc(name)}${dur ? ' (' + dur + ')' : ''} ──</div>`;

    // Show orchestrator tools
    for (const tool of (phase.orchestrator_tools || []).slice(0, 5)) {
      const tName = tool.name || tool.tool_name || '';
      const tSum = tool.summary || tool.input_summary || '';
      const tCls = _toolNameClass(tName);
      const tColor = tCls === 'read' ? 'var(--blue)' : tCls === 'write' ? 'var(--green)' : tCls === 'bash' ? 'var(--orange)' : 'var(--text-secondary)';
      html += `<div class="ov-trace-ev orch-tool"><span style="color:${tColor}">${esc(tName)}</span> ${esc(truncText(tSum, 60))}</div>`;
    }

    // Show brief text — strip phase markers for cleaner preview
    for (const txt of (phase.orchestrator_text || []).slice(0, 1)) {
      const cleaned = cleanPhaseMarkers(txt);
      if (cleaned) html += `<div class="ov-trace-ev text">${esc(truncText(cleaned, 300))}</div>`;
    }

    // Show agents as collapsible blocks
    for (const agent of (phase.agents || [])) {
      const role = (agent.role || '').toLowerCase();
      let icon = '⚙';
      if (role.includes('reviewer')) icon = '📝';
      else if (role.includes('verif')) icon = '☑';
      const desc = agent.description || agent.desc || '';
      const agentDur = agent.duration_ms ? fmtDurationMs(agent.duration_ms) : '';
      const toolCount = (agent.tool_timeline || []).length;
      const isRunning = !agent.status || agent.status === 'running';

      let agentBodyHtml = '';
      const recentTools = (agent.tool_timeline || []).slice(-5);
      for (const t of recentTools) {
        const tName = t.name || t.tool_name || '';
        const tDesc = t.summary || t.input_summary || '';
        const tCls = _toolNameClass(tName);
        const tColor = tCls === 'read' ? 'var(--blue)' : tCls === 'write' ? 'var(--green)' : tCls === 'bash' ? 'var(--orange)' : 'var(--text-secondary)';
        agentBodyHtml += `<div class="ov-trace-sub tool"><span style="color:${tColor}">${esc(tName)}</span> ${esc(truncText(tDesc, 50))}</div>`;
      }
      if (isRunning) {
        agentBodyHtml += `<div class="ov-trace-cursor" style="margin-left:1.25rem"></div>`;
      }

      html += `<div class="ov-trace-agent-block" onclick="this.classList.toggle('collapsed')">
        <div class="ov-trace-agent-header">
          <span class="ov-trace-agent-icon">${icon}</span>
          <span class="ov-trace-agent-name">${esc(truncText(desc, 40))}</span>
          <span class="ov-trace-agent-role">${esc(role)}</span>
          <span class="ov-trace-agent-stats">${agentDur ? agentDur + ' · ' : ''}${toolCount} tools</span>
          ${isRunning ? `<span class="ov-trace-agent-live"><span class="live-dot" style="width:5px;height:5px"></span></span>` : ''}
          <span class="ov-trace-agent-chevron">▾</span>
        </div>
        <div class="ov-trace-agent-body">${agentBodyHtml}</div>
      </div>`;
    }
  }

  // Live cursor at bottom
  html += `<div class="ov-trace-cursor" style="margin-left:0.75rem"></div>`;
  html += '</div>';
  return html;
}

function renderSummaryCard(trace, session) {
  const totals = trace.totals || {};
  const result = trace.result || trace.final_report || {};
  const agents = trace.phases
    ? trace.phases.flatMap(p => p.agents || [])
    : [];

  // Specs: support both array [{id, pass}] and object {SPEC-1: true}
  const specsObj = result.specs_satisfied || {};
  const specsArr = result.specs || [];
  let specsHtml = '';
  if (Object.keys(specsObj).length > 0) {
    specsHtml = Object.entries(specsObj).map(([id, pass]) =>
      `<span class="tl-spec ${pass ? 'pass' : 'fail'}" style="font-size:0.7rem">${pass ? '✓' : '✗'} ${esc(id)}</span>`
    ).join('');
  } else if (specsArr.length > 0) {
    specsHtml = specsArr.map(s => {
      const pass = s.pass !== false;
      return `<span class="tl-spec ${pass ? 'pass' : 'fail'}" style="font-size:0.7rem">${pass ? '✓' : '✗'} ${esc(s.id || s.name || '')}</span>`;
    }).join('');
  }

  // Tests: support both array [{name, pass}] and object {black: "pass"}
  const testsObj = result.test_results || {};
  const testsArr = result.tests || [];
  let testsHtml = '';
  if (Object.keys(testsObj).length > 0) {
    testsHtml = Object.entries(testsObj).map(([name, val]) => {
      const lower = String(val).toLowerCase();
      const pass = val === true || (/\bpass/i.test(lower) && !/\bfail/i.test(lower));
      const skip = lower === 'not_applicable' || lower === 'skipped' || lower === 'not_run';
      const cls = skip ? 'skip' : (pass ? 'pass' : 'fail');
      const icon = skip ? '—' : (pass ? '✓' : '✗');
      return `<span class="tl-test ${cls}" style="font-size:0.68rem">${icon} ${esc(name)}</span>`;
    }).join('');
  } else if (testsArr.length > 0) {
    testsHtml = testsArr.map(t => {
      const pass = t.pass !== false;
      return `<span class="tl-test ${pass ? 'pass' : 'fail'}" style="font-size:0.68rem">${esc(t.name || t.label || '')}: ${pass ? 'pass' : 'fail'}</span>`;
    }).join('');
  }

  const files = result.files_changed || (session && session.files_changed) || [];
  const filesHtml = files.length > 0
    ? files.slice(0, 6).map(f => `<span>${esc(f)}</span>`).join('')
    : '';

  const agentsHtml = agents.slice(0, 5).map(agent => {
    const role = (agent.role || '').toLowerCase();
    let icon = '⚙';
    let bgColor = 'var(--accent-bg)';
    let fgColor = 'var(--accent)';
    if (role.includes('reviewer')) { icon = '📝'; bgColor = 'var(--orange-bg)'; fgColor = 'var(--orange)'; }
    else if (role.includes('verif')) { icon = '☑'; bgColor = 'var(--green-bg)'; fgColor = 'var(--green)'; }
    const desc = agent.description || agent.desc || '';
    const dur = agent.duration_ms ? fmtDurationMs(agent.duration_ms) : '';
    const toolCount = (agent.tool_timeline || []).length;
    const status = agent.status || agent.verdict || 'completed';
    let statusClass = 'completed';
    if (status.toUpperCase() === 'APPROVED') statusClass = 'approved';
    else if (status.toUpperCase().includes('NEEDS')) statusClass = 'needs-fixes';

    return `<div class="ov-summary-agent">
      <span class="ov-sa-icon" style="background:${bgColor};color:${fgColor}">${icon}</span>
      <span class="ov-sa-desc">${esc(truncText(desc, 35))}</span>
      <span class="ov-sa-meta">${esc(role)}${dur ? ' · ' + dur : ''}${toolCount ? ' · ' + toolCount + ' tools' : ''}</span>
      <span class="tl-agent-status ${statusClass}" style="font-size:0.62rem">${esc(status)}</span>
    </div>`;
  }).join('');

  const totalTokens = totals.total_tokens || 0;
  const totalTools = totals.total_tool_calls || 0;
  const fixCycles = (trace.phases || []).reduce((n, p) => n + (p.fix_cycles || []).length, 0);
  const commit = session ? (session.commit_sha || '') : '';

  const success = result.success !== false && (session?.state || '').toLowerCase() !== 'failed';
  const statusIcon = success ? '✓' : '✗';
  const statusLabel = success ? 'COMPLETE' : 'FAILED';
  const statusColor = success ? 'var(--green)' : 'var(--red)';

  return `<div class="ov-summary">
    <div class="ov-summary-status">
      <span style="color:${statusColor};font-weight:700;font-size:0.9rem">${statusIcon} ${statusLabel}</span>
      ${result.summary ? `<span style="font-size:0.75rem;color:var(--text-secondary);margin-left:0.5rem">${esc(truncText(result.summary, 80))}</span>` : ''}
    </div>
    ${specsHtml ? `<div class="ov-summary-specs">${specsHtml}</div>` : ''}
    ${testsHtml ? `<div class="ov-summary-tests">${testsHtml}</div>` : ''}
    ${filesHtml ? `<div class="ov-summary-section">
      <div class="ov-summary-label">Files Changed</div>
      <div class="ov-summary-files">${filesHtml}</div>
    </div>` : ''}
    ${agentsHtml ? `<div class="ov-summary-section">
      <div class="ov-summary-label">Agents</div>
      <div class="ov-summary-agents">${agentsHtml}</div>
    </div>` : ''}
    <div class="ov-summary-section">
      <div class="ov-summary-label">Metrics</div>
      <div style="display:flex;gap:1rem;font-size:0.75rem;font-family:var(--font-mono);color:var(--text-secondary)">
        ${totalTokens ? `<span>${fmtTokens(totalTokens)} tokens</span>` : ''}
        ${totalTools ? `<span>${totalTools} tool calls</span>` : ''}
        <span>${fixCycles} fix cycle${fixCycles !== 1 ? 's' : ''}</span>
        ${commit ? `<span>commit <span style="color:var(--text-muted)">${esc(commit.slice(0, 7))}</span></span>` : ''}
      </div>
    </div>
  </div>`;
}

function updateTopStats(sessions) {
  const inner = document.getElementById('top-stats-inner');
  if (!inner) return;
  const entries = Object.values(sessions);
  const running = entries.filter(isTaskRunning).length;
  const totalCost = entries.reduce((sum, s) => sum + (s.total_cost_usd || 0), 0);
  const done = entries.filter(s => (s.state || '').toLowerCase() === 'completed').length;
  const failed = entries.filter(s => (s.state || '').toLowerCase() === 'failed').length;
  const clearBtn = failed > 0
    ? `<button class="ov-clear-failed-btn" title="Remove failed tasks from the list">clear failed</button>`
    : '';
  inner.innerHTML = `${running > 0 ? `<span><span class="dot"></span>${running} running</span>` : ''}
    <span>${fmtCost(totalCost)} spent</span><span class="stat-sep"></span><span class="stat-pass">${done}&#10003;</span><span class="stat-fail${failed > 0 ? ' has-fails' : ''}">${failed}&#10007;</span>${clearBtn}`;
  const btn = inner.querySelector('.ov-clear-failed-btn');
  if (btn) {
    btn.addEventListener('click', async () => {
      if (!confirm('Clear all failed sessions? This cannot be undone.')) return;
      const result = await clearFailedSessions();
      if (result.ok) renderOverview();
    });
  }
  renderOverviewStats(sessions);
}

function renderOverviewStats(sessions) {
  const el = document.getElementById('ov-stats-panel');
  if (!el) return;

  const entries = Object.values(sessions);
  if (entries.length === 0) { el.innerHTML = ''; return; }

  // Success-rate sparkline: sort by updated_at, take last 20, compute 1=success 0=fail
  const sorted = entries
    .filter(s => {
      const st = (s.state || '').toLowerCase();
      return st === 'completed' || st === 'failed';
    })
    .sort((a, b) => (a.updated_at || '').localeCompare(b.updated_at || ''))
    .slice(-20);
  const successValues = sorted.map(s => (s.state || '').toLowerCase() === 'completed' ? 1 : 0);
  const sparkHtml = typeof sparkline === 'function' && successValues.length > 0
    ? sparkline(successValues, 80, 20)
    : '';

  // Cost by model bar chart
  const modelCosts = {};
  for (const s of entries) {
    const model = s.model || 'unknown';
    modelCosts[model] = (modelCosts[model] || 0) + (s.total_cost_usd || 0);
  }
  const modelItems = Object.entries(modelCosts)
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([label, value]) => ({
      label: label.length > 10 ? label.slice(-10) : label,
      value: Math.round(value * 1000) / 1000,
      color: 'var(--accent)',
    }));
  const costChartHtml = typeof barChart === 'function' && modelItems.length > 0
    ? barChart(modelItems)
    : '';

  // Phase duration bar chart: collect avg durations across all traced sessions
  const phaseTotals = {};
  const phaseCounts = {};
  for (const s of entries) {
    const phases = (s.phases) || [];
    for (const p of phases) {
      const name = (p.name || '').toUpperCase();
      if (!name) continue;
      phaseTotals[name] = (phaseTotals[name] || 0) + (p.duration_ms || 0);
      phaseCounts[name] = (phaseCounts[name] || 0) + 1;
    }
  }
  const phaseOrder = ['UNDERSTAND', 'PLAN', 'BUILD', 'REVIEW', 'VERIFY'];
  const phaseColors = typeof PHASE_COLORS !== 'undefined' ? PHASE_COLORS : {};
  const phaseItems = phaseOrder
    .filter(name => phaseCounts[name])
    .map(name => ({
      label: name.slice(0, 6),
      value: Math.round(phaseTotals[name] / phaseCounts[name] / 1000),
      color: phaseColors[name] || 'var(--blue)',
    }));
  const phaseChartHtml = typeof barChart === 'function' && phaseItems.length > 0
    ? barChart(phaseItems)
    : '';

  let html = '<div class="ov-stats-section">';
  html += '<div class="ov-stats-title">Analytics</div>';
  html += '<div class="ov-stats-grid">';

  if (sparkHtml) {
    html += `<div class="ov-stats-card">
      <div class="ov-stats-card-label">Success trend</div>
      ${sparkHtml}
    </div>`;
  }

  if (costChartHtml) {
    html += `<div class="ov-stats-card">
      <div class="ov-stats-card-label">Cost by model ($)</div>
      ${costChartHtml}
    </div>`;
  }

  if (phaseChartHtml) {
    html += `<div class="ov-stats-card">
      <div class="ov-stats-card-label">Avg phase duration (s)</div>
      ${phaseChartHtml}
    </div>`;
  }

  html += '</div></div>';
  el.innerHTML = html;
}
