"""Shared, hardened decision logic for the memecoin signal engine.
Single source of truth imported by live.py, replay.py, and stress.py."""
import re, unicodedata, json as _json

# common English words that double as tickers -> a BARE mention is generic chatter; require $CASHTAG
COMMON_WORDS = {"moon", "cat", "dog", "trump", "doge", "pepe", "ai", "the", "go", "up", "gm", "wif",
                "elon", "bonk", "sol", "usa", "king", "baby", "safe", "inu", "meme", "frog", "boden",
                "based", "chad", "wojak", "gme", "trump", "maga", "cult", "win", "love", "god", "cat"}

def norm_name(n):
    """NFKC fold (kills fullwidth/compat), casefold, strip whitespace+punctuation.
    Residual (accepted): cross-script homographs (Cyrillic) and leetspeak still differ -
    wallet-clustering covers cross-name farming, so this is low-severity."""
    if not n: return ""
    s = unicodedata.normalize("NFKC", n).casefold()
    s = re.sub(r'\s+', '', s)
    return re.sub(r'[^\w]', '', s, flags=re.UNICODE)

DEDUP_WINDOW_S = 300   # a same-name mint within this many seconds of the prior one is a duplicate

def dedup_name(nm, clock, last_seen, window=DEDUP_WINDOW_S):
    """Shared name-dedup for both the live engine and the replay harness, so a replay reproduces the
    live run's dedup exactly instead of guessing. nm: normalized name. clock: a seconds-valued stamp
    (live passes each event's arrival time, replay passes the captured _ts). last_seen: a dict the
    caller owns. Records last_seen[nm]=clock and returns True if nm was seen within `window` of clock."""
    if not nm:
        return False
    prev = last_seen.get(nm)
    last_seen[nm] = clock
    return prev is not None and (clock - prev) < window

def cashtag_hit(sym, text):
    if not sym or not text: return False
    s = re.escape(sym)
    if len(sym) <= 2 or sym.casefold() in COMMON_WORDS:        # FIX: dictionary/short tickers need $form
        return bool(re.search(r'\$' + s + r'(?![A-Za-z0-9])', text, re.I))
    return bool(re.search(r'(?<![A-Za-z0-9])\$?' + s + r'(?![A-Za-z0-9])', text, re.I))

def classify(url):
    if not url: return ("none", None, None)
    u = url.split("?")[0].strip()
    m = re.search(r'(?:x|twitter)\.com/([^/]+)/status/(\d+)', u)
    if m: return ("status", m.group(1), m.group(2))
    m = re.search(r'(?:x|twitter)\.com/i/status/(\d+)', u)
    if m: return ("status", None, m.group(1))
    if re.search(r'/i/communities|/i/trending', u): return ("community", None, None)
    if re.search(r'/search', u): return ("search", None, None)
    m = re.search(r'(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/?$', u)
    if m: return ("profile", m.group(1), None)
    return ("other", None, None)

def zone_of(buy, buf):
    if buy <= 0: return "green"
    if len(buf) < 5: return "green"          # FIX: warmup/low-volume -> suppress, don't leak
    s = sorted(buf); p80 = s[int(.8*len(s))]; p95 = s[int(.95*len(s))]
    return "red" if buy >= p95 else "amber" if buy >= p80 else "green"

def score_resolved(zone, refs, blue, mism):
    """Scoring for a coin whose linked tweet resolved. handle-mismatch VOIDS verification
    (FIX: a spoofed handle can no longer net positive via cashtag-in-tweet)."""
    notes = []
    if mism:
        refs = False
        notes.append("URL-handle!=author(void-verify)")
    s = 2 if zone == "red" else 1
    if refs:
        s += 3
        if blue: s += 1                      # FIX: blue only helps a coin its tweet references
    else:
        s -= 2; notes.append("tweet-omits-coin")
    return s, notes

def independent_bonus(n_ca, n_ticker=0):
    """Discovery signal: distinct accounts (NOT the coin's own) posting it. CA mentions are unambiguous
    (only this coin) -> full weight. Ticker mentions are broad (tickers get reused across coins) -> soft,
    capped, and flagged when ticker buzz exists with no CA backing (likely a different same-ticker coin).
    Absence is not penalized (a real coin may just be too new to notice)."""
    if n_ca <= 0 and n_ticker <= 0:
        return 0, "lonely-shill(0 independent posters)"
    s, parts = 0, []
    if n_ca >= 4:   s += 3; parts.append(f"{n_ca}+ CA posters")
    elif n_ca >= 2: s += 2; parts.append(f"{n_ca} CA posters")
    elif n_ca == 1: s += 1; parts.append("1 CA poster")
    if n_ticker > 0:
        if n_ticker >= 2: s += 1                      # ticker buzz: soft, capped at +1
        tag = f"{n_ticker} ticker-only"
        if n_ca == 0 and n_ticker >= 3: tag += "(ambiguous: ticker reused, no CA match)"
        parts.append(tag)
    return s, "; ".join(parts)

def fetch_meta(uri, gateways, requests, max_bytes=1_000_000, timeout=6):
    """FIX: streamed fetch with early abort at max_bytes (zip-bomb / OOM guard) + tighter timeout."""
    last = None
    for g in gateways:
        try:
            url = g + uri[7:] if uri.startswith("ipfs://") else uri
            with requests.get(url, timeout=timeout, stream=True) as r:
                buf = b""
                for chunk in r.iter_content(8192):
                    buf += chunk
                    if len(buf) > max_bytes:
                        raise ValueError("oversize-metadata")
                return _json.loads(buf)
        except Exception as e:
            last = e
    raise last or RuntimeError("no-gateway")
