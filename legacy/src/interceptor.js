import { save }                                      from './storage.js';
import { runOnResponse, runOnWebSocket, runOnPageLoad } from './plugins.js';

// Injected into every page at load — proxies window.WebSocket so we
// can observe frames through window.__apigod__ before CDP sees them.
const WS_PROXY_SCRIPT = `
(function () {
  if (window.__apigod__) return;

  const _WS = window.WebSocket;
  const listeners = [];

  window.__apigod__ = {
    onFrame: (fn) => listeners.push(fn),
    _emit:   (ev)  => listeners.forEach(fn => fn(ev)),
  };

  function PatchedWS(url, protocols) {
    const ws = protocols ? new _WS(url, protocols) : new _WS(url);

    ws.addEventListener('message', (evt) => {
      window.__apigod__._emit({
        dir: 'recv',
        url,
        data: typeof evt.data === 'string' ? evt.data : '[binary]',
      });
    });

    const origSend = ws.send.bind(ws);
    ws.send = function (data) {
      window.__apigod__._emit({
        dir: 'send',
        url,
        data: typeof data === 'string' ? data : '[binary]',
      });
      return origSend(data);
    };

    return ws;
  }

  PatchedWS.CONNECTING = 0;
  PatchedWS.OPEN       = 1;
  PatchedWS.CLOSING    = 2;
  PatchedWS.CLOSED     = 3;
  PatchedWS.prototype  = _WS.prototype;

  try {
    Object.defineProperty(window, 'WebSocket', {
      configurable: true, writable: true, value: PatchedWS,
    });
  } catch (_) {
    window.WebSocket = PatchedWS;
  }
})();
`;

function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); }
  catch { return 'unknown'; }
}

export async function attach(context, page) {
  // Inject WS proxy into every new page and frame
  await context.addInitScript(WS_PROXY_SCRIPT);

  // Expose a binding so page JS can send WS frames to Node
  await context.exposeFunction('__apigod_ws__', async (ev) => {
    const pluginRecords = await runOnWebSocket(ev);
    if (pluginRecords) {
      for (const rec of pluginRecords) save(rec);
      return;
    }
    save({
      domain: domainOf(ev.url),
      type:   `ws-${ev.dir}`,
      url:    ev.url,
      data:   ev.data,
    });
  });

  // Wire the proxy emitter to the exposed binding on each new page
  context.on('page', async (p) => {
    await wireWsBinding(p);
    attachRoutes(p);
  });

  await wireWsBinding(page);
  attachRoutes(page);
}

async function wireWsBinding(page) {
  await page.addInitScript(`
    (function poll() {
      if (window.__apigod__) {
        window.__apigod__.onFrame((ev) => window.__apigod_ws__(ev));
      } else {
        setTimeout(poll, 50);
      }
    })();
  `);
}

function attachRoutes(page) {
  page.on('load', async () => {
    const url = page.url();
    if (!url || url === 'about:blank') return;
    const records = await runOnPageLoad(url, page).catch(() => null);
    if (records) for (const rec of records) save(rec);
  });

  // Intercept all HTTP/S requests + responses
  page.on('request', (req) => {
    const url = req.url();
    if (shouldSkip(url)) return;

    save({
      domain: domainOf(url),
      type:   'request',
      url,
      method: req.method(),
      data:   safeJson({ headers: req.headers(), body: req.postData() }),
    });
  });

  page.on('response', async (res) => {
    const url = res.url();
    if (shouldSkip(url)) return;

    let body = null;
    try {
      const ct = res.headers()['content-type'] ?? '';
      if (ct.includes('json') || ct.includes('text')) {
        body = await res.text().catch(() => null);
      }
    } catch {}

    // Give plugins first crack — if one matches, save their structured records
    // instead of (not in addition to) the raw response.
    if (body) {
      const pluginRecords = await runOnResponse(url, body);
      if (pluginRecords) {
        for (const rec of pluginRecords) save(rec);
        return;
      }
    }

    save({
      domain: domainOf(url),
      type:   'response',
      url,
      status: res.status(),
      method: res.request().method(),
      data:   safeJson({ headers: res.headers(), body }),
    });
  });
}

// Skip noise: fonts, images, analytics beacons, sourcemaps
function shouldSkip(url) {
  return /\.(woff2?|ttf|eot|png|jpe?g|gif|svg|ico|mp4|webm|css|map)(\?|$)/i.test(url)
      || /google-analytics|googletagmanager|doubleclick|segment\.io|sentry\.io|hotjar|newrelic/i.test(url);
}

function safeJson(obj) {
  try { return JSON.stringify(obj); }
  catch { return null; }
}
