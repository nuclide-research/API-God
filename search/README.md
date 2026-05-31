# search

A keyless X toolkit. `xsearch.py` reads X on your saved browser login; the rest build on its primitives. `ingest.py` runs them as a continuous pipeline, `livepipe.py` and `reactive.py` stream engagement in real time, and `census.py` and `fielddiff.py` map X's own GraphQL surface (rate-limit buckets and per-operation field exposure).

## Setup (once)

```bash
pip install -r requirements.txt
python -m playwright install chromium
python xsearch.py --login
```

`--login` opens a real browser. Log in to X. The tool waits for the `auth_token` cookie to appear, which is proof you are actually logged in, then saves the session to `~/.x-session/state.json`. It refuses to save a logged-out session, so a half-finished login cannot leave you with a dead file.

## xsearch.py

One query tool, several modes. Each reads a different X GraphQL endpoint off the wire.

| Mode | Command | Reads | Notes |
|------|---------|-------|-------|
| Search | `xsearch.py "solana depin"` | `SearchTimeline` | who is talking about a topic, `$ticker`, or contract |
| Track one | `xsearch.py elonmusk --track` | `UserTweets` | one account's timeline; add `--replies` for replies |
| Track many | `xsearch.py --list <id>` | `ListLatestTweetsTimeline` | one call returns every member of a List |
| Hydrate | `... \| xsearch.py --hydrate` | syndication CDN | ids on stdin to full tweets, keyless, no account |
| Batch | `... \| xsearch.py --batch` | `TweetResultsByRestIds` | ids to full tweets with reposts, authed |
| Probe | `xsearch.py "q" --probe` | search / `--track` / `--list` | read X's live `x-rate-limit-*` quota, or hammer to the 429 |
| xAI | `xsearch.py "q" --backend xai` | xAI x_search + CDN | paid (~$0.005), no account risk |

### How a search works

`find_session` opens a headless browser with your saved session, navigates to the search URL, and listens for the `SearchTimeline` response. Scrolling triggers X's own pagination requests, and each response is parsed off the wire by `extract_session`. Reading the JSON instead of scraping the painted page means the follower count, blue check, view count, and quote count all come through, which the rendered HTML hides.

```
$ python xsearch.py elonmusk --track --pages 5
100 tweets from @elonmusk

Sat May 30 15:45  ♥115779 ↻20580 👁 5760940  Release the body camera videos
Tue May 26 15:57  ♥103654 ↻12188 👁15539004  Starlink coming to American Airlines!
...
```

### Tracking a watchlist

A List is the cheapest way to watch many accounts. `ListLatestTweetsTimeline` returns one merged stream of every member's recent tweets in a single request, on the List endpoint's own rate-limit bucket. One call covered 22 different authors from a 51-member List in testing:

```
$ python xsearch.py --list 1283884222881640448
300 tweets from list 1283884222881640448

Sat May 30 17:03  @Royals   ♥457 ↻52   Saturday starters.
Fri May 29 19:17  @Chiefs   ♥2286 ↻182 Wrapped up week 1 of OTAs!
...
```

### Hydration: ids to full tweets

When you have tweet ids and want the full tweet, two paths:

- `--hydrate` resolves each id through the keyless syndication CDN. No account, no rate wall (~146 requests/min on one IP). It returns live engagement counts, so it doubles as a way to re-check a tweet's momentum. It does not return the retweet count.
- `--batch` resolves up to ~100 ids per call through `TweetResultsByRestIds`. It rides your session and does return the retweet count.

```bash
cat ids.txt | python xsearch.py --hydrate
cat ids.txt | python xsearch.py --batch
```

Both read ids from stdin: bare ids, status urls, or JSONL with an `id` field. So discovery feeds hydration directly:

```bash
python xsearch.py --list <id> --json | jq -r .id | python xsearch.py --hydrate
```

### Finding the walls

`--probe` hammers an endpoint until X returns a non-200 and reports where it cut off. Run it only on a throwaway account, because finding the limit means tripping it.

```
$ python xsearch.py "from:elonmusk" --probe
{ "endpoint": "SearchTimeline", "ok_before_limit": 45, "limit_status": 429, "limit_at_s": 63.3 }
```

## ingest.py

A continuous pipeline built from those primitives. It polls a List on an interval, dedups across polls, optionally re-hydrates live engagement keyless, and appends only new tweets to a JSONL sink:

```bash
python ingest.py --list <id> --interval 30 --cycles 20 --out stream.jsonl
```

The producer is the scarce, rate-limited side: one List call per poll. The consumer is the cheap, keyless side. The two are split on purpose, so a watchlist runs all day without touching the search wall.

## Mapping the surface

Two tools read X's own GraphQL surface rather than its content. Both ride the saved session and are read-only.

### census.py: the rate-limit map

X stamps `x-rate-limit-limit`/`remaining`/`reset` on every GraphQL response, including a 422 that fails validation. So one throwaway request to any operation reveals its bucket, with no browser and no valid query. `census.py` pulls the live queryId map from `main.js`, then sweeps every read operation:

```bash
python census.py                  # human table + bucket histogram
python census.py --json map.json  # save the full per-op map
```

The read is cheap but not free: each probe spends one token of the bucket it measures. Mutations are skipped unless you pass `--include-mutations`. A 2026-05-30 sweep of 93 reads: 500 is the default (67 ops), 17 scraping-attractive ops are throttled to 50 (search, the timelines, followers, the community feeds), and a few are special (typeahead 5000, tweet-detail 150). The snapshot is in `x-read-bucket-map.json`.

### fielddiff.py: which op leaks the most

X serves the same object through several resolvers, and they do not return the same fields. `fielddiff.py` resolves one tweet through the keyless CDN and the authed `TweetResultsByRestIds`, then diffs the field-name vocabulary of each:

```bash
python fielddiff.py <tweet_id>
```

For tweet 20 the CDN returned 31 field names and the authed path 106. The 86 authed-only fields are every engagement count (retweet, quote, reply, bookmark, views) and all viewer-relationship fields (blocking, following, muting), which cannot exist on an anonymous response. Introspection is off, but diffing two resolvers recovers the practical schema delta with no introspection query.
