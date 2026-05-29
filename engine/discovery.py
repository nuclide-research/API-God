"""Discovery stage: 'who, besides the creator, is posting this coin's CA?'
xAI x_search FINDS candidate posts -> free syndication resolver gets each author -> count distinct
authors that are NOT the coin's own claimed handle. That count is the independent (hard-to-fake) signal.

Gated on XAI_API_KEY: no key -> returns {'searched': False} so the engine logs a 'would-run' candidate
(for cost accounting) without fabricating data or spending money."""
import os, re, requests
from engine_core import classify

XAI_KEY = os.environ.get("XAI_API_KEY")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.3")

def xai_x_search(query):
    """Returns list of post URLs, or None if not searched (no key)."""
    if not XAI_KEY:
        return None
    try:
        r = requests.post("https://api.x.ai/v1/responses", timeout=60,
                          headers={"Authorization": f"Bearer {XAI_KEY}", "Content-Type": "application/json"},
                          json={"model": XAI_MODEL, "tools": [{"type": "x_search"}],
                                "input": f"Find recent posts mentioning {query}. List the accounts and what they said."})
        r.raise_for_status()
        d = r.json()
    except Exception:
        return []
    cites = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "url_citation" and o.get("url"): cites.append(o["url"])
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
        elif isinstance(o, str) and o.startswith("http"): cites.append(o)
    walk(d)
    return list(dict.fromkeys(cites))

def _post(tid):
    """Returns (author_handle, text) for a tweet id, or None."""
    try:
        r = requests.get(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token=x&lang=en", timeout=10)
        if r.status_code != 200: return None
        d = r.json()
        if not d or d.get("__typename") != "Tweet": return None
        return (d.get("user", {}).get("screen_name"), d.get("text") or "")
    except Exception:
        return None

def discover_independent(mint, own_handle, symbol=None):
    """Search CA (precise) + $ticker (broad). Count distinct non-own posters, split by what they actually
    said: text contains the CA -> ca_poster (unambiguous); text has only the $ticker -> ticker_poster
    (ambiguous, ticker reuse). Returns separate counts so the bonus can weight them differently."""
    query = f'"{mint}"' + (f' OR ${symbol}' if symbol else '')
    urls = xai_x_search(query)
    if urls is None:
        return {"searched": False, "n_ca": 0, "n_ticker": 0, "authors": []}
    own = (own_handle or "").lower()
    tick_re = re.compile(r'\$' + re.escape(symbol) + r'(?![A-Za-z0-9])', re.I) if symbol else None
    ca_authors, tick_authors = set(), set()
    for u in urls:
        kind, _, tid = classify(u)
        if kind != "status" or not tid: continue
        post = _post(tid)
        if not post: continue
        author, text = post
        if not author or author.lower() == own: continue
        if mint and mint in text:
            ca_authors.add(author)
        elif tick_re and tick_re.search(text):
            tick_authors.add(author)
    tick_authors -= ca_authors                          # don't double-count a CA poster as ticker-only
    return {"searched": True, "n_ca": len(ca_authors), "n_ticker": len(tick_authors),
            "authors": sorted(ca_authors | tick_authors)}
