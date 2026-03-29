'use strict';

async function renderPromptAnalytics() {
  const container = document.getElementById('pa-table-container');
  if (!container) return;

  container.innerHTML = '<div class="loading-overlay"><div class="loading-spinner"></div>Loading\u2026</div>';

  try {
    const res = await fetch('/api/analytics/by-prompt', { signal: AbortSignal.timeout(10000) });
    if (!res.ok) {
      container.innerHTML = '<div style="padding:1rem;color:var(--text-muted)">Failed to load analytics.</div>';
      return;
    }
    const data = await res.json();

    if (!Array.isArray(data) || data.length === 0) {
      container.innerHTML = '<div style="padding:1rem;color:var(--text-muted)">No prompt analytics data yet. Run some tasks first.</div>';
      return;
    }

    // Sort by run_count descending
    data.sort((a, b) => b.run_count - a.run_count);

    let html = '<table class="pa-table">';
    html += '<thead><tr>';
    html += '<th>Prompt Hash</th>';
    html += '<th>Runs</th>';
    html += '<th>Success Rate</th>';
    html += '<th>Avg Cost</th>';
    html += '<th>Avg Duration</th>';
    html += '</tr></thead>';
    html += '<tbody>';

    for (const row of data) {
      const successPct = (row.success_rate * 100).toFixed(0);
      const successClass = row.success_rate >= 0.8 ? 'pa-good' : row.success_rate >= 0.5 ? 'pa-warn' : 'pa-bad';
      const cost = '$' + (row.avg_cost_usd || 0).toFixed(2);
      const dur = _fmtDuration(row.avg_duration_s || 0);

      // Visual bar for success rate
      const barWidth = Math.round(row.success_rate * 100);

      html += '<tr>';
      html += `<td class="pa-hash"><code class="copy-target" onclick="copyToClipboard('${_esc(row.prompt_hash)}')" title="Click to copy">${_esc(row.prompt_hash)}</code></td>`;
      html += `<td class="pa-runs">${row.run_count}</td>`;
      html += `<td class="pa-success ${successClass}">
        <div class="pa-bar-bg"><div class="pa-bar" style="width:${barWidth}%"></div></div>
        <span>${successPct}%</span>
      </td>`;
      html += `<td class="pa-cost">${cost}</td>`;
      html += `<td class="pa-duration">${dur}</td>`;
      html += '</tr>';
    }

    html += '</tbody></table>';
    container.innerHTML = html;
  } catch (err) {
    container.innerHTML = `<div style="padding:1rem;color:var(--text-muted)">Error: ${_esc(err.message)}</div>`;
  }
}

function _fmtDuration(seconds) {
  if (seconds < 60) return Math.round(seconds) + 's';
  if (seconds < 3600) return Math.round(seconds / 60) + 'm';
  return (seconds / 3600).toFixed(1) + 'h';
}

function _esc(text) {
  const d = document.createElement('div');
  d.textContent = String(text);
  return d.innerHTML.replace(/'/g, '&#39;').replace(/"/g, '&quot;');
}
