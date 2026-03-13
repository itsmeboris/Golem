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
async function showView(view) {
  S.view = view;
  document.getElementById('view-overview').style.display = view === 'overview' ? 'flex' : 'none';
  document.getElementById('view-detail').style.display = view === 'detail' ? 'flex' : 'none';
  document.querySelectorAll('.nav-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.view === view)
  );

  // Update URL hash so the view persists across refresh / back-forward
  const newHash = view === 'detail' && S.selectedTaskId
    ? `#detail/${S.selectedTaskId}`
    : '#overview';
  if (location.hash !== newHash) history.pushState(null, '', newHash);

  if (view === 'overview') renderOverview();
  if (view === 'detail' && S.selectedTaskId) {
    if (typeof resetAutoScroll === 'function') resetAutoScroll();
    await renderDetail(S.selectedTaskId);
    // Auto-scroll to bottom on initial navigation to a running task
    const session = S.sessions[S.selectedTaskId];
    if (isTaskRunning(session) && typeof autoScrollIfAtBottom === 'function') {
      autoScrollIfAtBottom();
    }
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
  PREFLIGHT: 'var(--cyan, #5eead4)',
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
