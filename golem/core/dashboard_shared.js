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

/** Shorten a numeric task ID for display (e.g. 1772720007620 → "…7620"). */
function shortId(id) {
  const s = String(id);
  return s.length > 6 ? '\u2026' + s.slice(-4) : s;
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
  html = html.replace(/^(?!<[hulo\s]|<pre|<code|<\/|$)(.+)$/gm, '<p>$1</p>');
  return '<div class="markdown-body">' + html + '</div>';
}
