# SESSION

State of API-God for the next session. Read this first.

## Where it stands (all live on main @ e9e41ad)

Keyless X toolkit, built, tested, and live. It reads X (and can write) without the API by riding a saved browser session and reading X's own GraphQL/REST off the wire. The `engine/` memecoin scorer is hardened (M1). Offline suite: 51 passed, 1 xfail.

### search/ tools
- **xsearch.py**
  - `search` : SearchTimeline (topic, `$ticker`, contract)
  - `--track @h` : UserTweets (one account; `--replies` = UserTweetsAndReplies)
  - `--list <id>` : ListLatestTweetsTimeline (one call = every member, own bucket)
  - `--hydrate` : keyless syndication CDN, ids to full tweets (no account; no reposts)
  - `--batch` : TweetResultsByRestIds (ids to full tweets WITH reposts; authed via requests)
  - `--probe` : rate-limit mapper for any endpoint
  - `--backend xai` : paid x_search, no account risk
- **ingest.py** : continuous poll, dedup, JSONL sink. Source pluggable: `--list <id>` or `--search "solana"` (topic, low-noise ~2/min).
- **livepipe.py** : real-time engagement push. SSE on api.x.com/live_pipeline; per-tweet velocity; no polling.
- **reactive.py** : fuses discovery + livepipe. Discover a topic, subscribe its tweets, stream velocity, one process. The reader thread owns the live_pipeline session (discovery feeds ids via a queue) so requests.Session is never used cross-thread.

### Session file
- `~/.x-session/state.json` = the X login (currently @WillRondell burner; main backed up to `state.main.json`).
- Capture: `python search/xsearch.py --login` (waits for `auth_token`, refuses to save a logged-out session). The fresh `--login` browser can be blocked by X; reliable seed is exporting `storageState` from an already-logged-in browser, x.com cookies only.

## The X.com map (this session)
- **~190 GraphQL ops** (extract from `main.<hash>.js`: `queryId:"..",operationName:"..",operationType:".."`). Buckets: Communities(33), Lists(27), read-timelines(26), Accounts(17), Posts-write(16), plus Topics, Articles, Bookmarks, Broadcasts, Subscriptions, Notifications. Write surface: `CreateTweet`, `CreateNoteTweet`, `CreateRetweet`, `FavoriteTweet`, `CreateBookmark`.
- **REST islands**: `api.x.com/live_pipeline` (SSE engagement push), `grok.x.com/2/grok/add_response.json` (Grok stream) + `CreateGrokConversation`, `x.com/i/api/2/badge_count`, `cdn.syndication.twimg.com/tweet-result` (keyless).
- **Pure persisted-queries** (proven): X serves only by `queryId` + variables and ignores the client query body; bad variables coerce gracefully with no schema leak, and introspection is off. Schema-recovery-by-injection tooling (Clairvoyance, InQL, field-suggestions) is dead against X; the `main.<hash>.js` regex is the only recon path.
- **Rate-limit envelope**: SearchTimeline limit 50 / rolling 15 min then 429 (~15 min cooldown), the only hard wall (the earlier empirical ~45 was the rolling window undercounting). X returns the live quota in `x-rate-limit-limit`/`remaining`/`reset` headers, so `--probe` (search / `--track` / `--list`) reads the budget instead of exhausting it, and `count` is server-clamped (count=500 returns the same ~20 as default), so pagination is the only path to depth. Per-endpoint census from headers: SearchTimeline 50, UserTweets 50, ListLatestTweetsTimeline 500 (10x, and it multiplexes N authors per call, the efficient read frontier). Buckets are separate per endpoint. Syndication CDN has no per-IP limit. Auth = session cookies + `ct0` csrf + public web bearer; **no x-client-transaction-id needed**, so authed reads and writes run over plain `requests`.
- **Browserless bucket census** (proven): a 422 (omit `features`) still returns `x-rate-limit-*`, so any recognized queryId reveals its bucket via a plain `requests.get`, no browser, no valid query. The rejected request consumes 1 (cheap, not free). Censused: UserByScreenName 150 (no features, 200), TweetResultsByRestIds 500, TweetResultByRestId 500. A 403 HTML WAF block carries no headers, so the counter lives at the GraphQL routing layer (behind the edge WAF, in front of variable validation).
- **live_pipeline contract**: `GET events?topic=/tweet_engagement/<id>` (NDJSON); `POST 1.1/live_pipeline/update_subscriptions` body `sub_topics=<comma list>&unsub_topics=`; 120s subscription TTL, 25s heartbeat. Events push the count that changed.
- Full detail + queryIds in auto-memory `reference_x_searchtimeline_rate_limit`.

## What is next
1. **reactive.py velocity gate**: evict cold tweets from the ~40 live slots, keep the movers. The one evidence-backed refinement (in testing most subscriptions watched were dead weight). Tune the threshold against a live hot topic.
2. **TopicFollow** as a discovery source (X-curated topic feed; `Topic*` ops mapped).
3. **Hook livepipe velocity into the engine score**.
4. Later, optional: write verbs (`--post`/`--like`, burner-only), video (HLS), DM/Spaces mapping, persistent-browser ingest daemon.

## Notes
- Commits go local first, then fast-forward to `main` (no force; needs explicit "push to main").
- Burner-only for `--probe` and any rate-limit testing; finding a limit means tripping it.
- Forwarded AI design (Gemini and others) gets evaluated, not auto-implemented. Lines held this session: no proxy/fingerprint evasion, never paste auth tokens to a third party, and no speculative architecture (coordinators, cascade detectors, viral graphs) before the base loop is proven.
