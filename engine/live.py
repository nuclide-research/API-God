"""Live engine (engine_core logic) + resilience fixes: bounded WS reconnect w/ backoff,
SAFE_MODE with decay/auto-exit, boot watchdog (soft), tombstone penalty."""
import asyncio, json, time, sys, threading
from collections import deque, defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import websockets, requests
from engine_core import norm_name, cashtag_hit, classify, zone_of, score_resolved, fetch_meta, independent_bonus
from discovery import discover_independent
from outcomes import record

BUDGET = 540; DEDUP_S = 300
RAW = "/tmp/mints2.jsonl"; SUMMARY = "/tmp/run2_summary.txt"
GATEWAYS = ["https://pump.mypinata.cloud/ipfs/", "https://ipfs.io/ipfs/", "https://cloudflare-ipfs.com/ipfs/"]

lock = threading.Lock()
devbuf = deque(maxlen=200); events = []; survivors = []
gaps = Counter(); zone_count = Counter()
by_status = defaultdict(set); by_creator = defaultdict(list); by_author = defaultdict(set)
last_name = {}
SAFE = [False]; rlog = deque(maxlen=12); ok_streak = [0]

def resolve_tweet(tid):
    try: r = requests.get(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token=x&lang=en", timeout=8)
    except Exception: return ("neterr", None)
    if r.status_code == 404: return ("404", None)
    try: d = r.json()
    except Exception: return ("notjson", None)
    if not d: return ("empty", None)
    if d.get("__typename") == "TweetTombstone": return ("tombstone", None)
    return ("ok", d)

def note_resolve(is_ok):
    with lock:
        rlog.append(is_ok)
        if is_ok:
            ok_streak[0] += 1
            if SAFE[0] and ok_streak[0] >= 5:        # FIX: decay/auto-exit SAFE_MODE on recovery
                SAFE[0] = False; print("  ~~ SAFE_MODE OFF (resolver recovered)")
        else:
            ok_streak[0] = 0
            if not SAFE[0] and len(rlog) >= 6 and sum(1 for x in rlog if not x) >= 4:  # 4/6 fails
                SAFE[0] = True; print("  !! SAFE_MODE ON (resolver failing)")

def no_signal(rec, zone, reason):
    with lock: gaps[reason] += 1
    rec["notes"].append(reason.replace("_", "-"))
    if zone == "red": rec["notes"].append("big-buy-no-socials(review)")
    return _emit(rec)

def process(ev, zone):
    rec = {"mint": ev.get("mint"), "name": (ev.get("name") or "")[:24], "symbol": ev.get("symbol") or "",
           "dev_buy": ev.get("solAmount") or 0, "zone": zone, "creator": ev.get("traderPublicKey"),
           "score": 0, "notes": []}
    uri = ev.get("uri")
    if not uri: return no_signal(rec, zone, "no_uri")
    try: meta = fetch_meta(uri, GATEWAYS, requests)
    except Exception: return no_signal(rec, zone, "ipfs_err")
    tw = (meta.get("twitter") or "").strip()
    if not tw: return no_signal(rec, zone, "no_twitter")
    kind, handle, tid = classify(tw); rec["handle"] = handle
    if kind == "profile":
        with lock: gaps["profile_only"] += 1
        rec["notes"].append(f"profile @{handle}(+0)"); return _emit(rec)
    if kind != "status" or not tid:
        with lock: gaps[f"link_{kind}"] += 1
        rec["notes"].append(f"link={kind}"); return _emit(rec)
    if SAFE[0]:
        with lock: gaps["safe_mode_skip"] += 1
        rec["notes"].append("SAFE_MODE-skip"); return _emit(rec)
    status, d = resolve_tweet(tid); note_resolve(status == "ok")
    if status == "tombstone":
        with lock: gaps["tweet_tombstone"] += 1
        rec["score"] -= 1; rec["notes"].append("tombstone-pulled(suspect)"); return _emit(rec)
    if status != "ok": return no_signal(rec, zone, f"tweet_{status}")
    with lock:
        gaps["status_resolved"] += 1; by_status[tid].add(rec["mint"])
        by_author[d.get("user", {}).get("screen_name")].add(rec["mint"])
    author = d.get("user", {}).get("screen_name"); blue = d.get("user", {}).get("is_blue_verified")
    text = d.get("text") or ""
    refs = cashtag_hit(rec["symbol"], text) or (rec["mint"] and rec["mint"][:8] in text)
    mism = bool(handle and author and handle.lower() != author.lower())
    s, notes = score_resolved(zone, refs, blue, mism)
    rec["score"] += s; rec["notes"].extend(notes)
    rec.update({"author": author, "blue": blue, "verified": refs and not mism})
    disc = discover_independent(rec["mint"], handle, rec["symbol"])      # DISCOVERY: independent corroboration
    if disc["searched"]:
        b, dn = independent_bonus(disc["n_ca"], disc["n_ticker"]); rec["score"] += b
        rec["notes"].append(dn); rec["independent"] = disc["n_ca"]
    elif not SAFE[0]:
        with lock: gaps["discovery_would_run"] += 1
    return _emit(rec)

def _emit(rec):
    with lock: survivors.append(rec)

async def stream_once(ws, ex, deadline):
    await ws.send(json.dumps({"method": "subscribeNewToken"}))
    while True:
        rem = deadline - time.monotonic()
        if rem <= 0: return "done"
        try: msg = await asyncio.wait_for(ws.recv(), timeout=rem)
        except asyncio.TimeoutError: return "done"
        try: d = json.loads(msg)
        except Exception:
            with lock: gaps["malformed_frame"] += 1
            continue
        if "message" in d: print("ack:", d["message"]); continue
        events.append(d)
        with open(RAW, "a") as f: f.write(json.dumps(d) + "\n")
        buy = d.get("solAmount") or 0; nm = norm_name(d.get("name")); now = time.monotonic()
        with lock: devbuf.append(buy)
        if nm and nm in last_name and now - last_name[nm] < DEDUP_S:
            with lock: gaps["dedup_name"] += 1
            last_name[nm] = now; continue
        last_name[nm] = now
        z = zone_of(buy, list(devbuf))
        with lock: zone_count[z] += 1
        if z == "green":
            with lock: gaps["suppressed_green"] += 1
            continue
        ex.submit(process, d, z)

async def main():
    open(RAW, "w").close()
    try:
        b, _ = await asyncio.wait_for(asyncio.to_thread(resolve_tweet, 20), timeout=4)  # FIX: soft boot watchdog
        print("watchdog OK" if b == "ok" else f"watchdog soft-fail ({b}); continuing")
    except Exception:
        print("watchdog timeout; continuing (no hard block)")
    ex = ThreadPoolExecutor(max_workers=6)
    start = time.monotonic(); deadline = start + BUDGET
    backoff = 5; attempts = 0
    try:
        while time.monotonic() < deadline and attempts < 4:   # FIX: bounded reconnect w/ backoff (no ban-storm)
            try:
                async with websockets.connect("wss://pumpportal.fun/api/data", max_size=2**20) as ws:
                    print(f"streaming (attempt {attempts+1}), SPC zones, SAFE-decay ...")
                    backoff = 5
                    if await stream_once(ws, ex, deadline) == "done": break
            except Exception as e:
                attempts += 1
                wait = min(backoff, deadline - time.monotonic())
                if wait <= 0: break
                print(f"[ws drop: {type(e).__name__}; backoff {backoff}s]")
                await asyncio.sleep(wait); backoff = min(backoff * 2, 60)
    finally:
        ex.shutdown(wait=True)
    summarize()

def summarize():
    n = len(events); buf = sorted(devbuf)
    p80 = buf[int(.8*len(buf))] if buf else 0; p95 = buf[int(.95*len(buf))] if buf else 0
    out = [f"ENGINE v3-live | {n} mints in {BUDGET}s | SAFE={'YES' if SAFE[0] else 'no'} | malformed={gaps['malformed_frame']}",
           f"SPC p80={p80:.2f} p95={p95:.2f} | zones green {zone_count['green']} amber {zone_count['amber']} red {zone_count['red']} | dedup {gaps['dedup_name']}",
           f"outcomes: {dict((k,v) for k,v in sorted(gaps.items()) if k not in ('suppressed_green','dedup_name'))}"]
    wsize = {c: len(m) for c, m in by_creator.items()}; asize = {a: len(m) for a, m in by_author.items() if a}
    for s in survivors:
        wc = wsize.get(s["creator"], 1); ac = asize.get(s.get("author"), 1)
        if wc > 1 or ac > 1:
            s["score"] -= 2 + (1 if wc > 1 and ac > 1 else 0); s["serial"] = max(wc, ac)
            s["notes"].append(f"cluster w{wc}/a{ac}")
    for s in survivors:                                  # outcome-ledger record: AFTER clustering (serial known), single-threaded (no race)
        try:
            record({"mint": s.get("mint"), "creator": s.get("creator"), "score": s.get("score"),
                    "features": {"zone": s.get("zone"), "verified": s.get("verified"),
                                 "independent": s.get("independent"), "serial": s.get("serial")}})
        except Exception: pass
    ver = [s for s in survivors if s.get("verified")]
    out.append(f"verified: {len(ver)} | serial wallets: {len([c for c,m in by_creator.items() if len(m)>1])}")
    out.append("top:")
    for s in sorted(survivors, key=lambda s: s.get("score", 0), reverse=True)[:10]:
        out.append(f"  [{s.get('score'):+d}] {s['zone']:5} {s['name'][:20]:20} @{s.get('author')} buy={round(s.get('dev_buy'),2)} ver={s.get('verified')}")
    txt = "\n".join(out); print("\n" + txt); open(SUMMARY, "w").write(txt)

asyncio.run(main())
