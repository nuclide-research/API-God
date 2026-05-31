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


def _tweet_ts(created_at):
    """X created_at ('Wed Oct 10 20:19:24 +0000 2018') -> epoch seconds, for correct recency sort.
    Sorting the raw string sorts by day-name, not time, so a tracker must parse it."""
    import email.utils
    try:
        return email.utils.parsedate_to_datetime(created_at).timestamp()
    except Exception:
        return 0.0


def extract_user_timeline(data):
    """Walk a UserTweets / UserTweetsAndReplies GraphQL body -> list of rich records. The tweet shape
    is identical to search; only the outer timeline path differs (user.result.timeline_v2)."""
    out = []
    ins_list = None
    for path in (("user", "result", "timeline_v2"), ("user", "result", "timeline")):
        node = data.get("data", {})
        for k in path:
            node = node.get(k, {}) if isinstance(node, dict) else {}
        try:
            ins_list = node["timeline"]["instructions"]
            break
        except (KeyError, TypeError):
            continue
    if not ins_list:
        return out
    for ins in ins_list:
        if ins.get("type") != "TimelineAddEntries":
            continue
        for entry in ins.get("entries", []):
            content = entry.get("content", {})
            ic = content.get("itemContent", {})
            if ic.get("itemType") == "TimelineTweet":
                r = _parse_timeline_tweet(ic)
                if r: out.append(r)
            for item in content.get("items", []):                 # self-thread / conversation modules
                ic2 = item.get("item", {}).get("itemContent", {})
                if ic2.get("itemType") == "TimelineTweet":
                    r = _parse_timeline_tweet(ic2)
                    if r: out.append(r)
    return out


def extract_list_timeline(data):
    """Walk a ListLatestTweetsTimeline body -> list of rich records. Multi-author by nature: one call
    returns every member's recent tweets (proven 22 authors / 1 call). Path is list.tweets_timeline."""
    out = []
    ins_list = None
    for path in (("list", "tweets_timeline"), ("list", "timeline_response", "timeline")):
        node = data.get("data", {})
        for k in path:
            node = node.get(k, {}) if isinstance(node, dict) else {}
        try:
            ins_list = node["timeline"]["instructions"]
            break
        except (KeyError, TypeError):
            continue
    if not ins_list:
        return out
    for ins in ins_list:
        if ins.get("type") != "TimelineAddEntries":
            continue
        for entry in ins.get("entries", []):
            content = entry.get("content", {})
            ic = content.get("itemContent", {})
            if ic.get("itemType") == "TimelineTweet":
                r = _parse_timeline_tweet(ic)
                if r: out.append(r)
            for item in content.get("items", []):
                ic2 = item.get("item", {}).get("itemContent", {})
                if ic2.get("itemType") == "TimelineTweet":
                    r = _parse_timeline_tweet(ic2)
                    if r: out.append(r)
    return out


def extract_batch(data):
    """Parse a TweetResultsByRestIds body (data.tweetResult is a flat array) -> records. Unlike the
    keyless CDN, this path carries retweet_count."""
    out = []
    for e in (data.get("data", {}).get("tweetResult") or []):
        r = _parse_timeline_tweet({"tweet_results": e})
        if r:
            r["source"] = ["batch"]
            out.append(r)
    return out


def _drain_budget(first, delay):
    """Seconds to wait for a SearchTimeline response. The non-first budget tracks the scroll delay
    so a slow response is not cut off and pagination is not truncated (finding #2)."""
    return 6.0 if first else max(2.0, delay / 1000.0 + 1.5)


def _probe_report(log):
    """log = [(http_status, seconds_since_start), ...] for each SearchTimeline response. The first
    non-200 is where X cut us off; everything before it is how far we got before the rate-limit wall."""
    statuses = [s for s, _ in log]
    limit_idx = next((i for i, s in enumerate(statuses) if s != 200), None)
    ok = limit_idx if limit_idx is not None else sum(1 for s in statuses if s == 200)
    limit_status = statuses[limit_idx] if limit_idx is not None else None
    limit_at_s = round(log[limit_idx][1], 1) if limit_idx is not None else (round(log[-1][1], 1) if log else 0.0)
    rate = round(len(log) / (log[-1][1] / 60), 1) if log and log[-1][1] > 0 else 0.0
    return {"requests": len(log), "ok_before_limit": ok, "limit_status": limit_status,
            "limit_at_s": limit_at_s, "rate_per_min": rate}


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
        budget = _drain_budget(first, delay)
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


# ---------- backend: track (free, rides session, SEPARATE rate-limit bucket from search) ----------
async def find_track(handle, replies, pages, delay, headed):
    """Track one account via its own timeline (UserTweets / UserTweetsAndReplies). This is a different
    GraphQL endpoint than search, so it has its own rate-limit budget: it keeps serving 200 while
    SearchTimeline is 429'd (proven 2026-05-30), and it is the real timeline, lower latency than search."""
    if not STATE.exists():
        print("[track] no saved session (run: python xsearch.py --login) - skipping", file=sys.stderr)
        return []
    from playwright.async_api import async_playwright
    handle = handle.lstrip("@")
    url = f"https://x.com/{handle}" + ("/with_replies" if replies else "")
    marker = "UserTweetsAndReplies" if replies else "UserTweets"
    seen = {}
    bodies = asyncio.Queue()

    async def on_response(resp):
        if marker in resp.url:
            try: await bodies.put(await resp.json())
            except Exception: pass

    async def drain(first=False):
        budget = _drain_budget(first, delay)
        waited = 0.0
        while waited < budget and bodies.empty():
            await asyncio.sleep(0.5); waited += 0.5
        while not bodies.empty():
            for r in extract_user_timeline(await bodies.get()):
                if r["id"] not in seen:
                    seen[r["id"]] = r

    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=not headed, args=["--disable-blink-features=AutomationControlled"])
        c = await b.new_context(storage_state=str(STATE))
        p = await c.new_page()
        p.on("response", on_response)
        await p.goto(url, wait_until="domcontentloaded", timeout=30000)
        await drain(first=True)
        for _ in range(max(0, pages - 1)):
            before = len(seen)
            await p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await p.wait_for_timeout(delay)
            await drain()
            if len(seen) == before:
                break
        await b.close()
    return list(seen.values())


# ---------- backend: list (free, rides session, ONE call multiplexes every member) ----------
async def find_list(list_id, pages, delay, headed):
    """Track a whole List via ListLatestTweetsTimeline. ONE call returns all members' recent tweets,
    on the list endpoint's own rate-limit bucket (proven 22 distinct authors in a single response)."""
    if not STATE.exists():
        print("[list] no saved session (run: python xsearch.py --login) - skipping", file=sys.stderr)
        return []
    from playwright.async_api import async_playwright
    list_id = str(list_id).strip()
    url = f"https://x.com/i/lists/{list_id}"
    seen = {}
    bodies = asyncio.Queue()

    async def on_response(resp):
        if "ListLatestTweetsTimeline" in resp.url:
            try: await bodies.put(await resp.json())
            except Exception: pass

    async def drain(first=False):
        budget = _drain_budget(first, delay)
        waited = 0.0
        while waited < budget and bodies.empty():
            await asyncio.sleep(0.5); waited += 0.5
        while not bodies.empty():
            for r in extract_list_timeline(await bodies.get()):
                if r["id"] not in seen:
                    seen[r["id"]] = r

    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=not headed, args=["--disable-blink-features=AutomationControlled"])
        c = await b.new_context(storage_state=str(STATE))
        p = await c.new_page()
        p.on("response", on_response)
        await p.goto(url, wait_until="domcontentloaded", timeout=30000)
        await drain(first=True)
        for _ in range(max(0, pages - 1)):
            before = len(seen)
            await p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await p.wait_for_timeout(delay)
            await drain()
            if len(seen) == before:
                break
        await b.close()
    return list(seen.values())


# ---------- rate-limit probe (burner-account only) ----------
async def probe_session(url, marker, max_req, delay, reload=False):
    """Hammer one endpoint up to max_req times, log every `marker` response's HTTP status with a
    timestamp, and stop at the first non-200 (where X cuts us off). Scroll mode paginates one view;
    reload mode re-navigates each time, needed when one view runs out of pagination before the rate
    limit (a profile timeline does). Run on a throwaway account: finding the limit means tripping it."""
    if not STATE.exists():
        sys.exit("[probe] no saved session (run: python xsearch.py --login)")
    from playwright.async_api import async_playwright
    import time as _time
    log = []
    ratelimit = {}
    start = _time.monotonic()
    hit = asyncio.Event()

    async def on_response(resp):
        if marker in resp.url:
            log.append((resp.status, _time.monotonic() - start))
            try:                                                # X reports the exact quota in headers
                h = resp.headers
                if h.get("x-rate-limit-limit"):
                    ratelimit.clear()
                    ratelimit.update(limit=h.get("x-rate-limit-limit"),
                                     remaining=h.get("x-rate-limit-remaining"),
                                     reset=h.get("x-rate-limit-reset"))
            except Exception:
                pass
            if resp.status != 200:                              # the wall: stop, do not keep hammering
                hit.set()

    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        c = await b.new_context(storage_state=str(STATE))
        p = await c.new_page()
        p.on("response", on_response)
        await p.goto(url, wait_until="domcontentloaded", timeout=30000)
        await p.wait_for_timeout(1500)
        for _ in range(max_req):
            if hit.is_set():
                break
            if reload:                                          # fresh request each time (beats pagination depth)
                before = len(log)
                try: await p.goto(url, wait_until="commit", timeout=30000)
                except Exception: pass
                for _ in range(16):                             # wait for this reload's response, do not abort it
                    if len(log) > before or hit.is_set():
                        break
                    await p.wait_for_timeout(250)
            else:
                await p.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await p.wait_for_timeout(delay)
        await b.close()
    return log, ratelimit


# ---------- keyless hydration (no account, no per-token wall, ~146/min on one IP) ----------
def _cdn_resolve(tid, source="hydrate"):
    """Resolve one tweet id -> a rich record via the syndication CDN (keyless). None on miss.
    The hydration primitive shared by the xai backend and --hydrate."""
    try:
        tr = requests.get(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token=x&lang=en", timeout=10)
        if tr.status_code != 200: return None
        t = tr.json()
        if not t or t.get("__typename") != "Tweet": return None
    except Exception:
        return None
    usr = t.get("user", {})
    return _rec(str(tid), "@" + (usr.get("screen_name") or ""), usr.get("name") or "",
                (t.get("text") or "").replace("\n", " "), t.get("created_at") or "",
                f"https://x.com/{usr.get('screen_name')}/status/{tid}",
                t.get("favorite_count") or 0, t.get("conversation_count") or 0, 0, source,
                blue=usr.get("is_blue_verified", False))


def find_hydrate(ids, delay_ms=450):
    """Keyless bulk-hydrate: tweet ids -> full records via the CDN. No account, no per-token wall;
    paced to stay under the ~146/min IP ceiling. The high-yield half of the pipeline."""
    import time as _t
    out = []
    n = len(ids)
    for i, tid in enumerate(ids):
        tid = str(tid).strip()
        if not tid.isdigit():
            continue
        r = _cdn_resolve(tid, "hydrate")
        if r: out.append(r)
        if delay_ms and i + 1 < n:
            _t.sleep(delay_ms / 1000.0)
    return out


async def find_batch(ids):
    """Authed batch resolve via TweetResultsByRestIds: up to ~100 ids in ONE call, WITH retweet_count
    (which the keyless CDN drops). Rides the session: captures the client's features from a live
    request, then calls the batch endpoint from the page (bearer + ct0, no x-client-transaction-id)."""
    if not STATE.exists():
        print("[batch] no saved session (run: python xsearch.py --login) - skipping", file=sys.stderr)
        return []
    from playwright.async_api import async_playwright
    import urllib.parse as _up
    ids = [str(i).strip() for i in ids if str(i).strip().isdigit()]
    if not ids:
        return []
    cap = {}

    def on_request(req):
        if "/graphql/" in req.url and "features" not in cap:
            qs = _up.parse_qs(_up.urlparse(req.url).query)
            if "features" in qs:
                cap["features"] = qs["features"][0]
                cap["fieldToggles"] = qs.get("fieldToggles", ["{}"])[0]
                cap["bearer"] = req.headers.get("authorization")

    async with async_playwright() as pw:                       # browser only to capture client features
        b = await pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        p = await (await b.new_context(storage_state=str(STATE))).new_page()
        p.on("request", on_request)
        await p.goto("https://x.com/home", wait_until="domcontentloaded", timeout=30000)
        for _ in range(20):
            if cap.get("features") and cap.get("bearer"):
                break
            await p.wait_for_timeout(500)
        await b.close()
    if not (cap.get("features") and cap.get("bearer")):
        print("[batch] could not capture client features/bearer", file=sys.stderr)
        return []

    # the call itself: plain requests with the saved cookies (no transaction-id needed)
    st = json.loads(STATE.read_text())
    cookies = {c["name"]: c["value"] for c in st.get("cookies", []) if c.get("name") and c.get("value")}
    hdr = {"authorization": cap["bearer"], "x-csrf-token": cookies.get("ct0", ""),
           "x-twitter-active-user": "yes", "x-twitter-auth-type": "OAuth2Session",
           "content-type": "application/json", "x-twitter-client-language": "en",
           "user-agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")}
    out = []
    QID = "ZrFhyt8DYdkK3IY6_Le22g"
    for i in range(0, len(ids), 100):                          # TweetResultsByRestIds takes ~100 ids/call
        variables = json.dumps({"tweetIds": ids[i:i + 100], "includePromotedContent": False,
                                "withBirdwatchNotes": False, "withVoice": True, "withCommunity": True})
        try:
            r = requests.get(f"https://x.com/i/api/graphql/{QID}/TweetResultsByRestIds",
                             params={"variables": variables, "features": cap["features"], "fieldToggles": cap["fieldToggles"]},
                             headers=hdr, cookies=cookies, timeout=20)
            if r.status_code == 200:
                out.extend(extract_batch(r.json()))
            else:
                print(f"[batch] HTTP {r.status_code}: {r.text[:120]}", file=sys.stderr)
        except Exception as e:
            print(f"[batch] {e}", file=sys.stderr)
    return out


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
        r = _cdn_resolve(tid, "xai")
        if r: out[tid] = r
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


def _stdin_ids():
    """Read tweet ids from stdin: bare ids, status urls, or JSONL lines with an 'id' field."""
    ids = []
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line[0] in "{[":
            try: ids.append(str(json.loads(line)["id"]))
            except Exception: pass
        else:
            mm = re.search(r'(\d{8,})', line)
            if mm: ids.append(mm.group(1))
    return ids


def _emit(posts, args):
    """Shared output: JSONL (--json/--out), or readable. Track mode lists tweets newest/top first;
    search mode lists the distinct accounts talking, tagged by which backend(s) found each."""
    if not posts:
        sys.exit("no results (check --login for session, or XAI_API_KEY for xai)")
    key = (lambda r: r["likes"] + r["reposts"]) if args.sort == "engagement" else (lambda r: _tweet_ts(r["time"]))
    posts.sort(key=key, reverse=True)
    if args.json or args.out:
        out = open(args.out, "w") if args.out else sys.stdout
        for r in posts: print(json.dumps(r), file=out)
        if args.out: out.close(); print(f"{len(posts)} posts -> {args.out}", file=sys.stderr)
        return
    if getattr(args, "list", None) or getattr(args, "hydrate", False) or getattr(args, "batch", False):  # multi-author stream
        label = f"list {args.list}" if getattr(args, "list", None) else ("batch" if getattr(args, "batch", False) else "hydrated")
        print(f"{len(posts)} tweets ({label})\n")
        for r in posts[:args.limit]:
            print(f"{r['time'][:16]}  {r['handle'][:16]:16} ♥{r['likes']:>6} ↻{r['reposts']:>5}  {r['text'][:58]}")
        return
    if getattr(args, "track", False):                          # track: a tweet stream, top/newest first
        print(f"{len(posts)} tweets from @{args.query.lstrip('@')}\n")
        for r in posts[:args.limit]:
            print(f"{r['time'][:16]}  ♥{r['likes']:>6} ↻{r['reposts']:>5} 👁{r.get('views',0):>8}  {r['text'][:80]}")
        return
    # search: distinct accounts, each shown by their top post, tagged by which backend(s) found them
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


async def run(args):
    backends = {"session": ["session"], "xai": ["xai"], "both": ["session", "xai"]}[args.backend]
    tasks = []
    if "session" in backends:
        tasks.append(find_session(args.query, args.tab, args.pages, args.delay, args.headed))
    if "xai" in backends:
        tasks.append(asyncio.to_thread(find_xai, args.query))
    results = await asyncio.gather(*tasks)
    posts = merge(*results)
    _emit(posts, args)


def _logged_in(cookies):
    """True once X has set the login cookie. auth_token is the httpOnly proof of a real session; a
    guest/logged-out context never has it (finding 2026-05-30: --login silently saved guest sessions)."""
    return any(ck.get("name") == "auth_token" for ck in cookies)


async def login():
    from playwright.async_api import async_playwright
    STATE.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=False)
        c = await b.new_context(); p = await c.new_page()
        await p.goto("https://x.com/login")
        print("Log in to X in the window. Waiting for login (auth_token)...", file=sys.stderr)
        for _ in range(300):                                   # poll up to ~5 min, no Enter needed
            if _logged_in(await c.cookies()):
                break
            await asyncio.sleep(1)
        else:
            await b.close(); sys.exit("login not detected (no auth_token after 5 min) - nothing saved")
        await c.storage_state(path=str(STATE)); await b.close()
    print(f"session saved -> {STATE}", file=sys.stderr)


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
    ap.add_argument("--track", action="store_true",
                    help="track a @handle via its timeline (UserTweets) - a SEPARATE rate-limit bucket from search")
    ap.add_argument("--replies", action="store_true", help="track: include @-replies (UserTweetsAndReplies)")
    ap.add_argument("--probe", action="store_true",
                    help="rate-limit probe: read X's x-rate-limit-* headers (the live quota) and hammer to the 429 cutoff; works on search, --track, or --list")
    ap.add_argument("--max-req", type=int, default=120, help="probe: max requests to fire")
    ap.add_argument("--reload", action="store_true",
                    help="probe: re-navigate each iteration instead of scrolling (for views that run out of pagination, e.g. a profile timeline)")
    ap.add_argument("--list", metavar="LISTID",
                    help="track a whole List by id via ListLatestTweetsTimeline (one call = every member, its own bucket)")
    ap.add_argument("--hydrate", action="store_true",
                    help="keyless bulk-hydrate: read tweet ids (or JSONL) on stdin, resolve full tweets via the CDN (no account)")
    ap.add_argument("--batch", action="store_true",
                    help="authed batch resolve: read tweet ids on stdin, resolve via TweetResultsByRestIds (carries reposts the CDN drops)")
    args = ap.parse_args()
    if args.login: asyncio.run(login()); return
    if args.hydrate:
        _emit(find_hydrate(_stdin_ids(), 450), args)
        return
    if args.batch:
        _emit(asyncio.run(find_batch(_stdin_ids())), args)
        return
    if args.list and not args.probe:
        _emit(asyncio.run(find_list(args.list, args.pages, args.delay, args.headed)), args)
        return
    if not args.query and not (args.probe and args.list): ap.error("give a query/handle, --list ID, --hydrate (stdin), or --login first")
    if args.probe:
        from collections import Counter
        if args.track:
            h = args.query.lstrip("@")
            url = f"https://x.com/{h}" + ("/with_replies" if args.replies else "")
            marker = "UserTweetsAndReplies" if args.replies else "UserTweets"
        elif args.list:
            url = f"https://x.com/i/lists/{args.list}"          # ListLatestTweetsTimeline = its own bucket
            marker = "ListLatestTweetsTimeline"
        else:
            url = f"https://x.com/search?q={quote(args.query)}&src=typed_query&f={args.tab}"
            marker = "SearchTimeline"
        log, rl = asyncio.run(probe_session(url, marker, args.max_req, args.delay, args.reload))
        report = {"endpoint": marker, **_probe_report(log)}
        if rl:                                                  # X's own quota, read not exhausted
            report["rate_limit_headers"] = rl
        print(json.dumps(report, indent=2))
        print("status counts:", dict(Counter(s for s, _ in log)), file=sys.stderr)
        return
    if args.track:
        posts = asyncio.run(find_track(args.query, args.replies, args.pages, args.delay, args.headed))
        _emit(posts, args)
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
