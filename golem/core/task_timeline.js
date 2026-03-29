/* golem/core/task_timeline.js — Task Detail view: header, metrics, phase sidebar,
 * timeline, agent blocks, fix cycles, toolbar, result block, info tabs.
 * Depends on: task_api.js (S, fetchParsedTrace, PHASE_COLORS, isTaskRunning).
 */
'use strict';

async function renderDetail(eventId, prefetchedTrace) {
  const session = S.sessions[eventId];
  const trace = prefetchedTrace || await fetchParsedTrace(eventId);
  const running = isTaskRunning(session);

  const hasPhases = trace && trace.phases && trace.phases.length > 0;

  if (!trace || (!hasPhases && (running || session.state === 'failed'))) {
    // Pre-flight or failed before agent ran: render as a proper phase
    const el = document.getElementById('timeline-scroll');
    if (el) {
      const events = (session && session.event_log) || [];
      const isFailed = session.state === 'failed';
      const pfColor = isFailed ? 'var(--danger)' : (PHASE_COLORS.PREFLIGHT || 'var(--cyan)');

      let html = '';

      // Phase divider — identical to build/plan/review phases
      html += `<div class="tl-phase" data-phase="preflight">
        <span class="tl-phase-chevron">▾</span>
        <span class="tl-phase-line" style="border-color:${pfColor}"></span>
        <span class="tl-phase-name" style="color:${pfColor}">PRE-FLIGHT</span>
        <span class="tl-phase-line" style="border-color:${pfColor}"></span>
      </div>`;
      html += '<div class="tl-phase-content">';

      // Render each step as a tool-call-style block
      const steps = events.filter(ev => !ev.is_error);
      for (const ev of steps) {
        const icon = ev.summary.includes('worktree') ? '🌿' : ev.summary.includes('verification') ? '🔍' : '⚙';
        html += `<div class="tl-tool">
          <div class="tl-tool-header">
            <span class="tl-tool-icon">${icon}</span>
            <span class="tl-tool-name bash">supervisor</span>
            <span class="tl-tool-summary">${esc(ev.summary || '')}</span>
          </div>
        </div>`;
      }

      // Error events — render as expandable tool-call blocks per checker
      const errors = events.filter(ev => ev.is_error);
      for (const ev of errors) {
        const msg = ev.summary || '';
        // Split into per-checker sections for readability
        const sections = msg.split(/(?=\b(?:black|pylint|pytest):)/i).filter(Boolean);
        // Leading text (e.g. "Supervisor failed: ...") as summary
        const leadSection = sections.length > 0 && !/^(black|pylint|pytest):/i.test(sections[0]) ? sections.shift() : '';

        if (leadSection) {
          html += `<div class="tl-text" style="color:var(--danger);font-weight:600;font-size:0.82rem;margin:0.5rem 0">${esc(leadSection.replace(/[;,]\s*$/, '').trim())}</div>`;
        }

        for (const section of sections) {
          const match = section.match(/^(\w+):\s*([\s\S]*)$/);
          const checker = match ? match[1] : 'error';
          const body = match ? match[2].replace(/[;,]\s*$/, '').trim() : section.trim();
          const passed = false;
          const icon = passed ? '✓' : '✗';
          const nameColor = passed ? 'var(--green)' : 'var(--danger)';

          html += `<div class="tl-tool" onclick="this.classList.toggle('expanded')">
            <div class="tl-tool-header">
              <span class="tl-tool-icon" style="color:${nameColor}">${icon}</span>
              <span class="tl-tool-name" style="color:${nameColor}">${esc(checker)}</span>
              <span class="tl-tool-summary" style="color:var(--danger)">${esc(truncText(body, 100))}</span>
              <span class="tl-tool-chevron">▸</span>
            </div>
            <div class="tl-tool-body">
              <pre>${esc(body)}</pre>
            </div>
          </div>`;
        }
      }

      if (!events.length) html += '<div class="tl-text" style="color:var(--text-muted)">Waiting for trace data\u2026</div>';

      if (running) {
        html += `<div id="tl-live-cursor" style="display:flex;align-items:center;gap:0.5rem;padding:0.5rem 0.75rem;margin:0.25rem 0">
          <div class="ov-trace-cursor" style="margin:0"></div>
          <span style="font-size:0.72rem;color:var(--text-muted);font-family:var(--font-mono)">running pre-flight...</span>
        </div>`;
      }

      html += '</div>'; // end tl-phase-content
      el.innerHTML = html;
    }
    if (!hasPhases) {
      renderDetailHeader(session, trace || {phases:[]}, running);
      renderInfoTabs(trace || {phases:[]}, session, running);
      return;
    }
  }

  // Cache completed traces client-side
  if (!running && trace) S.parsedTraces[eventId] = trace;

  // Fetch prompt text if not already on the trace object
  if (!trace.prompt) {
    const promptText = await fetchPrompt(eventId);
    if (promptText) trace.prompt = promptText;
  }

  renderDetailHeader(session, trace, running);
  renderMetrics(trace, session);
  renderLiveStrip(session, trace, running);
  renderPhaseSidebar(trace, running, session);
  renderToolbar();
  renderTimeline(trace, running, session);
  renderInfoTabs(trace, session, running);
}

// ── Header ─────────────────────────────────────
function renderDetailHeader(session, trace, running) {
  const el = document.getElementById('td-header');
  if (!el) return;

  const state = session ? session.state : '';
  const chipClass = _stateToChipClass(state);
  const subject = esc(truncText(subjectTitle(session), 120));
  const taskId = session ? esc(String(session.parent_issue_id || session.id || '')) : '';
  const mode = session ? esc(session.execution_mode || 'subagent') : 'subagent';
  const liveHtml = running
    ? `<div class="tl-live-badge"><span class="live-dot"></span>Live</div>`
    : '';

  const isTerminal = ['completed', 'failed', 'human_review'].includes((state || '').toLowerCase());
  let actionsHtml = '';
  if (running) {
    actionsHtml = `<div class="td-actions"><button class="td-action-btn danger" data-action="cancel">Cancel</button></div>`;
  } else if (isTerminal) {
    actionsHtml = `<div class="td-actions">
        <button class="td-action-btn" data-action="rerun">Re-run</button>
        <button class="td-action-btn primary" data-action="edit-rerun">Edit &amp; Re-run</button>
      </div>`;
  }

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

  // Stats row — show cost/duration for both running and completed tasks
  let statsHtml = '';
  if (session || trace) {
    const meta = (trace && trace.result_meta) || {};
    const cost = meta.total_cost_usd || (session && session.total_cost_usd);
    const durMs = meta.duration_ms || ((session && session.duration_seconds) ? session.duration_seconds * 1000 : 0);
    const commitSha = !running && session && session.commit_sha;
    const retryCount = session && session.retry_count;
    const fixIter = session && session.fix_iteration;

    const parts = [];
    if (cost) parts.push(`<span style="color:var(--green)">${fmtCost(cost)}</span> cost`);
    if (durMs) parts.push(fmtDurationMs(durMs));
    if (commitSha) parts.push(`<code style="font-family:var(--font-mono);font-size:0.72rem;background:var(--bg-elevated);padding:0.1rem 0.3rem;border-radius:3px">${esc(String(commitSha).slice(0, 7))}</code>`);
    if (fixIter) parts.push(`<span style="color:var(--orange)">${fixIter} fix iter</span>`);
    if (retryCount) parts.push(`${retryCount} full retr${retryCount === 1 ? 'y' : 'ies'}`);

    if (parts.length > 0) {
      statsHtml = `<div class="td-stats">${parts.join(' <span style="color:var(--text-muted);margin:0 0.25rem">·</span> ')}</div>`;
    }
  }

  el.innerHTML = `
    <div class="td-top">
      <span class="td-id">#${taskId}</span>
      <span class="td-mode">${mode}</span>
      <span class="td-badge ${chipClass}">${esc(state.toLowerCase())}</span>
      ${liveHtml}
      ${actionsHtml}
    </div>
    <div class="td-subject">${subject}</div>
    ${statsHtml}
    ${depsHtml}
  `;

  // Click handler for dependency cards — navigate to that task
  el.querySelectorAll('.td-dep-card[data-dep-id]').forEach(card => {
    card.addEventListener('click', () => selectTask(card.dataset.depId));
  });

  // Wire action button handlers
  const cancelBtn = el.querySelector('[data-action="cancel"]');
  if (cancelBtn) cancelBtn.addEventListener('click', () => handleCancel(session));
  const rerunBtn = el.querySelector('[data-action="rerun"]');
  if (rerunBtn) rerunBtn.addEventListener('click', () => handleRerun(session));
  const editRerunBtn = el.querySelector('[data-action="edit-rerun"]');
  if (editRerunBtn) editRerunBtn.addEventListener('click', () => handleEditAndRerun(session));
}

// ── Task Actions ────────────────────────────────
function _getTaskId(session) {
  const raw = session ? (session.parent_issue_id || session.id || '') : '';
  // Coerce to numeric — FastAPI endpoints expect int task IDs
  const num = typeof raw === 'number' ? raw : parseInt(String(raw).replace(/^golem-(\d+).*/, '$1'), 10);
  return isNaN(num) ? '' : num;
}

async function handleCancel(session) {
  const taskId = _getTaskId(session);
  if (!taskId) return;
  if (!confirm('Cancel this task? This cannot be undone.')) return;
  const btn = document.querySelector('[data-action="cancel"]');
  if (btn) { btn.disabled = true; btn.classList.add('btn-loading'); btn.textContent = 'Cancelling\u2026'; }
  try {
    const result = await cancelTask(taskId);
    if (result.ok) {
      S.sessions = await fetchSessions();
      await renderDetail(S.selectedTaskId);
      return; // btn is now detached — skip finally re-enable
    }
    showToast('Cancel failed: ' + (result.detail || 'Unknown error'), 'error');
  } catch (e) {
    showToast('Cancel failed: ' + e.message, 'error');
  }
  if (btn) { btn.disabled = false; btn.classList.remove('btn-loading'); btn.textContent = 'Cancel'; }
}

async function handleRerun(session) {
  const btn = document.querySelector('[data-action="rerun"]');
  if (btn) { btn.disabled = true; btn.classList.add('btn-loading'); btn.textContent = 'Submitting\u2026'; }
  try {
    const prompt = await fetchPrompt(S.selectedTaskId);
    if (!prompt) { showToast('Could not fetch task prompt.', 'error'); if (btn) { btn.disabled = false; btn.classList.remove('btn-loading'); btn.textContent = 'Re-run'; } return; }
    const result = await resubmitTask(prompt, subjectTitle(session));
    if (result.ok) {
      S.sessions = await fetchSessions();
      if (S.view === 'overview') renderOverview();
      else await renderDetail(S.selectedTaskId);
      return; // btn is now detached — skip re-enable
    }
    showToast('Re-run failed: ' + (result.detail || 'Unknown error'), 'error');
  } catch (e) {
    showToast('Re-run failed: ' + e.message, 'error');
  }
  if (btn) { btn.disabled = false; btn.classList.remove('btn-loading'); btn.textContent = 'Re-run'; }
}

async function handleEditAndRerun(session) {
  try {
    const prompt = await fetchPrompt(S.selectedTaskId);
    const subject = subjectTitle(session);
    const modal = document.getElementById('resubmit-modal');
    const subjInput = document.getElementById('resubmit-subject');
    const promptInput = document.getElementById('resubmit-prompt');
    if (!modal || !promptInput) return;
    if (subjInput) subjInput.value = subject || '';
    promptInput.value = prompt || '';
    modal.style.display = 'flex';
  } catch (e) {
    showToast('Could not load task prompt: ' + e.message, 'error');
  }
}

function _closeResubmitModal() {
  const modal = document.getElementById('resubmit-modal');
  if (modal) modal.style.display = 'none';
}

async function _submitResubmitModal() {
  const subjInput = document.getElementById('resubmit-subject');
  const promptInput = document.getElementById('resubmit-prompt');
  const submitBtn = document.getElementById('resubmit-submit-btn');
  if (!promptInput) return;
  const prompt = promptInput.value.trim();
  if (!prompt) { showToast('Prompt cannot be empty.', 'info'); return; }
  const subject = subjInput ? subjInput.value.trim() : '';
  if (submitBtn) { submitBtn.disabled = true; submitBtn.classList.add('btn-loading'); submitBtn.textContent = 'Submitting\u2026'; }
  try {
    const result = await resubmitTask(prompt, subject);
    if (result.ok) {
      _closeResubmitModal();
      S.sessions = await fetchSessions();
      if (S.view === 'overview') renderOverview();
      else await renderDetail(S.selectedTaskId);
      return;
    }
    showToast('Submit failed: ' + (result.detail || 'Unknown error'), 'error');
  } catch (e) {
    showToast('Submit failed: ' + e.message, 'error');
  }
  if (submitBtn) { submitBtn.disabled = false; submitBtn.classList.remove('btn-loading'); submitBtn.textContent = 'Submit'; }
}

// ── Metrics ────────────────────────────────────
function renderMetrics(trace, session) {
  const el = document.getElementById('td-metrics');
  if (!el) return;

  const totals = trace.totals || {};
  const meta = trace.result_meta || {};
  const cost = meta.total_cost_usd || (session && session.total_cost_usd) || totals.total_cost_usd;
  const duration = totals.duration_ms || meta.duration_ms
    || ((session && session.duration_seconds) ? session.duration_seconds * 1000 : 0);
  const agents = totals.subagent_count || totals.total_agents || 0;
  const tools = totals.tool_calls || totals.total_tool_calls || 0;
  const tokens = totals.tokens || totals.total_tokens || 0;
  const fixCycles = (trace.phases || []).reduce((n, p) => n + (p.fix_cycles || []).length, 0);
  const fixIter = session && session.fix_iteration || 0;
  const retryCount = session && session.retry_count || 0;
  const fixColor = (fixCycles + fixIter) > 0 ? 'style="color:var(--orange)"' : '';
  const retryColor = retryCount > 0 ? 'style="color:var(--orange)"' : '';

  el.innerHTML = `
    <div class="metric"><span class="metric-label">Cost</span><span class="metric-value">${fmtCost(cost)}</span></div>
    <div class="metric"><span class="metric-label">Duration</span><span class="metric-value">${fmtDurationMs(duration)}</span></div>
    <div class="metric"><span class="metric-label">Agents</span><span class="metric-value">${agents}</span></div>
    <div class="metric"><span class="metric-label">Tools</span><span class="metric-value">${tools}</span></div>
    <div class="metric"><span class="metric-label">Tokens</span><span class="metric-value">${fmtTokens(tokens)}</span></div>
    <div class="metric"><span class="metric-label">Fix Cycles</span><span class="metric-value" ${fixColor}>${fixCycles}</span></div>
    <div class="metric"><span class="metric-label">Fix Iters</span><span class="metric-value" ${fixColor}>${fixIter}</span></div>
    <div class="metric"><span class="metric-label">Full Retries</span><span class="metric-value" ${retryColor}>${retryCount}</span></div>
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
  const cost = session && session.total_cost_usd ? fmtCost(session.total_cost_usd) : '';

  el.innerHTML = `
    <span class="td-live-dot"></span>
    <span class="td-live-text">Auto-updating</span>
    <span class="td-live-phase">${phaseName} phase</span>
    <span class="td-live-elapsed">${elapsed} elapsed</span>
    ${cost ? `<span class="td-live-cost" style="color:var(--green)">${cost}</span>` : ''}
  `;
}

// ── Phase Sidebar ──────────────────────────────
function renderPhaseSidebar(trace, running, session) {
  const nav = document.getElementById('phase-nav');
  if (!nav) return;

  const phases = trace.phases || [];

  // Prepend PRE-FLIGHT to sidebar if session has pre-flight events
  const preflightEvents = _getPreflightEvents((session && session.event_log) || []);
  let preflightHtml = '';
  if (preflightEvents.length > 0) {
    const pfColor = PHASE_COLORS.PREFLIGHT || 'var(--cyan)';
    const pfFirst = preflightEvents[0].timestamp;
    const pfLast = preflightEvents[preflightEvents.length - 1].timestamp;
    const pfDurMs = pfFirst && pfLast ? (pfLast - pfFirst) * 1000 : 0;
    const pfDurStr = pfDurMs > 0 ? fmtDurationMs(pfDurMs) : '';
    preflightHtml = `<button class="phase-link" data-phase="preflight">
      <span class="ph-dot" style="background:${pfColor}"></span>
      PRE-FLIGHT
      ${pfDurStr ? `<span class="ph-dur">${pfDurStr}</span>` : ''}
    </button>`;
  }

  nav.innerHTML = preflightHtml + phases.map(p => {
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
  const phases = ['preflight', 'understand', 'plan', 'build', 'review', 'verify'];
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
  const wasAtBottom = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 50;
  return {
    scrollTop: scroll.scrollTop,
    wasAtBottom,
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
  // Restore scroll position — if user was at bottom, stay at bottom (for live tailing)
  if (state.wasAtBottom) {
    scroll.scrollTop = scroll.scrollHeight;
  } else {
    scroll.scrollTop = state.scrollTop;
  }
}

// ── Pre-flight event extraction ────────────────
// session.event_log contains ALL milestones (pre-flight + orchestrator + post-run).
// Extract only the pre-flight portion: events before orchestration starts.
function _getPreflightEvents(eventLog) {
  const events = [];
  for (const ev of eventLog) {
    const s = ev.summary || '';
    if (s.startsWith('Starting single-session orchestration') ||
        s.startsWith('Starting multi-session orchestration') ||
        s.startsWith('Resumed from checkpoint')) {
      break;
    }
    events.push(ev);
  }
  return events;
}

// ── Timeline ───────────────────────────────────
function renderTimeline(trace, running, session) {
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

  // Pre-flight phase from session.event_log (collapsed by default for completed tasks)
  const preflightEvents = _getPreflightEvents((session && session.event_log) || []);
  if (preflightEvents.length > 0) {
    const pfColor = PHASE_COLORS.PREFLIGHT || 'var(--cyan)';
    const pfErrors = preflightEvents.filter(ev => ev.is_error);
    const pfSteps = preflightEvents.filter(ev => !ev.is_error);
    const pfCollapsed = !running ? ' collapsed' : '';

    // Duration from first to last event timestamp
    const pfFirst = preflightEvents[0].timestamp;
    const pfLast = preflightEvents[preflightEvents.length - 1].timestamp;
    const pfDurMs = pfFirst && pfLast ? (pfLast - pfFirst) * 1000 : 0;
    const pfDurStr = pfDurMs > 0 ? fmtDurationMs(pfDurMs) : '';

    // Summary meta for the phase bar
    const pfMetaParts = [];
    if (pfDurStr) pfMetaParts.push(pfDurStr);
    if (pfErrors.length > 0) {
      const failedCheckers = pfErrors.map(ev => {
        const m = (ev.summary || '').match(/^(\w+):/);
        return m ? m[1] : 'error';
      });
      pfMetaParts.push(`failed: ${failedCheckers.join(', ')}`);
    } else {
      pfMetaParts.push(`${pfSteps.length} check${pfSteps.length !== 1 ? 's' : ''} passed`);
    }
    const pfMeta = pfMetaParts.join(' · ');

    html += `<div class="tl-phase${pfCollapsed}" id="phase-preflight" data-phase="preflight" onclick="togglePhase(this)">
      <span class="tl-phase-chevron">${running ? '▾' : '▸'}</span>
      <span class="tl-phase-line" style="border-color:${pfColor}"></span>
      <span class="tl-phase-name" style="color:${pfColor}">PRE-FLIGHT</span>
      <span class="tl-phase-meta">${esc(pfMeta)}</span>
      <span class="tl-phase-line" style="border-color:${pfColor}"></span>
    </div>
    <div class="tl-phase-content">`;

    for (const ev of pfSteps) {
      const icon = (ev.summary || '').includes('worktree') ? '🌿' : (ev.summary || '').includes('verification') ? '🔍' : '⚙';
      html += `<div class="tl-tool">
        <div class="tl-tool-header">
          <span class="tl-tool-icon">${icon}</span>
          <span class="tl-tool-name bash">supervisor</span>
          <span class="tl-tool-summary">${esc(ev.summary || '')}</span>
        </div>
      </div>`;
    }

    for (const ev of pfErrors) {
      const msg = ev.summary || '';
      const sections = msg.split(/(?=\b(?:black|pylint|pytest):)/i).filter(Boolean);
      const leadSection = sections.length > 0 && !/^(black|pylint|pytest):/i.test(sections[0]) ? sections.shift() : '';

      if (leadSection) {
        html += `<div class="tl-text" style="color:var(--danger);font-weight:600;font-size:0.82rem;margin:0.5rem 0">${esc(leadSection.replace(/[;,]\s*$/, '').trim())}</div>`;
      }

      for (const section of sections) {
        const match = section.match(/^(\w+):\s*([\s\S]*)$/);
        const checker = match ? match[1] : 'error';
        const body = match ? match[2].replace(/[;,]\s*$/, '').trim() : section.trim();
        html += `<div class="tl-tool" onclick="this.classList.toggle('expanded')">
          <div class="tl-tool-header">
            <span class="tl-tool-icon" style="color:var(--danger)">✗</span>
            <span class="tl-tool-name" style="color:var(--danger)">${esc(checker)}</span>
            <span class="tl-tool-summary" style="color:var(--danger)">${esc(truncText(body, 100))}</span>
            <span class="tl-tool-chevron">▸</span>
          </div>
          <div class="tl-tool-body">
            <pre>${esc(body)}</pre>
          </div>
        </div>`;
      }
    }

    html += `</div>`; // end tl-phase-content
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

    // Completion summary banner (uses `result` already declared above)
    if (result) {
      const status = result.status || (result.success !== false ? 'COMPLETE' : 'BLOCKED');
      const success = status === 'COMPLETE' || status === 'PASS';
      const bgColor = success ? 'var(--bg-success)' : 'var(--bg-danger)';
      const borderColor = success ? 'var(--border-success)' : 'var(--border-danger)';
      const statusClass = success ? 'complete' : 'blocked';
      const summaryText = result.summary || '';

      // Test results
      const testsObj = result.test_results || {};
      let testsHtml = '';
      if (Object.keys(testsObj).length > 0) {
        testsHtml = '<div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-top:0.3rem">' +
          Object.entries(testsObj).map(([name, val]) => {
            const lower = String(val).toLowerCase();
            const pass = val === true || (/\bpass/i.test(lower) && !/\bfail/i.test(lower));
            const skip = lower === 'not_applicable' || lower === 'skipped' || lower === 'not_run';
            const color = skip ? 'var(--text-muted)' : (pass ? 'var(--green)' : 'var(--danger)');
            const icon = skip ? '—' : (pass ? '✓' : '✗');
            return `<span style="color:${color};font-size:0.7rem">${icon} ${esc(name)}</span>`;
          }).join('') + '</div>';
      }

      // Files changed
      const files = result.files_changed || [];
      let filesHtml = '';
      if (files.length > 0) {
        filesHtml = `<div style="color:var(--text-muted);font-size:0.68rem;margin-top:0.3rem">Files changed: ${
          files.map(f => `<code style="font-family:var(--font-mono);font-size:0.65rem;background:var(--bg-elevated);padding:0.1rem 0.3rem;border-radius:3px">${esc(f)}</code>`).join(' ')
        }</div>`;
      }

      html += `<div style="margin-top:0.75rem;padding:0.6rem 0.75rem;background:${bgColor};border:1px solid ${borderColor};border-radius:6px">
        <div style="display:flex;align-items:center;gap:0.5rem;margin-bottom:0.3rem">
          <span class="status ${statusClass}" style="font-size:0.7rem;font-weight:700;padding:0.15rem 0.5rem;border-radius:3px">${esc(status.toUpperCase())}</span>
          ${summaryText ? `<span style="font-size:0.75rem;color:var(--text-secondary)">${esc(summaryText)}</span>` : ''}
        </div>
        ${testsHtml}
        ${filesHtml}
      </div>`;
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
  const fixBuilder = cycle.fix_builder || cycle.fix_agent || cycle.builder || null;
  const cycleNum = cycle.cycle_number || cycle.index || 1;
  const dur = cycle.duration_ms ? fmtDurationMs(cycle.duration_ms) : '';
  const tokens = cycle.total_tokens ? fmtTokens(cycle.total_tokens) : '';
  const metaParts = [dur, tokens ? `${tokens} tokens` : ''].filter(Boolean);

  const issuesHtml = issues.map(issue => {
    const conf = issue.confidence != null ? Math.round(issue.confidence) : '';
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
      const lower = String(val).toLowerCase();
      const pass = val === true || (/\bpass/i.test(lower) && !/\bfail/i.test(lower));
      const skip = lower === 'not_applicable' || lower === 'skipped' || lower === 'not_run';
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

  // Default first tab: Report if available, otherwise Errors
  const firstTab = report ? 'report' : 'errors';

  el.innerHTML = `
    <div class="tab-bar">
      ${report ? `<button class="tab-btn${firstTab === 'report' ? ' active' : ''}" onclick="activateTab(this,'tab-report')">Report</button>` : ''}
      <button class="tab-btn${firstTab === 'errors' ? ' active' : ''}" onclick="activateTab(this,'tab-errors')">Errors</button>
      <button class="tab-btn" onclick="activateTab(this,'tab-raw')">Raw Session</button>
    </div>
    ${report ? `<div class="tab-content${firstTab === 'report' ? ' active' : ''}" id="tab-report">
      ${renderMarkdown(report)}
    </div>` : ''}
    <div class="tab-content${firstTab === 'errors' ? ' active' : ''}" id="tab-errors">${errors}</div>
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
