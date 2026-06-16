import { api } from '../api.js';
import { toast } from '../components/toast.js';
import { codeBlock, escHtml as esc, fmtTime } from '../components/panel.js';

export async function render(el) {
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">◎ Brain Inspector</h1>
      <div class="page-actions">
        <label class="row" style="gap:6px;font-size:13px;color:var(--text-secondary)">
          <input type="checkbox" id="brain-autoref" checked /> Auto-refresh 30s
        </label>
        <button class="btn btn-ghost btn-sm" id="brain-refresh">↻ Refresh</button>
      </div>
    </div>

    <div id="brain-loading" class="loading-full"><div class="spinner"></div></div>
    <div id="brain-content" style="display:none">

      <!-- Context summary row -->
      <div class="grid-3 mb-16" id="brain-summary"></div>

      <!-- Main 2-col layout -->
      <div class="grid-2" style="align-items:start;gap:20px">

        <!-- Left: tasks + context -->
        <div class="stack">
          <div class="card">
            <div class="card-header">Context Snapshot</div>
            <div class="card-body" id="brain-ctx"></div>
          </div>
          <div class="card">
            <div class="card-header">
              Pending Tasks
              <span id="brain-task-count" class="badge badge-info">0</span>
            </div>
            <div class="card-body" id="brain-tasks"></div>
          </div>
          <div class="card">
            <div class="card-header">Raw Brain JSON</div>
            <div class="card-body" id="brain-raw"></div>
          </div>
        </div>

        <!-- Right: memories -->
        <div class="card">
          <div class="card-header">
            Memories
            <div class="row" style="gap:8px">
              <span id="brain-mem-source" class="badge badge-purple">—</span>
              <span id="brain-mem-count"  class="badge badge-info">0</span>
              <select id="brain-mem-filter" class="select" style="width:auto;padding:3px 8px;font-size:11px">
                <option value="all">All</option>
                <option value="recency">Recency only</option>
                <option value="semantic">Semantic only</option>
              </select>
            </div>
          </div>
          <div class="card-body" id="brain-memories" style="max-height:560px;overflow-y:auto"></div>
        </div>

      </div>
    </div>
  `;

  let lastData = null;

  document.getElementById('brain-refresh').addEventListener('click', load);
  document.getElementById('brain-mem-filter').addEventListener('change', () => renderMemories(lastData));

  async function load() {
    try {
      const data = await api.get('/brain/');
      lastData = data;
      document.getElementById('brain-loading').style.display = 'none';
      document.getElementById('brain-content').style.display = '';
      renderSummary(data);
      renderCtx(data);
      renderTasks(data);
      renderMemories(data);
      document.getElementById('brain-raw').innerHTML = codeBlock(data);
    } catch (e) {
      toast.error('Brain load failed: ' + e.message);
    }
  }

  await load();

  const autoEl = document.getElementById('brain-autoref');
  let timer = setInterval(() => { if (autoEl.checked) load(); }, 30_000);
  return () => clearInterval(timer);

  function renderSummary(d) {
    const b = d.brain || {};
    const ins = d.insights || {};
    const cards = [
      { label: 'Top Task',      value: b.top_task ? b.top_task.slice(0, 40) + (b.top_task.length > 40 ? '…' : '') : 'None', cls: b.top_task ? 'info' : 'ok' },
      { label: 'Focus State',   value: b.focus_state || '—', cls: b.focus_state === 'focused' ? 'ok' : 'warn' },
      { label: 'Memory Source', value: ins.memory_source || '—', cls: ins.memory_source === 'semantic' ? 'ok' : 'info' },
    ];
    document.getElementById('brain-summary').innerHTML = cards.map(c => `
      <div class="health-card ${c.cls}">
        <div class="health-card-label">${c.label}</div>
        <div class="health-card-value" style="font-size:14px;font-weight:600;line-height:1.3">${esc(String(c.value))}</div>
      </div>
    `).join('');
  }

  function renderCtx(d) {
    const ctx = d.context || {};
    const ins = d.insights || {};
    document.getElementById('brain-ctx').innerHTML = `
      <div class="kv-stack">
        <div class="kv"><span class="kv-key">Current Time</span><span class="kv-val">${esc(ctx.current_time||'—')}</span></div>
        <div class="kv"><span class="kv-key">Focus Minutes</span><span class="kv-val">${ctx.focus_minutes ?? '—'} min</span></div>
        <div class="kv"><span class="kv-key">Pending Tasks</span><span class="kv-val">${ctx.pending_task_count ?? '—'}</span></div>
        <div class="kv"><span class="kv-key">Top Application</span><span class="kv-val">${esc(ctx.top_application||'—')}</span></div>
        <div class="kv"><span class="kv-key">Distraction Level</span>
          <span class="kv-val">
            ${ins.distraction_level === 'high'
              ? '<span class="text-warning">high</span>'
              : '<span class="text-success">low</span>'}
          </span>
        </div>
        <div class="kv"><span class="kv-key">Memory Count</span><span class="kv-val">${ins.memory_count ?? '—'}</span></div>
        <div class="kv"><span class="kv-key">Memory Source</span>
          <span class="kv-val">
            ${ins.memory_source === 'semantic'
              ? '<span class="badge badge-success">semantic</span>'
              : '<span class="badge badge-info">recency</span>'}
          </span>
        </div>
      </div>
      <hr class="divider" />
      <div class="section-title mt-8">Application Usage</div>
      ${(ctx.applications||[]).length ? `
        <div class="bar-chart">
          ${(ctx.applications||[]).slice(0, 8).map(a => {
            const maxSec = Math.max(...(ctx.applications||[]).map(x => x.seconds), 1);
            const pct = Math.round((a.seconds / maxSec) * 100);
            return `
              <div class="bar-row">
                <div class="bar-label" title="${esc(a.window)}">${esc(a.window)}</div>
                <div class="bar-track"><div class="bar-fill" style="width:${pct}%"></div></div>
                <div class="bar-val">${a.seconds}s</div>
              </div>
            `;
          }).join('')}
        </div>
      ` : `<div class="text-muted fs-12">No window data</div>`}
    `;
  }

  function renderTasks(d) {
    const tasks = d.tasks || [];
    document.getElementById('brain-task-count').textContent = tasks.length;
    if (!tasks.length) {
      document.getElementById('brain-tasks').innerHTML = `<div class="empty-state">No tasks</div>`;
      return;
    }
    document.getElementById('brain-tasks').innerHTML = `
      <div class="table-wrap" style="border:none">
        <table>
          <thead><tr><th>#</th><th>Description</th><th>Priority</th><th>Status</th></tr></thead>
          <tbody>
            ${tasks.slice(0, 10).map(t => `
              <tr>
                <td class="mono text-muted">${t.id}</td>
                <td class="truncate">${esc(t.description)}</td>
                <td>${priorityBadge(t.priority)}</td>
                <td>${statusBadge(t.status)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  function renderMemories(d) {
    if (!d) return;
    const filter = document.getElementById('brain-mem-filter').value;
    const source = d.insights?.memory_source || 'recency';
    const mems = d.memories || [];

    document.getElementById('brain-mem-source').textContent = source;
    document.getElementById('brain-mem-count').textContent = mems.length;

    if (filter === 'recency' && source === 'semantic') {
      document.getElementById('brain-memories').innerHTML = `<div class="empty-state">Current mode is semantic — switch filter to "All" or trigger a brain tick</div>`;
      return;
    }
    if (filter === 'semantic' && source === 'recency') {
      document.getElementById('brain-memories').innerHTML = `<div class="empty-state">Current mode is recency (no embeddings matched) — filter shows nothing</div>`;
      return;
    }

    if (!mems.length) {
      document.getElementById('brain-memories').innerHTML = `<div class="empty-state">No memories retrieved</div>`;
      return;
    }

    document.getElementById('brain-memories').innerHTML = `
      <div class="memory-grid">
        ${mems.map(m => `
          <div class="memory-card">
            <div class="memory-meta">
              <span class="badge badge-muted">#${m.id}</span>
              <span class="badge badge-purple">${esc(m.type||'general')}</span>
              ${m.has_embedding !== undefined
                ? (m.has_embedding
                  ? `<span class="badge badge-success">emb ✓</span>`
                  : `<span class="badge badge-muted">no emb</span>`)
                : ''}
              ${'importance' in m ? `<span class="badge badge-info">imp ${m.importance}</span>` : ''}
            </div>
            <div class="memory-text">${esc(m.text||'')}</div>
          </div>
        `).join('')}
      </div>
    `;
  }
}

function priorityBadge(p) {
  const m = { high:'danger', medium:'warning', low:'muted' };
  return `<span class="badge badge-${m[p]||'muted'}">${p}</span>`;
}
function statusBadge(s) {
  const m = { pending:'info', done:'success', executed:'success' };
  return `<span class="badge badge-${m[s]||'muted'}">${s}</span>`;
}
