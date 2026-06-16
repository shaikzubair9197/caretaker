import { api } from '../api.js';
import { toast } from '../components/toast.js';
import { escHtml as esc, fmtTime } from '../components/panel.js';

export async function render(el) {
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">▦ Telemetry Viewer</h1>
      <div class="page-actions">
        <button class="btn btn-ghost btn-sm" id="tel-refresh">↻ Refresh</button>
      </div>
    </div>

    <!-- Summary row -->
    <div id="tel-stats" class="health-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px"></div>

    <div class="grid-2" style="gap:20px;align-items:start">

      <!-- Left: application chart -->
      <div class="card">
        <div class="card-header">Application Usage</div>
        <div class="card-body" id="tel-chart"></div>
      </div>

      <!-- Right: context summary -->
      <div class="card">
        <div class="card-header">Context Summary (last 1h)</div>
        <div class="card-body" id="tel-summary"></div>
      </div>

    </div>

    <!-- Sessions table -->
    <div class="card mt-16">
      <div class="card-header">
        Window Sessions
        <span id="tel-session-count" class="badge badge-info">0</span>
      </div>
      <div class="table-wrap" style="border:none;border-radius:0" id="tel-table">
        <div class="loading-full"><div class="spinner"></div></div>
      </div>
    </div>
  `;

  document.getElementById('tel-refresh').addEventListener('click', load);
  await load();

  async function load() {
    try {
      const [sessions, summary] = await Promise.all([
        api.get('/telemetry/'),
        api.get('/telemetry/summary'),
      ]);
      renderStats(sessions, summary);
      renderChart(summary);
      renderSummary(summary);
      renderTable(sessions);
    } catch (e) {
      toast.error('Telemetry load failed: ' + e.message);
    }
  }

  function renderStats(sessions, summary) {
    const open   = sessions.filter(s => !s.ended_at).length;
    const closed = sessions.filter(s =>  s.ended_at).length;
    const apps   = new Set(sessions.map(s => s.window_title)).size;
    document.getElementById('tel-stats').innerHTML = [
      { label: 'Focus Minutes',  val: summary.focus_minutes ?? '—',  cls: (summary.focus_minutes||0) >= 30 ? 'ok' : 'warn' },
      { label: 'Active Sessions', val: open,    cls: open > 0 ? 'warn' : 'ok' },
      { label: 'Closed Sessions', val: closed,  cls: 'info' },
      { label: 'Unique Apps',     val: apps,    cls: 'info' },
    ].map(c => `
      <div class="health-card ${c.cls}">
        <div class="health-card-label">${c.label}</div>
        <div class="health-card-value">${c.val}</div>
      </div>
    `).join('');
  }

  function renderChart(summary) {
    const apps = summary.applications || [];
    if (!apps.length) {
      document.getElementById('tel-chart').innerHTML = `<div class="empty-state">No application data</div>`;
      return;
    }
    const max = Math.max(...apps.map(a => a.seconds), 1);
    document.getElementById('tel-chart').innerHTML = `
      <div class="bar-chart">
        ${apps.slice(0, 15).map(a => `
          <div class="bar-row">
            <div class="bar-label" title="${esc(a.window)}">${esc(a.window)}</div>
            <div class="bar-track">
              <div class="bar-fill" style="width:${Math.max(2, Math.round((a.seconds/max)*100))}%"></div>
            </div>
            <div class="bar-val">${formatDuration(a.seconds)}</div>
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderSummary(summary) {
    document.getElementById('tel-summary').innerHTML = `
      <div class="kv-stack">
        <div class="kv">
          <span class="kv-key">Total focus</span>
          <span class="kv-val">${summary.focus_minutes ?? '—'} minutes</span>
        </div>
        <div class="kv">
          <span class="kv-key">Active applications</span>
          <span class="kv-val">${(summary.applications||[]).length}</span>
        </div>
        <div class="kv">
          <span class="kv-key">Top application</span>
          <span class="kv-val">${esc((summary.applications||[])[0]?.window || '—')}</span>
        </div>
        <div class="kv">
          <span class="kv-key">Top app duration</span>
          <span class="kv-val">${formatDuration((summary.applications||[])[0]?.seconds || 0)}</span>
        </div>
      </div>
      <hr class="divider" />
      <div class="section-title mb-8">All Applications</div>
      <div style="max-height:220px;overflow-y:auto">
        ${(summary.applications||[]).map((a, i) => `
          <div class="row mb-8" style="gap:8px">
            <span class="badge badge-muted">${i + 1}</span>
            <span style="flex:1;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(a.window)}</span>
            <span class="text-muted fs-12">${formatDuration(a.seconds)}</span>
          </div>
        `).join('') || '<div class="text-muted fs-12">—</div>'}
      </div>
    `;
  }

  function renderTable(sessions) {
    document.getElementById('tel-session-count').textContent = sessions.length;
    if (!sessions.length) {
      document.getElementById('tel-table').innerHTML = `<div class="empty-state">No window sessions recorded</div>`;
      return;
    }
    document.getElementById('tel-table').innerHTML = `
      <table>
        <thead>
          <tr><th>#</th><th>Window Title</th><th>Started</th><th>Ended</th><th>Duration</th><th>State</th></tr>
        </thead>
        <tbody>
          ${sessions.slice(0, 100).map(s => `
            <tr>
              <td class="mono text-muted">${s.id}</td>
              <td class="truncate" style="max-width:260px" title="${esc(s.window_title)}">${esc(s.window_title)}</td>
              <td class="text-muted fs-12">${fmtTime(s.started_at)}</td>
              <td class="text-muted fs-12">${s.ended_at ? fmtTime(s.ended_at) : '<span class="text-warning">active</span>'}</td>
              <td class="mono text-muted">${s.duration_seconds != null ? formatDuration(s.duration_seconds) : '—'}</td>
              <td>${s.ended_at
                  ? '<span class="badge badge-muted">closed</span>'
                  : '<span class="badge badge-warning">open</span>'}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }
}

function formatDuration(seconds) {
  if (!seconds) return '0s';
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds/60)}m ${seconds%60}s`;
  return `${Math.floor(seconds/3600)}h ${Math.floor((seconds%3600)/60)}m`;
}
