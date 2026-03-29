/* golem/core/heartbeat_widget.js — Heartbeat chip + popover.
 * Circular countdown ring, condition indicators, heartbeat-rhythm animation.
 * Depends on: shared.js (fmtCost, fmtAgo, esc).
 */
'use strict';

let _hbData = null;
let _hbPopoverOpen = false;

// Client-side countdown
let _hbAnchorTime = 0;
let _hbAnchorSeconds = 0;
let _hbTotalInterval = 300;
let _hbEnabled = false;
let _hbTickTimer = null;

const _HB_STATES = {
  idle:             { color: 'purple', label: 'waiting',   desc: 'Accumulating idle time' },
  scanning:         { color: 'blue',   label: 'scanning',  desc: 'Scanning for work' },
  submitted:        { color: 'green',  label: 'working',   desc: 'Task submitted' },
  paused:           { color: 'yellow', label: 'paused',    desc: 'External tasks active' },
  budget_exhausted: { color: 'red',    label: 'no budget',  desc: 'Daily budget reached' },
};

/* ── Data ───────────────────────────────────────────────── */

async function fetchHeartbeat() {
  try {
    const res = await fetch('/api/heartbeat', { signal: AbortSignal.timeout(10000) });
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

function _hbRemaining() {
  return Math.max(0, _hbAnchorSeconds - (Date.now() - _hbAnchorTime) / 1000);
}

function _fmtShort(seconds) {
  var s = Math.round(seconds);
  if (s <= 0) return 'now';
  if (s < 60) return s + 's';
  return Math.round(s / 60) + 'm';
}

function _fmtClock(seconds) {
  var s = Math.round(seconds);
  if (s <= 0) return '0:00';
  var m = Math.floor(s / 60);
  var sec = s % 60;
  return m + ':' + (sec < 10 ? '0' : '') + sec;
}

/* ── SVG Ring ───────────────────────────────────────────── */

function _ringsvg(size, r, sw, remaining, total, color) {
  var circ = 2 * Math.PI * r;
  var pct = total > 0 ? remaining / total : 0;
  var offset = circ * (1 - pct);
  var h = size / 2;
  return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '">'
    + '<circle cx="' + h + '" cy="' + h + '" r="' + r + '" fill="none" '
    + 'stroke="var(--border)" stroke-width="' + sw + '" opacity="0.4"/>'
    + '<circle class="hb-ring-arc" cx="' + h + '" cy="' + h + '" r="' + r + '" fill="none" '
    + 'stroke="var(--' + color + ')" stroke-width="' + sw + '" '
    + 'stroke-dasharray="' + circ.toFixed(2) + '" '
    + 'stroke-dashoffset="' + offset.toFixed(2) + '" '
    + 'stroke-linecap="round" '
    + 'transform="rotate(-90 ' + h + ' ' + h + ')"/>'
    + '</svg>';
}

/* ── Conditions ─────────────────────────────────────────── */

function _hbConditions(data) {
  var conds = [];
  var st = data.state;

  // Budget
  var budgetOk = data.daily_spend_usd < data.daily_budget_usd;
  conds.push({
    s: budgetOk ? 'ok' : 'fail',
    l: 'Budget',
    v: fmtCost(data.daily_spend_usd, 2) + ' / ' + fmtCost(data.daily_budget_usd, 2)
  });

  // Idle status
  if (st === 'paused') {
    conds.push({ s: 'fail', l: 'Idle', v: 'External tasks running' });
  } else if (st === 'idle') {
    conds.push({ s: 'wait', l: 'Idle', v: 'Accumulating idle time' });
  } else if (st === 'scanning' || st === 'submitted') {
    conds.push({ s: 'ok', l: 'Idle', v: 'System idle' });
  } else if (st === 'budget_exhausted') {
    conds.push({ s: 'wait', l: 'Idle', v: '\u2014' });
  }

  // Inflight slots
  var n = data.inflight_task_ids ? data.inflight_task_ids.length : 0;
  conds.push({
    s: n > 0 ? 'wait' : 'ok',
    l: 'Slots',
    v: n > 0 ? n + ' inflight' : 'Available'
  });

  return conds;
}

function _condIcon(status) {
  if (status === 'ok')   return '<span class="hb-ci hb-ci-ok">\u2713</span>';
  if (status === 'fail') return '<span class="hb-ci hb-ci-fail">\u2717</span>';
  return '<span class="hb-ci hb-ci-wait">\u25CB</span>';
}

/* ── Chip HTML ──────────────────────────────────────────── */

function _hbChipHTML(data) {
  if (!data || !data.enabled) {
    return '<span class="hb-chip hb-disabled" id="hb-chip">'
      + '<span class="hb-dot hb-dot-gray"></span>'
      + '<span class="hb-label">off</span>'
      + '</span>';
  }

  var info = _HB_STATES[data.state] || _HB_STATES.idle;
  var rem = _hbRemaining();
  var dotAnim = data.state === 'scanning' ? ' hb-scan'
    : (data.state !== 'paused' && data.state !== 'budget_exhausted') ? ' hb-beat' : '';
  var budgetStr = data.daily_budget_usd
    ? fmtCost(data.daily_spend_usd, 2) + '/' + fmtCost(data.daily_budget_usd, 2) : '';

  return '<span class="hb-chip hb-' + info.color + '" id="hb-chip">'
    + '<span class="hb-dot hb-dot-' + info.color + dotAnim + '"></span>'
    + '<span class="hb-label">' + esc(info.label) + '</span>'
    + '<span class="hb-chip-ring">' + _ringsvg(18, 7, 2, rem, _hbTotalInterval, info.color) + '</span>'
    + '<span class="hb-chip-time">' + _fmtShort(rem) + '</span>'
    + (budgetStr ? '<span class="hb-sep">\u00b7</span><span class="hb-pct">' + budgetStr + '</span>' : '')
    + '<span class="hb-caret">\u25be</span>'
    + '</span>';
}

/* ── Popover HTML ───────────────────────────────────────── */

function _hbPopoverHTML(data) {
  if (!data || !data.enabled) return '';

  var info = _HB_STATES[data.state] || _HB_STATES.idle;
  var rem = _hbRemaining();
  var pct = _hbBudgetPct(data);
  var scanAgo = data.last_scan_at ? fmtAgo(data.last_scan_at) : 'never';
  var tierLabel = data.last_scan_tier ? 'Tier ' + data.last_scan_tier : '';

  // Conditions
  var conds = _hbConditions(data);
  var condHTML = '';
  for (var i = 0; i < conds.length; i++) {
    var c = conds[i];
    condHTML += '<div class="hb-cond hb-cond-' + c.s + '">'
      + _condIcon(c.s)
      + '<span class="hb-cond-label">' + esc(c.l) + '</span>'
      + '<span class="hb-cond-val">' + esc(c.v) + '</span>'
      + '</div>';
  }

  return '<div class="hb-popover" id="hb-popover">'
    // Header
    + '<div class="hb-pop-hd">'
    + '<span class="hb-pop-title">Heartbeat</span>'
    + '<span class="hb-pop-badge hb-pop-badge-' + info.color + '">' + esc(info.label) + '</span>'
    + '</div>'
    // Ring hero
    + '<div class="hb-pop-hero">'
    + '<div class="hb-pop-ring">'
    + _ringsvg(80, 34, 3.5, rem, _hbTotalInterval, info.color)
    + '<div class="hb-pop-ring-inner">'
    + '<span class="hb-pop-ring-time" id="hb-ring-time">' + _fmtClock(rem) + '</span>'
    + '<span class="hb-pop-ring-sub">next check</span>'
    + '</div>'
    + '</div>'
    + '<div class="hb-pop-desc hb-c-' + info.color + '">' + esc(info.desc) + '</div>'
    + '</div>'
    // Conditions
    + '<div class="hb-pop-conds">' + condHTML + '</div>'
    // Budget bar
    + '<div class="hb-pop-bar"><div class="hb-pop-bar-fill hb-pop-bar-' + info.color
    + '" style="width:' + pct + '%"></div></div>'
    // Footer
    + '<div class="hb-pop-ft">'
    + '<span>Scan: ' + esc(scanAgo) + (tierLabel ? ' \u00b7 ' + tierLabel : '') + '</span>'
    + '<span>' + (data.candidate_count || 0) + ' candidates \u00b7 '
    + (data.dedup_entry_count || 0) + ' deduped</span>'
    + '</div>'
    // Trigger
    + '<button class="hb-pop-trigger" id="hb-trigger-btn">Trigger Now</button>'
    + '</div>';
}

/* ── Trigger ────────────────────────────────────────────── */

async function _hbTrigger() {
  if (!confirm('Trigger heartbeat scan now?')) return;
  try {
    await fetch('/api/heartbeat/trigger', { method: 'POST', signal: AbortSignal.timeout(30000) });
    await updateHeartbeat();
  } catch (_e) { /* ignore */ }
}

/* ── Render ─────────────────────────────────────────────── */

function renderHeartbeatChip(data) {
  _hbData = data;
  var container = document.getElementById('hb-container');
  if (!container) return;

  var wasOpen = _hbPopoverOpen;
  container.innerHTML = _hbChipHTML(data) + (wasOpen ? _hbPopoverHTML(data) : '');

  var chip = document.getElementById('hb-chip');
  if (chip) {
    chip.addEventListener('click', function (e) {
      e.stopPropagation();
      _hbPopoverOpen = !_hbPopoverOpen;
      renderHeartbeatChip(_hbData);
    });
  }

  var triggerBtn = document.getElementById('hb-trigger-btn');
  if (triggerBtn) {
    triggerBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      _hbTrigger();
    });
  }
}

/* ── Countdown Tick ─────────────────────────────────────── */

function _updateRingArc(selector, r, remaining, total) {
  var arc = document.querySelector(selector);
  if (!arc) return;
  var circ = 2 * Math.PI * r;
  var pct = total > 0 ? remaining / total : 0;
  arc.setAttribute('stroke-dashoffset', (circ * (1 - pct)).toFixed(2));
}

function _hbCountdownTick() {
  if (!_hbEnabled) return;
  var rem = _hbRemaining();

  // Chip
  var tickEl = document.querySelector('.hb-chip-time');
  if (tickEl) tickEl.textContent = _fmtShort(rem);
  _updateRingArc('.hb-chip-ring .hb-ring-arc', 7, rem, _hbTotalInterval);

  // Popover
  var ringTime = document.getElementById('hb-ring-time');
  if (ringTime) ringTime.textContent = _fmtClock(rem);
  _updateRingArc('.hb-pop-ring .hb-ring-arc', 34, rem, _hbTotalInterval);

  if (rem <= 0) updateHeartbeat();
}

/* ── Public ─────────────────────────────────────────────── */

async function updateHeartbeat() {
  var data = await fetchHeartbeat();
  if (data) {
    _hbEnabled = !!data.enabled;
    _hbTotalInterval = Math.max(_hbTotalInterval, data.next_tick_seconds || 0);
    _hbAnchorSeconds = data.next_tick_seconds || 0;
    _hbAnchorTime = Date.now();
    renderHeartbeatChip(data);
  }
  if (!_hbTickTimer) {
    _hbTickTimer = setInterval(_hbCountdownTick, 1000);
  }
}

// Close popover on outside click
document.addEventListener('click', function (e) {
  if (_hbPopoverOpen && !e.target.closest('#hb-container')) {
    _hbPopoverOpen = false;
    renderHeartbeatChip(_hbData);
  }
});
