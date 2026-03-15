/* golem/core/task_live.js — SSE live updates with polling fallback, incremental DOM updates,
 * live cursor, auto-scroll.
 * Depends on: task_api.js (S, fetchSessions, fetchParsedTrace, isTaskRunning),
 *             task_overview.js (renderOverview), task_timeline.js (renderDetail).
 * Loaded last — wires up DOMContentLoaded.
 */
'use strict';

let _pollInFlight = false;

// ── SSE state ──────────────────────────────────
let _eventSource = null;
let _renderTimeout = null;
let _needsSessionUpdate = false;
let _needsTraceUpdate = false;
let _reconnectTimeout = null;

// ── Batched SSE flush ──────────────────────────
async function _flushSSEUpdates() {
  _renderTimeout = null;
  if (_pollInFlight) return;
  _pollInFlight = true;
  try {
    if (_needsSessionUpdate || _needsTraceUpdate) {
      if (S.view === 'overview') {
        await renderOverview();
      } else if (S.view === 'detail' && S.selectedTaskId) {
        S.sessions = await fetchSessions();
        const session = S.sessions[S.selectedTaskId];
        if (isTaskRunning(session)) {
          // Running task — always re-render for live cost/duration/phase updates
          const trace = _needsTraceUpdate
            ? await fetchParsedTrace(S.selectedTaskId, true)
            : undefined;
          await renderDetail(S.selectedTaskId, trace || undefined);
          updateLiveCursor();
          autoScrollIfAtBottom();
        } else {
          // Task just completed or session updated — full render
          await renderDetail(S.selectedTaskId);
        }
      }
    }
  } finally {
    _pollInFlight = false;
    _needsSessionUpdate = false;
    _needsTraceUpdate = false;
  }
}

// ── Debounce helper ────────────────────────────
function _scheduleRender() {
  if (_renderTimeout !== null) return; // already scheduled within 500ms window
  _renderTimeout = setTimeout(_flushSSEUpdates, 500);
}

// ── SSE connection ─────────────────────────────
function connectSSE() {
  if (_reconnectTimeout !== null) {
    clearTimeout(_reconnectTimeout);
    _reconnectTimeout = null;
  }
  if (_eventSource) {
    _eventSource.close();
    _eventSource = null;
  }

  const es = new EventSource('/api/events');
  _eventSource = es;

  es.addEventListener('open', () => {
    // SSE connected — stop fallback polling if it was running
    stopPolling();
    // Clear any pending reconnect attempt
    if (_reconnectTimeout !== null) {
      clearTimeout(_reconnectTimeout);
      _reconnectTimeout = null;
    }
  });

  es.addEventListener('session_update', () => {
    _needsSessionUpdate = true;
    _scheduleRender();
  });

  es.addEventListener('trace_update', (event) => {
    try {
      const data = JSON.parse(event.data);
      const eid = (data && data.event_id) || '';
      // Trace file stems are "golem-123" but S.selectedTaskId is "123".
      // Match if event_id contains the selected task ID.
      const selected = S.selectedTaskId || '';
      const isCurrentTask = selected && (eid === selected || eid.includes(selected));
      if (S.view === 'overview' || isCurrentTask) {
        _needsTraceUpdate = true;
        _scheduleRender();
      }
    } catch (_e) {
      // Malformed data — ignore
    }
  });

  es.addEventListener('error', () => {
    es.close();
    _eventSource = null;
    // Fall back to polling while SSE is down
    _startFallbackPolling();
    // Attempt to reconnect SSE after 5s
    if (_reconnectTimeout === null) {
      _reconnectTimeout = setTimeout(() => {
        _reconnectTimeout = null;
        connectSSE();
      }, 5000);
    }
  });
}

// ── Fallback polling ───────────────────────────
function _startFallbackPolling() {
  if (S.pollTimer) return;
  S.pollTimer = setInterval(async () => {
    if (_pollInFlight) return; // prevent concurrent ticks
    _pollInFlight = true;
    try {
      if (S.view === 'overview') {
        await renderOverview();
      } else if (S.view === 'detail' && S.selectedTaskId) {
        // Refresh session state so we detect when a task completes
        S.sessions = await fetchSessions();
        const session = S.sessions[S.selectedTaskId];
        if (isTaskRunning(session)) {
          // Incremental: only fetch new events since last poll
          const trace = await fetchParsedTrace(S.selectedTaskId, true);
          // Always re-render: shows session status even before trace is available
          await renderDetail(S.selectedTaskId, trace || undefined);
          updateLiveCursor();
          autoScrollIfAtBottom();
        } else {
          // Task just completed — do a final full render to show completion state
          await renderDetail(S.selectedTaskId);
        }
      }
    } finally {
      _pollInFlight = false;
    }
  }, 5000);
}

function startPolling() {
  _startFallbackPolling();
}

function stopPolling() {
  if (S.pollTimer) {
    clearInterval(S.pollTimer);
    S.pollTimer = null;
  }
}

// ── Live cursor update ─────────────────────────
function updateLiveCursor() {
  const cursor = document.getElementById('tl-live-cursor');
  if (!cursor) return;
  if (S.selectedTaskId && isTaskRunning(S.sessions[S.selectedTaskId])) {
    cursor.style.display = 'flex';
  } else {
    cursor.style.display = 'none';
  }
}

// ── Auto-scroll ────────────────────────────────
let _initialScrollDone = false;

function autoScrollIfAtBottom() {
  const scroll = document.getElementById('timeline-scroll');
  if (!scroll) return;
  // On first render of a running task detail, always scroll to bottom
  if (!_initialScrollDone) {
    scroll.scrollTop = scroll.scrollHeight;
    _initialScrollDone = true;
    return;
  }
  const atBottom = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 50;
  if (atBottom) {
    scroll.scrollTop = scroll.scrollHeight;
  }
}

function resetAutoScroll() {
  _initialScrollDone = false;
}

document.addEventListener('DOMContentLoaded', async () => {
  // Navigation tabs
  document.querySelectorAll('.nav-tab').forEach(tab =>
    tab.addEventListener('click', () => showView(tab.dataset.view))
  );

  const backLink = document.getElementById('td-back-link');
  if (backLink) backLink.addEventListener('click', () => showView('overview'));

  // Modal listeners
  const modalClose = document.getElementById('resubmit-modal-close');
  const modalCancel = document.getElementById('resubmit-cancel-btn');
  const modalSubmit = document.getElementById('resubmit-submit-btn');
  const modalOverlay = document.getElementById('resubmit-modal');
  if (modalClose) modalClose.addEventListener('click', _closeResubmitModal);
  if (modalCancel) modalCancel.addEventListener('click', _closeResubmitModal);
  if (modalSubmit) modalSubmit.addEventListener('click', _submitResubmitModal);
  if (modalOverlay) modalOverlay.addEventListener('click', (e) => {
    if (e.target === modalOverlay) _closeResubmitModal();
  });

  // Deep link: #detail/golem-123-20260310 — must load sessions first
  const hash = location.hash.slice(1);
  if (hash.startsWith('detail/')) {
    S.selectedTaskId = hash.slice(7);
    S.sessions = await fetchSessions();
    showView('detail');
  } else {
    showView('overview');
  }

  // Browser back/forward support
  window.addEventListener('hashchange', async () => {
    const h = location.hash.slice(1);
    if (h.startsWith('detail/')) {
      S.selectedTaskId = h.slice(7);
      showView('detail');
    } else {
      showView('overview');
    }
  });

  // Use SSE for live updates; fall back to polling if EventSource is unavailable
  if (typeof EventSource === 'undefined') {
    startPolling();
  } else {
    connectSSE();
  }
});
