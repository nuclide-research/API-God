#!/usr/bin/env python3
"""xsearch - find who's talking about a topic/coin on X. Two pluggable backends, switch or combine:

  session  FREE. Rides your saved X session and reads X's own SearchTimeline GraphQL off the wire
           (fires on the network response, not the painted DOM -> reliable, and rich: it recovers
           follower count / blue / views / quotes that DOM scraping cannot). Account at risk if you
           hammer it. Setup once: python xsearch.py --login
  xai      CLEAN. xAI x_search finds posts, the free syndication CDN enriches them. ~$0.005/search,
           no account risk. Needs XAI_API_KEY (console.x.ai).
  both     Run both at once, merge, and tag each result by who found it. [SX] = found by BOTH
           backends independently = corroborated. Union = wider coverage.

  python xsearch.py "solana depin"                    # default backend (session)
  python xsearch.py '$GIGA' --backend xai
  python xsearch.py <contract_address> --backend both
  python xsearch.py "solana" --backend both --json --out who.jsonl
"""
import asyncio, json, sys, os, argparse, re
from pathlib import Path
from urllib.parse import quote

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

STATE = Path.home() / ".x-session" / "state.json"
XAI_KEY = os.environ.get("XAI_API_KEY")
XAI_MODEL = os.environ.get("XAI_MODEL", "grok-4.3")


def _rec(id, handle, name, text, time, url, likes, replies, reposts, source,
         followers=0, blue=False, verified=False, views=0, quotes=0, lang="", is_retweet=False):
    return {"id": id, "handle": handle, "name": name, "text": text, "time": time, "url": url,
            "likes": likes, "replies": replies, "reposts": reposts,
            "followers": followers, "blue": blue, "verified": verified,
            "views": views, "quotes": quotes, "lang": lang, "is_retweet": is_retweet,
            "source": [source]}


# ---------- X internal-GraphQL parser (ported from api-god-x) ----------
# Read the SearchTimeline response off the wire instead of scraping the rendered DOM. The response
# fires once X answers, independent of whether the page has painted, so it does not race the render
# the way DOM scraping does, and the JSON carries follower/blue/view/quote that the DOM hides.
def _parse_timeline_tweet(item_content):
    """One TimelineTweet itemContent -> a rich record, or None for ads / non-tweets."""
    try:
        result = item_content["tweet_results"]["result"]
        if result.get("__typename") == "TweetWithVisibilityResults":
            result = result.get("tweet", result)
        legacy = result.get("legacy", {})
        ur = result.get("core", {}).get("user_results", {}).get("result", {})
        u_legacy = ur.get("legacy", {})
        u_core = ur.get("core", {})
        tid = legacy.get("id_str") or result.get("rest_id", "")
        screen = u_core.get("screen_name") or u_legacy.get("screen_name", "")
        if not tid or not screen:
            return None
        return _rec(
            tid, "@" + screen, u_core.get("name") or u_legacy.get("name", ""),
            (legacy.get("full_text") or legacy.get("text") or "").replace("\n", " "),
            legacy.get("created_at", ""), f"https://x.com/{screen}/status/{tid}",
            legacy.get("favorite_count", 0), legacy.get("reply_count", 0),
            legacy.get("retweet_count", 0), "session",
            followers=u_legacy.get("followers_count", 0),
            blue=ur.get("is_blue_verified", False),
            verified=u_legacy.get("verified", False),
            views=int((result.get("views", {}) or {}).get("count") or 0),
            quotes=legacy.get("quote_count", 0),
            lang=legacy.get("lang", ""),
            is_retweet="retweeted_status_result" in legacy,
        )
    except (KeyError, TypeError, ValueError):
        return None


def extract_session(data):
    """Walk a SearchTimeline GraphQL body -> list of rich records."""
    out = []
    try:
        instructions = data["data"]["search_by_raw_query"]["search_timeline"]["timeline"]["instructions"]
    except (KeyError, TypeError):
        return out
    for ins in instructions:
        if ins.get("type") != "TimelineAddEntries":
            continue
        for entry in ins.get("entries", []):
            content = entry.get("content", {})
            if content.get("entryType") == "TimelineTimelineCursor":
                continue
            ic = content.get("itemContent", {})
            if ic.get("itemType") == "TimelineTweet":
                r = _parse_timeline_tweet(ic)
                if r: out.append(r)
            for item in content.get("items", []):                 # module / carousel entries
                ic2 = item.get("item", {}).get("itemContent", {})
                if ic2.get("itemType") == "TimelineTweet":
                    r = _parse_timeline_tweet(ic2)
                    if r: out.append(r)
    return out


# ---------- backend: session (free, rides your X login) ----------
async def find_session(query, tab, pages, delay, headed):
    if not STATE.exists():
        print("[session] no saved session (run: python xsearch.py --login) - skipping", file=sys.stderr)
        return []
    from playwright.async_api import async_playwright
    url = f"https://x.com/search?q={quote(query)}&src=typed_query&f={tab}"
    seen = {}
    bodies = asyncio.Queue()

    async def on_response(resp):
        if "SearchTimeline" in resp.url:
            try: await bodies.put(await resp.json())
            except Exception: pass

    async def drain(first=False):
        # Wait for the SearchTimeline response to arrive (poll, exit early once it does), then parse
        # every queued body. Longer budget on the first pass; X can answer slower than a fixed sleep.
        budget = 6.0 if first else 2.0
        waited = 0.0
        while waited < budget and bodies.empty():
            await asyncio.sleep(0.5); waited += 0.5
        while not bodies.empty():
            for r in extract_session(await bodies.get()):
                if r["id"] not in seen:
                    seen[r["id"]] = r

    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=not headed, args=["--disable-blink-features=AutomationControlled"])
        c = await b.new_context(storage_state=str(STATE))
        p = await c.new_page()
        p.on("response", on_response)
        await p.goto(url, wait_until="domcontentloaded", timeout=30000)
        await drain(first=True)
        for _ in range(max(0, pages - 1)):                 # scrolling triggers X's pagination requests
            before = len(seen)
            await p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await p.wait_for_timeout(delay)
            await drain()
            if len(seen) == before:                        # no new tweets this pass -> end of results
                break
        await b.close()
    return list(seen.values())


# ---------- backend: xai (clean, ~$0.005/search, no account risk) ----------
def find_xai(query):
    if not XAI_KEY:
        print("[xai] XAI_API_KEY not set - skipping", file=sys.stderr)
        return []
    try:
        r = requests.post("https://api.x.ai/v1/responses", timeout=60,
                          headers={"Authorization": f"Bearer {XAI_KEY}", "Content-Type": "application/json"},
                          json={"model": XAI_MODEL, "tools": [{"type": "x_search"}],
                                "input": f"Find recent posts mentioning {query}. List the accounts and what they said."})
        r.raise_for_status(); d = r.json()
    except Exception as e:
        print(f"[xai] search failed: {e}", file=sys.stderr); return []
    urls = []
    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "url_citation" and o.get("url"): urls.append(o["url"])
            for v in o.values(): walk(v)
        elif isinstance(o, list):
            for v in o: walk(v)
        elif isinstance(o, str) and o.startswith("http"): urls.append(o)
    walk(d)
    out = {}
    for u in dict.fromkeys(urls):
        m = re.search(r'/status/(\d+)', u)
        if not m: continue
        tid = m.group(1)
        if tid in out: continue
        try:
            tr = requests.get(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token=x&lang=en", timeout=10)
            if tr.status_code != 200: continue
            t = tr.json()
            if not t or t.get("__typename") != "Tweet": continue
        except Exception:
            continue
        usr = t.get("user", {})
        out[tid] = _rec(tid, "@" + (usr.get("screen_name") or ""), usr.get("name") or "",
                        (t.get("text") or "").replace("\n", " "), t.get("created_at") or "",
                        f"https://x.com/{usr.get('screen_name')}/status/{tid}",
                        t.get("favorite_count") or 0, t.get("conversation_count") or 0, 0, "xai",
                        blue=usr.get("is_blue_verified", False))
    return list(out.values())


def merge(*lists):
    by_id = {}
    for lst in lists:
        for r in lst:
            cur = by_id.get(r["id"])
            if cur:
                cur["source"] = sorted(set(cur["source"]) | set(r["source"]))
                # prefer the record that has engagement metrics
                if r["likes"] + r["reposts"] > cur["likes"] + cur["reposts"]:
                    src = cur["source"]; by_id[r["id"]] = {**r, "source": src}
            else:
                by_id[r["id"]] = dict(r)
    return list(by_id.values())


async def run(args):
    backends = {"session": ["session"], "xai": ["xai"], "both": ["session", "xai"]}[args.backend]
    tasks = []
    if "session" in backends:
        tasks.append(find_session(args.query, args.tab, args.pages, args.delay, args.headed))
    if "xai" in backends:
        tasks.append(asyncio.to_thread(find_xai, args.query))
    results = await asyncio.gather(*tasks)
    posts = merge(*results)
    if not posts:
        sys.exit("no results (check --login for session, or XAI_API_KEY for xai)")
    key = (lambda r: r["likes"] + r["reposts"]) if args.sort == "engagement" else (lambda r: r["time"])
    posts.sort(key=key, reverse=True)

    if args.json or args.out:
        out = open(args.out, "w") if args.out else sys.stdout
        for r in posts: print(json.dumps(r), file=out)
        if args.out: out.close(); print(f"{len(posts)} posts -> {args.out}", file=sys.stderr)
        return

    # readable: distinct accounts, each shown by their top post, tagged by which backend(s) found them
    by_acct = {}
    for r in posts:
        cur = by_acct.get(r["handle"])
        if not cur:
            by_acct[r["handle"]] = dict(r)
        else:
            cur["source"] = sorted(set(cur["source"]) | set(r["source"]))
            if r["likes"] + r["reposts"] > cur["likes"] + cur["reposts"]:
                src = cur["source"]; by_acct[r["handle"]] = {**r, "source": src}
    accts = sorted(by_acct.values(), key=key, reverse=True)
    tag = lambda s: f"[{'S' if 'session' in s else ' '}{'X' if 'xai' in s else ' '}]"
    nS = sum('session' in a['source'] for a in accts); nX = sum('xai' in a['source'] for a in accts)
    nB = sum(len(a['source']) > 1 for a in accts)
    print(f"{len(accts)} accounts for {args.query!r}  (backend={args.backend}: {nS} session, {nX} xai, {nB} in both)\n")
    for r in accts[:args.limit]:
        bl = "*" if r.get("blue") else " "
        print(f"{tag(r['source'])}{bl}{r['handle'][:18]:18} {r.get('followers',0):>8}f "
              f"♥{r['likes']:>6} ↻{r['reposts']:>5} 👁{r.get('views',0):>8}  {r['text'][:64]}")


async def login():
    from playwright.async_api import async_playwright
    STATE.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=False); c = await b.new_context(); p = await c.new_page()
        await p.goto("https://x.com/login")
        print("Log in to X, then press Enter here to save the session...")
        input()
        await c.storage_state(path=str(STATE)); await b.close()
    print(f"session saved -> {STATE}")


def main():
    ap = argparse.ArgumentParser(description="find who's talking about a topic/coin on X")
    ap.add_argument("query", nargs="?", help="topic, $ticker, or contract address")
    ap.add_argument("--backend", choices=["session", "xai", "both"], default="session")
    ap.add_argument("--login", action="store_true", help="one-time: save your X session (for the session backend)")
    ap.add_argument("--tab", choices=["live", "top"], default="live")
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--delay", type=int, default=1300)
    ap.add_argument("--sort", choices=["recent", "engagement"], default="engagement")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--out", help="write JSONL of every post")
    ap.add_argument("--json", action="store_true", help="JSONL to stdout")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()
    if args.login: asyncio.run(login()); return
    if not args.query: ap.error("give a query, or --login first")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
