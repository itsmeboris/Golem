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
  const res = await fetch('/api/sessions');
  const data = await res.json();
  return data.sessions || {};
}

async function fetchParsedTrace(eventId, incremental = false) {
  // Return cached if available and not requesting incremental update
  if (!incremental && S.parsedTraces[eventId]) return S.parsedTraces[eventId];

  // Build URL with optional since_event for incremental updates
  let url = `/api/trace-parsed/${eventId}`;
  if (incremental && S.lastEventCounts[eventId]) {
    url += `?since_event=${S.lastEventCounts[eventId]}`;
  }

  const res = await fetch(url);
  if (!res.ok) return null;
  const data = await res.json();

  // Track event count for incremental polling
  if (data.total_events) S.lastEventCounts[eventId] = data.total_events;

  return data;
}

async function fetchPrompt(eventId) {
  const res = await fetch(`/api/prompt/${eventId}`);
  if (!res.ok) return null;
  const data = await res.json();
  return data.prompt || '';
}

// ── Navigation ─────────────────────────────────
function showView(view) {
  S.view = view;
  document.getElementById('view-overview').style.display = view === 'overview' ? 'flex' : 'none';
  document.getElementById('view-detail').style.display = view === 'detail' ? 'flex' : 'none';
  document.querySelectorAll('.nav-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.view === view)
  );
  if (view === 'overview') renderOverview();   // defined in task_overview.js
  if (view === 'detail' && S.selectedTaskId) renderDetail(S.selectedTaskId);  // defined in task_timeline.js
}

function selectTask(eventId) {
  S.selectedTaskId = eventId;
  showView('detail');
}

// ── Helpers ────────────────────────────────────
const PHASE_COLORS = {
  UNDERSTAND: 'var(--blue)', PLAN: 'var(--purple)',
  BUILD: 'var(--accent)', REVIEW: 'var(--orange)', VERIFY: 'var(--green)'
};

function isTaskRunning(session) {
  return session && ['RUNNING', 'VERIFYING', 'VALIDATING'].includes(session.state);
}

function _stateToChipClass(state) {
  if (state === 'COMPLETED') return 'done';
  if (['RUNNING', 'VERIFYING', 'VALIDATING'].includes(state)) return 'running';
  if (['FAILED', 'HUMAN_REVIEW'].includes(state)) return 'failed';
  return 'waiting';
}
