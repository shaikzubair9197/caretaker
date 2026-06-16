// Central HTTP client — every API call goes through here.
// Config is persisted in localStorage so it survives page reloads.

const DEFAULTS = {
  url: 'http://127.0.0.1:8000',
  key: 'f9d56275f2a949ceedf8a215fb97b6073cddbfbf5a1c804e',
};

export const cfg = {
  get url() { return localStorage.getItem('ct_url') || DEFAULTS.url; },
  get key() { return localStorage.getItem('ct_key') || DEFAULTS.key; },
  save(url, key) {
    localStorage.setItem('ct_url', url.trim().replace(/\/$/, ''));
    localStorage.setItem('ct_key', key.trim());
  },
};

// Core fetch wrapper
async function req(method, path, body = null, signal = null) {
  const opts = {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': cfg.key,
    },
  };
  if (signal) opts.signal = signal;
  if (body !== null) opts.body = JSON.stringify(body);

  let res;
  try {
    res = await fetch(cfg.url + path, opts);
  } catch (e) {
    throw new ApiError(0, 'Network error — is the server running?', null);
  }

  let data = null;
  const ct = res.headers.get('content-type') || '';
  if (ct.includes('application/json')) {
    data = await res.json();
  } else {
    data = await res.text();
  }

  if (!res.ok) {
    const msg = (data && data.detail) ? data.detail : `HTTP ${res.status}`;
    throw new ApiError(res.status, msg, data);
  }
  return data;
}

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

export const api = {
  get:    (path, signal)       => req('GET',    path, null,  signal),
  post:   (path, body, signal) => req('POST',   path, body,  signal),
  patch:  (path, body)         => req('PATCH',  path, body),
  delete: (path)               => req('DELETE', path),
};
