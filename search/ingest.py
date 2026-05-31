#!/usr/bin/env python3
"""ingest - continuous X ingestion engine.

Composes xsearch's proven primitives into a producer/consumer pipeline:

  producer  poll a List with ListLatestTweetsTimeline. ONE call multiplexes every member's recent
            tweets on the list endpoint's own rate-limit bucket (proven: 22 authors / 1 call), so a
            watchlist of N accounts costs one request per poll, not N. Dedup across polls.
  consumer  drain the queue, optionally re-hydrate live engagement keyless via the syndication CDN
            (no account, no per-token wall), and append each new tweet to a JSONL sink for the engine.

The split is the whole point: discovery is the scarce, rate-limited side (kept to one call per poll);
hydration is the cheap, keyless side. Search's 45/15-min wall is never touched.

  python ingest.py --list 1283884222881640448 --interval 30 --cycles 5 --out /tmp/x-stream.jsonl
  python ingest.py --search "solana" --interval 30 --cycles 20     # watch a topic, low-noise
  python ingest.py --list <id> --hydrate            # also re-poll live likes/replies per new tweet
"""
import asyncio, json, sys, os, argparse, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xsearch


def _new(posts, seen):
    """Filter to tweets whose id is not in `seen`; record them in `seen`. The dedup heart of the loop:
    a List re-returns the same tweets every poll, so only the genuinely-new ones move downstream."""
    out = []
    for p in posts:
        if p["id"] not in seen:
            seen.add(p["id"])
            out.append(p)
    return out


async def watch(fetch, label, interval, cycles, out_path, hydrate):
    """Poll `fetch` (a coroutine returning tweet records) `cycles` times, `interval` seconds apart;
    new tweets flow producer -> queue -> consumer -> JSONL sink. The source is pluggable: a List
    (ListLatestTweetsTimeline, one call = every member) or a topic search (SearchTimeline, paced
    under the wall). Returns run stats."""
    seen = set()
    q = asyncio.Queue()
    stats = {"polled": 0, "new": 0}

    async def producer():
        for c in range(cycles):
            try:
                posts = await fetch()
            except Exception as e:
                print(f"[poll {c + 1}] error: {e}", file=sys.stderr)
                posts = []
            new = _new(posts, seen)
            stats["polled"] += len(posts)
            stats["new"] += len(new)
            for p in new:
                await q.put(p)
            print(f"[poll {c + 1}/{cycles}] {len(posts)} tweets, {len(new)} new "
                  f"(total new {stats['new']})", file=sys.stderr)
            if c + 1 < cycles:
                await asyncio.sleep(interval)
        await q.put(None)                                       # sentinel: producer done

    async def consumer():
        with open(out_path, "a") as sink:
            while True:
                p = await q.get()
                if p is None:
                    break
                if hydrate:                                     # keyless live-engagement refresh
                    r = await asyncio.to_thread(xsearch._cdn_resolve, p["id"], "hydrate")
                    if r:
                        p = {**p, "likes": r["likes"], "replies": r["replies"], "_hydrated": True}
                p["_ingested"] = time.time()
                sink.write(json.dumps(p) + "\n")
                sink.flush()

    await asyncio.gather(producer(), consumer())
    return stats


def main():
    ap = argparse.ArgumentParser(description="continuous X ingestion: poll a source -> dedup -> hydrate -> JSONL sink")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--list", metavar="LISTID", help="watch a List (one call = every member, its own bucket)")
    src.add_argument("--search", metavar="QUERY", help="watch a topic via SearchTimeline (e.g. 'solana' or '$SOL'), paced under the wall")
    ap.add_argument("--interval", type=int, default=30, help="seconds between polls")
    ap.add_argument("--cycles", type=int, default=5, help="number of polls (bounded run)")
    ap.add_argument("--pages", type=int, default=3, help="scroll pages per poll")
    ap.add_argument("--out", default="/tmp/x-stream.jsonl", help="JSONL sink")
    ap.add_argument("--hydrate", action="store_true", help="re-poll live engagement keyless per new tweet")
    args = ap.parse_args()
    if args.list:
        label = f"list {args.list}"
        fetch = lambda: xsearch.find_list(args.list, args.pages, 1000, False)
    else:
        label = f"search {args.search!r}"
        fetch = lambda: xsearch.find_session(args.search, "live", args.pages, 1000, False)
    print(f"[ingest] watching {label} every {args.interval}s x{args.cycles}", file=sys.stderr)
    stats = asyncio.run(watch(fetch, label, args.interval, args.cycles, args.out, args.hydrate))
    print(f"done: {stats['new']} new tweets ({label}) -> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
