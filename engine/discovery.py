"""Discovery stage: 'who, besides the creator, is posting this coin's CA?'

FINDS candidate posts -> gets each author + text -> counts distinct authors that are NOT the coin's
own claimed handle. That count is the independent (hard-to-fake) corroboration signal.

Two finders:
  xai      (default, when XAI_API_KEY is set) xAI x_search -> free syndication resolver per post.
  session  (opt-in: DISCOVERY_SESSION=1) subprocess the upgraded ../search/xsearch.py session backend,
           which reads X's SearchTimeline off the wire. FREE, but it drives a real X session in a
           browser, so it is for backtest / replay use, never the live firehose (a browser per coin
           is an X-ban risk). Off by default keeps the live engine keyless and browser-free.

No key and no session opt-in -> returns {'searched': False}: the engine logs a 'would-run' candidate
(for cost accounting) without fabricating data or spending money."""
import os, re, json, sys, subprocess, requests
from engine_core import classify

XAI_KEY = os.environ.get("XAI_API_KEY")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.3")
SESSION_DISCOVERY = os.environ.get("DISCOVERY_SESSION") == "1"     # opt-in free path (off the hot path)
XSEARCH = os.path.join(os.path.dirname(__file__), "..", "search", "xsearch.py")


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


def _xai_pairs(query):
    """xAI finds -> syndication resolves each -> list of (author, text). None if no key."""
    urls = xai_x_search(query)
    if urls is None:
        return None
    pairs = []
    for u in urls:
        kind, _, tid = classify(u)
        if kind != "status" or not tid:
            continue
        post = _post(tid)
        if post and post[0]:
            pairs.append(post)
    return pairs


def _session_pairs(query):
    """Free, opt-in: subprocess the xsearch session backend -> list of (author, text).
    Runs out-of-process so the engine carries no browser of its own. None on failure."""
    try:
        r = subprocess.run([sys.executable, XSEARCH, query, "--backend", "session", "--pages", "3", "--json"],
                           capture_output=True, text=True, timeout=150)
    except Exception:
        return None
    pairs = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        h = (d.get("handle") or "").lstrip("@")
        if h:
            pairs.append((h, d.get("text") or ""))
    return pairs


def discover_independent(mint, own_handle, symbol=None):
    """Search CA (precise) + $ticker (broad). Count distinct non-own posters, split by what they
    actually said: text contains the CA -> ca_poster (unambiguous); text has only the $ticker ->
    ticker_poster (ambiguous, ticker reuse). Returns separate counts so the bonus can weight them
    differently. Backend: xAI when keyed, else the session subprocess when DISCOVERY_SESSION=1, else
    none (searched: False)."""
    query = f'"{mint}"' + (f' OR ${symbol}' if symbol else '')
    if XAI_KEY:
        pairs = _xai_pairs(query)
    elif SESSION_DISCOVERY:
        pairs = _session_pairs(query)
    else:
        pairs = None
    if pairs is None:
        return {"searched": False, "n_ca": 0, "n_ticker": 0, "authors": []}
    own = (own_handle or "").lower()
    tick_re = re.compile(r'\$' + re.escape(symbol) + r'(?![A-Za-z0-9])', re.I) if symbol else None
    ca_authors, tick_authors = set(), set()
    for author, text in pairs:
        if not author or author.lower() == own:
            continue
        if mint and mint in text:
            ca_authors.add(author)
        elif tick_re and tick_re.search(text):
            tick_authors.add(author)
    tick_authors -= ca_authors                          # don't double-count a CA poster as ticker-only
    return {"searched": True, "n_ca": len(ca_authors), "n_ticker": len(tick_authors),
            "authors": sorted(ca_authors | tick_authors)}
