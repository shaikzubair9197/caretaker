import { api } from '../api.js';
import { toast } from '../components/toast.js';
import { escHtml as esc, fmtTime } from '../components/panel.js';

export async function render(el) {
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">◉ Commitment Explorer</h1>
      <div class="page-actions">
        <select id="cm-filter" class="select" style="width:150px">
          <option value="">All statuses</option>
          <option value="pending">pending</option>
          <option value="done">done</option>
          <option value="drafted">drafted</option>
          <option value="dismissed">dismissed</option>
        </select>
        <button class="btn btn-ghost btn-sm" id="cm-refresh">↻ Refresh</button>
      </div>
    </div>

    <div id="cm-stats" class="health-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px"></div>

    <div class="card">
      <div class="table-wrap" style="border:none;border-radius:0" id="cm-wrap">
        <div class="loading-full"><div class="spinner"></div></div>
      </div>
    </div>
  `;

  let all = [];
  document.getElementById('cm-refresh').addEventListener('click', load);
  document.getElementById('cm-filter').addEventListener('change', applyFilter);
  await load();

  async function load() {
    try {
      all = await api.get('/commitments/');
      renderStats();
      applyFilter();
    } catch (e) {
      toast.error('Failed to load commitments: ' + e.message);
    }
  }

  function renderStats() {
    const counts = { pending: 0, done: 0, drafted: 0, dismissed: 0 };
    all.forEach(c => { if (counts[c.status] !== undefined) counts[c.status]++; });
    document.getElementById('cm-stats').innerHTML = [
      { label: 'Pending',   val: counts.pending,   cls: 'info' },
      { label: 'Done',      val: counts.done,      cls: 'ok' },
      { label: 'Drafted',   val: counts.drafted,   cls: 'warn' },
      { label: 'Dismissed', val: counts.dismissed, cls: 'info' },
    ].map(c => `
      <div class="health-card ${c.cls}">
        <div class="health-card-label">${c.label}</div>
        <div class="health-card-value">${c.val}</div>
      </div>
    `).join('');
  }

  function applyFilter() {
    const f = document.getElementById('cm-filter').value;
    const rows = f ? all.filter(c => c.status === f) : all;
    renderTable(rows);
  }

  function renderTable(rows) {
    if (!rows.length) {
      document.getElementById('cm-wrap').innerHTML = `<div class="empty-state">No commitments</div>`;
      return;
    }
    document.getElementById('cm-wrap').innerHTML = `
      <table>
        <thead>
          <tr>
            <th>#</th><th>Action</th><th>Person</th><th>Type</th>
            <th>Status</th><th>Due</th><th>Created</th><th>Source</th><th>Mark</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(c => `
            <tr>
              <td class="mono text-muted">${c.id}</td>
              <td class="truncate" style="max-width:200px" title="${esc(c.action)}">${esc(c.action)}</td>
              <td>${c.person ? esc(c.person) : '<span class="text-muted">—</span>'}</td>
              <td><span class="badge badge-purple">${esc(c.commitment_type)}</span></td>
              <td>${statusBadge(c.status)}</td>
              <td class="text-muted fs-12">${c.due_date ? fmtTime(c.due_date) : '—'}</td>
              <td class="text-muted fs-12">${fmtTime(c.created_at)}</td>
              <td class="mono text-muted fs-12">${c.source_id != null ? `src #${c.source_id}` : '—'}</td>
              <td>
                <div class="row" style="gap:4px">
                  ${c.status !== 'done' ? `
                    <button class="btn btn-success btn-xs" onclick="setCmStatus(${c.id},'done')">✓ Done</button>
                  ` : `
                    <button class="btn btn-ghost btn-xs" onclick="setCmStatus(${c.id},'pending')">↩ Reopen</button>
                  `}
                  ${c.status !== 'dismissed' ? `
                    <button class="btn btn-danger btn-xs" onclick="setCmStatus(${c.id},'dismissed')">✕</button>
                  ` : ''}
                </div>
              </td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  window.setCmStatus = async (id, status) => {
    try {
      await api.patch(`/commitments/${id}/status`, { status });
      toast.success(`Commitment #${id} → ${status}`);
      await load();
    } catch (e) {
      toast.error('Status update failed: ' + e.message);
    }
  };
}

function statusBadge(s) {
  const m = { pending:'info', done:'success', drafted:'purple', dismissed:'muted' };
  return `<span class="badge badge-${m[s]||'muted'}">${s}</span>`;
}
