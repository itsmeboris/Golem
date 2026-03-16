/* golem/core/heartbeat_widget.js — Heartbeat chip + popover in top bar.
 * Depends on: shared.js (fmtCost, fmtAgo, esc).
 * Fetches /api/heartbeat and renders a status chip; click to expand details.
 */
'use strict';

let _hbData = null;
let _hbPopoverOpen = false;

const _HB_STATES = {
  idle:             { color: 'purple', label: 'idle' },
  scanning:         { color: 'blue',   label: 'scanning', pulse: true },
  submitted:        { color: 'green',  label: 'submitted' },
  paused:           { color: 'yellow', label: 'paused' },
  budget_exhausted: { color: 'red',    label: 'budget' },
};

async function fetchHeartbeat() {
  try {
    const res = await fetch('/api/heartbeat');
    if (!res.ok) return null;
    return await res.json();
  } catch (_e) {
    return null;
  }
}

function _hbBudgetPct(data) {
  if (!data || !data.daily_budget_usd) return 0;
  return Math.min(100, Math.round((data.daily_spend_usd / data.daily_budget_usd) * 100));
}

function _hbNextTick(data) {
  if (!data || !data.next_tick_seconds) return '';
  const s = data.next_tick_seconds;
  if (s <= 0) return 'now';
  if (s < 60) return s + 's';
  return Math.round(s / 60) + 'm';
}

function _hbChipHTML(data) {
  if (!data || !data.enabled) {
    return '<span class="hb-chip hb-disabled" id="hb-chip">'
      + '<span class="hb-dot hb-dot-gray"></span>'
      + '<span class="hb-label">off</span>'
      + '</span>';
  }

  const info = _HB_STATES[data.state] || _HB_STATES.idle;
  const pct = _hbBudgetPct(data);
  const pulseClass = info.pulse ? ' hb-pulse' : '';

  return '<span class="hb-chip hb-' + info.color + '" id="hb-chip">'
    + '<span class="hb-dot hb-dot-' + info.color + pulseClass + '"></span>'
    + '<span class="hb-label">' + esc(info.label) + '</span>'
    + '<span class="hb-pct">' + pct + '%</span>'
    + '<span class="hb-caret">&#9660;</span>'
    + '</span>';
}

function _hbPopoverHTML(data) {
  if (!data || !data.enabled) return '';

  const info = _HB_STATES[data.state] || _HB_STATES.idle;
  const pct = _hbBudgetPct(data);
  const inflight = data.inflight_task_ids ? data.inflight_task_ids.length : 0;
  const tierLabel = data.last_scan_tier ? 'Tier ' + data.last_scan_tier : '';
  const scanAgo = data.last_scan_at ? fmtAgo(data.last_scan_at) : 'never';
  const nextTick = _hbNextTick(data);

  return '<div class="hb-popover" id="hb-popover">'
    + '<div class="hb-pop-header">'
    +   '<span class="hb-pop-title">Heartbeat</span>'
    +   '<span class="hb-pop-badge hb-pop-badge-' + info.color + '">' + esc(info.label) + '</span>'
    + '</div>'
    + '<div class="hb-pop-metrics">'
    +   '<div class="hb-pop-metric">'
    +     '<div class="hb-pop-metric-label">Budget</div>'
    +     '<div class="hb-pop-metric-value">' + fmtCost(data.daily_spend_usd, 2)
    +       '<span class="hb-pop-metric-dim"> / ' + fmtCost(data.daily_budget_usd, 2) + '</span></div>'
    +   '</div>'
    +   '<div class="hb-pop-metric">'
    +     '<div class="hb-pop-metric-label">Inflight</div>'
    +     '<div class="hb-pop-metric-value">' + inflight + '<span class="hb-pop-metric-dim"> / 1</span></div>'
    +   '</div>'
    + '</div>'
    + '<div class="hb-pop-bar-track"><div class="hb-pop-bar-fill hb-pop-bar-' + info.color
    +   '" style="width:' + pct + '%"></div></div>'
    + '<div class="hb-pop-footer">'
    +   '<span>Last scan: ' + esc(scanAgo) + (tierLabel ? ' (' + tierLabel + ')' : '') + '</span>'
    +   '<span>' + (data.candidate_count || 0) + ' candidates</span>'
    + '</div>'
    + (nextTick
      ? '<div class="hb-pop-next">Next tick in ' + nextTick + '</div>'
      : '')
    + '<button class="hb-pop-trigger" id="hb-trigger-btn">Trigger Now</button>'
    + '</div>';
}

async function _hbTrigger() {
  try {
    await fetch('/api/heartbeat/trigger', { method: 'POST' });
    // Refresh immediately after trigger
    await updateHeartbeat();
  } catch (_e) { /* ignore */ }
}

function renderHeartbeatChip(data) {
  _hbData = data;
  const container = document.getElementById('hb-container');
  if (!container) return;

  // Preserve popover state across re-renders
  const wasOpen = _hbPopoverOpen;
  container.innerHTML = _hbChipHTML(data) + (wasOpen ? _hbPopoverHTML(data) : '');

  const chip = document.getElementById('hb-chip');
  if (chip) {
    chip.addEventListener('click', function (e) {
      e.stopPropagation();
      _hbPopoverOpen = !_hbPopoverOpen;
      renderHeartbeatChip(_hbData);
    });
  }

  const triggerBtn = document.getElementById('hb-trigger-btn');
  if (triggerBtn) {
    triggerBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      _hbTrigger();
    });
  }
}

async function updateHeartbeat() {
  const data = await fetchHeartbeat();
  if (data) renderHeartbeatChip(data);
}

// Close popover on outside click
document.addEventListener('click', function (e) {
  if (_hbPopoverOpen && !e.target.closest('#hb-container')) {
    _hbPopoverOpen = false;
    renderHeartbeatChip(_hbData);
  }
});
