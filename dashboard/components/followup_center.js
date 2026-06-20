// Follow-up Center — global right-side slide-out for reviewing AI-generated
// meeting follow-up drafts (Plan 3 refinement U1-U4). Mounted ONCE at boot so it
// persists across hash navigation, exactly like PopupManager. Masked-only: it
// renders the tokens the backend stored and never displays decrypted data.

import { api } from '../api.js';
import { toast } from './toast.js';
import { escHtml, fmtTime, emptyState, loadingHtml } from './panel.js';

const GROUP_LABELS = {
  pending_approval:     'Pending Approval',
  clarification_needed: 'Clarification Needed',
  failed:               'Failed',
  executed:             'Executed',
  dismissed:            'Dismissed',
};
const GROUP_ORDER = ['pending_approval', 'clarification_needed', 'failed', 'executed', 'dismissed'];
const TYPE_LABEL = {
  teams_message_draft: 'Teams', email_draft: 'Email', reminder_draft: 'Reminder',
  calendar_reminder_draft: 'Calendar', followup_suggestion_draft: 'Suggestion',
  clarification_needed: 'Clarification',
};

export class FollowupCenter {
  constructor(rootId) {
    this.root = document.getElementById(rootId);
    this.isOpen = false;
    this.data = { meetings: [], ungrouped: [] };
    this.selected = new Set();
    this.filters = { q: '', status: '', draft_type: '', meeting_id: '', min_confidence: '' };
    this._countsTimer = null;
  }

  mount() {
    if (!this.root) return;
    this._renderShell();
    this._bindEvents();
    this.pollCounts();
    this._countsTimer = setInterval(() => this.pollCounts(), 20000); // H4 async refresh
  }

  // ── Shell ──────────────────────────────────────────────────────────────────
  _renderShell() {
    this.root.innerHTML = `
      <button class="fc-toggle" id="fc-toggle" title="Meeting Follow-up Center">
        <span class="fc-toggle-icon">✦</span>
        <span class="fc-toggle-label">Follow-ups</span>
        <span class="fc-badge" id="fc-badge" hidden>0</span>
      </button>
      <div class="fc-backdrop" id="fc-backdrop" hidden></div>
      <aside class="fc-panel" id="fc-panel" aria-hidden="true" role="dialog" aria-label="Meeting Follow-up Center">
        <header class="fc-header">
          <div class="fc-title">✦ Meeting Follow-up Center</div>
          <div class="fc-header-actions">
            <span class="fc-updated" id="fc-updated"></span>
            <button class="fc-icon-btn" id="fc-refresh" title="Refresh">↻</button>
            <button class="fc-icon-btn" id="fc-close" title="Close">✕</button>
          </div>
        </header>
        <div class="fc-filters">
          <input type="search" class="fc-search" id="fc-q" placeholder="Search title / preview…" />
          <select class="fc-select" id="fc-f-status">
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="executed">Executed</option>
            <option value="failed">Failed</option>
            <option value="dismissed">Dismissed</option>
          </select>
          <select class="fc-select" id="fc-f-type">
            <option value="">All types</option>
            <option value="teams_message">Teams</option>
            <option value="email">Email</option>
            <option value="reminder">Reminder</option>
            <option value="calendar_reminder">Calendar</option>
            <option value="followup_suggestion">Suggestion</option>
            <option value="clarification">Clarification</option>
          </select>
          <select class="fc-select" id="fc-f-meeting"><option value="">All meetings</option></select>
          <input type="number" class="fc-conf" id="fc-f-conf" placeholder="min conf" min="0" max="1" step="0.05" />
          <button class="fc-mini-btn" id="fc-clear">Clear</button>
        </div>
        <div class="fc-bulk">
          <label class="fc-selall"><input type="checkbox" id="fc-selall" /> Select pending</label>
          <span class="fc-sel-count" id="fc-sel-count">0 selected</span>
          <span class="fc-bulk-spacer"></span>
          <button class="fc-mini-btn ok" data-act="approve-selected">Approve Selected</button>
          <button class="fc-mini-btn danger" data-act="reject-selected">Reject Selected</button>
          <button class="fc-mini-btn" data-act="regen-selected">Regenerate Selected</button>
          <button class="fc-mini-btn primary" data-act="approve-all">Approve All</button>
        </div>
        <div class="fc-body" id="fc-body">${loadingHtml('Loading drafts…')}</div>
      </aside>
    `;
  }

  _bindEvents() {
    this._el('fc-toggle').addEventListener('click', () => this.toggle());
    this._el('fc-close').addEventListener('click', () => this.close());
    this._el('fc-backdrop').addEventListener('click', () => this.close());
    this._el('fc-refresh').addEventListener('click', () => this.refresh());

    const serverFilter = () => { this._readFilters(); this.refresh(); };
    this._el('fc-f-status').addEventListener('change', serverFilter);
    this._el('fc-f-type').addEventListener('change', serverFilter);
    this._el('fc-f-conf').addEventListener('change', serverFilter);
    this._el('fc-f-meeting').addEventListener('change', () => { this._readFilters(); this._render(); });
    this._el('fc-q').addEventListener('input', () => { this.filters.q = this._el('fc-q').value.trim().toLowerCase(); this._render(); });
    this._el('fc-clear').addEventListener('click', () => this._clearFilters());
    this._el('fc-selall').addEventListener('change', (e) => this._selectAllPending(e.target.checked));

    const panel = this._el('fc-panel');
    panel.addEventListener('click', (e) => this._onClick(e));
    panel.addEventListener('change', (e) => this._onChange(e));
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && this.isOpen) this.close(); });
  }

  _el(id) { return document.getElementById(id); }

  // ── Open / close ─────────────────────────────────────────────────────────
  toggle() { this.isOpen ? this.close() : this.open(); }

  open() {
    this.isOpen = true;
    this._el('fc-panel').classList.add('open');
    this._el('fc-panel').setAttribute('aria-hidden', 'false');
    this._el('fc-backdrop').hidden = false;
    this.refresh();
  }

  close() {
    this.isOpen = false;
    this._el('fc-panel').classList.remove('open');
    this._el('fc-panel').setAttribute('aria-hidden', 'true');
    this._el('fc-backdrop').hidden = true;
  }

  // ── Data ───────────────────────────────────────────────────────────────────
  async refresh() {
    const qs = new URLSearchParams();
    if (this.filters.status) qs.set('status', this.filters.status);
    if (this.filters.draft_type) qs.set('draft_type', this.filters.draft_type);
    if (this.filters.min_confidence) qs.set('min_confidence', this.filters.min_confidence);
    const body = this._el('fc-body');
    body.innerHTML = loadingHtml('Loading drafts…');
    try {
      const data = await api.get('/drafts' + (qs.toString() ? `?${qs}` : ''));
      this.data = { meetings: data.meetings || [], ungrouped: data.ungrouped || [] };
      this._el('fc-updated').textContent = 'updated ' + new Date().toLocaleTimeString();
      this._syncMeetingOptions();
      this._render();
      this.pollCounts();
    } catch (e) {
      body.innerHTML = `<div class="fc-error">Failed to load drafts: ${escHtml(e.message)}</div>`; // H5 degraded
    }
  }

  async pollCounts() {
    try {
      const c = await api.get('/drafts/counts');
      const total = (c.pending || 0) + (c.clarification || 0) + (c.failed || 0);
      const badge = this._el('fc-badge');
      if (!badge) return;
      badge.textContent = total;
      badge.hidden = total === 0;
      badge.classList.toggle('has-failed', (c.failed || 0) > 0);
    } catch { /* badge is best-effort */ }
  }

  // ── Filters ────────────────────────────────────────────────────────────────
  _readFilters() {
    this.filters.status = this._el('fc-f-status').value;
    this.filters.draft_type = this._el('fc-f-type').value;
    this.filters.meeting_id = this._el('fc-f-meeting').value;
    this.filters.min_confidence = this._el('fc-f-conf').value;
  }

  _clearFilters() {
    this.filters = { q: '', status: '', draft_type: '', meeting_id: '', min_confidence: '' };
    ['fc-f-status', 'fc-f-type', 'fc-f-meeting', 'fc-f-conf', 'fc-q'].forEach((id) => { const el = this._el(id); if (el) el.value = ''; });
    this.refresh();
  }

  _syncMeetingOptions() {
    const sel = this._el('fc-f-meeting');
    const current = sel.value;
    const opts = ['<option value="">All meetings</option>'].concat(
      (this.data.meetings || []).map((m) =>
        `<option value="${m.meeting_transcript_id}">${escHtml(m.subject || ('Meeting #' + m.meeting_transcript_id))}</option>`)
    );
    sel.innerHTML = opts.join('');
    sel.value = current;
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  _render() {
    const body = this._el('fc-body');
    const meetings = (this.data.meetings || []).filter((m) =>
      !this.filters.meeting_id || String(m.meeting_transcript_id) === String(this.filters.meeting_id));

    const sections = meetings.map((m) => this._renderMeeting(m)).filter(Boolean);
    if (this.filters.meeting_id === '' && (this.data.ungrouped || []).length) {
      const cards = this._renderGroupFlat('Unlinked drafts', this.data.ungrouped);
      if (cards) sections.push(cards);
    }

    body.innerHTML = sections.length ? sections.join('') : emptyState('No drafts match — generate from a meeting or adjust filters', '✦');
    this._updateSelCount();
  }

  _matches(dto) {
    if (!this.filters.q) return true;
    const hay = `${dto.display_title || ''} ${dto.preview || ''}`.toLowerCase();
    return hay.includes(this.filters.q);
  }

  _renderMeeting(m) {
    // apply client search across all groups
    const groups = {};
    let visible = 0;
    for (const g of GROUP_ORDER) {
      const items = (m.groups[g] || []).filter((d) => this._matches(d));
      groups[g] = items;
      visible += items.length;
    }
    if (!visible) return '';

    const c = m.counts || {};
    const chips = `
      <span class="fc-chip pending">${c.pending || 0} pending</span>
      <span class="fc-chip clar">${c.clarification || 0} clarify</span>
      <span class="fc-chip fail">${c.failed || 0} failed</span>
      <span class="fc-chip exec">${c.executed || 0} executed</span>`;

    const groupsHtml = GROUP_ORDER.map((g) => {
      const items = groups[g];
      if (!items.length) return '';
      return `
        <div class="fc-group">
          <div class="fc-group-head">${GROUP_LABELS[g]} <span class="fc-group-n">${items.length}</span></div>
          ${items.map((d) => this._renderCard(d)).join('')}
        </div>`;
    }).join('');

    return `
      <section class="fc-meeting">
        <details open class="fc-meeting-details">
          <summary class="fc-meeting-summary">
            <div class="fc-meeting-title">${escHtml(m.subject || ('Meeting #' + m.meeting_transcript_id))}</div>
            <div class="fc-meeting-meta">
              <span>${m.meeting_start ? fmtTime(m.meeting_start) : '—'}</span>
              <span>· ${(m.participant_tokens || []).length} participants</span>
              <span>· ${c.total || 0} actions</span>
            </div>
            <div class="fc-chips">${chips}</div>
          </summary>
          <div class="fc-groups">${groupsHtml}</div>
        </details>
      </section>`;
  }

  _renderGroupFlat(label, items) {
    const vis = items.filter((d) => this._matches(d));
    if (!vis.length) return '';
    return `
      <section class="fc-meeting">
        <div class="fc-meeting-summary"><div class="fc-meeting-title">${escHtml(label)}</div></div>
        <div class="fc-groups"><div class="fc-group">${vis.map((d) => this._renderCard(d)).join('')}</div></div>
      </section>`;
  }

  _renderCard(d) {
    const id = d.action_id;
    const isPending = d.status === 'pending';
    const isClar = d.action_type === 'clarification_needed';
    const selectable = isPending && !isClar;
    const conf = (d.confidence != null) ? Math.round(d.confidence * 100) + '%' : '—';
    const checked = this.selected.has(id) ? 'checked' : '';
    const citeN = (d.citations || []).length;

    return `
      <div class="fc-card" data-card="${id}">
        <div class="fc-card-top">
          ${selectable ? `<input type="checkbox" class="fc-sel" data-sel="${id}" ${checked} />` : '<span class="fc-sel-spacer"></span>'}
          <span class="fc-type">${escHtml(TYPE_LABEL[d.action_type] || d.action_type)}</span>
          <span class="fc-status fc-status-${escHtml(d.status)}">${escHtml(d.status)}</span>
          ${d.conflict_flag ? '<span class="fc-conflict" title="Conflicting knowledge versions">⚠ conflict</span>' : ''}
          <span class="fc-conf" title="retrieval confidence">${conf}</span>
          <span class="fc-ver">v${d.version ?? '—'}</span>
        </div>
        <div class="fc-card-title">${escHtml(d.display_title || '(untitled)')}</div>
        <div class="fc-card-preview" id="fc-prev-${id}">${escHtml(d.preview || (d.reason ? 'Clarification: ' + d.reason : ''))}</div>
        <div class="fc-card-meta">
          ${d.execution_target ? `<span title="execution target (masked)">→ ${escHtml(d.execution_target)}</span>` : ''}
          <button class="fc-link" data-act="inspect-citations" data-id="${id}">Citations (${citeN})</button>
          <button class="fc-link" data-act="inspect-versions" data-id="${id}">Versions</button>
          <button class="fc-link" data-act="inspect-timeline" data-id="${id}">Timeline</button>
        </div>
        <div class="fc-drawer" id="fc-drawer-${id}" hidden></div>
        <div class="fc-card-actions">
          ${isPending && !isClar ? `
            <button class="fc-mini-btn ok"     data-act="approve" data-id="${id}">Approve</button>
            <button class="fc-mini-btn"         data-act="edit"    data-id="${id}">Edit</button>
            <button class="fc-mini-btn"         data-act="regen"   data-id="${id}">Regenerate</button>
            <button class="fc-mini-btn danger"  data-act="reject"  data-id="${id}">Reject</button>` : ''}
          ${isPending && isClar ? `<button class="fc-mini-btn danger" data-act="reject" data-id="${id}">Dismiss</button>` : ''}
        </div>
      </div>`;
  }

  // ── Selection ──────────────────────────────────────────────────────────────
  _onChange(e) {
    const sel = e.target.closest('[data-sel]');
    if (!sel) return;
    const id = Number(sel.dataset.sel);
    if (sel.checked) this.selected.add(id); else this.selected.delete(id);
    this._updateSelCount();
  }

  _allPendingIds() {
    const ids = [];
    for (const m of this.data.meetings || []) {
      for (const d of m.groups.pending_approval || []) ids.push(d.action_id);
    }
    for (const d of this.data.ungrouped || []) if (d.status === 'pending' && d.action_type !== 'clarification_needed') ids.push(d.action_id);
    return ids;
  }

  _selectAllPending(on) {
    this.selected = new Set(on ? this._allPendingIds() : []);
    document.querySelectorAll('#fc-panel .fc-sel').forEach((cb) => { cb.checked = on; });
    this._updateSelCount();
  }

  _updateSelCount() {
    const el = this._el('fc-sel-count');
    if (el) el.textContent = `${this.selected.size} selected`;
  }

  // ── Click routing ──────────────────────────────────────────────────────────
  _onClick(e) {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    const id = btn.dataset.id ? Number(btn.dataset.id) : null;
    switch (act) {
      case 'approve':          return this._approve(id);
      case 'reject':           return this._reject(id);
      case 'regen':            return this._regenerate(id);
      case 'edit':             return this._edit(id);
      case 'save-edit':        return this._saveEdit(id);
      case 'cancel-edit':      return this._closeDrawer(id);
      case 'inspect-citations':return this._inspectCitations(id);
      case 'inspect-versions': return this._inspectVersions(id);
      case 'inspect-timeline': return this._inspectTimeline(id);
      case 'approve-selected': return this._bulkApprove([...this.selected]);
      case 'reject-selected':  return this._bulkReject([...this.selected]);
      case 'regen-selected':   return this._bulkRegenerate([...this.selected]);
      case 'approve-all':      return this._bulkApprove(this._allPendingIds(), true);
    }
  }

  // ── Single actions ─────────────────────────────────────────────────────────
  async _approve(id) {
    try { const r = await api.post(`/agent/actions/${id}/approve`); toast.success(`#${id} ${r.status}`); this.refresh(); }
    catch (e) { toast.error('Approve failed: ' + e.message); }
  }

  async _reject(id) {
    try { await api.post(`/agent/actions/${id}/dismiss`); toast.info(`#${id} dismissed`); this.refresh(); }
    catch (e) { toast.error('Reject failed: ' + e.message); }
  }

  async _regenerate(id) {
    try { await api.post(`/drafts/${id}/regenerate`, {}); toast.success(`#${id} regenerated`); this.refresh(); }
    catch (e) { toast.error('Regenerate failed: ' + e.message); }
  }

  // ── Inline edit ────────────────────────────────────────────────────────────
  _findDto(id) {
    for (const m of this.data.meetings || []) for (const g of GROUP_ORDER) for (const d of m.groups[g] || []) if (d.action_id === id) return d;
    for (const d of this.data.ungrouped || []) if (d.action_id === id) return d;
    return null;
  }

  _edit(id) {
    const d = this._findDto(id);
    if (!d) return;
    const drawer = this._el(`fc-drawer-${id}`);
    const t = d.action_type;
    let fields = '';
    // values are masked; escape on the way into inputs (H6)
    if (t === 'email_draft') {
      fields = `
        <label class="fc-edit-lbl">Subject</label>
        <input class="fc-edit-input" data-edit="subject" value="${escHtml(d.display_title || '')}" />
        <label class="fc-edit-lbl">Body</label>
        <textarea class="fc-edit-area" data-edit="body">${escHtml(d.preview || '')}</textarea>`;
    } else if (t === 'teams_message_draft') {
      fields = `<label class="fc-edit-lbl">Message</label><textarea class="fc-edit-area" data-edit="body">${escHtml(d.preview || '')}</textarea>`;
    } else if (t === 'followup_suggestion_draft') {
      fields = `<label class="fc-edit-lbl">Suggestion</label><textarea class="fc-edit-area" data-edit="suggestion_text">${escHtml(d.preview || '')}</textarea>`;
    } else { // reminder / calendar
      fields = `<label class="fc-edit-lbl">Title</label><input class="fc-edit-input" data-edit="title" value="${escHtml(d.preview || d.display_title || '')}" />`;
    }
    drawer.innerHTML = `
      <div class="fc-edit">
        ${fields}
        <div class="fc-edit-actions">
          <button class="fc-mini-btn ok" data-act="save-edit" data-id="${id}">Save</button>
          <button class="fc-mini-btn" data-act="cancel-edit" data-id="${id}">Cancel</button>
        </div>
      </div>`;
    drawer.hidden = false;
  }

  async _saveEdit(id) {
    const drawer = this._el(`fc-drawer-${id}`);
    const updates = {};
    drawer.querySelectorAll('[data-edit]').forEach((el) => { updates[el.dataset.edit] = el.value; });
    try {
      await api.patch(`/drafts/${id}/payload`, updates);
      toast.success(`#${id} edited`);
      this.refresh();
    } catch (e) { toast.error('Edit failed: ' + e.message); }
  }

  _closeDrawer(id) { const dr = this._el(`fc-drawer-${id}`); if (dr) { dr.hidden = true; dr.innerHTML = ''; } }

  // ── Inspection drawers ─────────────────────────────────────────────────────
  _inspectCitations(id) {
    const d = this._findDto(id);
    const drawer = this._el(`fc-drawer-${id}`);
    const cites = (d && d.citations) || [];
    drawer.innerHTML = cites.length
      ? `<div class="fc-inspect"><div class="fc-inspect-h">Citations</div>${cites.map((c) =>
          `<div class="fc-cite"><code>${escHtml(c.value_ref || '?')}</code> <span class="fc-dim">${escHtml(c.source_type || '')} #${escHtml(String(c.source_id ?? ''))}</span></div>`).join('')}</div>`
      : `<div class="fc-inspect"><div class="fc-dim">No citations recorded.</div></div>`;
    drawer.hidden = false;
  }

  async _inspectVersions(id) {
    const drawer = this._el(`fc-drawer-${id}`);
    drawer.innerHTML = loadingHtml('Loading versions…');
    drawer.hidden = false;
    try {
      const r = await api.get(`/drafts/${id}/version-history`);
      const rows = r.versions || [];
      drawer.innerHTML = rows.length
        ? `<div class="fc-inspect"><div class="fc-inspect-h">Version history · ${escHtml(r.knowledge_key || '')}</div>${rows.map((v) =>
            `<div class="fc-ver-row ${v.is_active ? 'active' : ''}">v${v.version} ${v.is_active ? '<b>active</b>' : 'superseded'} · ${escHtml(v.title_masked || '')} <span class="fc-dim">${v.valid_from ? fmtTime(v.valid_from) : ''}</span></div>`).join('')}</div>`
        : `<div class="fc-inspect"><div class="fc-dim">No version history.</div></div>`;
    } catch (e) { drawer.innerHTML = `<div class="fc-error">${escHtml(e.message)}</div>`; }
  }

  async _inspectTimeline(id) {
    const drawer = this._el(`fc-drawer-${id}`);
    drawer.innerHTML = loadingHtml('Loading timeline…');
    drawer.hidden = false;
    try {
      const r = await api.get(`/drafts/${id}/audit-trail`);
      const ev = r.events || [];
      drawer.innerHTML = ev.length
        ? `<div class="fc-inspect"><div class="fc-inspect-h">Timeline</div>${ev.map((e) =>
            `<div class="fc-tl ${e.outcome === 'ERROR' ? 'err' : ''}"><span class="fc-tl-dot"></span><b>${escHtml(e.event_type)}</b> <span class="fc-dim">${e.created_at ? fmtTime(e.created_at) : ''}</span></div>`).join('')}</div>`
        : `<div class="fc-inspect"><div class="fc-dim">No audit events.</div></div>`;
    } catch (e) { drawer.innerHTML = `<div class="fc-error">${escHtml(e.message)}</div>`; }
  }

  // ── Bulk actions ───────────────────────────────────────────────────────────
  async _bulkApprove(ids, isAll = false) {
    if (!ids.length) return toast.info(isAll ? 'No pending drafts to approve' : 'Nothing selected');
    try {
      const r = await api.post('/agent/actions/approve-batch', { action_ids: ids });
      toast.success(`${r.approved}/${r.submitted} approved`);
      this.selected.clear();
      this.refresh();
    } catch (e) { toast.error('Approve failed: ' + e.message); }
  }

  async _bulkReject(ids) {
    if (!ids.length) return toast.info('Nothing selected');
    try {
      const r = await api.post('/agent/actions/dismiss-batch', { action_ids: ids });
      toast.info(`${r.dismissed}/${r.submitted} dismissed`);
      this.selected.clear();
      this.refresh();
    } catch (e) { toast.error('Reject failed: ' + e.message); }
  }

  async _bulkRegenerate(ids) {
    if (!ids.length) return toast.info('Nothing selected');
    let ok = 0;
    for (const id of ids) { // H8: sequential, no fan-out
      try { await api.post(`/drafts/${id}/regenerate`, {}); ok++; }
      catch { /* keep going */ }
    }
    toast.success(`${ok}/${ids.length} regenerated`);
    this.selected.clear();
    this.refresh();
  }
}
