import { api, ApiError } from '../api.js';
import { toast } from '../components/toast.js';
import { codeBlock, statusBadge, fmtTime, escHtml as esc } from '../components/panel.js';

export async function render(el) {
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">⚡ Panic Dump Playground</h1>
    </div>

    <div class="grid-2" style="gap:20px;align-items:start">
      <!-- Left: input + controls -->
      <div class="stack">
        <div class="card">
          <div class="card-header">Input</div>
          <div class="card-body stack">
            <div class="form-group">
              <label class="form-label">Panic text</label>
              <textarea id="pd-text" class="textarea" rows="8"
                placeholder="Enter anything: tasks, commitments, emails with PII…&#10;e.g. I need to send the API key abc123def456 to vignesh@example.com by Friday and fix the auth bug"
              ></textarea>
            </div>
            <div class="row">
              <button class="btn btn-primary" id="pd-submit">▶ Submit Panic Dump</button>
              <button class="btn btn-ghost btn-sm" id="pd-clear">Clear</button>
              <span id="pd-spinner" style="display:none"><div class="spinner spinner-sm"></div></span>
            </div>
          </div>
        </div>

        <!-- Request payload -->
        <div class="card" id="pd-req-card" style="display:none">
          <div class="card-header">Request Payload</div>
          <div class="card-body" id="pd-req-body"></div>
        </div>
      </div>

      <!-- Right: results -->
      <div class="stack" id="pd-results" style="display:none">

        <!-- Response summary -->
        <div class="card">
          <div class="card-header">
            Response
            <span id="pd-status-badge"></span>
          </div>
          <div class="card-body">
            <div class="kv-stack mb-12">
              <div class="kv"><span class="kv-key">Items extracted</span><span class="kv-val" id="pd-item-count">—</span></div>
              <div class="kv"><span class="kv-key">LLM status</span><span class="kv-val" id="pd-llm-status">—</span></div>
              <div class="kv"><span class="kv-key">LLM used</span><span class="kv-val" id="pd-llm-used">—</span></div>
              <div class="kv"><span class="kv-key">Sensitivity label</span><span class="kv-val" id="pd-sensitivity">—</span></div>
              <div class="kv"><span class="kv-key">Overload detected</span><span class="kv-val" id="pd-overload">—</span></div>
              <div class="kv"><span class="kv-key">Round-trip time</span><span class="kv-val" id="pd-rtt">—</span></div>
            </div>

            <!-- LLM diagnostic box — shown only on LLM failure -->
            <div id="pd-llm-diag" style="display:none;margin-top:12px;border:1px solid var(--warning);border-radius:6px;padding:12px 14px;background:rgba(245,158,11,.07)">
              <div style="font-weight:600;font-size:13px;margin-bottom:8px;color:var(--warning)">⚠ LLM Diagnostic</div>
              <div class="kv-stack">
                <div class="kv"><span class="kv-key">Status code</span><span class="kv-val" id="pd-diag-status">—</span></div>
                <div class="kv"><span class="kv-key">Reason</span><span class="kv-val" id="pd-diag-reason" style="color:var(--text-secondary)">—</span></div>
                <div class="kv"><span class="kv-key">Fix</span><span class="kv-val" id="pd-diag-fix" style="color:var(--success);font-family:var(--mono)">—</span></div>
              </div>
            </div>

            <div id="pd-raw-json" style="margin-top:12px"></div>
          </div>
        </div>

        <!-- Execution timeline -->
        <div class="card">
          <div class="card-header">Execution Timeline</div>
          <div class="card-body" id="pd-timeline"></div>
        </div>

        <!-- Extracted items -->
        <div class="card" id="pd-items-card">
          <div class="card-header">Extracted Items</div>
          <div class="card-body" id="pd-items-body"></div>
        </div>

        <!-- Transaction outcome -->
        <div class="card" id="pd-txn-card">
          <div class="card-header">Transaction Debug</div>
          <div class="card-body" id="pd-txn-body"></div>
        </div>

      </div>
    </div>
  `;

  document.getElementById('pd-submit').addEventListener('click', run);
  document.getElementById('pd-clear').addEventListener('click', () => {
    document.getElementById('pd-text').value = '';
    document.getElementById('pd-results').style.display = 'none';
    document.getElementById('pd-req-card').style.display = 'none';
  });
  document.getElementById('pd-text').addEventListener('keydown', e => {
    if (e.key === 'Enter' && e.ctrlKey) run();
  });

  async function run() {
    const text = document.getElementById('pd-text').value.trim();
    if (!text) { toast.warn('Enter some text first'); return; }

    const submit = document.getElementById('pd-submit');
    const spinner = document.getElementById('pd-spinner');
    submit.disabled = true;
    spinner.style.display = '';

    // Show request payload
    const payload = { text };
    document.getElementById('pd-req-card').style.display = '';
    document.getElementById('pd-req-body').innerHTML = codeBlock(payload);

    const t0 = performance.now();
    try {
      const data = await api.post('/panic_dump/', payload);
      const rtt = Math.round(performance.now() - t0);

      document.getElementById('pd-results').style.display = '';

      // Summary
      document.getElementById('pd-item-count').textContent = data.item_count;
      document.getElementById('pd-llm-status').innerHTML = llmStatusBadge(data.llm_status || 'NOT_CALLED');
      document.getElementById('pd-llm-used').innerHTML = data.llm_used
        ? '<span class="text-success">Yes</span>' : '<span class="text-muted">No — regex fallback</span>';
      document.getElementById('pd-sensitivity').innerHTML = sensitivityBadge(data.sensitivity_label);
      document.getElementById('pd-overload').textContent = data.overload_detected ? '⚠ Yes' : 'No';
      document.getElementById('pd-rtt').textContent = rtt + 'ms';
      document.getElementById('pd-status-badge').innerHTML = `<span class="badge badge-success">200 OK</span>`;

      // Diagnostic box — only when LLM did not succeed
      const diagEl = document.getElementById('pd-llm-diag');
      if (!data.llm_used && data.llm_status && data.llm_status !== 'NOT_CALLED') {
        diagEl.style.display = '';
        document.getElementById('pd-diag-status').innerHTML = llmStatusBadge(data.llm_status);
        document.getElementById('pd-diag-reason').textContent = data.llm_reason || '—';
        document.getElementById('pd-diag-fix').textContent   = data.llm_fix   || '—';
      } else {
        diagEl.style.display = 'none';
      }

      document.getElementById('pd-raw-json').innerHTML = codeBlock(data);

      // Timeline
      renderTimeline(data, rtt);

      // Items
      renderItems(data.items || []);

      // Transaction debug — fetch latest source item
      fetchTxnDebug();

      toast.success(`Extracted ${data.item_count} items in ${rtt}ms`);
    } catch (e) {
      const rtt = Math.round(performance.now() - t0);
      document.getElementById('pd-results').style.display = '';
      document.getElementById('pd-status-badge').innerHTML = `<span class="badge badge-danger">${e.status || 'ERR'}</span>`;
      document.getElementById('pd-rtt').textContent = rtt + 'ms';
      document.getElementById('pd-raw-json').innerHTML = codeBlock({ error: e.message, body: e.body });
      toast.error('Panic dump failed: ' + e.message);
    } finally {
      submit.disabled = false;
      spinner.style.display = 'none';
    }
  }

  function renderTimeline(data, rtt) {
    const llmStatus = data.llm_status || 'NOT_CALLED';
    const llmOk = llmStatus === 'SUCCESS';
    const llmDot = llmOk ? 'ok' : 'skip';
    const llmDetail = llmOk
      ? `Status: ${llmStatus}`
      : `Status: ${llmStatus} → regex fallback activated${data.llm_reason ? ' (' + data.llm_reason + ')' : ''}`;
    const steps = [
      { dot: 'ok',  title: 'Input Received',       detail: `${data.item_count !== undefined ? 'text received' : ''}` },
      { dot: 'ok',  title: 'Preprocessing',         detail: `Noise filter → injection sanitise → Presidio PII mask → sensitivity: ${esc(data.sensitivity_label||'—')}` },
      { dot: llmDot, title: `LLM Call`,             detail: llmDetail },
      { dot: 'ok',  title: `Tasks Extracted`,       detail: `${data.item_count || 0} item(s) parsed` },
      { dot: 'ok',  title: 'Commitments Staged',    detail: `${data.item_count || 0} commitment(s) via CommitmentService` },
      { dot: 'ok',  title: 'Memory Upserted',       detail: 'masked_text stored with savepoint dedup' },
      { dot: 'ok',  title: 'DB COMMIT',             detail: `One atomic commit — all records flushed (${rtt}ms total)` },
    ];

    document.getElementById('pd-timeline').innerHTML = `
      <div class="timeline">
        ${steps.map(s => `
          <div class="timeline-step">
            <div class="tl-dot ${s.dot}">${s.dot === 'ok' ? '✓' : s.dot === 'skip' ? '⟳' : '✕'}</div>
            <div class="tl-body">
              <div class="tl-title">${s.title}</div>
              <div class="tl-detail">${s.detail}</div>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderItems(items) {
    if (!items.length) {
      document.getElementById('pd-items-body').innerHTML = `<div class="empty-state">No items extracted</div>`;
      return;
    }
    document.getElementById('pd-items-body').innerHTML = `
      <div class="table-wrap" style="border:none">
        <table>
          <thead><tr><th>#</th><th>Action</th><th>Person</th><th>Type</th><th>Priority</th><th>Due</th></tr></thead>
          <tbody>
            ${items.map((item, i) => `
              <tr>
                <td class="mono text-muted">${i + 1}</td>
                <td class="truncate" style="max-width:240px">${esc(item.action)}</td>
                <td>${item.person ? esc(item.person) : '<span class="text-muted">—</span>'}</td>
                <td><span class="badge badge-purple">${esc(item.commitment_type)}</span></td>
                <td>${priorityBadge(item.priority)}</td>
                <td class="text-muted">${item.due_hint ? esc(item.due_hint) : '—'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  async function fetchTxnDebug() {
    try {
      const items = await api.get('/source-items/?limit=1');
      if (!items.length) return;
      const src = items[0];
      const children = await api.get(`/source-items/${src.id}/children`);

      document.getElementById('pd-txn-body').innerHTML = `
        <div class="timeline">
          <div class="timeline-step">
            <div class="tl-dot ok">✓</div>
            <div class="tl-body">
              <div class="tl-title">SourceItem #${src.id} created</div>
              <div class="tl-detail">type=${src.source_type}, label=${src.sensitivity_label}</div>
            </div>
          </div>
          <div class="timeline-step">
            <div class="tl-dot ok">✓</div>
            <div class="tl-body">
              <div class="tl-title">${children.tasks.length} Task(s) inserted</div>
              <div class="tl-detail">${children.tasks.map(t => `<span class="badge badge-info">${esc(t.description.slice(0,40))}</span>`).join(' ') || 'none'}</div>
            </div>
          </div>
          <div class="timeline-step">
            <div class="tl-dot ok">✓</div>
            <div class="tl-body">
              <div class="tl-title">${children.commitments.length} Commitment(s) inserted</div>
              <div class="tl-detail">${children.commitments.map(c => `<span class="badge badge-orange">${esc(c.action.slice(0,40))}</span>`).join(' ') || 'none'}</div>
            </div>
          </div>
          <div class="timeline-step">
            <div class="tl-dot ok">✓</div>
            <div class="tl-body">
              <div class="tl-title">${children.memories.length} Memory upserted</div>
              <div class="tl-detail">${children.memories.map(m => `<span class="badge badge-purple">${esc(m.type)}</span>`).join(' ') || 'none'}</div>
            </div>
          </div>
          <div class="timeline-step">
            <div class="tl-dot ok">✓</div>
            <div class="tl-body">
              <div class="tl-title">DB COMMIT</div>
              <div class="tl-detail text-success">One atomic transaction committed successfully</div>
            </div>
          </div>
        </div>
      `;
    } catch { /* non-critical */ }
  }
}

function llmStatusBadge(s) {
  const m = {
    SUCCESS:'success',
    CONNECTION_REFUSED:'danger', DNS_FAILURE:'danger', TIMEOUT:'warning',
    MODEL_NOT_FOUND:'danger', HTTP_ERROR:'danger',
    JSON_PARSE_ERROR:'danger', SCHEMA_VALIDATION_FAILURE:'warning',
    UNKNOWN_ERROR:'danger', EMPTY_RESULT:'warning', ITEMS_SCHEMA_INVALID:'warning',
    NOT_CALLED:'muted',
  };
  return `<span class="badge badge-${m[s]||'muted'}">${s}</span>`;
}
function sensitivityBadge(s) {
  const m = { PUBLIC:'success', INTERNAL:'info', CONFIDENTIAL:'warning', RESTRICTED:'danger' };
  return `<span class="badge badge-${m[s]||'muted'}">${s||'—'}</span>`;
}
function priorityBadge(p) {
  const m = { high:'danger', medium:'warning', low:'muted' };
  return `<span class="badge badge-${m[p]||'muted'}">${p||'—'}</span>`;
}
