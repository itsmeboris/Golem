/* golem/core/task_live.js — Polling loop, incremental DOM updates, live cursor, auto-scroll.
 * Depends on: task_api.js (S, fetchParsedTrace, isTaskRunning),
 *             task_overview.js (renderOverview), task_timeline.js (renderDetail).
 * Loaded last — wires up DOMContentLoaded.
 */
'use strict';

let _pollInFlight = false;

function startPolling() {
  if (S.pollTimer) return;
  S.pollTimer = setInterval(async () => {
    if (_pollInFlight) return;  // prevent concurrent ticks
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
          if (trace) {
            renderDetail(S.selectedTaskId, trace);  // avoid double fetch
            updateLiveCursor();
            autoScrollIfAtBottom();
          }
        }
      }
    } finally {
      _pollInFlight = false;
    }
  }, 5000);
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
function autoScrollIfAtBottom() {
  const scroll = document.getElementById('timeline-scroll');
  if (!scroll) return;
  const atBottom = scroll.scrollTop + scroll.clientHeight >= scroll.scrollHeight - 50;
  if (atBottom) {
    scroll.scrollTop = scroll.scrollHeight;
  }
}

document.addEventListener('DOMContentLoaded', async () => {
  // Navigation tabs
  document.querySelectorAll('.nav-tab').forEach(tab =>
    tab.addEventListener('click', () => showView(tab.dataset.view))
  );

  const backLink = document.getElementById('td-back-link');
  if (backLink) backLink.addEventListener('click', () => showView('overview'));

  // Deep link: #detail/golem-123-20260310 — must load sessions first
  const hash = location.hash.slice(1);
  if (hash.startsWith('detail/')) {
    S.selectedTaskId = hash.slice(7);
    S.sessions = await fetchSessions();
    showView('detail');
  } else {
    showView('overview');
  }

  startPolling();
});
