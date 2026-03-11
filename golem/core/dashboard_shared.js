/* ══════════════════════════════════════════════════════════════
   Shared JS utilities for both Flow and Golem dashboards.
   Loaded via <script src="/dashboard/shared.js">.
   ══════════════════════════════════════════════════════════════ */

/* ── DOM shortcuts ─────────────────────────────────────────── */
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

/* ── Formatting helpers ────────────────────────────────────── */
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

function fmtCost(c, decimals) {
  if (c == null) return '-';
  if (decimals === undefined) decimals = c >= 1 ? 2 : c >= 0.01 ? 2 : 4;
  return '$' + c.toFixed(decimals);
}

function fmtDuration(s) {
  if (!s && s !== 0) return '-';
  if (s < 60) return Math.round(s) + 's';
  return (s / 60).toFixed(1) + 'm';
}

/** Compute live elapsed seconds for a session.
 *  Uses duration_seconds if already set (completed), otherwise computes from created_at. */
function liveDuration(session) {
  if (session.duration_seconds) return session.duration_seconds;
  if (session.created_at) {
    const start = new Date(session.created_at).getTime();
    if (!isNaN(start)) return (Date.now() - start) / 1000;
  }
  return 0;
}

function fmtTime(iso) {
  if (!iso) return '-';
  try { return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' }); }
  catch (e) { return iso; }
}

function fmtAgo(iso) {
  if (!iso) return '';
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return Math.round(s) + 's ago';
  if (s < 3600) return Math.round(s / 60) + 'm ago';
  return Math.round(s / 3600) + 'h ago';
}

function fmtCountdown(iso) {
  if (!iso) return '';
  const remaining = (new Date(iso).getTime() - Date.now()) / 1000;
  if (remaining <= 0) return 'starting...';
  if (remaining < 60) return 'starts in ' + Math.round(remaining) + 's';
  return 'starts in ' + (remaining / 60).toFixed(1) + 'm';
}

function fmtUptime(s) {
  if (!s) return '';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? h + 'h ' + m + 'm' : m + 'm';
}

function truncText(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '\u2026' : s;
}

/** Display a task ID — show the full number for clarity. */
function shortId(id) {
  return String(id);
}

/* ── JSON formatting & highlighting ────────────────────────── */

/** Try to detect and pretty-print JSON; returns {formatted, isJson, isPartialJson} */
function tryFormatJson(text) {
  if (!text) return { formatted: text, isJson: false, isPartialJson: false };
  const t = text.trim();

  /* Direct JSON object or array */
  if ((t[0] === '{' || t[0] === '[') && (t[t.length - 1] === '}' || t[t.length - 1] === ']')) {
    try {
      const obj = JSON.parse(t);
      return { formatted: JSON.stringify(obj, null, 2), isJson: true, isPartialJson: false };
    } catch (e) { /* not valid JSON, maybe truncated */ }
  }

  /* Looks like JSON but truncated */
  if ((t[0] === '{' || t[0] === '[') && t.length > 50) {
    const quotes = (t.match(/"/g) || []).length;
    const colons = (t.match(/:/g) || []).length;
    if (quotes > 4 && colons > 2) {
      return { formatted: t, isJson: false, isPartialJson: true };
    }
  }

  /* Find JSON embedded in text */
  for (const prefix of ['{"', '[{', '[\n']) {
    const jsonStart = text.indexOf(prefix);
    if (jsonStart > 0) {
      const pre = text.slice(0, jsonStart).trim();
      const jsonPart = text.slice(jsonStart).trim();
      try {
        const obj = JSON.parse(jsonPart);
        return { formatted: pre + '\n\n' + JSON.stringify(obj, null, 2), isJson: true, isPartialJson: false };
      } catch (e) { /* not valid JSON */ }
    }
  }

  return { formatted: text, isJson: false, isPartialJson: false };
}

/** Syntax highlight already-escaped JSON string */
function highlightJson(escaped) {
  return escaped
    .replace(/(&quot;[^&]*?&quot;)\s*:/g, '<span class="json-key">$1</span>:')
    .replace(/:\s*(&quot;[^&]*?&quot;)/g, ': <span class="json-str">$1</span>')
    .replace(/:\s*(\d+\.?\d*)/g, ': <span class="json-num">$1</span>')
    .replace(/:\s*(true|false)/g, ': <span class="json-bool">$1</span>')
    .replace(/:\s*(null)/g, ': <span class="json-null">$1</span>');
}

/* ── Markdown renderer ─────────────────────────────────────── */

/** Strip ## Phase: markers — already rendered as phase headers in the timeline */
function cleanPhaseMarkers(text) {
  return text.replace(/^## Phase:\s*(UNDERSTAND|PLAN|BUILD|REVIEW|VERIFY)\s*$/gm, '').trim();
}

/** Convert markdown to styled HTML with fenced code block support */
function renderMarkdown(md) {
  if (!md) return '<div class="no-data">No content</div>';

  /* Extract fenced code blocks before HTML-escaping */
  const codeBlocks = [];
  let processed = md.replace(/```(\w*)\n([\s\S]*?)```/g, function(match, lang, code) {
    const idx = codeBlocks.length;
    codeBlocks.push({ lang: lang || '', code: code });
    return '___CODEBLOCK_' + idx + '___';
  });

  /* Extract ★ Insight callout blocks before escaping.
     Pattern: backtick-wrapped "★ Insight ───…" line, content, backtick-wrapped "───…" line */
  const insightBlocks = [];
  processed = processed.replace(
    /`★\s*Insight[^`]*`\s*\n([\s\S]*?)\n`─{10,}`/g,
    function(match, content) {
      const idx = insightBlocks.length;
      insightBlocks.push(content.trim());
      return '___INSIGHT_' + idx + '___';
    }
  );

  let html = esc(processed);

  /* Restore code blocks with formatting and syntax highlighting */
  html = html.replace(/___CODEBLOCK_(\d+)___/g, function(match, idx) {
    const block = codeBlocks[parseInt(idx)];
    const lang = block.lang.toLowerCase();
    const escaped = esc(block.code);
    const langTag = lang ? '<span class="lang-tag">' + lang + '</span>' : '';

    if (lang === 'json') {
      try {
        const obj = JSON.parse(block.code);
        const formatted = esc(JSON.stringify(obj, null, 2));
        return '<pre class="code-block json">' + langTag + '<code>' + highlightJson(formatted) + '</code></pre>';
      } catch(e) {
        return '<pre class="code-block json">' + langTag + '<code>' + highlightJson(escaped) + '</code></pre>';
      }
    }
    return '<pre class="code-block' + (lang ? ' lang-' + lang : '') + '">' + langTag + '<code>' + escaped + '</code></pre>';
  });

  /* Restore insight callout blocks */
  html = html.replace(/___INSIGHT_(\d+)___/g, function(match, idx) {
    const content = esc(insightBlocks[parseInt(idx)]);
    return '<aside class="insight-callout"><span class="insight-icon">★</span><div class="insight-body">' + content + '</div></aside>';
  });

  /* Standard markdown transforms */
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  html = html.replace(/^[*-] (.+)$/gm, '<li>$1</li>');
  html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');
  /* Paragraphs: skip lines already wrapped in HTML tags */
  html = html.replace(/^(?!<[hulo\s]|<pre|<code|<aside|<\/|$)(.+)$/gm, '<p>$1</p>');
  return '<div class="markdown-body">' + html + '</div>';
}

/* ── Theme Toggle ─────────────────────────────────────────── */
function setTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem('golem-theme', theme); } catch(e) {}
  const btn = document.getElementById('theme-toggle');
  if (btn) btn.textContent = theme === 'light' ? '\u2600' : '\u263D';
}

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'dark';
  setTheme(current === 'dark' ? 'light' : 'dark');
}

/* Restore saved theme on load */
(function() {
  try {
    const saved = localStorage.getItem('golem-theme');
    if (saved) setTheme(saved);
  } catch(e) {}
})();

/* ── New Trace Viewer Utils ───────────────────────────────── */

/** Format token count: 1200 → "1.2K", 1200000 → "1.2M" */
function fmtTokens(n) {
  if (!n) return '0';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

/** Format duration from ms: 5000 → "5s", 65000 → "1m 5s" */
function fmtDurationMs(ms) {
  if (!ms) return '0s';
  const s = Math.round(ms / 1000);
  if (s === 0) return '<1s';
  return fmtDuration(s);
}

/** Create DOM element with classes and attributes */
function el(tag, classes, attrs) {
  const e = document.createElement(tag);
  if (classes) e.className = classes;
  if (attrs) Object.entries(attrs).forEach(([k, v]) => {
    if (k === 'text') e.textContent = v;
    else if (k === 'html') e.innerHTML = v;
    else e.setAttribute(k, v);
  });
  return e;
}
