# SESSION

State of API-God for the next session. Read this first.

## Where it stands (all live on main @ 57e8684)

The keyless X toolkit is built, tested, and live. `xsearch.py` reads X without the API by riding a saved browser session and reading X's own GraphQL responses off the wire. `ingest.py` runs it as a continuous pipeline. The `engine/` memecoin scorer is hardened (M1: 16 review findings fixed test-first). Offline suite: 47 passed, 1 xfail.

### xsearch.py modes
- `search` : SearchTimeline (topic, `$ticker`, contract)
- `--track` : UserTweets (one account; `--replies` for replies)
- `--list` : ListLatestTweetsTimeline (one call = every member of a List)
- `--hydrate` : keyless syndication CDN, ids to full tweets (no account, no reposts)
- `--batch` : TweetResultsByRestIds (ids to full tweets WITH reposts, authed)
- `--probe` : rate-limit mapper for any endpoint
- `--backend xai` : paid x_search, no account risk

### Session
- `~/.x-session/state.json` holds the X login (currently the @WillRondell burner; main backed up to `state.main.json`).
- Capture: `python search/xsearch.py --login` (waits for `auth_token`, refuses to save a logged-out session).
- The fresh `--login` browser can be blocked by X. The reliable seed is exporting `storageState` from an already-logged-in browser, filtered to x.com cookies only.

## The rate-limit map (measured on the burner)
- `SearchTimeline`: ~45 requests / rolling 15 min, then 429, ~15 min cooldown. The only hard wall.
- `UserTweets` and `ListLatestTweetsTimeline`: separate per-endpoint buckets (serve 200 while search is 429'd).
- Syndication CDN `tweet-result`: keyless, no per-IP limit seen (250 requests, all 200, ~146/min).
- Strategy: one search or List call harvests ids; hydrate the rest keyless via the CDN.
- Full read surface = 157 GraphQL ops (extracted from `main.<hash>.js`). queryIds and detail in auto-memory `reference_x_searchtimeline_rate_limit`.

## What is next (optional, nothing is broken)
1. `find_list` / `find_track` open a browser per call. For an all-day ingest daemon, refactor to hold one browser open and re-navigate per poll.
2. `--batch` reuses a live request's `features`/`fieldToggles`, which are coupled to X's current bundle. If `--batch` starts returning 400/422, the bundle changed; re-capture features.

## Notes
- Commits go local first, then fast-forward to `main` (no force). READMEs written for root, `search/`, `engine/`.
- Burner-only for `--probe` and any rate-limit testing; finding a limit means tripping it.
