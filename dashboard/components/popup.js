/**
 * PopupManager — CRO-informed in-app notification system.
 *
 * Supports three popup types:
 *   banner   — sticky top bar (site-wide announcement)
 *   slide-in — fixed bottom-right corner (non-blocking nudge)
 *   modal    — centred overlay with backdrop (high-priority interruption)
 *
 * All popups honour:
 *   - Frequency capping via localStorage (no re-show until cooldown expires)
 *   - Accessibility: role="dialog", aria-label, focus trap (modal), Escape key
 *   - Dismissal: × button, click-outside (modal), Escape
 */

const CONTAINER_ID = 'popup-container';

export class PopupManager {
  constructor() {
    this._timers = [];
    this._intervals = [];
  }

  /**
   * Show a popup, respecting frequency capping.
   *
   * @param {object} opts
   * @param {string}   opts.id           Unique popup key (used for cooldown tracking)
   * @param {'banner'|'slide-in'|'modal'} opts.type
   * @param {'accent'|'warning'|'danger'|'success'} [opts.variant='accent']
   * @param {string}   opts.title        Bold headline text
   * @param {string}   [opts.body]       Secondary text / subheadline
   * @param {Array<{label:string, href?:string, action?:Function, primary?:boolean}>} [opts.actions]
   * @param {number}   [opts.cooldownMs=3600000]  Re-show cooldown in ms (default 1 h)
   * @param {boolean}  [opts.force=false]          Bypass cooldown check
   */
  show(opts) {
    const {
      id, type, variant = 'accent', title, body = '',
      actions = [], cooldownMs = 60 * 60 * 1000, force = false,
    } = opts;

    if (!force && this._isCoolingDown(id, cooldownMs)) return;
    // Only one banner/slide-in of the same id at a time
    if (document.getElementById(`popup-${id}`)) return;

    const el = this._build(id, type, variant, title, body, actions);
    document.getElementById(CONTAINER_ID)?.appendChild(el);

    // Focus management for modal
    if (type === 'modal') {
      this._trapFocus(el);
    }
  }

  /**
   * Dismiss a specific popup by id.
   */
  dismiss(id) {
    const el = document.getElementById(`popup-${id}`);
    if (el) this._animateOut(el);
  }

  /**
   * Register a delayed one-shot trigger.
   * @param {number} delayMs
   * @param {Function} fn  Called after delay; should call this.show(...)
   */
  delay(delayMs, fn) {
    const t = setTimeout(fn, delayMs);
    this._timers.push(t);
  }

  /**
   * Register a recurring interval trigger.
   * @param {number} intervalMs
   * @param {Function} fn  Called immediately then on each interval
   */
  interval(intervalMs, fn) {
    fn(); // run immediately
    const t = setInterval(fn, intervalMs);
    this._intervals.push(t);
    return t;
  }

  /**
   * Cancel all timers/intervals (call on page destroy).
   */
  destroy() {
    this._timers.forEach(clearTimeout);
    this._intervals.forEach(clearInterval);
    this._timers = [];
    this._intervals = [];
  }

  // ── Private ────────────────────────────────────────────────────────────────

  _isCoolingDown(id, cooldownMs) {
    const key = `ct_popup_dismiss_${id}`;
    const ts = parseInt(localStorage.getItem(key) || '0', 10);
    return ts && (Date.now() - ts < cooldownMs);
  }

  _recordDismiss(id) {
    localStorage.setItem(`ct_popup_dismiss_${id}`, String(Date.now()));
  }

  _build(id, type, variant, title, body, actions) {
    const el = document.createElement('div');
    el.id = `popup-${id}`;
    el.className = `popup popup-${type} popup-${variant}`;
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-label', title);

    // Actions HTML
    const actionsHtml = actions.map(a => {
      const cls = a.primary ? 'btn btn-primary btn-sm popup-action' : 'btn btn-ghost btn-sm popup-action';
      if (a.href) {
        return `<a href="${_esc(a.href)}" class="${cls}" data-action-label="${_esc(a.label)}">${_esc(a.label)}</a>`;
      }
      return `<button class="${cls}" data-action-label="${_esc(a.label)}">${_esc(a.label)}</button>`;
    }).join('');

    el.innerHTML = `
      <div class="popup-inner">
        <div class="popup-content">
          <span class="popup-title">${_esc(title)}</span>
          ${body ? `<span class="popup-body">${_esc(body)}</span>` : ''}
        </div>
        ${actionsHtml ? `<div class="popup-actions">${actionsHtml}</div>` : ''}
        <button class="popup-close" aria-label="Dismiss" title="Dismiss (Esc)">✕</button>
      </div>
      ${type === 'modal' ? '<div class="popup-backdrop"></div>' : ''}
    `;

    // Dismiss — × button
    el.querySelector('.popup-close').addEventListener('click', () => {
      this._recordDismiss(id);
      this._animateOut(el);
    });

    // Dismiss — click outside (modal only)
    if (type === 'modal') {
      el.querySelector('.popup-backdrop').addEventListener('click', () => {
        this._recordDismiss(id);
        this._animateOut(el);
      });
    }

    // Dismiss — Escape key
    const onKey = (e) => {
      if (e.key === 'Escape') {
        this._recordDismiss(id);
        this._animateOut(el);
        document.removeEventListener('keydown', onKey);
      }
    };
    document.addEventListener('keydown', onKey);

    // Action buttons with callbacks
    actions.forEach(a => {
      if (!a.action) return;
      const btn = el.querySelector(`[data-action-label="${_esc(a.label)}"]`);
      if (btn) btn.addEventListener('click', () => {
        a.action();
        if (a.dismissOnClick !== false) {
          this._recordDismiss(id);
          this._animateOut(el);
        }
      });
    });

    return el;
  }

  _animateOut(el) {
    el.classList.add('popup-out');
    el.addEventListener('animationend', () => el.remove(), { once: true });
    // Fallback removal in case animation doesn't fire
    setTimeout(() => el.remove(), 400);
  }

  _trapFocus(el) {
    const focusable = el.querySelectorAll('button, a, input, [tabindex]:not([tabindex="-1"])');
    if (!focusable.length) return;
    focusable[0].focus();
    el.addEventListener('keydown', (e) => {
      if (e.key !== 'Tab') return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey ? document.activeElement === first : document.activeElement === last) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    });
  }
}

function _esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
