import { api } from '../api.js';
import { toast } from '../components/toast.js';
import { codeBlock, escHtml as esc, fmtTime, fmtMs } from '../components/panel.js';

export async function render(el) {
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">◈ LLM Audit Log</h1>
      <div class="page-actions">
        <select id="llm-filter" class="select" style="width:190px">
          <option value="">All statuses</option>
          <option value="SUCCESS">SUCCESS</option>
          <option value="CONNECTION_REFUSED">CONNECTION_REFUSED</option>
          <option value="DNS_FAILURE">DNS_FAILURE</option>
          <option value="TIMEOUT">TIMEOUT</option>
          <option value="MODEL_NOT_FOUND">MODEL_NOT_FOUND</option>
          <option value="HTTP_ERROR">HTTP_ERROR</option>
          <option value="JSON_PARSE_ERROR">JSON_PARSE_ERROR</option>
          <option value="SCHEMA_VALIDATION_FAILURE">SCHEMA_VALIDATION_FAILURE</option>
          <option value="UNKNOWN_ERROR">UNKNOWN_ERROR</option>
        </select>
        <select id="llm-type" class="select" style="width:160px">
          <option value="">All call types</option>
          <option value="panic_extract">panic_extract</option>
          <option value="intent_reason">intent_reason</option>
        </select>
        <label class="row" style="gap:6px;font-size:13px;color:var(--text-secondary)">
          <input type="checkbox" id="llm-autoref" checked /> Auto-refresh
        </label>
        <button class="btn btn-ghost btn-sm" id="llm-refresh">↻ Refresh</button>
      </div>
    </div>

    <!-- Stats row -->
    <div id="llm-stats" class="health-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px"></div>

    <!-- Legend -->
    <div class="row-wrap mb-16" style="gap:6px">
      <span class="badge badge-success">● SUCCESS</span>
      <span class="badge badge-danger">● CONNECTION_REFUSED</span>
      <span class="badge badge-danger">● DNS_FAILURE</span>
      <span class="badge badge-warning">● TIMEOUT</span>
      <span class="badge badge-danger">● MODEL_NOT_FOUND</span>
      <span class="badge badge-danger">● HTTP_ERROR</span>
      <span class="badge badge-danger">● JSON_PARSE_ERROR</span>
      <span class="badge badge-warning">● SCHEMA_VALIDATION_FAILURE</span>
      <span class="badge badge-danger">● UNKNOWN_ERROR</span>
    </div>

    <div class="card">
      <div class="table-wrap" style="border:none;border-radius:0" id="llm-wrap">
        <div class="loading-full"><div class="spinner"></div></div>
      </div>
    </div>
  `;

  let all = [];
  document.getElementById('llm-refresh').addEventListener('click', load);
  document.getElementById('llm-filter').addEventListener('change', applyFilter);
  document.getElementById('llm-type').addEventListener('change', applyFilter);

  await load();
  const autoEl = document.getElementById('llm-autoref');
  const timer = setInterval(() => { if (autoEl.checked) load(); }, 15_000);
  return () => clearInterval(timer);

  async function load() {
    try {
      all = await api.get('/llm/audit?limit=200');
      renderStats();
      applyFilter();
    } catch (e) {
      toast.error('Audit log failed: ' + e.message);
    }
  }

  function renderStats() {
    let successes = 0, failures = 0, totalMs = 0, msCount = 0;
    all.forEach(r => {
      if (r.status === 'SUCCESS') successes++;
      else failures++;
      if (r.duration_ms != null) { totalMs += r.duration_ms; msCount++; }
    });
    const avg = msCount ? Math.round(totalMs / msCount) : null;
    document.getElementById('llm-stats').innerHTML = [
      { label: 'Successful calls', val: successes,  cls: 'ok' },
      { label: 'Failed / Fallback', val: failures,  cls: failures > 0 ? 'error' : 'ok' },
      { label: 'Success rate',      val: all.length ? Math.round(successes / all.length * 100) + '%' : '—', cls: 'info' },
      { label: 'Avg Duration',      val: avg != null ? avg + 'ms' : '—', cls: 'info' },
    ].map(c => `
      <div class="health-card ${c.cls}">
        <div class="health-card-label">${c.label}</div>
        <div class="health-card-value" style="font-size:26px">${c.val}</div>
        <div class="health-card-sub">of ${all.length} total</div>
      </div>
    `).join('');
  }

  function applyFilter() {
    const sf = document.getElementById('llm-filter').value;
    const tf = document.getElementById('llm-type').value;
    const rows = all.filter(r => {
      if (sf && r.status !== sf) return false;
      if (tf && r.call_type !== tf) return false;
      return true;
    });
    renderTable(rows);
  }

  function renderTable(rows) {
    if (!rows.length) {
      document.getElementById('llm-wrap').innerHTML = `<div class="empty-state">No log entries match the filter</div>`;
      return;
    }
    document.getElementById('llm-wrap').innerHTML = `
      <table>
        <thead>
          <tr>
            <th>#</th><th>Time</th><th>Type</th><th>Model</th>
            <th>Status</th><th>Duration</th><th>Entities</th><th>Injection</th><th></th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(r => {
            const hasWarning = r.injection_warnings?.length > 0;
            return `
            <tr class="row-expand" onclick="toggleExpand('llme-${r.id}')">
              <td class="mono text-muted">${r.id}</td>
              <td class="text-muted fs-12 mono">${fmtTime(r.created_at)}</td>
              <td><span class="badge badge-purple">${esc(r.call_type)}</span></td>
              <td class="mono text-muted fs-12">${esc(r.model)}</td>
              <td>${statusBadge(r.status)}</td>
              <td class="mono ${r.duration_ms > 10000 ? 'text-warning' : 'text-muted'}">${fmtMs(r.duration_ms)}</td>
              <td>${(r.entity_types||[]).length > 0
                  ? r.entity_types.map(e => `<span class="badge badge-orange fs-11">${esc(e)}</span>`).join(' ')
                  : '<span class="text-muted">—</span>'}</td>
              <td>${hasWarning
                  ? `<span class="badge badge-danger">⚠ ${r.injection_warnings.length}</span>`
                  : '<span class="text-muted">—</span>'}</td>
              <td class="text-muted fs-11">▼</td>
            </tr>
            <tr class="expand-content hidden" id="llme-${r.id}">
              <td colspan="9">
                <div class="expand-inner">
                  <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">
                    <div class="kv-stack">
                      <div class="kv"><span class="kv-key">Prompt SHA256</span>
                        <span class="kv-val mono fs-11">${esc((r.prompt_sha256||'').slice(0,16))}…</span></div>
                      <div class="kv"><span class="kv-key">Prompt size</span>
                        <span class="kv-val mono">${r.prompt_size_chars != null ? r.prompt_size_chars + ' chars' : '—'}</span></div>
                      <div class="kv"><span class="kv-key">Prompt preview</span>
                        <span class="kv-val mono fs-11" style="word-break:break-all">${r.prompt_preview ? esc(r.prompt_preview) : '—'}</span></div>
                      <div class="kv"><span class="kv-key">Fallback reason</span>
                        <span class="kv-val">${r.fallback_reason ? esc(r.fallback_reason) : '—'}</span></div>
                      <div class="kv"><span class="kv-key">Entity types</span>
                        <span class="kv-val">${(r.entity_types||[]).join(', ') || '—'}</span></div>
                      <div class="kv"><span class="kv-key">Injection warnings</span>
                        <span class="kv-val ${hasWarning ? 'text-danger' : ''}">${(r.injection_warnings||[]).join(', ') || 'none'}</span></div>
                      ${r.exception_type ? `
                      <div class="kv"><span class="kv-key">Exception type</span>
                        <span class="kv-val text-danger mono fs-11">${esc(r.exception_type)}</span></div>
                      <div class="kv"><span class="kv-key">Exception message</span>
                        <span class="kv-val text-danger fs-11" style="word-break:break-all">${esc(r.exception_message||'—')}</span></div>
                      ` : ''}
                    </div>
                    <div>
                      ${r.preflight_data ? `
                      <div style="font-size:12px;font-weight:600;margin-bottom:6px;color:var(--text-secondary)">Pre-flight diagnostic</div>
                      ${codeBlock(r.preflight_data)}
                      <div style="margin-top:8px"></div>
                      ` : ''}
                      ${codeBlock({ status: r.status, duration_ms: r.duration_ms, model: r.model, call_type: r.call_type })}
                    </div>
                  </div>
                </div>
              </td>
            </tr>
          `}).join('')}
        </tbody>
      </table>
    `;
  }

  window.toggleExpand = (id) => {
    const row = document.getElementById(id);
    if (row) row.classList.toggle('hidden');
  };
}

function statusBadge(s) {
  const m = {
    SUCCESS:'success',
    CONNECTION_REFUSED:'danger', DNS_FAILURE:'danger', TIMEOUT:'warning',
    MODEL_NOT_FOUND:'danger', HTTP_ERROR:'danger',
    JSON_PARSE_ERROR:'danger', SCHEMA_VALIDATION_FAILURE:'warning',
    UNKNOWN_ERROR:'danger',
    // legacy
    success:'success', fallback:'warning', error:'danger', validation_failed:'warning', unavailable:'danger',
  };
  return `<span class="badge badge-${m[s]||'muted'}" style="font-weight:600">${s}</span>`;
}
