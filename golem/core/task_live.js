/* golem/core/task_live.js — SSE live updates with polling fallback, incremental DOM updates,
 * live cursor, auto-scroll.
 * Depends on: task_api.js (S, fetchSessions, fetchParsedTrace, isTaskRunning),
 *             task_overview.js (renderOverview), task_timeline.js (renderDetail).
 * Loaded last — wires up DOMContentLoaded.
 */
'use strict';

let _pollInFlight = false;
let _lastHbFetch = 0;

function _maybeRefreshHeartbeat() {
  var now = Date.now();
  if (now - _lastHbFetch < 30000) return;
  _lastHbFetch = now;
  if (typeof updateHeartbeat === 'function') updateHeartbeat();
}

// ── SSE state ──────────────────────────────────
let _eventSource = null;
let _renderTimeout = null;
let _needsSessionUpdate = false;
let _needsTraceUpdate = false;
let _needsMergeQueueUpdate = false;
let _reconnectTimeout = null;

// ── Batched SSE flush ──────────────────────────
async function _flushSSEUpdates() {
  _renderTimeout = null;
  if (_pollInFlight) return;
  _pollInFlight = true;
  try {
    _maybeRefreshHeartbeat();
    if (_needsSessionUpdate || _needsTraceUpdate || _needsMergeQueueUpdate) {
      if (S.view === 'overview') {
        await renderOverview();
      } else if (S.view === 'detail' && S.selectedTaskId) {
        S.sessions = await fetchSessions();
        const session = S.sessions[S.selectedTaskId];
        if (isTaskRunning(session)) {
          const trace = _needsTraceUpdate
            ? await fetchParsedTrace(S.selectedTaskId, true)
            : undefined;
          await renderDetail(S.selectedTaskId, trace || undefined);
          updateLiveCursor();
          autoScrollIfAtBottom();
        } else {
          await renderDetail(S.selectedTaskId);
        }
      } else if (S.view === 'merge-queue' && _needsMergeQueueUpdate) {
        if (typeof renderMergeQueue === 'function') await renderMergeQueue();
      }
    }
  } finally {
    _pollInFlight = false;
    _needsSessionUpdate = false;
    _needsTraceUpdate = false;
    _needsMergeQueueUpdate = false;
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

  es.addEventListener('merge_queue_update', () => {
    if (S.view === 'merge-queue') {
      _needsMergeQueueUpdate = true;
      _scheduleRender();
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
      _maybeRefreshHeartbeat();
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
  // Populate heartbeat chip immediately on page load
  if (typeof updateHeartbeat === 'function') updateHeartbeat();

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

  // Deep link restore on page load — read hash and navigate to matching view
  const hash = getHashRoute();
  if (hash.startsWith('task/')) {
    S.selectedTaskId = hash.slice(5);
    S.sessions = await fetchSessions();
    showView('detail');
  } else if (hash.startsWith('detail/')) {
    // Legacy format: #detail/<id> — still supported for bookmarked URLs
    S.selectedTaskId = hash.slice(7);
    S.sessions = await fetchSessions();
    showView('detail');
  } else if (hash === 'merge-queue') {
    showView('merge-queue');
  } else if (hash === 'config') {
    showView('config');
  } else if (hash === 'prompts') {
    showView('prompts');
  } else {
    showView('overview');
  }

  // Browser back/forward support
  window.addEventListener('hashchange', async () => {
    const h = getHashRoute();
    if (h.startsWith('task/')) {
      S.selectedTaskId = h.slice(5);
      showView('detail');
    } else if (h.startsWith('detail/')) {
      // Legacy format: #detail/<id> — still supported for bookmarked URLs
      S.selectedTaskId = h.slice(7);
      showView('detail');
    } else if (h === 'merge-queue') {
      showView('merge-queue');
    } else if (h === 'config') {
      showView('config');
    } else if (h === 'prompts') {
      showView('prompts');
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
