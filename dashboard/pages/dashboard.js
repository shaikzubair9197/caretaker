import { api, ApiError } from '../api.js';
import { toast } from '../components/toast.js';
import { fmtTime } from '../components/panel.js';

export async function render(el) {
  // Register CRO-style in-app popup triggers (meeting alert, pending actions, health warning)
  const pm = window._caretakerPopups;
  if (pm) registerDashboardPopups(pm);
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">System Dashboard</h1>
      <div class="page-actions">
        <span id="dash-ts" class="text-muted fs-12"></span>
        <button class="btn btn-ghost btn-sm" id="dash-refresh">↻ Refresh</button>
      </div>
    </div>
    <div id="health-grid" class="health-grid">${skeletonCards(8)}</div>
    <div id="ollama-diag-panel" style="margin-top:8px;display:none"></div>
    <div class="grid-2" style="margin-top:8px;">
      <div id="recent-panel"></div>
      <div id="llm-panel"></div>
    </div>
  `;

  document.getElementById('dash-refresh').addEventListener('click', load);
  await load();

  // Auto-refresh every 30 s
  const timer = setInterval(load, 30_000);
  return () => clearInterval(timer);

  async function load() {
    try {
      const [health, tasks, llm] = await Promise.all([
        api.get('/health/detailed'),
        api.get('/tasks/'),
        api.get('/llm/audit?limit=5'),
      ]);
      renderCards(health, tasks);
      renderOllamaDiag(health);
      renderRecent(tasks);
      renderLlm(llm);
      document.getElementById('dash-ts').textContent = 'Updated ' + new Date().toLocaleTimeString();
    } catch (e) {
      toast.error('Dashboard load failed: ' + e.message);
    }
  }

  function renderCards(h, tasks) {
    const pending = tasks.filter(t => t.status === 'pending').length;
    const cards = [
      { label: 'API Status',         value: h.api === 'ok' ? '●' : '✕',
        sub: h.api,  cls: h.api === 'ok' ? 'ok' : 'error',   page: null },
      { label: 'Database',            value: h.database === 'ok' ? '●' : '✕',
        sub: h.database, cls: h.database === 'ok' ? 'ok' : 'error', page: null },
      { label: 'Ollama LLM',          value: h.ollama === 'ok' ? '●' : '✕',
        sub: h.ollama, cls: h.ollama === 'ok' ? 'ok' : 'error', page: null },
      { label: 'Pending Tasks',       value: pending,
        sub: `of ${tasks.length} total`, cls: pending > 0 ? 'info' : 'ok', page: '#tasks' },
      { label: 'Memories',            value: h.counts.memories ?? '—',
        sub: 'stored', cls: 'info', page: '#brain' },
      { label: 'Commitments',         value: h.counts.commitments ?? '—',
        sub: 'total', cls: 'info', page: '#commitments' },
      { label: 'Pending Actions',     value: h.counts.pending_actions ?? '—',
        sub: 'awaiting approval', cls: (h.counts.pending_actions > 0) ? 'warn' : 'ok', page: '#agent' },
      { label: 'LLM Calls (24h)',     value: h.counts.llm_calls_24h ?? '—',
        sub: `total: ${h.counts.llm_calls ?? 0}`, cls: 'info', page: '#llm-audit' },
    ];

    document.getElementById('health-grid').innerHTML = cards.map(c => `
      <div class="health-card ${c.cls}" ${c.page ? `onclick="location.hash='${c.page}'" style="cursor:pointer"` : ''}>
        <div class="health-card-label">${c.label}</div>
        <div class="health-card-value" style="${c.label.endsWith('Status') || c.label === 'Ollama LLM' || c.label === 'Database' ? 'font-size:36px' : ''}">${c.value}</div>
        <div class="health-card-sub">${c.sub}</div>
      </div>
    `).join('');

    // Extra cards for timestamps + LLM success tracking
    const fallbackCls = (h.fallback_activations_today || 0) > 0 ? 'warn' : 'ok';
    const extra = document.createElement('div');
    extra.style.cssText = 'grid-column: 1 / -1; display: grid; grid-template-columns: repeat(4,1fr); gap: 12px;';
    extra.innerHTML = `
      <div class="health-card info">
        <div class="health-card-label">Last Panic Dump</div>
        <div class="health-card-value" style="font-size:14px;font-weight:500">${fmtTime(h.last_panic_dump) || '—'}</div>
        <div class="health-card-sub">${h.counts.source_items ?? 0} source items total</div>
      </div>
      <div class="health-card info">
        <div class="health-card-label">Last Telemetry</div>
        <div class="health-card-value" style="font-size:14px;font-weight:500">${fmtTime(h.last_telemetry) || '—'}</div>
        <div class="health-card-sub">window tracking</div>
      </div>
      <div class="health-card info">
        <div class="health-card-label">Last Successful LLM</div>
        <div class="health-card-value" style="font-size:14px;font-weight:500">${fmtTime(h.last_successful_llm_call) || '—'}</div>
        <div class="health-card-sub">last SUCCESS status</div>
      </div>
      <div class="health-card ${fallbackCls}">
        <div class="health-card-label">Fallbacks Today</div>
        <div class="health-card-value" style="font-size:26px">${h.fallback_activations_today ?? 0}</div>
        <div class="health-card-sub">non-SUCCESS LLM calls</div>
      </div>
    `;
    const grid = document.getElementById('health-grid');
    // remove previous extra if any
    grid.querySelectorAll('[data-extra]').forEach(e => e.remove());
    extra.dataset.extra = '1';
    grid.appendChild(extra);
  }

  function renderOllamaDiag(h) {
    const panel = document.getElementById('ollama-diag-panel');
    const d = h.ollama_diagnostics;
    if (!d) { panel.style.display = 'none'; return; }

    // Only show the expanded diagnostics panel when Ollama has a problem
    const hasIssue = !d.model_available;
    panel.style.display = hasIssue ? '' : 'none';
    if (!hasIssue) return;

    const modelBadge = d.model_available
      ? `<span class="badge badge-success">✓ ${esc(d.configured_model)}</span>`
      : `<span class="badge badge-danger">✕ ${esc(d.configured_model)} not found</span>`;

    const installed = (d.installed_models || []).length
      ? d.installed_models.map(m => `<span class="badge badge-muted">${esc(m)}</span>`).join(' ')
      : '<span class="text-muted">none installed</span>';

    panel.innerHTML = `
      <div class="card" style="border-color:var(--danger);margin-bottom:8px">
        <div class="card-header" style="color:var(--danger)">⚠ Ollama Diagnostics — action required</div>
        <div class="card-body">
          <div class="grid-2" style="gap:16px">
            <div class="kv-stack">
              <div class="kv"><span class="kv-key">Service reachable</span>
                <span class="kv-val">${d.reachable
                  ? '<span class="text-success">Yes</span>'
                  : '<span class="text-danger">No — run: ollama serve</span>'}</span></div>
              <div class="kv"><span class="kv-key">Configured model</span><span class="kv-val">${modelBadge}</span></div>
              <div class="kv"><span class="kv-key">Installed models</span><span class="kv-val">${installed}</span></div>
              <div class="kv"><span class="kv-key">Preflight latency</span>
                <span class="kv-val mono">${d.preflight_ms != null ? d.preflight_ms + 'ms' : '—'}</span></div>
              ${d.error ? `<div class="kv"><span class="kv-key">Error</span>
                <span class="kv-val text-danger mono">${esc(d.error)}</span></div>` : ''}
            </div>
            <div>
              <div style="font-size:12px;color:var(--text-secondary);margin-bottom:6px">Suggested fix:</div>
              ${!d.reachable
                ? `<code style="display:block;padding:8px;background:var(--bg-elevated);border-radius:4px;font-size:13px">ollama serve</code>`
                : `<code style="display:block;padding:8px;background:var(--bg-elevated);border-radius:4px;font-size:13px">ollama pull ${esc(d.configured_model)}</code>`
              }
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function renderRecent(tasks) {
    const recent = tasks.slice(0, 6);
    const rows = recent.length ? recent.map(t => `
      <tr>
        <td class="mono text-muted">#${t.id}</td>
        <td class="truncate" style="max-width:220px">${esc(t.description)}</td>
        <td>${priorityBadge(t.priority)}</td>
        <td>${statusBadge(t.status)}</td>
      </tr>
    `).join('') : `<tr><td colspan="4" class="empty-state">No tasks yet</td></tr>`;

    document.getElementById('recent-panel').innerHTML = `
      <div class="card">
        <div class="card-header">Recent Tasks</div>
        <div class="table-wrap" style="border:none;border-radius:0">
          <table><thead><tr><th>#</th><th>Description</th><th>Priority</th><th>Status</th></tr></thead>
          <tbody>${rows}</tbody></table>
        </div>
        <div class="card-footer"><a href="#tasks" style="color:var(--accent)">View all →</a></div>
      </div>
    `;
  }

  function renderLlm(logs) {
    const rows = logs.length ? logs.map(l => `
      <tr>
        <td class="mono text-muted fs-11">${fmtTime(l.created_at)}</td>
        <td>${esc(l.call_type)}</td>
        <td>${llmStatusBadge(l.status)}</td>
        <td class="mono text-muted">${l.duration_ms != null ? l.duration_ms + 'ms' : '—'}</td>
      </tr>
    `).join('') : `<tr><td colspan="4" class="empty-state">No LLM calls yet</td></tr>`;

    document.getElementById('llm-panel').innerHTML = `
      <div class="card">
        <div class="card-header">Recent LLM Calls</div>
        <div class="table-wrap" style="border:none;border-radius:0">
          <table><thead><tr><th>Time</th><th>Type</th><th>Status</th><th>Duration</th></tr></thead>
          <tbody>${rows}</tbody></table>
        </div>
        <div class="card-footer"><a href="#llm-audit" style="color:var(--accent)">View audit log →</a></div>
      </div>
    `;
  }
}

function skeletonCards(n) {
  return Array(n).fill(0).map(() => `
    <div class="health-card info" style="opacity:.4">
      <div class="health-card-label" style="background:var(--bg-elevated);height:10px;border-radius:3px;width:60%"></div>
      <div class="health-card-value" style="background:var(--bg-elevated);height:32px;border-radius:3px;width:40%;font-size:0"></div>
      <div class="health-card-sub" style="background:var(--bg-elevated);height:10px;border-radius:3px;width:70%"></div>
    </div>
  `).join('');
}

// ── Dashboard popup triggers ─────────────────────────────────────────────────

/**
 * Registers three CRO-optimized popup triggers:
 *
 *  1. Meeting alert banner   — polls /meeting/prep/next every 60 s;
 *                              shows when a meeting is ≤ 20 min away.
 *  2. Pending actions slide-in — delayed 30 s then polls /agent/actions/pending
 *                              every 60 s; shows when count > 0.
 *  3. Health warning banner  — immediate check on /health/detailed;
 *                              shows when Ollama or DB is not ok.
 */
function registerDashboardPopups(pm) {

  // 1. Meeting alert ─────────────────────────────────────────────────────────
  pm.interval(60_000, async () => {
    try {
      const data = await api.get('/meeting/prep/next?within_minutes=20');
      const ev = data?.event;
      if (!ev) return;
      const mins = ev.minutes_until ?? '?';
      const title = ev.title || 'Upcoming meeting';
      const organizer = ev.organizer?.name ? ` · ${ev.organizer.name}` : '';
      pm.show({
        id: `meeting-${ev.external_id || 'next'}`,
        type: 'banner',
        variant: 'accent',
        title: `Meeting in ${mins} min`,
        body: `${title}${organizer}`,
        cooldownMs: 15 * 60 * 1000, // 15 min — re-alert if still active
        actions: [
          ev.join_url ? {
            label: 'Join',
            href: ev.join_url,
            primary: true,
            dismissOnClick: true,
          } : null,
          {
            label: 'Prep',
            action: () => { location.hash = '#meeting'; },
            dismissOnClick: true,
          },
        ].filter(Boolean),
      });
    } catch { /* silent — /meeting/prep/next may 404 if no upcoming meeting */ }
  });

  // 2. Pending agent actions ─────────────────────────────────────────────────
  pm.delay(30_000, () => {
    pm.interval(60_000, async () => {
      try {
        const data = await api.get('/agent/actions/pending');
        const count = data?.count ?? 0;
        if (count === 0) return;
        pm.show({
          id: 'pending-actions',
          type: 'slide-in',
          variant: 'warning',
          title: `${count} action${count === 1 ? '' : 's'} need your review`,
          body: 'Agent proposed changes awaiting approval.',
          cooldownMs: 60 * 60 * 1000,
          actions: [{
            label: 'Review Now',
            primary: true,
            action: () => { location.hash = '#agent'; },
            dismissOnClick: true,
          }],
        });
      } catch { /* silent */ }
    });
  });

  // 3. System health warning ─────────────────────────────────────────────────
  pm.delay(2_000, async () => {
    try {
      const h = await api.get('/health/detailed');
      const down = [];
      if (h.ollama !== 'ok') down.push('Ollama LLM');
      if (h.database !== 'ok') down.push('Database');
      if (!down.length) return;
      pm.show({
        id: 'health-warning',
        type: 'banner',
        variant: 'danger',
        title: 'Service issue detected',
        body: `${down.join(' and ')} unreachable — some features may be unavailable.`,
        cooldownMs: 30 * 60 * 1000,
        actions: [{
          label: 'Details',
          action: () => {
            document.getElementById('dash-refresh')?.click();
          },
          dismissOnClick: false,
        }],
      });
    } catch { /* silent */ }
  });
}

function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function statusBadge(s) {
  const m = { pending:'info', done:'success', executed:'success', dismissed:'muted', failed:'danger' };
  return `<span class="badge badge-${m[s]||'muted'}">${s}</span>`;
}
function priorityBadge(p) {
  const m = { high:'danger', medium:'warning', low:'muted' };
  return `<span class="badge badge-${m[p]||'muted'}">${p}</span>`;
}
function llmStatusBadge(s) {
  const m = {
    SUCCESS:'success',
    CONNECTION_REFUSED:'danger', DNS_FAILURE:'danger', TIMEOUT:'warning',
    MODEL_NOT_FOUND:'danger', HTTP_ERROR:'danger',
    JSON_PARSE_ERROR:'danger', SCHEMA_VALIDATION_FAILURE:'warning',
    UNKNOWN_ERROR:'danger', NOT_CALLED:'muted',
    // legacy values kept for backwards compat with existing rows
    success:'success', fallback:'warning', error:'danger', validation_failed:'warning', unavailable:'danger',
  };
  return `<span class="badge badge-${m[s]||'muted'}">${s}</span>`;
}
