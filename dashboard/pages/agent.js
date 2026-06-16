import { api } from '../api.js';
import { toast } from '../components/toast.js';
import { codeBlock, escHtml as esc, fmtTime } from '../components/panel.js';

export async function render(el) {
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">◇ Agent Actions</h1>
      <div class="page-actions">
        <button class="btn btn-ghost btn-sm" id="ag-tick">▶ Trigger Tick</button>
        <button class="btn btn-ghost btn-sm" id="ag-idle">⏱ Trigger Idle</button>
        <button class="btn btn-ghost btn-sm" id="ag-refresh">↻ Refresh</button>
      </div>
    </div>

    <div id="ag-loading" class="loading-full"><div class="spinner"></div></div>
    <div id="ag-content" style="display:none">

      <!-- Lifecycle legend -->
      <div class="card mb-16">
        <div class="card-header">Action Lifecycle</div>
        <div class="card-body">
          <div class="row-wrap">
            <div class="flow-node"><span class="status-dot muted"></span> pending</div>
            <div class="flow-arrow">→</div>
            <div class="flow-node active"><span class="status-dot ok"></span> approved</div>
            <div class="flow-arrow">→</div>
            <div class="flow-node done"><span class="status-dot ok"></span> executed</div>
            <div style="margin:0 12px;color:var(--text-muted)">or</div>
            <div class="flow-node" style="border-color:var(--danger);color:var(--danger)"><span class="status-dot error"></span> dismissed</div>
          </div>
        </div>
      </div>

      <!-- Tick result -->
      <div id="ag-tick-result" style="display:none" class="card mb-16">
        <div class="card-header">Last Tick Result</div>
        <div class="card-body" id="ag-tick-body"></div>
      </div>

      <!-- Actions table -->
      <div class="card">
        <div class="card-header">
          All Actions
          <span id="ag-count" class="badge badge-info">0</span>
        </div>
        <div class="table-wrap" style="border:none;border-radius:0">
          <table id="ag-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Type</th>
                <th>Status</th>
                <th>Created</th>
                <th>Approved</th>
                <th>Actions</th>
                <th></th>
              </tr>
            </thead>
            <tbody id="ag-tbody"></tbody>
          </table>
        </div>
      </div>
    </div>
  `;

  document.getElementById('ag-refresh').addEventListener('click', load);
  document.getElementById('ag-tick').addEventListener('click', triggerTick);
  document.getElementById('ag-idle').addEventListener('click', triggerIdle);

  await load();

  async function load() {
    try {
      const data = await api.get('/agent/actions');
      document.getElementById('ag-loading').style.display = 'none';
      document.getElementById('ag-content').style.display = '';
      renderTable(data.actions || []);
    } catch (e) {
      toast.error('Failed to load actions: ' + e.message);
    }
  }

  function renderTable(actions) {
    document.getElementById('ag-count').textContent = actions.length;
    if (!actions.length) {
      document.getElementById('ag-tbody').innerHTML = `
        <tr><td colspan="7" class="empty-state">No agent actions yet — trigger a tick or idle event</td></tr>
      `;
      return;
    }

    document.getElementById('ag-tbody').innerHTML = actions.map(a => `
      <tr class="row-expand" onclick="toggleExpand('ag-exp-${a.id}')">
        <td class="mono text-muted">#${a.id}</td>
        <td><span class="badge badge-purple">${esc(a.action_type)}</span></td>
        <td>${statusBadge(a.status)}</td>
        <td class="text-muted fs-12">${fmtTime(a.created_at)}</td>
        <td class="text-muted fs-12">${a.approved_at ? fmtTime(a.approved_at) : '—'}</td>
        <td onclick="event.stopPropagation()">
          ${a.status === 'pending' ? `
            <div class="row" style="gap:6px">
              <button class="btn btn-success btn-xs" onclick="approveAction(${a.id})">✓ Approve</button>
              <button class="btn btn-danger btn-xs"  onclick="dismissAction(${a.id})">✕ Dismiss</button>
            </div>
          ` : `<span class="text-muted fs-12">${a.status}</span>`}
        </td>
        <td class="text-muted fs-11">▼</td>
      </tr>
      <tr class="expand-content hidden" id="ag-exp-${a.id}">
        <td colspan="7">
          <div class="expand-inner">
            <div class="grid-2" style="gap:12px">
              <div>
                <div class="section-title mb-8">Payload</div>
                ${codeBlock(a.payload || {})}
              </div>
              <div id="ag-exec-${a.id}">
                <div class="section-title mb-8">Execution Result</div>
                <div class="text-muted fs-12">Approve or dismiss to see result</div>
              </div>
            </div>
          </div>
        </td>
      </tr>
    `).join('');
  }

  // Global action handlers (onclick from table rows)
  window.approveAction = async (id) => {
    try {
      const result = await api.post(`/agent/actions/${id}/approve`);
      document.getElementById(`ag-exec-${id}`).innerHTML = `
        <div class="section-title mb-8">Execution Result</div>
        <div class="mb-8">
          ${statusBadge(result.status)}
          <span class="text-muted fs-12 ml-8">→ ${result.status}</span>
        </div>
        ${codeBlock(result.execution || {})}
      `;
      toast.success(`Action #${id} approved and ${result.status}`);
      await load();
    } catch (e) {
      toast.error('Approve failed: ' + e.message);
    }
  };

  window.dismissAction = async (id) => {
    try {
      await api.post(`/agent/actions/${id}/dismiss`);
      toast.info(`Action #${id} dismissed`);
      await load();
    } catch (e) {
      toast.error('Dismiss failed: ' + e.message);
    }
  };

  window.toggleExpand = (id) => {
    const row = document.getElementById(id);
    if (row) row.classList.toggle('hidden');
  };

  async function triggerTick() {
    try {
      const data = await api.get('/agent/tick');
      document.getElementById('ag-tick-result').style.display = '';
      document.getElementById('ag-tick-body').innerHTML = `
        <div class="grid-2" style="gap:12px">
          <div>
            <div class="section-title mb-8">Intent</div>
            <div class="kv-stack">
              <div class="kv"><span class="kv-key">Work state</span><span class="kv-val">${esc(data.intent?.summary?.work_state||'—')}</span></div>
              <div class="kv"><span class="kv-key">Focus minutes</span><span class="kv-val">${data.intent?.summary?.focus_minutes ?? '—'}</span></div>
              <div class="kv"><span class="kv-key">Top task</span><span class="kv-val">${esc(data.intent?.summary?.top_task||'—')}</span></div>
            </div>
            <div class="section-title mt-12 mb-8">Actions Generated</div>
            ${(data.executed||[]).map(a => `
              <div class="row-wrap mb-8">
                <span class="badge badge-purple">${esc(a.type)}</span>
                <span class="badge badge-${a.source==='llm'?'success':'info'}">${esc(a.source)}</span>
                <span class="text-muted fs-12">${esc(a.message||'')}</span>
              </div>
            `).join('') || '<div class="text-muted fs-12">No actions</div>'}
          </div>
          <div>
            <div class="section-title mb-8">Full Response</div>
            ${codeBlock(data)}
          </div>
        </div>
      `;
      toast.success('Tick complete — ' + (data.executed?.length || 0) + ' action(s)');
      await load();
    } catch (e) {
      toast.error('Tick failed: ' + e.message);
    }
  }

  async function triggerIdle() {
    try {
      const data = await api.post('/agent/idle');
      toast.success(`Idle event queued — action #${data.action_id}`);
      await load();
    } catch (e) {
      toast.error('Idle trigger failed: ' + e.message);
    }
  }
}

function statusBadge(s) {
  const m = { pending:'info', approved:'info', executed:'success', dismissed:'muted', failed:'danger' };
  return `<span class="badge badge-${m[s]||'muted'}">${s}</span>`;
}
