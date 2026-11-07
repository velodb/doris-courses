/* Install before JupyterLab captures fetch. Never replay writes or kernel messages. */
(() => {
  'use strict';
  const origin = location.origin;
  const messages = workshopMessages;
  const localized = path => path + '?locale=' + encodeURIComponent(workshopLocale);
  if (location.pathname === '/_workshop/reauth') {
    if (window.opener) {
      window.opener.postMessage({type: 'doris-workshop-authenticated'}, origin);
      window.close();
    }
    return;
  }
  if (window.__dorisWorkshopRecovery) return;
  window.__dorisWorkshopRecovery = true;
  const nativeFetch = window.fetch.bind(window);
  const NativeWebSocket = window.WebSocket;
  let state = 'connected', popup = null, pendingProbe = null, expiresAt = Infinity;
  let expiryTimer, banner;
  const waitingReads = new Set();

  function display(next) {
    if (state === 'ended') return;
    state = next;
    if (state === 'connected' || state === 'ended') {
      for (const resume of [...waitingReads]) resume(state);
    }
    const update = () => {
      if (!banner) {
        banner = document.createElement('section');
        banner.id = 'workshop-connection';
        banner.lang = workshopLocale;
        banner.setAttribute('role', 'status');
        banner.setAttribute('aria-live', 'polite');
        const text = document.createElement('span');
        const action = document.createElement('button');
        action.type = 'button';
        action.textContent = messages['recovery.action'];
        action.addEventListener('click', () => {
          popup = window.open(localized('/_workshop/reauth'), 'doris-workshop-reauth', 'width=720,height=760');
          if (!popup) text.textContent = messages['recovery.popup'];
        });
        banner.append(text, action);
        document.body.append(banner);
      }
      banner.hidden = state === 'connected';
      banner.dataset.state = state;
      banner.firstChild.textContent = {
        connected: '',
        network: messages['recovery.network'],
        auth: messages['recovery.auth'],
        ended: messages['recovery.ended']
      }[state];
      banner.lastChild.hidden = state !== 'auth';
    };
    if (document.body) update();
    else document.addEventListener('DOMContentLoaded', update, {once: true});
  }

  function authFailure(response) {
    return response.type === 'opaqueredirect' || response.status === 401 || response.status === 403;
  }

  function waitForSignIn(signal) {
    if (state === 'connected') return Promise.resolve();
    return new Promise((resolve, reject) => {
      const abort = () => {
        waitingReads.delete(resume);
        signal?.removeEventListener('abort', abort);
        reject(new DOMException('Request aborted', 'AbortError'));
      };
      const resume = next => {
        waitingReads.delete(resume);
        signal?.removeEventListener('abort', abort);
        if (next === 'ended') reject(new TypeError('Workshop has ended'));
        else resolve();
      };
      if (signal?.aborted) { abort(); return; }
      if (state === 'ended') { resume('ended'); return; }
      waitingReads.add(resume);
      signal?.addEventListener('abort', abort, {once: true});
    });
  }

  async function probe() {
    if (state === 'ended') return;
    if (Date.now() >= expiresAt) { display('ended'); return; }
    if (pendingProbe) return pendingProbe;
    pendingProbe = (async () => {
      try {
        const response = await nativeFetch(localized('/_workshop/session'), {
          credentials: 'same-origin', cache: 'no-store', redirect: 'manual',
          signal: AbortSignal.timeout(10000)
        });
        if (response.headers.get('X-Workshop-Access') === 'expired') { display('ended'); return; }
        if (authFailure(response)) { display('auth'); return; }
        if (!response.ok) throw new TypeError('Workshop connection unavailable');
        const session = await response.json();
        expiresAt = Date.parse(session.expires_at);
        if (!Number.isFinite(expiresAt)) throw new TypeError('Invalid workshop expiry');
        clearTimeout(expiryTimer);
        expiryTimer = setTimeout(() => display('ended'), Math.max(0, expiresAt - Date.now()));
        display(Date.now() >= expiresAt ? 'ended' : 'connected');
      } catch (_) {
        if (state !== 'auth') display('network');
      } finally { pendingProbe = null; }
    })();
    return pendingProbe;
  }

  window.fetch = async function(input, init) {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    if (url.origin !== origin || !url.pathname.startsWith('/api/')) return nativeFetch(input, init);
    const method = String(init?.method || input.method || 'GET').toUpperCase();
    const safeRead = method === 'GET' || method === 'HEAD';
    const signal = init?.signal || input.signal;
    try {
      while (true) {
        if (safeRead && state === 'auth') await waitForSignIn(signal);
        const response = await nativeFetch(input, {...init, redirect: 'manual'});
        if (response.headers.get('X-Workshop-Access') === 'expired') {
          display('ended');
          throw new TypeError('Workshop has ended');
        }
        if (authFailure(response)) {
          display('auth');
          // Keep idempotent reads pending until sign-in is restored. Passing an
          // auth ResponseError to JupyterLab would dispose the live kernel client;
          // rejecting every poll as NetworkError would show another error dialog.
          if (safeRead) { await waitForSignIn(signal); continue; }
          // Writes are never repeated, including saves, starts and restarts.
          throw new TypeError('Workshop sign-in is required');
        }
        return response;
      }
    } catch (error) {
      if (state === 'connected') display('network');
      throw error;
    }
  };

  class WorkshopWebSocket extends NativeWebSocket {
    constructor(...args) {
      super(...args);
      const url = new URL(args[0], location.href);
      if (url.host !== location.host || !/^\/api\/kernels\/[^/]+\/channels$/.test(url.pathname)) return;
      this.addEventListener('close', event => {
        if (event.code === 1000) return;
        if (event.code === 4003) { display('ended'); return; }
        if (state === 'connected') display('network');
        void probe();
      });
      this.addEventListener('open', () => { void probe(); });
    }
  }
  window.WebSocket = WorkshopWebSocket;
  window.addEventListener('message', event => {
    if (event.origin === origin && event.source === popup && event.data?.type === 'doris-workshop-authenticated') void probe();
  });
  window.addEventListener('online', () => { void probe(); });
  window.addEventListener('offline', () => display('network'));
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void probe(); });
  setInterval(() => { void probe(); }, 30000);
  void probe();
})();
