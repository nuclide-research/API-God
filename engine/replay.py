"""Replay engine over captured mints (no PumpPortal connection). Uses shared engine_core logic.
Tests SPC zoning + serial detection + scoring on the real dataset; enrich/resolve hit IPFS + syndication only."""
import json, sys, threading
from collections import deque, defaultdict, Counter
from concurrent.futures import ThreadPoolExecutor
import requests
from engine_core import norm_name, cashtag_hit, classify, zone_of, score_resolved, fetch_meta, independent_bonus
from discovery import discover_independent

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mints.jsonl"
ROLL = 200; DEDUP_IDX = 50
GATEWAYS = ["https://pump.mypinata.cloud/ipfs/", "https://ipfs.io/ipfs/", "https://cloudflare-ipfs.com/ipfs/"]

lock = threading.Lock()
devbuf = deque(maxlen=ROLL)
survivors = []; gaps = Counter(); zone_count = Counter()
by_status = defaultdict(set); by_creator = defaultdict(list); by_author = defaultdict(set)
last_name = {}

def resolve_tweet(tid):
    try: r = requests.get(f"https://cdn.syndication.twimg.com/tweet-result?id={tid}&token=x&lang=en", timeout=10)
    except Exception: return ("neterr", None)
    if r.status_code == 404: return ("404", None)
    try: d = r.json()
    except Exception: return ("notjson", None)
    if not d: return ("empty", None)
    if d.get("__typename") == "TweetTombstone": return ("tombstone", None)
    return ("ok", d)

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
    kind, handle, tid = classify(tw); rec["link_kind"] = kind; rec["handle"] = handle
    if kind == "profile":
        with lock: gaps["profile_only"] += 1
        rec["notes"].append(f"profile @{handle}(+0)"); return _emit(rec)
    if kind != "status" or not tid:
        with lock: gaps[f"link_{kind}"] += 1
        rec["notes"].append(f"link={kind}"); return _emit(rec)
    status, d = resolve_tweet(tid)
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
    s, notes = score_resolved(zone, refs, blue, mism)        # shared, fixed scoring
    rec["score"] += s; rec["notes"].extend(notes)
    rec.update({"author": author, "blue": blue, "verified": refs and not mism, "likes": d.get("favorite_count")})
    # ---- DISCOVERY STAGE: independent corroboration (paid xAI, gated on having reached a real tweet) ----
    disc = discover_independent(rec["mint"], handle, rec["symbol"])
    if disc["searched"]:
        b, dn = independent_bonus(disc["n_ca"], disc["n_ticker"]); rec["score"] += b
        rec["notes"].append(dn); rec["independent"] = disc["n_ca"]
    else:
        with lock: gaps["discovery_would_run"] += 1
        rec["notes"].append("discovery-skip(set XAI_API_KEY)")
    return _emit(rec)

def _emit(rec):
    with lock: survivors.append(rec)

events = [json.loads(l) for l in open(SRC) if l.strip()]
print(f"replaying {len(events)} mints (engine_core logic) ...")
ex = ThreadPoolExecutor(max_workers=8)
for i, ev in enumerate(events):
    buy = ev.get("solAmount") or 0; nm = norm_name(ev.get("name"))
    by_creator[ev.get("traderPublicKey")].append(ev.get("mint"))
    devbuf.append(buy)
    if nm and nm in last_name and i - last_name[nm] < DEDUP_IDX:
        gaps["dedup_name"] += 1; last_name[nm] = i; continue
    last_name[nm] = i
    z = zone_of(buy, list(devbuf)); zone_count[z] += 1
    if z == "green": gaps["suppressed_green"] += 1; continue
    ex.submit(process, ev, z)
ex.shutdown(wait=True)

# cluster re-score post-pass (both wallet + author; penalizes every member)
wsize = {c: len(m) for c, m in by_creator.items()}; asize = {a: len(m) for a, m in by_author.items() if a}
for s in survivors:
    wc = wsize.get(s["creator"], 1); ac = asize.get(s.get("author"), 1)
    if wc > 1 or ac > 1:
        s["score"] -= 2; tags = []
        if wc > 1: tags.append(f"wallet x{wc}")
        if ac > 1: tags.append(f"author x{ac}")
        if wc > 1 and ac > 1: s["score"] -= 1; tags.append("BOTH")
        s["notes"].append("+".join(tags)); s["serial"] = max(wc, ac)

n = len(events); buf = sorted(devbuf); p80 = buf[int(.8*len(buf))]; p95 = buf[int(.95*len(buf))]
print("\n" + "=" * 66)
print(f"REPLAY (core)  |  {n} mints  |  SPC p80={p80:.2f} p95={p95:.2f} SOL")
print(f"zones: green {zone_count['green']} | amber {zone_count['amber']} | red {zone_count['red']} | dedup {gaps['dedup_name']}")
print("stage outcomes:", {k: v for k, v in sorted(gaps.items()) if k not in ('suppressed_green', 'dedup_name')})
verified = [s for s in survivors if s.get("verified")]
imp = [s for s in survivors if any('omits' in x or 'void-verify' in x for x in s.get("notes", []))]
clustered = [s for s in survivors if s.get("serial")]
print(f"verified: {len(verified)} | impersonation/spoof: {len(imp)} | cluster-penalized: {len(clustered)}")
print("\ntop survivors:")
for s in sorted(survivors, key=lambda s: s.get("score", 0), reverse=True)[:10]:
    print(f"  [{s.get('score'):+d}] {s['zone']:5} {s['name'][:20]:20} @{s.get('author')} buy={round(s.get('dev_buy'),2)} "
          f"ver={s.get('verified')} {';'.join(s['notes'])[:40]}")
print("\nbottom survivors:")
for s in sorted(survivors, key=lambda s: s.get("score", 0))[:6]:
    print(f"  [{s.get('score'):+d}] {s['zone']:5} {s['name'][:20]:20} {';'.join(s['notes'])[:52]}")
