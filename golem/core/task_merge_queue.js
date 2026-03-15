/* golem/core/task_merge_queue.js — Merge Queue tab rendering.
 * Depends on: dashboard_shared.js (esc, fmtDuration), task_api.js (S, fetchMergeQueue).
 */
'use strict';

async function renderMergeQueue() {
  const data = await fetchMergeQueue();
  const metricsEl = document.getElementById('mq-metrics');
  const contentEl = document.getElementById('mq-content');
  if (!metricsEl || !contentEl) return;

  if (!data) {
    contentEl.innerHTML = `<div class="mq-offline">
      <h2>Merge Queue Unavailable</h2>
      <p>Dashboard running in standalone mode — merge queue not connected.</p>
    </div>`;
    metricsEl.innerHTML = '';
    return;
  }

  // Metrics
  const pending = data.pending || [];
  const active = data.active;
  const deferred = data.deferred || [];
  const conflicts = data.conflicts || [];
  const history = data.history || [];

  const today = new Date().toISOString().slice(0, 10);
  const mergedToday = history.filter(h => h.success && (h.timestamp || '').startsWith(today)).length;
  const failedToday = history.filter(h => !h.success && (h.timestamp || '').startsWith(today)).length;

  metricsEl.innerHTML = `
    <div class="metric"><span class="metric-label">Pending</span><span class="metric-value" style="color:var(--yellow)">${pending.length}</span></div>
    <div class="metric"><span class="metric-label">Merging</span><span class="metric-value" style="color:var(--green)">${active ? 1 : 0}</span></div>
    <div class="metric"><span class="metric-label">Deferred</span><span class="metric-value" style="color:var(--orange)">${deferred.length}</span></div>
    <div class="metric"><span class="metric-label">Conflicts</span><span class="metric-value" style="color:var(--red)">${conflicts.length}</span></div>
    <div class="metric"><span class="metric-label">Merged Today</span><span class="metric-value" style="color:var(--green)">${mergedToday}</span></div>
    <div class="metric"><span class="metric-label">Failed Today</span><span class="metric-value" style="color:var(--red)">${failedToday}</span></div>
  `;

  // Sections
  let html = '';

  // Active
  html += _mqSection('Active', 'var(--green)', active ? 1 : 0, active ? '1 merging' : 'idle');
  if (active) {
    html += _mqEntryRow(active, 'merging');
    html += _mqExpandPanel(active);
  } else {
    html += '<div style="padding:0.5rem 1.25rem;font-size:0.78rem;color:var(--text-muted);font-style:italic">No active merge</div>';
  }
  html += '</div></div>';

  // Pending
  html += _mqSection('Pending', 'var(--yellow)', pending.length, `${pending.length} queued`);
  if (pending.length === 0) {
    html += '<div style="padding:0.5rem 1.25rem;font-size:0.78rem;color:var(--text-muted);font-style:italic">Queue empty</div>';
  }
  for (const entry of pending) {
    html += _mqEntryRow(entry, 'pending');
    html += _mqExpandPanel(entry);
  }
  html += '</div></div>';

  // Deferred
  html += _mqSection('Deferred', 'var(--orange)', deferred.length, `${deferred.length} waiting for clean tree`);
  for (const entry of deferred) {
    html += _mqEntryRow(entry, 'deferred', true);
    html += _mqExpandPanel(entry);
  }
  if (deferred.length === 0) {
    html += '<div style="padding:0.5rem 1.25rem;font-size:0.78rem;color:var(--text-muted);font-style:italic">None</div>';
  }
  html += '</div></div>';

  // Conflicts
  html += _mqSection('Conflicts', 'var(--red)', conflicts.length, `${conflicts.length} needs resolution`);
  for (const entry of conflicts) {
    html += _mqEntryRow(entry, 'conflict', true);
    html += _mqExpandPanel(entry);
  }
  if (conflicts.length === 0) {
    html += '<div style="padding:0.5rem 1.25rem;font-size:0.78rem;color:var(--text-muted);font-style:italic">None</div>';
  }
  html += '</div></div>';

  // Recent
  const recent = history.slice().reverse().slice(0, 20);
  html += _mqSection('Recent', 'var(--text-muted)', recent.length, `last ${recent.length} merges`);
  for (const h of recent) {
    const status = h.success ? 'success' : (h.deferred ? 'deferred' : 'failed');
    const label = h.success ? 'merged' : (h.deferred ? 'deferred' : 'failed');
    const sha = h.merge_sha ? h.merge_sha.slice(0, 7) : '';
    const timeAgo = h.timestamp ? _mqTimeAgo(h.timestamp) : '';
    html += `<div class="mq-entry mq-entry--history">
      <span class="mq-entry-dot ${status}"></span>
      <span class="mq-entry-id">#${h.session_id}</span>
      <span class="mq-entry-detail">${esc(h.error || '')}</span>
      <span class="mq-entry-badge ${status}">${label}</span>
      <span class="mq-entry-files">${(h.changed_files || []).length} files</span>
      <span class="mq-entry-time">${esc(timeAgo)}</span>
      ${sha ? `<span class="mq-entry-sha">${h.success ? esc(sha) : '<span style="color:var(--red)">conflict</span>'}</span>` : ''}
    </div>`;
  }
  if (recent.length === 0) {
    html += '<div style="padding:0.5rem 1.25rem;font-size:0.78rem;color:var(--text-muted);font-style:italic">No merge history</div>';
  }
  html += '</div></div>';

  contentEl.innerHTML = html;

  // Wire up expand/collapse and retry
  contentEl.querySelectorAll('.mq-entry:not(.mq-entry--history)').forEach(row => {
    row.addEventListener('click', () => {
      const detail = row.nextElementSibling;
      if (detail && detail.classList.contains('mq-entry-expand')) {
        detail.classList.toggle('open');
        const chev = row.querySelector('.mq-entry-chevron');
        if (chev) chev.classList.toggle('open');
      }
    });
  });

  contentEl.querySelectorAll('.mq-section-header').forEach(header => {
    header.addEventListener('click', () => {
      header.parentElement.classList.toggle('collapsed');
    });
  });

  contentEl.querySelectorAll('.mq-retry-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      btn.disabled = true;
      btn.textContent = 'Retrying…';
      try {
        const resp = await fetch(`/api/merge-queue/retry/${btn.dataset.sessionId}`, { method: 'POST' });
        if (resp.ok) {
          await renderMergeQueue();
        } else {
          const data = await resp.json();
          btn.textContent = data.error || 'Failed';
        }
      } catch (_e) {
        btn.textContent = 'Error';
      }
    });
  });
}

function _mqSection(label, color, count, countText) {
  return `<div class="mq-section">
    <div class="mq-section-header">
      <span class="mq-section-label" style="color:${color}">${label}</span>
      <span class="mq-section-count">${esc(countText)}</span>
      <span class="mq-section-line"></span>
      <span class="mq-section-chevron">▾</span>
    </div>
    <div class="mq-section-body">`;
}

function _mqEntryRow(entry, status, showRetry) {
  const files = (entry.changed_files || []).length;
  const time = entry.queued_at ? _mqTimeAgo(entry.queued_at) : '';
  const retryBtn = showRetry
    ? `<button class="mq-retry-btn" data-session-id="${entry.session_id}">Retry</button>`
    : '';
  return `<div class="mq-entry">
    <span class="mq-entry-dot ${status}"></span>
    <span class="mq-entry-id">#${entry.session_id}</span>
    <span class="mq-entry-branch">${esc(entry.branch_name || '')}</span>
    <span class="mq-entry-detail"></span>
    <span class="mq-entry-badge ${status}">${status}</span>
    <span class="mq-entry-files">${files ? files + ' files' : '—'}</span>
    <span class="mq-entry-time">${esc(time)}</span>
    ${retryBtn}
    <span class="mq-entry-chevron">▸</span>
  </div>`;
}

function _mqExpandPanel(entry) {
  const files = (entry.changed_files || []).map(f =>
    `<span class="mq-changed-file">${esc(f)}</span>`
  ).join('');
  return `<div class="mq-entry-expand">
    <div class="mq-detail-row"><span class="mq-detail-label">Branch</span><span class="mq-detail-value"><code>${esc(entry.branch_name || '')}</code></span></div>
    <div class="mq-detail-row"><span class="mq-detail-label">Priority</span><span class="mq-detail-value">${entry.priority}</span></div>
    <div class="mq-detail-row"><span class="mq-detail-label">Queued At</span><span class="mq-detail-value">${esc(entry.queued_at || '')}</span></div>
    ${files ? `<div class="mq-detail-row"><span class="mq-detail-label">Files</span><span class="mq-detail-value">${files}</span></div>` : ''}
  </div>`;
}

function _mqTimeAgo(isoStr) {
  try {
    const then = new Date(isoStr).getTime();
    const now = Date.now();
    const diffS = Math.floor((now - then) / 1000);
    if (diffS < 60) return `${diffS}s`;
    if (diffS < 3600) return `${Math.floor(diffS / 60)}m`;
    if (diffS < 86400) return `${Math.floor(diffS / 3600)}h`;
    return `${Math.floor(diffS / 86400)}d`;
  } catch (_e) {
    return '';
  }
}
