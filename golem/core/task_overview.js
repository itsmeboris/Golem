/* golem/core/task_overview.js — Overview tab: task list + preview panel.
 * Depends on: task_api.js (S, fetchSessions, fetchParsedTrace, PHASE_COLORS, isTaskRunning).
 */
'use strict';

async function renderOverview() {
  const sessions = await fetchSessions();
  S.sessions = sessions;

  const listEl = document.getElementById('ov-task-list');
  if (!listEl) return;
  listEl.innerHTML = '';

  // Sort: running first, then by updated_at desc
  const sorted = Object.entries(sessions).sort(([, a], [, b]) => {
    const aRun = isTaskRunning(a);
    const bRun = isTaskRunning(b);
    if (aRun !== bRun) return bRun - aRun;
    return (b.updated_at || '').localeCompare(a.updated_at || '');
  });

  for (const [eventId, session] of sorted) {
    listEl.appendChild(renderTaskRow(eventId, session));
  }

  const countEl = document.getElementById('ov-task-count');
  if (countEl) countEl.textContent = `${sorted.length} tasks`;
  updateTopStats(sessions);

  if (S.selectedTaskId) {
    renderPreview(S.selectedTaskId);
    // Highlight selected row
    listEl.querySelectorAll('.ov-task').forEach(r => {
      r.classList.toggle('selected', r.dataset.eventId === S.selectedTaskId);
    });
  } else if (sorted.length > 0) {
    S.selectedTaskId = sorted[0][0];
    renderPreview(S.selectedTaskId);
    const firstRow = listEl.querySelector('.ov-task');
    if (firstRow) firstRow.classList.add('selected');
  }
}

function renderTaskRow(eventId, session) {
  const state = session.state || '';
  const chipClass = _stateToChipClass(state);
  const stateLabel = state.toLowerCase();
  const issueId = session.parent_issue_id || session.id || '';
  const subject = session.subject || session.parent_subject || eventId;
  const cost = session.total_cost_usd != null ? fmtCost(session.total_cost_usd) : '—';
  const phase = session.supervisor_phase || '';
  const activity = phase ? esc(phase) : '';

  // Dependency chips
  const deps = session.depends_on || [];
  let depsHtml = '';
  if (deps.length > 0) {
    const chips = deps.map(depId => {
      const depSess = S.sessions[depId];
      const depClass = depSess ? _stateToChipClass(depSess.state) : 'waiting';
      const depNum = String(depId).replace(/^golem-(\d+).*/, '$1');
      return `<span class="ov-dep-chip ${depClass}"><span class="dep-dot"></span>#${esc(depNum)}</span>`;
    }).join('');
    depsHtml = `<span class="ov-task-deps">
      <span class="ov-dep-arrow"><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 3L6 8l4 5"/></svg></span>
      ${chips}
    </span>`;
  }

  const div = document.createElement('div');
  div.className = 'ov-task';
  div.dataset.eventId = eventId;
  div.innerHTML = `
    <span class="ov-task-id">#${esc(String(issueId))}</span>
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

  const running = isTaskRunning(session);
  const issueId = session.parent_issue_id || session.id || '';
  const subject = session.subject || session.parent_subject || eventId;
  const cost = session.total_cost_usd != null ? fmtCost(session.total_cost_usd) : '—';
  const dur = session.duration_seconds ? fmtDuration(session.duration_seconds) : '';

  const trace = await fetchParsedTrace(eventId);
  const phaseStripHtml = trace ? renderPhaseStrip(trace, running) : '';

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

function renderPhaseStrip(trace, isRunning) {
  const phases = trace.phases || [];
  if (phases.length === 0) return '';

  // Calculate proportional flex values based on duration
  const totalMs = phases.reduce((sum, p) => sum + (p.duration_ms || 0), 0);
  const phaseNames = ['UNDERSTAND', 'PLAN', 'BUILD', 'REVIEW', 'VERIFY'];

  // Determine which phase is active (last one with data)
  const lastPhaseWithData = [...phases].reverse().find(p => p.duration_ms);
  const activePhase = isRunning && lastPhaseWithData ? lastPhaseWithData.name : null;

  const items = phaseNames.map(name => {
    const phase = phases.find(p => (p.name || '').toUpperCase() === name);
    const color = PHASE_COLORS[name] || 'var(--text-muted)';
    const dur = phase && phase.duration_ms ? phase.duration_ms : 0;
    const flexVal = totalMs > 0 && dur > 0 ? Math.max(1, Math.round(dur / totalMs * 10)) : 1;
    const isActive = phase && phase.name === activePhase;
    const hasData = phase && phase.duration_ms > 0;

    let barStyle = `width:100%;height:4px;border-radius:2px;`;
    let label = '';
    if (!hasData) {
      barStyle += `background:var(--bg-elevated)`;
      label = `<span style="font-size:0.6rem;color:var(--text-muted)">${name}</span>`;
    } else if (isActive) {
      barStyle += `background:var(--bg-elevated);overflow:hidden`;
      const innerBar = `<div style="width:60%;height:100%;background:${color};border-radius:2px;animation:grow 2s ease-in-out infinite alternate"></div>`;
      label = `<span style="font-size:0.6rem;color:${color};font-weight:600">${name}</span>`;
      return `<div style="flex:${flexVal};display:flex;flex-direction:column;align-items:center;gap:2px">
        <div style="${barStyle}">${innerBar}</div>
        ${label}
      </div>`;
    } else {
      barStyle += `background:${color}`;
      const durLabel = isRunning ? name : fmtDurationMs(phase.duration_ms);
      label = `<span style="font-size:0.6rem;color:${color};font-weight:600">${esc(durLabel)}</span>`;
    }

    return `<div style="flex:${flexVal};display:flex;flex-direction:column;align-items:center;gap:2px">
      <div style="${barStyle}"></div>
      ${label}
    </div>`;
  });

  return `<div style="display:flex;gap:2px;padding:0.5rem 1rem;border-bottom:1px solid var(--border-subtle);background:var(--bg-surface)">
    ${items.join('')}
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

    // Show brief text
    for (const txt of (phase.orchestrator_text || []).slice(0, 1)) {
      html += `<div class="ov-trace-ev text">${esc(truncText(txt, 100))}</div>`;
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
  const result = trace.result || {};
  const agents = trace.phases
    ? trace.phases.flatMap(p => p.agents || [])
    : [];

  const specs = result.specs || [];
  const specsHtml = specs.map(s => {
    const pass = s.pass !== false;
    return `<span class="tl-spec ${pass ? 'pass' : 'fail'}" style="font-size:0.7rem">${pass ? '✓' : '✗'} ${esc(s.id || s.name || '')}</span>`;
  }).join('');

  const tests = result.tests || [];
  const testsHtml = tests.map(t => {
    const pass = t.pass !== false;
    return `<span class="tl-test ${pass ? 'pass' : 'fail'}" style="font-size:0.68rem">${esc(t.name || t.label || '')}: ${pass ? 'pass' : 'fail'}</span>`;
  }).join('');

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

  return `<div class="ov-summary">
    <div class="ov-summary-status">
      <span style="color:var(--green);font-weight:700;font-size:0.9rem">✓ COMPLETE</span>
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
  const stats = document.getElementById('top-stats');
  if (!stats) return;
  const entries = Object.values(sessions);
  const running = entries.filter(isTaskRunning).length;
  const totalCost = entries.reduce((sum, s) => sum + (s.total_cost_usd || 0), 0);
  const done = entries.filter(s => s.state === 'COMPLETED').length;
  const failed = entries.filter(s => s.state === 'FAILED').length;
  stats.innerHTML = `${running > 0 ? `<span><span class="dot"></span>${running} running</span>` : ''}
    <span>${fmtCost(totalCost)} spent</span><span>${done}&#10003; ${failed}&#10007;</span>`;
}
