/* golem/core/task_api.js — State management, API client, view navigation.
 * Loaded first. Exposes: S (state), fetch*, showView, selectTask.
 * Uses template literals for any HTML generation.
 */
'use strict';

// ── State ──────────────────────────────────────
const S = {
  view: 'overview',        // 'overview' | 'detail'
  sessions: {},            // event_id → session data
  selectedTaskId: null,    // event_id of selected task
  parsedTraces: {},        // event_id → ParsedTrace (cached client-side)
  lastEventCounts: {},     // event_id → total_events (for incremental polling)
  pollTimer: null,
  showPrompt: false,
  showThinking: false,
  searchQuery: '',
};

// ── API Client ─────────────────────────────────
async function fetchSessions() {
  const res = await fetch('/api/sessions', { signal: AbortSignal.timeout(10000) });
  const data = await res.json();
  return data.sessions || {};
}

async function fetchMergeQueue() {
  try {
    const resp = await fetch('/api/merge-queue', { signal: AbortSignal.timeout(10000) });
    if (!resp.ok) return null;
    return await resp.json();
  } catch (_e) {
    return null;
  }
}

async function fetchParsedTrace(eventId, incremental = false) {
  // Return cached if available and not requesting incremental update
  if (!incremental && S.parsedTraces[eventId]) return S.parsedTraces[eventId];

  // Build URL with optional since_event for incremental updates
  let url = `/api/trace-parsed/${eventId}`;
  if (incremental && S.lastEventCounts[eventId]) {
    url += `?since_event=${S.lastEventCounts[eventId]}`;
  }

  const res = await fetch(url, { signal: AbortSignal.timeout(10000) });
  if (!res.ok) return null;
  const data = await res.json();

  // Track event count for incremental polling
  if (data.total_events) S.lastEventCounts[eventId] = data.total_events;

  return data;
}

async function fetchPrompt(eventId) {
  const res = await fetch(`/api/prompt/${eventId}`, { signal: AbortSignal.timeout(10000) });
  if (!res.ok) return null;
  const data = await res.json();
  return data.prompt || '';
}

async function cancelTask(taskId) {
  const res = await fetch(`/api/cancel/${taskId}`, { method: 'POST', signal: AbortSignal.timeout(30000) });
  if (!res.ok) {
    try { return await res.json(); } catch (_) { return { ok: false, detail: res.statusText }; }
  }
  return res.json();
}

async function clearFailedSessions() {
  const res = await fetch('/api/sessions/clear-failed', { method: 'POST', signal: AbortSignal.timeout(30000) });
  if (!res.ok) {
    try { return await res.json(); } catch (_) { return { ok: false, detail: res.statusText }; }
  }
  return res.json();
}

async function resubmitTask(prompt, subject) {
  const res = await fetch('/api/submit', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, subject }),
    signal: AbortSignal.timeout(30000),
  });
  if (!res.ok) {
    try { return await res.json(); } catch (_) { return { ok: false, detail: res.statusText }; }
  }
  return res.json();
}

// ── Navigation ─────────────────────────────────
async function showView(view) {
  S.view = view;
  const views = ['overview', 'detail', 'merge-queue', 'config', 'prompts'];
  views.forEach(v => {
    const el = document.getElementById(`view-${v}`);
    if (el) el.style.display = (v === view) ? 'flex' : 'none';
  });
  document.querySelectorAll('.nav-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.view === view)
  );

  // Update URL hash — use pushState to avoid triggering hashchange
  if (view === 'detail' && S.selectedTaskId) {
    const newHash = `#detail/${S.selectedTaskId}`;
    if (location.hash !== newHash) history.pushState(null, '', newHash);
  } else if (view === 'merge-queue') {
    if (location.hash !== '#merge-queue') history.pushState(null, '', '#merge-queue');
  } else if (view === 'config') {
    if (location.hash !== '#config') history.pushState(null, '', '#config');
  } else {
    if (location.hash !== '#overview') history.pushState(null, '', '#overview');
  }

  if (view === 'overview') renderOverview();
  if (view === 'detail' && S.selectedTaskId) {
    if (typeof resetAutoScroll === 'function') resetAutoScroll();
    await renderDetail(S.selectedTaskId);
    const session = S.sessions[S.selectedTaskId];
    if (isTaskRunning(session) && typeof autoScrollIfAtBottom === 'function') {
      autoScrollIfAtBottom();
    }
  }
  if (view === 'merge-queue' && typeof renderMergeQueue === 'function') {
    renderMergeQueue();
  }
  if (view === 'config' && typeof window.initConfigTab === 'function') {
    window.initConfigTab();
  }
  if (view === 'prompts' && typeof renderPromptAnalytics === 'function') {
    renderPromptAnalytics();
  }
}

function selectTask(eventId) {
  S.selectedTaskId = eventId;
  if (typeof resetAutoScroll === 'function') resetAutoScroll();
  showView('detail');
}

// ── Helpers ────────────────────────────────────
function subjectTitle(session) {
  const raw = (session && (session.subject || session.parent_subject)) || '';
  // Strip detection tag, markdown heading markers, then take first non-empty line
  return raw.split('\n')
    .map(l => l.replace(/^\[AGENT\]\s*/i, '').replace(/^#+\s*/, '').trim())
    .find(l => l) || raw;
}

const PHASE_COLORS = {
  PREFLIGHT: 'var(--cyan)',
  UNDERSTAND: 'var(--blue)', PLAN: 'var(--purple)',
  BUILD: 'var(--accent)', REVIEW: 'var(--orange)', VERIFY: 'var(--green)'
};

function isTaskRunning(session) {
  if (!session) return false;
  const s = (session.state || '').toLowerCase();
  return ['running', 'verifying', 'validating', 'retrying', 'detected'].includes(s);
}

function _stateToChipClass(state) {
  const s = (state || '').toLowerCase();
  if (s === 'completed') return 'done';
  if (['running', 'verifying', 'validating', 'retrying', 'detected'].includes(s)) return 'running';
  if (['failed', 'human_review'].includes(s)) return 'failed';
  return 'waiting';
}
