import { api } from '../api.js';
import { toast } from '../components/toast.js';
import { codeBlock, escHtml as esc, fmtTime } from '../components/panel.js';

export async function render(el) {
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">⟁ Transaction Debug Panel</h1>
      <div class="page-actions">
        <button class="btn btn-ghost btn-sm" id="txn-refresh">↻ Refresh</button>
      </div>
    </div>

    <div class="card mb-16">
      <div class="card-header">Transaction Model</div>
      <div class="card-body">
        <div class="row-wrap" style="gap:0">
          <div class="flow-node done"><span class="status-dot ok"></span>SourceItem created</div>
          <div class="flow-arrow">↓</div>
          <div class="flow-node done"><span class="status-dot ok"></span>Tasks inserted <span class="savepoint-tag">SAVEPOINT/task</span></div>
          <div class="flow-arrow">↓</div>
          <div class="flow-node done"><span class="status-dot ok"></span>Commitments staged</div>
          <div class="flow-arrow">↓</div>
          <div class="flow-node done"><span class="status-dot ok"></span>Memory upserted <span class="savepoint-tag">SAVEPOINT/mem</span></div>
          <div class="flow-arrow">↓</div>
          <div class="flow-node active"><span class="status-dot ok" style="background:var(--success)"></span>DB COMMIT ✓</div>
        </div>
        <p class="text-muted fs-12 mt-12">
          Each Task insert runs inside <code>BEGIN NESTED</code> (SAVEPOINT). A duplicate description
          rolls back only that savepoint — the outer transaction (other tasks, commitments, memory) remains intact.
          Memory uses the same pattern. One <code>db.commit()</code> at the end covers everything atomically.
        </p>
      </div>
    </div>

    <div id="txn-loading" class="loading-full"><div class="spinner"></div></div>
    <div id="txn-list"></div>
  `;

  document.getElementById('txn-refresh').addEventListener('click', load);
  await load();

  async function load() {
    try {
      const items = await api.get('/source-items/?limit=30');
      document.getElementById('txn-loading').style.display = 'none';

      if (!items.length) {
        document.getElementById('txn-list').innerHTML = `<div class="empty-state">No source items yet — submit a panic dump first</div>`;
        return;
      }

      document.getElementById('txn-list').innerHTML = items.map(src => `
        <div class="txn-item">
          <div class="txn-source" id="txn-src-${src.id}" onclick="txnToggle(${src.id})">
            <div class="row" style="gap:10px">
              <span class="badge badge-info">#${src.id}</span>
              <span class="badge badge-purple">${esc(src.source_type)}</span>
              <span class="badge badge-${sensCls(src.sensitivity_label)}">${esc(src.sensitivity_label||'—')}</span>
              <span class="text-muted fs-12">${fmtTime(src.created_at)}</span>
            </div>
            <div class="row" style="gap:8px">
              <span class="text-muted fs-12 truncate" style="max-width:320px">${esc((src.masked_text||src.raw_text||'').slice(0,80))}…</span>
              <span class="text-muted fs-11">▼</span>
            </div>
          </div>
          <div class="txn-children" id="txn-children-${src.id}" style="display:none"></div>
        </div>
      `).join('');

      // Pre-load first item
      if (items.length) loadChildren(items[0].id, items[0]);
    } catch (e) {
      toast.error('Failed to load source items: ' + e.message);
    }
  }

  window.txnToggle = async (id) => {
    const el = document.getElementById(`txn-children-${id}`);
    if (!el) return;
    if (el.style.display === 'none') {
      el.style.display = '';
      if (!el.dataset.loaded) {
        const src = document.getElementById(`txn-src-${id}`);
        await loadChildren(id, null);
      }
    } else {
      el.style.display = 'none';
    }
  };

  async function loadChildren(id, src) {
    const container = document.getElementById(`txn-children-${id}`);
    if (!container) return;
    if (container.dataset.loaded) return;
    container.dataset.loaded = '1';
    container.innerHTML = `<div class="loading-inline"><div class="spinner spinner-sm"></div> Loading…</div>`;

    try {
      const children = await api.get(`/source-items/${id}/children`);
      const srcData = src || {};

      // Build the transaction flow visualization
      const taskRows = children.tasks.map(t => `
        <div class="txn-child task">
          <span class="badge badge-info">Task #${t.id}</span>
          <span class="truncate" style="flex:1;font-size:12px">${esc(t.description)}</span>
          <span class="badge badge-${prioCls(t.priority)}">${t.priority}</span>
          <span class="savepoint-tag">SAVEPOINT</span>
        </div>
      `).join('') || `<div class="txn-child" style="color:var(--text-muted);font-size:12px">No tasks</div>`;

      const cmRows = children.commitments.map(c => `
        <div class="txn-child commit">
          <span class="badge badge-orange">Commitment #${c.id}</span>
          <span class="truncate" style="flex:1;font-size:12px">${esc(c.action)}</span>
          ${c.person ? `<span class="badge badge-muted">${esc(c.person)}</span>` : ''}
        </div>
      `).join('') || `<div class="txn-child" style="color:var(--text-muted);font-size:12px">No commitments</div>`;

      const memRows = children.memories.map(m => `
        <div class="txn-child memory">
          <span class="badge badge-purple">Memory #${m.id}</span>
          <span class="truncate" style="flex:1;font-size:12px">${esc(m.text)}</span>
          <span class="badge badge-muted">${esc(m.type)}</span>
          <span class="savepoint-tag">SAVEPOINT</span>
        </div>
      `).join('') || `<div class="txn-child" style="color:var(--text-muted);font-size:12px">No memories</div>`;

      container.innerHTML = `
        <div class="timeline" style="padding-left:0">

          <div class="timeline-step">
            <div class="tl-dot ok">✓</div>
            <div class="tl-body">
              <div class="tl-title">SourceItem #${id} archived</div>
              <div class="tl-detail">
                ${srcData.masked_text
                  ? `masked_text stored (${srcData.masked_text.length} chars)`
                  : 'raw text archived'}
              </div>
            </div>
          </div>

          <div class="timeline-step">
            <div class="tl-dot ${children.tasks.length ? 'ok' : 'pending'}">
              ${children.tasks.length ? '✓' : '○'}
            </div>
            <div class="tl-body">
              <div class="tl-title">${children.tasks.length} Task(s) inserted</div>
              <div class="tl-detail mb-8">Each wrapped in BEGIN NESTED — duplicates skip without aborting outer transaction</div>
              <div class="stack" style="gap:4px">${taskRows}</div>
            </div>
          </div>

          <div class="timeline-step">
            <div class="tl-dot ${children.commitments.length ? 'ok' : 'pending'}">
              ${children.commitments.length ? '✓' : '○'}
            </div>
            <div class="tl-body">
              <div class="tl-title">${children.commitments.length} Commitment(s) staged</div>
              <div class="tl-detail mb-8">via CommitmentService.create() — no auto-commit, caller owns transaction</div>
              <div class="stack" style="gap:4px">${cmRows}</div>
            </div>
          </div>

          <div class="timeline-step">
            <div class="tl-dot ${children.memories.length ? 'ok' : 'pending'}">
              ${children.memories.length ? '✓' : '○'}
            </div>
            <div class="tl-body">
              <div class="tl-title">${children.memories.length} Memory upserted</div>
              <div class="tl-detail mb-8">MemoryService uses BEGIN NESTED — duplicate text silently returns existing record</div>
              <div class="stack" style="gap:4px">${memRows}</div>
            </div>
          </div>

          <div class="timeline-step">
            <div class="tl-dot ok">✓</div>
            <div class="tl-body">
              <div class="tl-title text-success">DB COMMIT</div>
              <div class="tl-detail">Single db.commit() at end of panic_dump — all staged records flushed atomically</div>
            </div>
          </div>

        </div>

        ${srcData.masked_text ? `
          <hr class="divider" />
          <div class="section-title mb-8">Masked Text (stored in Memory)</div>
          ${codeBlock(srcData.masked_text, 'text')}
        ` : ''}
      `;
    } catch (e) {
      container.innerHTML = `<div class="text-danger fs-12 p-8">Failed to load children: ${esc(e.message)}</div>`;
    }
  }
}

function sensCls(s) {
  return { PUBLIC:'success', INTERNAL:'info', CONFIDENTIAL:'warning', RESTRICTED:'danger' }[s] || 'muted';
}
function prioCls(p) {
  return { high:'danger', medium:'warning', low:'muted' }[p] || 'muted';
}
