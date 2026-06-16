// Collapsible panel component + clipboard utility.

/** Render a collapsible card panel.
 *  @param {string} title
 *  @param {string} bodyHtml
 *  @param {boolean} [startOpen=true]
 */
export function collapsible(title, bodyHtml, startOpen = true) {
  const id = 'panel-' + Math.random().toString(36).slice(2);
  return `
    <div class="panel ${startOpen ? '' : 'collapsed'}" id="${id}">
      <div class="panel-header" onclick="togglePanel('${id}')">
        <span>${escHtml(title)}</span>
        <span class="panel-chevron">▼</span>
      </div>
      <div class="panel-body">${bodyHtml}</div>
    </div>
  `;
}

// Global toggle function used by onclick handlers
window.togglePanel = function(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('collapsed');
};

/** Render a code block with a copy button.
 *  @param {any} data  — if object, JSON-stringified
 *  @param {string} [lang='json']
 */
export function codeBlock(data, lang = 'json') {
  const text = (typeof data === 'string') ? data : JSON.stringify(data, null, 2);
  const id = 'cb-' + Math.random().toString(36).slice(2);
  return `
    <div class="copy-wrap">
      <pre class="code-block" id="${id}">${escHtml(text)}</pre>
      <button class="copy-btn" onclick="copyBlock('${id}')">copy</button>
    </div>
  `;
}

// Global copy function
window.copyBlock = function(id) {
  const el = document.getElementById(id);
  if (!el) return;
  navigator.clipboard.writeText(el.textContent).then(() => {
    const btn = el.parentElement.querySelector('.copy-btn');
    if (btn) { btn.textContent = '✓'; setTimeout(() => { btn.textContent = 'copy'; }, 1500); }
  }).catch(() => {
    // Fallback for non-secure contexts
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
    document.execCommand('copy');
    sel.removeAllRanges();
  });
};

/** HTML-escape a string. */
export function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** Return a status badge HTML string. */
export function badge(text, type = 'muted') {
  return `<span class="badge badge-${type}">${escHtml(String(text))}</span>`;
}

/** Map a status string to a badge type. */
export function statusBadge(status) {
  const map = {
    ok: 'success', success: 'success', executed: 'success', done: 'success',
    pending: 'info', approved: 'info',
    fallback: 'warning', warn: 'warning', schema_invalid: 'warning', empty_result: 'warning',
    error: 'danger', failed: 'danger', unavailable: 'danger',
    dismissed: 'muted', drafted: 'purple',
  };
  return badge(status, map[status] || 'muted');
}

/** Format an ISO timestamp to local date+time. */
export function fmtTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch { return iso; }
}

/** Format duration in ms. */
export function fmtMs(ms) {
  if (ms == null) return '—';
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Render an empty state placeholder. */
export function emptyState(msg = 'No data yet', icon = '◌') {
  return `<div class="empty-state"><div class="empty-icon">${icon}</div>${escHtml(msg)}</div>`;
}

/** Render a loading inline spinner. */
export function loadingHtml(msg = 'Loading…') {
  return `<div class="loading-full"><div class="loading-inline"><div class="spinner spinner-sm"></div> ${escHtml(msg)}</div></div>`;
}
