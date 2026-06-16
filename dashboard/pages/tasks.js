import { api } from '../api.js';
import { toast } from '../components/toast.js';
import { escHtml as esc } from '../components/panel.js';

export async function render(el) {
  el.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">▣ Task Explorer</h1>
      <div class="page-actions">
        <button class="btn btn-ghost btn-sm" id="tk-refresh">↻ Refresh</button>
        <button class="btn btn-primary btn-sm" id="tk-create-open">+ Create Task</button>
      </div>
    </div>

    <!-- Create task form (hidden by default) -->
    <div class="card mb-16" id="tk-create-card" style="display:none">
      <div class="card-header">New Task</div>
      <div class="card-body">
        <div class="form-row">
          <div class="form-group" style="flex:3">
            <label class="form-label">Description</label>
            <input id="tk-desc" class="input" type="text" placeholder="What needs to be done?" />
          </div>
          <div class="form-group" style="flex:1">
            <label class="form-label">Priority</label>
            <select id="tk-priority" class="select">
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="low">low</option>
            </select>
          </div>
          <div class="form-group" style="flex:none;padding-bottom:1px">
            <label class="form-label">&nbsp;</label>
            <button class="btn btn-primary" id="tk-create-submit">Create</button>
          </div>
        </div>
      </div>
    </div>

    <!-- Filters -->
    <div class="card mb-16">
      <div class="card-body" style="padding:10px 16px">
        <div class="row-wrap">
          <input id="tk-search"  class="input" type="text" placeholder="Search…" style="width:220px" />
          <select id="tk-status" class="select" style="width:130px">
            <option value="">All statuses</option>
            <option value="pending">pending</option>
            <option value="done">done</option>
          </select>
          <select id="tk-prio" class="select" style="width:130px">
            <option value="">All priorities</option>
            <option value="high">high</option>
            <option value="medium">medium</option>
            <option value="low">low</option>
          </select>
          <button class="btn btn-ghost btn-sm" id="tk-sort-id">Sort: ID ↓</button>
          <span class="spacer"></span>
          <span id="tk-count" class="text-muted fs-12"></span>
        </div>
      </div>
    </div>

    <!-- Tasks table -->
    <div class="card">
      <div class="table-wrap" style="border:none;border-radius:0" id="tk-wrap">
        <div class="loading-full"><div class="spinner"></div></div>
      </div>
    </div>
  `;

  let allTasks = [];
  let sortDesc = true;

  document.getElementById('tk-refresh').addEventListener('click', load);
  document.getElementById('tk-create-open').addEventListener('click', () => {
    const c = document.getElementById('tk-create-card');
    c.style.display = c.style.display === 'none' ? '' : 'none';
  });
  document.getElementById('tk-create-submit').addEventListener('click', createTask);
  document.getElementById('tk-desc').addEventListener('keydown', e => { if (e.key === 'Enter') createTask(); });
  document.getElementById('tk-search').addEventListener('input', applyFilters);
  document.getElementById('tk-status').addEventListener('change', applyFilters);
  document.getElementById('tk-prio').addEventListener('change', applyFilters);
  document.getElementById('tk-sort-id').addEventListener('click', () => {
    sortDesc = !sortDesc;
    document.getElementById('tk-sort-id').textContent = `Sort: ID ${sortDesc ? '↓' : '↑'}`;
    applyFilters();
  });

  await load();

  async function load() {
    try {
      allTasks = await api.get('/tasks/');
      applyFilters();
    } catch (e) {
      toast.error('Failed to load tasks: ' + e.message);
    }
  }

  function applyFilters() {
    const search = document.getElementById('tk-search').value.toLowerCase();
    const status = document.getElementById('tk-status').value;
    const prio   = document.getElementById('tk-prio').value;

    let tasks = allTasks.filter(t => {
      if (search && !t.description.toLowerCase().includes(search)) return false;
      if (status && t.status !== status) return false;
      if (prio   && t.priority !== prio)  return false;
      return true;
    });

    tasks = [...tasks].sort((a, b) => sortDesc ? b.id - a.id : a.id - b.id);
    document.getElementById('tk-count').textContent = `${tasks.length} of ${allTasks.length}`;
    renderTable(tasks);
  }

  function renderTable(tasks) {
    if (!tasks.length) {
      document.getElementById('tk-wrap').innerHTML = `<div class="empty-state">No tasks match the current filter</div>`;
      return;
    }
    document.getElementById('tk-wrap').innerHTML = `
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Description</th>
            <th>Priority</th>
            <th>Status</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          ${tasks.map(t => `
            <tr>
              <td class="mono text-muted">${t.id}</td>
              <td>${esc(t.description)}</td>
              <td>${priorityBadge(t.priority)}</td>
              <td>${statusBadge(t.status)}</td>
              <td class="mono text-muted fs-12">${t.source_id != null ? `src #${t.source_id}` : '—'}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  async function createTask() {
    const desc = document.getElementById('tk-desc').value.trim();
    const prio = document.getElementById('tk-priority').value;
    if (!desc) { toast.warn('Enter a task description'); return; }
    try {
      const t = await api.post('/tasks/', { description: desc, priority: prio });
      toast.success(`Task #${t.id} created`);
      document.getElementById('tk-desc').value = '';
      document.getElementById('tk-create-card').style.display = 'none';
      await load();
    } catch (e) {
      toast.error('Create failed: ' + e.message);
    }
  }
}

function statusBadge(s) {
  const m = { pending:'info', done:'success' };
  return `<span class="badge badge-${m[s]||'muted'}">${s}</span>`;
}
function priorityBadge(p) {
  const m = { high:'danger', medium:'warning', low:'muted' };
  return `<span class="badge badge-${m[p]||'muted'}">${p}</span>`;
}
