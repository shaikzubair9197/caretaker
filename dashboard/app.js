// Main app entry point — router + navigation + config panel.
import { cfg } from './api.js';
import { toast } from './components/toast.js';
import { PopupManager } from './components/popup.js';

// Global PopupManager — pages register their own triggers against this instance.
export const popupManager = new PopupManager();
window._caretakerPopups = popupManager;

// Page registry — lazy-loaded
const PAGES = {
  'dashboard':   () => import('./pages/dashboard.js'),
  'panic-dump':  () => import('./pages/panic_dump.js'),
  'brain':       () => import('./pages/brain.js'),
  'agent':       () => import('./pages/agent.js'),
  'tasks':       () => import('./pages/tasks.js'),
  'commitments': () => import('./pages/commitments.js'),
  'llm-audit':   () => import('./pages/llm_audit.js'),
  'telemetry':   () => import('./pages/telemetry.js'),
  'txn-debug':   () => import('./pages/txn_debug.js'),
  'dev-testing': () => import('./pages/dev_testing.js'),
};

let currentDestroy = null;

async function navigate(hash) {
  const page = (hash || '').replace('#', '') || 'dashboard';
  const loader = PAGES[page] || PAGES['dashboard'];

  // Update nav highlight
  document.querySelectorAll('.nav-link').forEach(a => {
    a.classList.toggle('active', a.dataset.page === page);
  });

  const content = document.getElementById('content');
  content.innerHTML = `<div class="loading-full"><div class="spinner"></div></div>`;

  // Tear down previous page if it registered a cleanup fn
  if (typeof currentDestroy === 'function') {
    currentDestroy();
    currentDestroy = null;
  }
  // Cancel any popup timers registered by the previous page
  popupManager.destroy();

  try {
    const mod = await loader();
    content.innerHTML = '';
    const result = await mod.render(content);
    if (typeof result === 'function') currentDestroy = result;
  } catch (err) {
    console.error('Page load error:', err);
    content.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⚠</div>
        <div class="text-danger" style="margin-bottom:8px;">Failed to load page</div>
        <div class="text-muted fs-12">${err.message}</div>
      </div>`;
  }
}

// Config panel wiring
function initConfig() {
  const urlEl = document.getElementById('cfg-url');
  const keyEl = document.getElementById('cfg-key');
  const saveEl = document.getElementById('cfg-save');

  urlEl.value = cfg.url;
  // Show masked key for security
  keyEl.placeholder = cfg.key ? '••••••••' : 'X-API-Key';

  saveEl.addEventListener('click', () => {
    const url = urlEl.value.trim() || 'http://127.0.0.1:8000';
    const key = keyEl.value.trim();
    cfg.save(url, key || cfg.key);
    keyEl.value = '';
    keyEl.placeholder = '••••••••';
    toast.success('Config saved');
  });
}

// Router
window.addEventListener('hashchange', () => navigate(location.hash));

// Boot
initConfig();
navigate(location.hash || '#dashboard');
