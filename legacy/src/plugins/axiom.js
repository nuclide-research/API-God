// axiom.trade plugin — captures trending token data from the socket.io feed at :3001
// Socket.io frame format: "42[\"event\",data]" (packet type 4 = message, subtype 2 = event)

function domainOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ''); }
  catch { return 'unknown'; }
}

function parseSioFrame(raw) {
  if (typeof raw !== 'string') return null;
  // Socket.io engine.io packet: leading digit(s), then JSON for message types
  // Type 4 = message, subtype 2 = event: "42[...]"
  const m = raw.match(/^42(\[[\s\S]*)$/);
  if (!m) return null;
  try {
    const parsed = JSON.parse(m[1]);
    if (!Array.isArray(parsed) || parsed.length < 1) return null;
    return { event: parsed[0], data: parsed[1] };
  } catch {
    return null;
  }
}

// Maps an axiom trending token entry to a structured record
function mapToken(token, event, wsUrl) {
  if (!token || typeof token !== 'object') return null;

  return {
    domain:         domainOf(wsUrl),
    type:           'axiom-token',
    url:            wsUrl,
    // canonical fields
    mint:           token.mint          ?? token.address ?? null,
    symbol:         token.symbol        ?? token.ticker  ?? null,
    name:           token.name          ?? null,
    price_usd:      token.priceUsd      ?? token.price   ?? null,
    price_sol:      token.priceSol      ?? null,
    market_cap:     token.marketCap     ?? token.mc      ?? null,
    liquidity:      token.liquidity     ?? token.liq     ?? null,
    volume_1m:      token.volume1m      ?? token.v1m     ?? null,
    volume_5m:      token.volume5m      ?? token.v5m     ?? null,
    volume_1h:      token.volume1h      ?? token.v1h     ?? null,
    buys:           token.buys          ?? token.b       ?? null,
    sells:          token.sells         ?? token.s       ?? null,
    price_change_1m: token.priceChange1m ?? token.pc1m   ?? null,
    price_change_5m: token.priceChange5m ?? token.pc5m   ?? null,
    age_mins:       token.ageMins       ?? null,
    dex:            token.dex           ?? token.exchange ?? null,
    event,
    data: JSON.stringify(token),
  };
}

export default {
  name: 'axiom',

  // HTTP — axiom.trade REST responses
  match: (url) => /axiom\.trade/i.test(url),

  // WebSocket — match the socket.io feed on :3001 or any axiom.trade WS
  matchWS: (url) => /axiom\.trade|:3001\b/.test(url),

  async onResponse(url, body) {
    // Capture REST snapshots/trending endpoints if they exist
    if (!/trending|snapshot|token/i.test(url)) return null;
    let parsed;
    try { parsed = JSON.parse(body); } catch { return null; }

    const tokens = Array.isArray(parsed) ? parsed
      : Array.isArray(parsed?.data) ? parsed.data
      : Array.isArray(parsed?.tokens) ? parsed.tokens
      : null;

    if (!tokens) return null;

    return tokens
      .map(t => mapToken(t, 'http-snapshot', url))
      .filter(Boolean);
  },

  async onWebSocket(frame) {
    // Only care about incoming frames
    if (frame.dir !== 'recv') return null;

    const sio = parseSioFrame(frame.data);
    if (!sio) return null;

    const { event, data } = sio;

    // Primary: trending rankings broadcast
    if (/new-trending|trending-update|trending_v2/i.test(event)) {
      const tokens = Array.isArray(data) ? data
        : Array.isArray(data?.tokens) ? data.tokens
        : Array.isArray(data?.data) ? data.data
        : null;

      if (!tokens) return null;

      return tokens
        .map(t => mapToken(t, event, frame.url))
        .filter(Boolean);
    }

    // Secondary: individual token update events
    if (/token.update|price.update/i.test(event)) {
      const rec = mapToken(data, event, frame.url);
      return rec ? [rec] : null;
    }

    // Wallet alert / sniper events
    if (/wallet.alert|sniper|copy.trade/i.test(event)) {
      return [{
        domain: domainOf(frame.url),
        type:   'axiom-alert',
        url:    frame.url,
        event,
        data:   typeof data === 'string' ? data : JSON.stringify(data),
      }];
    }

    return null;
  },
};
