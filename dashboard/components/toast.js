// Singleton toast notification system.
// Usage: toast.success('Saved!') | toast.error('Failed') | toast.warn('…') | toast.info('…')

const ICONS = { success: '✓', error: '✕', warn: '⚠', info: 'ℹ' };
const DURATION = { success: 3000, info: 3000, warn: 4000, error: 5000 };

function show(type, message) {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.innerHTML = `
    <span class="toast-icon">${ICONS[type]}</span>
    <span class="toast-msg">${escHtml(String(message))}</span>
  `;
  container.appendChild(el);

  const dismiss = () => {
    el.classList.add('out');
    el.addEventListener('animationend', () => el.remove(), { once: true });
  };

  const timer = setTimeout(dismiss, DURATION[type]);
  el.addEventListener('click', () => { clearTimeout(timer); dismiss(); });
}

function escHtml(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

export const toast = {
  success: (msg) => show('success', msg),
  error:   (msg) => show('error',   msg),
  warn:    (msg) => show('warn',    msg),
  info:    (msg) => show('info',    msg),
};
