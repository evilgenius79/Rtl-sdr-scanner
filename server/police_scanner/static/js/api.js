// Tiny fetch wrapper that injects the CSRF header on state-changing methods.
function getCookie(name) {
  return document.cookie.split('; ').reduce((acc, c) => {
    const [k, ...v] = c.split('=');
    return k === name ? decodeURIComponent(v.join('=')) : acc;
  }, '');
}

export async function api(path, { method = 'GET', body, params } = {}) {
  const url = new URL(path, location.origin);
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, v);
    }
  }
  const headers = { 'Accept': 'application/json' };
  let payload = body;
  if (body && typeof body === 'object' && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }
  if (!['GET', 'HEAD'].includes(method)) {
    const csrf = getCookie('scanner_csrf');
    if (csrf) headers['X-CSRF-Token'] = csrf;
  }
  const res = await fetch(url.toString(), {
    method,
    headers,
    body: payload,
    credentials: 'same-origin',
  });
  if (res.status === 401) {
    location.href = '/login';
    throw new Error('unauthenticated');
  }
  if (!res.ok) {
    // Prefer FastAPI's JSON {detail: "..."} body. If the response was rewritten
    // by an intermediary (Cloudflare error page, etc.) into HTML, surface the
    // status + a snippet so the user sees something useful instead of a bare
    // "Bad Gateway".
    let msg = `${res.status} ${res.statusText}`.trim();
    try {
      const d = await res.clone().json();
      if (typeof d.detail === 'string') msg = d.detail;
      else if (Array.isArray(d.detail) && d.detail[0]?.msg) msg = d.detail[0].msg;
    } catch {
      try {
        const t = (await res.text()).trim();
        if (t) {
          const titleMatch = t.match(/<title>([^<]+)<\/title>/i);
          const snippet = titleMatch ? titleMatch[1] : t.slice(0, 200);
          msg = `${msg} — ${snippet}`;
        }
      } catch {}
    }
    throw new Error(msg);
  }
  if (res.status === 204) return null;
  return res.json();
}

export function connectWS(onMessage) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ws.addEventListener('message', (ev) => {
    try { onMessage(JSON.parse(ev.data)); } catch {}
  });
  return ws;
}
