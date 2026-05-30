"""Route 2: xAI x_search FINDS candidate posts -> free syndication resolver ENRICHES them.

FIND half (xAI): needs XAI_API_KEY + a re-check of docs.x.ai (this API changes fast - the old
  search_parameters endpoint was already retired). Returns model prose + a list of post URLs.
ENRICH half: the same free, key-less syndication resolver we've used all session. Proven.

Run with NO key -> uses mock citations (real tweet IDs) so the full flow produces real output.
Run with XAI_API_KEY set -> goes live against xAI.
"""
import os, json, requests
from engine_core import classify, resolve_tweet   # reuse the shared classifier + resolver (#11)

XAI_KEY = os.environ.get("XAI_API_KEY")

def xai_x_search(query, handles=None, since=None, model="grok-4.3"):
    """FIND. Returns (prose, [post_urls]). Shape per docs.x.ai x_search tool on /v1/responses."""
    if not XAI_KEY:
        return ("[mock] accounts discussing the token", [        # real IDs from this session
            "https://x.com/kunalbhatia91/status/2060227680424096039",
            "https://x.com/i/status/2060226272668815460",
        ])
    tool = {"type": "x_search"}
    if handles: tool["allowed_x_handles"] = handles[:20]         # bounded watchlist (<=20)
    if since:   tool["from_date"] = since                        # ISO YYYY-MM-DD
    body = {"model": model, "tools": [tool],
            "input": f"Find recent posts about {query}. List the accounts and what they said."}
    r = requests.post("https://api.x.ai/v1/responses", timeout=60,
                      headers={"Authorization": f"Bearer {XAI_KEY}", "Content-Type": "application/json"},
                      json=body)
    r.raise_for_status()
    d = r.json()
    cites = []                                                   # tolerant: URLs live in citations + inline url_citation objs
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "url_citation" and o.get("url"): cites.append(o["url"])
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
        elif isinstance(o, str) and o.startswith("http"): cites.append(o)
    walk(d)
    return (str(d.get("output", ""))[:200], list(dict.fromkeys(cites)))

def status_ids(urls):
    """Keep only resolvable STATUS links, extract tweet IDs (reuses engine_core.classify)."""
    ids = []
    for u in urls:
        kind, _, tid = classify(u)
        if kind == "status" and tid: ids.append(tid)
    return list(dict.fromkeys(ids))

def resolve(tid):
    """ENRICH (free, no key) via the shared resolver (#11). (No follower/view counts in this payload.)"""
    st, d = resolve_tweet(tid)
    if st != "ok":
        return None
    u = d.get("user", {})
    return {"author": u.get("screen_name"), "name": u.get("name"), "blue": u.get("is_blue_verified"),
            "created_at": d.get("created_at"), "likes": d.get("favorite_count"),
            "replies": d.get("conversation_count"), "text": (d.get("text") or "")[:150]}

if __name__ == "__main__":
    query = "$SIA self-improving AI token"          # narrow to a ticker/topic, NOT bare "Solana"
    prose, urls = xai_x_search(query)
    live = "LIVE" if XAI_KEY else "MOCK (set XAI_API_KEY to go live)"
    print(f"[1] FIND via xAI x_search  ({live}):  query={query!r}")
    print(f"    -> {len(urls)} candidate post URLs returned")
    ids = status_ids(urls)
    print(f"    -> {len(ids)} are resolvable status links\n")
    print("[2] ENRICH each via the free syndication resolver:")
    seen, recs = set(), []
    for tid in ids:
        rec = resolve(tid)
        if rec and rec["author"] not in seen:                   # dedup by author
            seen.add(rec["author"]); recs.append(rec)
            print(f"    @{rec['author']:16} blue={rec['blue']!s:5} {rec['likes']:>4} likes  {rec['text']!r}")
    print(f"\n[3] {len(recs)} structured records (deduped). "
          f"Cost: 1 xAI call (~$0.005) + {len(ids)} free resolves.")
