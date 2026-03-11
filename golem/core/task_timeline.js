/* golem/core/task_timeline.js — Task Detail view: header, metrics, phase sidebar,
 * timeline, agent blocks, fix cycles, toolbar, result block, info tabs.
 * Depends on: task_api.js (S, fetchParsedTrace, PHASE_COLORS, isTaskRunning).
 */
'use strict';

async function renderDetail(eventId, prefetchedTrace) {
  const session = S.sessions[eventId];
  const trace = prefetchedTrace || await fetchParsedTrace(eventId);
  const running = isTaskRunning(session);

  if (!trace) {
    if (!running) return;
    // Pre-flight: show session status while waiting for trace data
    const el = document.getElementById('timeline-scroll');
    if (el) {
      const events = (session && session.event_log) || [];
      let html = '<div style="padding:1.5rem">';
      html += '<div class="tl-live-badge" style="margin-bottom:1rem"><span class="live-dot"></span>Starting</div>';
      for (const ev of events) {
        html += `<div style="padding:0.25rem 0;font-size:0.78rem;color:var(--text-secondary)">${esc(ev.summary || '')}</div>`;
      }
      if (!events.length) html += '<div style="color:var(--text-muted);font-size:0.78rem">Waiting for trace data\u2026</div>';
      html += '<div class="ov-trace-cursor" style="margin-top:0.75rem"></div>';
      html += '</div>';
      el.innerHTML = html;
    }
    return;
  }

  // Cache completed traces client-side
  if (!running && trace) S.parsedTraces[eventId] = trace;

  // Fetch prompt text if not already on the trace object
  if (!trace.prompt) {
    const promptText = await fetchPrompt(eventId);
    if (promptText) trace.prompt = promptText;
  }

  renderDetailHeader(session, trace, running);
  renderMetrics(trace);
  renderLiveStrip(session, trace, running);
  renderPhaseSidebar(trace, running);
  renderToolbar();
  renderTimeline(trace, running);
  renderInfoTabs(trace, session, running);
}

// ── Header ─────────────────────────────────────
function renderDetailHeader(session, trace, running) {
  const el = document.getElementById('td-header');
  if (!el) return;

  const state = session ? session.state : '';
  const chipClass = _stateToChipClass(state);
  const subject = session ? esc(session.subject || session.parent_subject || '') : '';
  const taskId = session ? esc(String(session.parent_issue_id || session.id || '')) : '';
  const mode = session ? esc(session.execution_mode || 'subagent') : 'subagent';
  const liveHtml = running
    ? `<div class="tl-live-badge"><span class="live-dot"></span>Live</div>`
    : '';

  // Dependencies and dependents
  const eventId = S.selectedTaskId || '';
  const deps = (session && session.depends_on) || [];
  const dependents = Object.entries(S.sessions || {})
    .filter(([, s]) => (s.depends_on || []).includes(eventId))
    .map(([id]) => id);

  let depsHtml = '';
  if (deps.length > 0 || dependents.length > 0) {
    const renderDepCard = (depId) => {
      const depSess = S.sessions[depId];
      const depState = depSess ? depSess.state : '';
      const depClass = _stateToChipClass(depState);
      const depNum = String(depSess ? (depSess.parent_issue_id || depSess.id || depId) : depId).replace(/^golem-(\d+).*/, '$1');
      const depSubject = depSess ? esc(truncText(depSess.subject || depSess.parent_subject || '', 30)) : '';
      return `<span class="td-dep-card ${depClass}" data-dep-id="${esc(depId)}">
        <div class="td-dep-info">
          <span class="td-dep-id">#${esc(depNum)}</span>
          ${depSubject ? `<span class="td-dep-subject">${depSubject}</span>` : ''}
        </div>
        <span class="td-dep-meta">${esc(depState.toLowerCase())}</span>
      </span>`;
    };
    let sections = '';
    if (deps.length > 0) {
      sections += `<div class="td-deps-label">Depends on</div><div class="td-deps-list">${deps.map(renderDepCard).join('')}</div>`;
    }
    if (dependents.length > 0) {
      sections += `<div class="td-deps-label">Blocks</div><div class="td-deps-list">${dependents.map(renderDepCard).join('')}</div>`;
    }
    depsHtml = `<div class="td-deps">${sections}</div>`;
  }

  el.innerHTML = `
    <div class="td-top">
      <span class="td-id">#${taskId}</span>
      <span class="td-mode">${mode}</span>
      <span class="td-badge ${chipClass}">${esc(state.toLowerCase())}</span>
      ${liveHtml}
    </div>
    <div class="td-subject">${subject}</div>
    ${depsHtml}
  `;

  // Click handler for dependency cards — navigate to that task
  el.querySelectorAll('.td-dep-card[data-dep-id]').forEach(card => {
    card.addEventListener('click', () => selectTask(card.dataset.depId));
  });
}

// ── Metrics ────────────────────────────────────
function renderMetrics(trace) {
  const el = document.getElementById('td-metrics');
  if (!el) return;

  const totals = trace.totals || {};
  const meta = trace.result_meta || {};
  const cost = meta.total_cost_usd || totals.total_cost_usd;
  const duration = totals.duration_ms || totals.total_duration_ms || meta.duration_ms || 0;
  const agents = totals.subagent_count || totals.total_agents || 0;
  const tools = totals.tool_calls || totals.total_tool_calls || 0;
  const tokens = totals.tokens || totals.total_tokens || 0;
  const fixCycles = (trace.phases || []).reduce((n, p) => n + (p.fix_cycles || []).length, 0);
  const fixColor = fixCycles > 0 ? 'style="color:var(--orange)"' : '';

  el.innerHTML = `
    <div class="metric"><span class="metric-label">Cost</span><span class="metric-value">${fmtCost(cost)}</span></div>
    <div class="metric"><span class="metric-label">Duration</span><span class="metric-value">${fmtDurationMs(duration)}</span></div>
    <div class="metric"><span class="metric-label">Agents</span><span class="metric-value">${agents}</span></div>
    <div class="metric"><span class="metric-label">Tools</span><span class="metric-value">${tools}</span></div>
    <div class="metric"><span class="metric-label">Tokens</span><span class="metric-value">${fmtTokens(tokens)}</span></div>
    <div class="metric"><span class="metric-label">Fix Cycles</span><span class="metric-value" ${fixColor}>${fixCycles}</span></div>
  `;
}

// ── Live Strip ─────────────────────────────────
function renderLiveStrip(session, trace, running) {
  const el = document.getElementById('td-live-strip');
  if (!el) return;

  if (!running) {
    el.style.display = 'none';
    return;
  }

  el.style.display = '';
  const phases = trace.phases || [];
  const lastPhase = phases.length > 0 ? phases[phases.length - 1] : null;
  const phaseName = lastPhase ? esc(lastPhase.name || '') : '';
  const elapsed = session ? fmtDurationMs((session.duration_seconds || 0) * 1000) : '';

  el.innerHTML = `
    <span class="td-live-dot"></span>
    <span class="td-live-text">Auto-updating</span>
    <span class="td-live-phase">${phaseName} phase</span>
    <span class="td-live-elapsed">${elapsed} elapsed</span>
  `;
}

// ── Phase Sidebar ──────────────────────────────
function renderPhaseSidebar(trace, running) {
  const nav = document.getElementById('phase-nav');
  if (!nav) return;

  const phases = trace.phases || [];
  nav.innerHTML = phases.map(p => {
    const name = p.name || '';
    const color = PHASE_COLORS[name.toUpperCase()] || 'var(--text-muted)';
    const dur = p.duration_ms ? fmtDurationMs(p.duration_ms) : '';
    return `<button class="phase-link" data-phase="${esc(name.toLowerCase())}">
      <span class="ph-dot" style="background:${color}"></span>
      ${esc(name)}
      ${dur ? `<span class="ph-dur">${dur}</span>` : ''}
    </button>`;
  }).join('');

  // Attach click handlers via event delegation (avoids inline onclick XSS surface)
  nav.querySelectorAll('.phase-link').forEach(btn => {
    btn.addEventListener('click', () => scrollToPhase(btn.dataset.phase));
  });

  // Activate first phase
  const first = nav.querySelector('.phase-link');
  if (first) first.classList.add('active');

  initScrollSpy();
}

function scrollToPhase(phaseName) {
  const target = document.getElementById('phase-' + phaseName.toLowerCase());
  if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  document.querySelectorAll('.phase-link').forEach(l => l.classList.remove('active'));
  document.querySelector(`.phase-link[data-phase="${phaseName.toLowerCase()}"]`)?.classList.add('active');
}

let _scrollSpyController = null;

function initScrollSpy() {
  const scroll = document.getElementById('timeline-scroll');
  if (!scroll) return;
  // Tear down previous listener to prevent accumulation across poll ticks
  if (_scrollSpyController) _scrollSpyController.abort();
  _scrollSpyController = new AbortController();
  const phases = ['understand', 'plan', 'build', 'review', 'verify'];
  scroll.addEventListener('scroll', () => {
    let current = phases[0];
    for (const p of phases) {
      const target = document.getElementById('phase-' + p);
      if (target && target.offsetTop - scroll.offsetTop <= scroll.scrollTop + 60) current = p;
    }
    document.querySelectorAll('.phase-link').forEach(l =>
      l.classList.toggle('active', l.dataset.phase?.toLowerCase() === current)
    );
  }, { signal: _scrollSpyController.signal });
}

// ── Toolbar ────────────────────────────────────
function renderToolbar() {
  const el = document.getElementById('tl-toolbar');
  if (!el) return;

  el.innerHTML = `
    <button id="btn-prompt" onclick="togglePromptSection()">Prompt</button>
    <button onclick="expandAllPhases()">Expand all</button>
    <button onclick="collapseAllPhases()">Collapse all</button>
    <button id="btn-thinking" onclick="toggleThinking(this)">Show thinking</button>
    <input class="tl-search" type="search" placeholder="Search timeline..."
      oninput="applySearch(this.value)">
  `;
}

function togglePromptSection() {
  const sec = document.getElementById('prompt-section');
  if (!sec) return;
  const isVisible = sec.style.display !== 'none';
  sec.style.display = isVisible ? 'none' : '';
  const btn = document.getElementById('btn-prompt');
  if (btn) btn.classList.toggle('active', !isVisible);
  S.showPrompt = !isVisible;
}

function expandAllPhases() {
  document.querySelectorAll('.tl-phase.collapsed').forEach(p => p.classList.remove('collapsed'));
  document.querySelectorAll('.tl-tool, .tl-agent-section-btn').forEach(e => {
    e.classList.add('expanded');
    e.classList.add('open');
  });
}

function collapseAllPhases() {
  document.querySelectorAll('.tl-phase').forEach(p => p.classList.add('collapsed'));
  document.querySelectorAll('.tl-tool, .tl-agent-section-btn').forEach(e => {
    e.classList.remove('expanded');
    e.classList.remove('open');
  });
}

function toggleThinking(btn) {
  S.showThinking = !S.showThinking;
  btn.classList.toggle('active', S.showThinking);
  document.querySelectorAll('.tl-thinking').forEach(el => {
    el.style.display = S.showThinking ? '' : 'none';
  });
}

function applySearch(query) {
  S.searchQuery = query.toLowerCase();
  const scroll = document.getElementById('timeline-scroll');
  if (!scroll) return;
  scroll.querySelectorAll('.tl-phase-content').forEach(content => {
    content.querySelectorAll('.tl-tool, .tl-agent, .tl-text').forEach(item => {
      const text = item.textContent.toLowerCase();
      item.style.display = (!S.searchQuery || text.includes(S.searchQuery)) ? '' : 'none';
    });
  });
}

// ── UI State Preservation ─────────────────────
function _captureTimelineState() {
  const scroll = document.getElementById('timeline-scroll');
  if (!scroll) return null;
  const collapsed = [];
  scroll.querySelectorAll('.tl-phase.collapsed').forEach(el => {
    if (el.dataset.phase) collapsed.push(el.dataset.phase);
  });
  const expandedTools = [];
  scroll.querySelectorAll('.tl-tool.expanded').forEach(el => {
    expandedTools.push(Array.from(el.parentElement.children).indexOf(el));
  });
  const openSections = [];
  scroll.querySelectorAll('.tl-agent-section-btn.open').forEach(btn => {
    openSections.push(btn.textContent.trim());
  });
  return {
    scrollTop: scroll.scrollTop,
    collapsed,
    expandedTools,
    openSections,
    promptVisible: document.getElementById('prompt-section')?.style.display !== 'none',
  };
}

function _restoreTimelineState(state) {
  if (!state) return;
  const scroll = document.getElementById('timeline-scroll');
  if (!scroll) return;
  // Restore collapsed phases
  state.collapsed.forEach(phase => {
    const el = scroll.querySelector(`.tl-phase[data-phase="${phase}"]`);
    if (el) el.classList.add('collapsed');
  });
  // Restore expanded tools (by index within parent)
  state.expandedTools.forEach(idx => {
    const tools = scroll.querySelectorAll('.tl-tool');
    if (tools[idx]) tools[idx].classList.add('expanded');
  });
  // Restore open agent sections
  state.openSections.forEach(text => {
    scroll.querySelectorAll('.tl-agent-section-btn').forEach(btn => {
      if (btn.textContent.trim() === text) btn.classList.add('open');
    });
  });
  // Restore prompt visibility
  const prompt = document.getElementById('prompt-section');
  if (prompt && state.promptVisible) prompt.style.display = '';
  // Restore scroll position
  scroll.scrollTop = state.scrollTop;
}

// ── Timeline ───────────────────────────────────
function renderTimeline(trace, running) {
  const scroll = document.getElementById('timeline-scroll');
  if (!scroll) return;

  // Capture UI state before re-render so we can restore toggle states
  const savedState = _captureTimelineState();

  const phases = trace.phases || [];
  const result = trace.result || trace.final_report || null;
  let html = '';

  // Prompt section (hidden by default)
  const promptText = trace.prompt || '';
  html += `<div class="tl-prompt" id="prompt-section" style="display:none">
    <div class="tl-prompt-body" style="display:block">
      ${renderMarkdown(promptText)}
    </div>
  </div>`;

  // System init
  const model = trace.model || '';
  const cwd = trace.cwd || '';
  if (model || cwd) {
    html += `<div class="tl-init">
      ▶ <span>Model: ${esc(model)}</span> · <span>CWD: ${esc(cwd)}</span>
    </div>`;
  }

  // Phases
  for (const phase of phases) {
    const name = phase.name || '';
    const color = PHASE_COLORS[name.toUpperCase()] || 'var(--text-muted)';
    const dur = phase.duration_ms ? fmtDurationMs(phase.duration_ms) : '';
    const tokens = phase.total_tokens ? `${fmtTokens(phase.total_tokens)} tokens` : '';
    const fixCount = (phase.fix_cycles || []).length;
    const metaParts = [dur, tokens, fixCount > 0 ? `${fixCount} fix cycle${fixCount > 1 ? 's' : ''}` : ''].filter(Boolean);
    const meta = metaParts.join(' · ');

    html += `<div class="tl-phase" id="phase-${esc(name.toLowerCase())}" data-phase="${esc(name.toLowerCase())}" onclick="togglePhase(this)">
      <span class="tl-phase-chevron">▾</span>
      <span class="tl-phase-line" style="border-color:${color}"></span>
      <span class="tl-phase-name" style="color:${color}">${esc(name)}</span>
      ${meta ? `<span class="tl-phase-meta">${esc(meta)}</span>` : ''}
      <span class="tl-phase-line" style="border-color:${color}"></span>
    </div>
    <div class="tl-phase-content">`;

    // Orchestrator text — render as markdown, strip redundant phase markers
    // Also strip JSON code blocks that duplicate the final result block
    for (const textBlock of (phase.orchestrator_text || [])) {
      let cleaned = cleanPhaseMarkers(textBlock);
      if (result) cleaned = cleaned.replace(/```json\s*\n[\s\S]*?\n```/g, '').trim();
      if (cleaned) html += `<div class="tl-text">${renderMarkdown(cleaned)}</div>`;
    }

    // Orchestrator thinking (hidden by default, toggled by "Show thinking" button)
    for (const thinkBlock of (phase.orchestrator_thinking || [])) {
      if (thinkBlock) html += `<div class="tl-thinking" style="display:none">${renderMarkdown(thinkBlock)}</div>`;
    }

    // Orchestrator tool calls
    for (const tool of (phase.orchestrator_tools || [])) {
      html += renderToolCall(tool);
    }

    // Agent blocks
    for (const agent of (phase.subagents || phase.agents || [])) {
      html += renderAgentBlock(agent);
    }

    // Fix cycles
    for (const cycle of (phase.fix_cycles || [])) {
      html += renderFixCycle(cycle);
    }

    html += `</div>`; // end tl-phase-content
  }

  // Live cursor (running) or completed section
  if (running) {
    html += `<div id="tl-live-cursor" style="display:flex;align-items:center;gap:0.5rem;padding:0.5rem 0.75rem;margin:0.5rem 0">
      <div class="ov-trace-cursor" style="margin:0"></div>
      <span style="font-size:0.72rem;color:var(--text-muted);font-family:var(--font-mono)">running...</span>
    </div>`;
  } else {
    html += `<div id="tl-completed-section">`;

    // Result block
    if (result) {
      html += renderResultBlock(result, trace);
    }

    html += `</div>`; // end tl-completed-section
  }

  scroll.innerHTML = html;

  // Restore UI state (expanded/collapsed, scroll position, prompt visibility)
  _restoreTimelineState(savedState);

  // Enforce prompt visibility from authoritative state flag
  const promptEl = document.getElementById('prompt-section');
  if (promptEl) promptEl.style.display = S.showPrompt ? '' : 'none';

  // Apply thinking visibility
  if (!S.showThinking) {
    scroll.querySelectorAll('.tl-thinking').forEach(el => { el.style.display = 'none'; });
  }

  // Apply active search filter
  if (S.searchQuery) applySearch(S.searchQuery);
}

// ── Tool Call ──────────────────────────────────
function _toolNameClass(name) {
  const n = (name || '').toLowerCase();
  if (n === 'read') return 'read';
  if (n === 'write' || n === 'edit') return 'write';
  if (n === 'bash') return 'bash';
  return 'grep';
}

function renderToolCall(tool) {
  const name = tool.name || tool.tool_name || tool.tool || '';
  const summary = tool.summary || tool.input_summary || tool.description || '';
  const meta = tool.output_summary || '';
  const cls = _toolNameClass(name);

  return `<div class="tl-tool" onclick="this.classList.toggle('expanded')">
    <div class="tl-tool-header">
      <span class="tl-tool-icon">⚙</span>
      <span class="tl-tool-name ${cls}">${esc(name)}</span>
      <span class="tl-tool-summary">${esc(truncText(summary, 80))}</span>
      ${meta ? `<span class="tl-tool-meta">${esc(meta)}</span>` : ''}
      <span class="tl-tool-chevron">▸</span>
    </div>
    <div class="tl-tool-body">
      <pre>${esc(tool.output || tool.result || tool.result_preview || '')}</pre>
    </div>
  </div>`;
}

// ── Agent Block ────────────────────────────────
function renderAgentBlock(agent) {
  const role = (agent.role || '').toLowerCase();
  let icon = '⚙';
  let iconClass = 'builder';
  if (role.includes('reviewer') || role.includes('review')) { icon = '📝'; iconClass = 'reviewer'; }
  else if (role.includes('verif')) { icon = '☑'; iconClass = 'verifier'; }

  const desc = agent.description || agent.desc || '';
  const model = agent.model || '';
  const dur = agent.duration_ms ? fmtDurationMs(agent.duration_ms) : '';
  const tokens = agent.total_tokens ? fmtTokens(agent.total_tokens) : '';
  const toolCount = (agent.tool_timeline || []).length;
  const status = agent.status || agent.verdict || 'completed';

  let statusClass = 'completed';
  const statusUp = status.toUpperCase();
  if (statusUp === 'APPROVED') statusClass = 'approved';
  else if (statusUp === 'NEEDS_FIXES' || statusUp === 'NEEDS_FIX') statusClass = 'needs-fixes';
  else if (statusUp === 'RUNNING') statusClass = 'running';

  // Tool timeline — show first 6 visible, rest hidden behind "N more" toggle
  const tools = agent.tool_timeline || [];
  const showTools = tools.slice(0, 6);
  const hiddenTools = tools.slice(6);
  const _renderToolItem = (t, hidden) => {
    const tName = t.name || t.tool_name || t.tool || '';
    const tDesc = t.summary || t.input_summary || t.description || '';
    const tOk = t.success || t.ok ? `<span class="tl-agent-tool-ok">✓</span>` : '';
    const tCls = _toolNameClass(tName);
    return `<div class="tl-agent-tool${hidden ? ' tl-agent-tool-hidden' : ''}"${hidden ? ' style="display:none"' : ''}>
      <span class="tl-agent-tool-icon">⚙</span>
      <span class="tl-agent-tool-name ${tCls}">${esc(tName)}</span>
      <span class="tl-agent-tool-desc">${esc(truncText(tDesc, 60))}</span>
      ${tOk}
    </div>`;
  };
  let toolsHtml = showTools.map(t => _renderToolItem(t, false)).join('');
  toolsHtml += hiddenTools.map(t => _renderToolItem(t, true)).join('');
  if (hiddenTools.length > 0) {
    toolsHtml += `<div class="tl-agent-tools-more" onclick="this.parentElement.querySelectorAll('.tl-agent-tool-hidden').forEach(e=>{e.style.display=''});this.style.display='none'">...${hiddenTools.length} more tool calls</div>`;
  }

  const promptHtml = agent.prompt
    ? `<div class="tl-agent-section">
        <button class="tl-agent-section-btn" onclick="toggleAgentSection(this)">
          <span class="chevron">▸</span> Prompt
        </button>
        <div class="tl-agent-section-body">
          <pre>${esc(agent.prompt)}</pre>
        </div>
      </div>` : '';

  const outputHtml = agent.output
    ? `<div class="tl-agent-section">
        <button class="tl-agent-section-btn" onclick="toggleAgentSection(this)">
          <span class="chevron">▸</span> Output
        </button>
        <div class="tl-agent-section-body">
          <pre>${esc(agent.output)}</pre>
        </div>
      </div>` : '';

  return `<div class="tl-agent">
    <div class="tl-agent-header">
      <div class="tl-agent-icon ${iconClass}">${icon}</div>
      <div class="tl-agent-info">
        <div class="tl-agent-desc">${esc(desc)}</div>
        <div class="tl-agent-role">${esc(role)}${model ? ' · ' + esc(model) : ''}</div>
      </div>
      <div class="tl-agent-stats">
        ${dur ? `<span><span class="stat-val">${dur}</span></span>` : ''}
        ${tokens ? `<span><span class="stat-val">${tokens}</span> tok</span>` : ''}
        ${toolCount > 0 ? `<span><span class="stat-val">${toolCount}</span> tools</span>` : ''}
      </div>
      <span class="tl-agent-status ${statusClass}">${esc(status)}</span>
    </div>
    <div class="tl-agent-sections">
      ${promptHtml}
      ${toolCount > 0 ? `<div class="tl-agent-section" style="margin-top:0.4rem">
        <div style="font-size:0.72rem;color:var(--text-muted);font-weight:600;margin-bottom:0.3rem;padding-left:0.1rem">Tool Timeline</div>
        <div class="tl-agent-tools">${toolsHtml}</div>
      </div>` : ''}
      ${outputHtml}
    </div>
  </div>`;
}

// ── Fix Cycle ──────────────────────────────────
function renderFixCycle(cycle) {
  const issues = cycle.issues || [];
  const fixBuilder = cycle.fix_agent || cycle.builder || null;
  const cycleNum = cycle.cycle_number || cycle.index || 1;
  const dur = cycle.duration_ms ? fmtDurationMs(cycle.duration_ms) : '';
  const tokens = cycle.total_tokens ? fmtTokens(cycle.total_tokens) : '';
  const metaParts = [dur, tokens ? `${tokens} tokens` : ''].filter(Boolean);

  const issuesHtml = issues.map(issue => {
    const conf = issue.confidence != null ? Math.round(issue.confidence * 100) : '';
    const text = issue.text || issue.description || '';
    const file = issue.file || issue.location || '';
    const fixed = issue.fixed !== false;
    return `<div class="tl-fix-issue">
      ${conf ? `<span class="tl-fix-issue-conf">${conf}%</span>` : ''}
      <span class="tl-fix-issue-text">${esc(truncText(text, 100))}</span>
      ${file ? `<span class="tl-fix-issue-file">${esc(file)}</span>` : ''}
      <span class="tl-fix-issue-status ${fixed ? 'fixed' : 'open'}">${fixed ? 'fixed ✓' : 'open'}</span>
    </div>`;
  }).join('');

  const builderHtml = fixBuilder ? renderAgentBlock(fixBuilder) : '';

  return `<div class="tl-fix-cycle-wrap" onclick="event.target.closest('.tl-fix-cycle-header') && this.classList.toggle('collapsed')">
    <div class="tl-fix-cycle-header">
      <span class="tl-fix-cycle-badge">FIX CYCLE ${cycleNum}</span>
      <span class="tl-fix-cycle-title">${issues.length} issue${issues.length !== 1 ? 's' : ''} found</span>
      ${metaParts.length > 0 ? `<span class="tl-fix-cycle-meta">${esc(metaParts.join(' · '))}</span>` : ''}
      <span class="tl-fix-cycle-chevron">▾</span>
    </div>
    <div class="tl-fix-cycle-body">
      <div class="tl-fix-cycle-flow">
        <div class="tl-fix-flow-step">
          <div class="tl-fix-flow-icon reviewer">📝</div>
          <div class="tl-fix-flow-label">Review Found</div>
          <div class="tl-fix-flow-status" style="color:var(--orange)">${issues.length} issue${issues.length !== 1 ? 's' : ''}</div>
        </div>
        <div class="tl-fix-flow-arrow"></div>
        <div class="tl-fix-flow-step">
          <div class="tl-fix-flow-icon builder">⚙</div>
          <div class="tl-fix-flow-label">Builder Fix</div>
          <div class="tl-fix-flow-status" style="color:var(--accent)">${dur || 'fixing'}</div>
        </div>
        <div class="tl-fix-flow-arrow"></div>
        <div class="tl-fix-flow-step">
          <div class="tl-fix-flow-icon recheck">✓</div>
          <div class="tl-fix-flow-label">Re-check</div>
          <div class="tl-fix-flow-status" style="color:${(cycle.recheck_status === 'APPROVED' || cycle.recheck_status === 'completed') ? 'var(--green)' : cycle.recheck_status === 'NEEDS_FIXES' ? 'var(--orange)' : 'var(--text-muted)'}">${esc(cycle.recheck_status || 'pending')}</div>
        </div>
      </div>
      ${issues.length > 0 ? `<div class="tl-fix-cycle-issues">${issuesHtml}</div>` : ''}
      ${builderHtml ? `<div class="tl-fix-cycle-separator"><span>Fix Builder Details</span></div>${builderHtml}` : ''}
    </div>
  </div>`;
}

// ── Result Block ───────────────────────────────
function renderResultBlock(result, trace) {
  // Support both schemas: trace.result (legacy) and trace.final_report
  const status = result.status || (result.success !== false ? 'COMPLETE' : 'BLOCKED');
  const success = status === 'COMPLETE' || status === 'PASS';
  const statusClass = success ? 'complete' : 'blocked';
  const statusLabel = status.toUpperCase();
  const summary = result.summary || trace.result_summary || '';

  // Specs: support both array [{id, pass}] and object {SPEC-1: true}
  let specsHtml = '';
  const specsObj = result.specs_satisfied || {};
  const specsArr = result.specs || [];
  if (Object.keys(specsObj).length > 0) {
    specsHtml = Object.entries(specsObj).map(([id, pass]) =>
      `<span class="tl-spec ${pass ? 'pass' : 'fail'}">${pass ? '✓' : '✗'} ${esc(id)}</span>`
    ).join('');
  } else if (specsArr.length > 0) {
    specsHtml = specsArr.map(s => {
      const pass = s.pass !== false;
      return `<span class="tl-spec ${pass ? 'pass' : 'fail'}">${pass ? '✓' : '✗'} ${esc(s.id || s.name || '')}</span>`;
    }).join('');
  }

  // Tests: support both array [{name, pass}] and object {black: "pass"}
  let testsHtml = '';
  const testsObj = result.test_results || {};
  const testsArr = result.tests || [];
  if (Object.keys(testsObj).length > 0) {
    testsHtml = Object.entries(testsObj).map(([name, val]) => {
      const pass = val === 'pass' || val === true;
      const skip = val === 'not_applicable' || val === 'skipped';
      const cls = skip ? 'skip' : (pass ? 'pass' : 'fail');
      const icon = skip ? '—' : (pass ? '✓' : '✗');
      return `<span class="tl-test ${cls}">${icon} ${esc(name)}</span>`;
    }).join('');
  } else if (testsArr.length > 0) {
    testsHtml = testsArr.map(t => {
      const pass = t.pass !== false;
      return `<span class="tl-test ${pass ? 'pass' : 'fail'}">${pass ? '✓' : '✗'} ${esc(t.name || t.label || '')}</span>`;
    }).join('');
  }

  // Concerns
  const concerns = result.concerns || [];
  const concernsHtml = concerns.length > 0
    ? `<div style="margin-top:0.5rem;font-size:0.78rem;color:var(--orange)">
        ${concerns.map(c => `<div>⚠ ${esc(c)}</div>`).join('')}
      </div>`
    : '';

  const files = result.files_changed || [];
  const filesHtml = files.length > 0
    ? `<div style="margin-top:0.5rem;font-size:0.78rem;color:var(--text-secondary)">
        Files changed: ${files.map(f => `<code style="font-family:var(--font-mono);font-size:0.75rem;background:var(--bg-elevated);padding:0.1rem 0.3rem;border-radius:3px">${esc(f)}</code>`).join(', ')}
      </div>`
    : '';

  return `<div class="tl-result">
    <div class="tl-result-header">
      <span class="status ${statusClass}">${statusLabel}</span>
      ${summary ? `<span style="font-size:0.78rem;color:var(--text-secondary)">${esc(summary)}</span>` : ''}
    </div>
    <div class="tl-result-body">
      ${specsHtml ? `<div class="tl-specs">${specsHtml}</div>` : ''}
      ${testsHtml ? `<div class="tl-tests">${testsHtml}</div>` : ''}
      ${filesHtml}
      ${concernsHtml}
    </div>
  </div>`;
}

// ── Info Tabs ──────────────────────────────────
function renderInfoTabs(trace, session, running) {
  const el = document.getElementById('info-tabs');
  if (!el) return;

  // Hide tabs while running
  if (running) {
    el.style.display = 'none';
    return;
  }

  el.style.display = '';

  const report = trace.report_markdown || '';
  const errors = (session && session.errors && session.errors.length > 0)
    ? session.errors.map(e => `<div class="tl-text">${esc(e)}</div>`).join('')
    : '<span style="color:var(--text-muted)">No errors recorded.</span>';

  const rawSession = session ? JSON.stringify(session, null, 2) : '{}';

  el.innerHTML = `
    <div class="tab-bar">
      <button class="tab-btn active" onclick="activateTab(this,'tab-report')">Report</button>
      <button class="tab-btn" onclick="activateTab(this,'tab-errors')">Errors</button>
      <button class="tab-btn" onclick="activateTab(this,'tab-raw')">Raw Session</button>
    </div>
    <div class="tab-content active" id="tab-report">
      ${report ? renderMarkdown(report) : '<div style="color:var(--text-secondary)">No report available.</div>'}
    </div>
    <div class="tab-content" id="tab-errors">${errors}</div>
    <div class="tab-content" id="tab-raw"><pre style="font-size:0.70rem">${esc(rawSession)}</pre></div>
  `;
}

function activateTab(btn, tabId) {
  const container = btn.closest('#info-tabs');
  if (!container) return;
  container.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  container.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  btn.classList.add('active');
  const tabEl = document.getElementById(tabId);
  if (tabEl) tabEl.classList.add('active');
}

// ── Toggle helpers ─────────────────────────────
function togglePhase(el) {
  el.classList.toggle('collapsed');
}

function toggleAgentSection(btn) {
  btn.classList.toggle('open');
}
