# API-God (Go) — Solana Memecoin Signal Engine

**Date:** 2026-05-29
**Status:** Approved (design)
**Scope of this spec:** Phase 1 only (the free, key-less core). P2/P3 get their own spec → plan cycles.
**Supersedes:** the Node/Playwright implementation, which moves to `legacy/`.

## What It Is

A headless, single Go binary. It watches every new Solana memecoin mint in real time, resolves the X
account/tweet behind each coin, scores it, and writes structured rows to SQLite with a query CLI on top.

The core insight, carried over from the project's data-access work: the valuable signal does not require
the $50k X API or any session scraping. New mints arrive free over a public websocket, each carrying the
token's metadata URI; that URI's JSON carries the project's X link; and a tweet behind an X status link
resolves through an unauthenticated syndication CDN. The whole Phase-1 spine runs with **no API keys and
no browser** (every source is plain HTTP or websocket against public endpoints), so there is no
authenticated session to get banned.

## Non-Goals (Phase 1)

- No browser / Playwright in the core (the Node capture layer is retired to `legacy/`).
- No trade execution. This engine produces signals; it does not place orders.
- No Helius DAS on the hot path (it does not carry socials, see Appendix B). DAS is P2 backfill only.
- No xAI / live-X (P2). No multi-agent synthesis (P3).
- No GUI (CLI only).

## Architecture

A pipeline of independent stages, one goroutine each, wired by Go channels. Each stage sits behind an
interface so a source or resolver can be swapped without touching anything downstream.

```
 source              enrich                 resolve               score              sink
 PumpPortal WS  -->   uri -> off-chain  -->  syndication CDN  -->  signal record -->  SQLite
 (new mints)          JSON -> socials        (status -> tweet)     + verification     + query CLI
    |                 (twitter/tg/web)        profile -> handle
    |                                            ^
    └─(P2) Helius DAS backfill                    │
    └─(P2) xAI x_search discovery ────────────────┘   candidate status ids
```

### Stage 1 — `source`

- ONE PumpPortal websocket connection, all subscriptions multiplexed over it. The single-connection rule
  is enforced by PumpPortal with hourly IP bans; reconnect with exponential backoff, never reconnect-spam.
- Subscribe `{"method":"subscribeNewToken"}` (Phase 1). `subscribeMigration` is a separate later channel.
- Tolerant JSON decode (the payload shape drifts; unknown keys must not hard-fail) into:
  `MintEvent{ Mint, Creator (=traderPublicKey), Name, Symbol, URI, SolDevBuy (=solAmount), MarketCapSol, Pool, Signature, Raw }`
- Emits `MintEvent` on an outbound channel.
- `Source interface { Stream(ctx) (<-chan MintEvent, error) }`. Phase-1 impl: `PumpPortalSource`. Later:
  `GeyserSource` / `BitquerySource` for lower latency, no downstream change.

### Stage 2 — `enrich`

- Input `MintEvent.URI`. GET the off-chain JSON through a gateway pool with fallback:
  `pump.mypinata.cloud` → `ipfs.io` → `cloudflare-ipfs.com` → `dweb.link`. Trim trailing nulls/whitespace
  on the URI before fetching.
- Extract top-level socials `twitter` / `telegram` / `website`. Treat empty string AND missing key as
  "no link" (the launchpad SDK writes `""` when absent).
- Canonicalize the twitter link (strip `t.co`/UTM, normalize `twitter.com`↔`x.com`) and classify it:
  `status | profile | search | community | none`.
- Cache forever, keyed by CID/txid (content-addressed, immutable).
- Output: `MintEvent` augmented with `Socials{ TwitterRaw, TwitterKind, Handle, StatusID, Telegram, Website }`.

### Stage 3 — `resolve`

- `Resolver interface { Resolve(ctx, link) (*Tweet, error) }`. Phase-1 impl: `SyndicationResolver`.
- For a `status` link: GET `cdn.syndication.twimg.com/tweet-result?id=<id>&token=<derived>&lang=en`.
  - The `token` is derived from the id by replicating react-tweet's algorithm exactly (see Appendix D).
    A naive Go base-36 of an integer produces the WRONG token.
  - Branch on HTTP status / shape BEFORE `json.Unmarshal`: 200 `Tweet` (parse), 404 → HTML error page
    (not JSON; treat as not-found), `__typename == "TweetTombstone"` (protected/suspended), empty `{}`
    body (treat as failure). Do not assume the body is a Tweet.
  - Extract `Tweet{ TweetID, Text, AuthorHandle (=user.screen_name), AuthorName (=user.name),
    Blue (=user.is_blue_verified), CreatedAt, Favorites (=favorite_count), Replies (=conversation_count), Raw }`.
- For a `profile` link: extract the handle only; tweet pull is deferred to P2 (xAI/X-v2). Record the handle.
- This stage is the single highest deprecation risk in the system (undocumented endpoint). It lives behind
  the `Resolver` interface with a fallback hook so P2's path can take over if X kills it.

### Stage 4 — `score`

The synthesis seam (P3 expands it). Emits a `Signal` per mint:

- Mechanical fields: dev-buy size (`SolDevBuy`), initial market cap (`MarketCapSol`), `HasSocials`,
  author quality (`Blue`, name presence), recency (`CreatedAt` vs mint time).
- **Bidirectional verification as a first-class field.** Socials are attacker-controlled free text and
  impersonation is documented (fake-account → fake coin). So: does the resolved tweet text actually
  reference the mint address or ticker? If the coin claims an X account/tweet that never mentions it,
  set `ImpersonationFlag`. `TwitterVerified` is true only when the tweet corroborates the coin.
- `score` is a float plus `Notes`. Phase-1 weighting is deliberately simple and documented; P3 replaces
  it with pluggable scorers.

### Stage 5 — `sink` / storage

- SQLite via `modernc.org/sqlite` (pure Go, no cgo → a genuine static single binary).
- Normalized fields are real **columns**, with a `raw` JSON sidecar per table for forensics. (This is the
  explicit correction of the Node review finding where a plugin computed normalized fields and the storage
  layer silently dropped them.)

```sql
CREATE TABLE mints (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  mint TEXT UNIQUE NOT NULL,
  creator TEXT, name TEXT, symbol TEXT, uri TEXT,
  sol_dev_buy REAL, market_cap_sol REAL, pool TEXT,
  raw TEXT
);
CREATE TABLE tweets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  tweet_id TEXT, mint TEXT REFERENCES mints(mint),
  author_handle TEXT, author_name TEXT, blue INTEGER,
  text TEXT, created_at TEXT, favorites INTEGER, replies INTEGER,
  link_type TEXT, raw TEXT
);
CREATE TABLE signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  mint TEXT REFERENCES mints(mint),
  score REAL, dev_buy REAL, has_socials INTEGER,
  twitter_verified INTEGER, impersonation_flag INTEGER,
  notes TEXT, raw TEXT
);
CREATE INDEX idx_mints_ts   ON mints(ts);
CREATE INDEX idx_tweets_mint ON tweets(mint);
CREATE INDEX idx_signals_score ON signals(score DESC);
-- All list queries ORDER BY ts, id  (the deterministic tiebreak the Node query lacked).
```

## CLI

```
apigod run                         # start the live pipeline (source -> ... -> sqlite)
apigod query mints   [--since --min-mcap --has-twitter]
apigod query tweets  [--since --verified --handle]
apigod query signals [--since --min-score]
apigod resolve <tweet-url|id>      # one-shot syndication resolve (also the test surface)
apigod enrich  <metadata-uri|mint> # one-shot socials extraction
apigod stats                       # row counts / rates by table
# (P2) apigod watch --handles ...
```

Output: NDJSON to stdout by default, `--pretty` for indented. Flag parsing via the Go stdlib `flag`
package per subcommand (no heavyweight CLI framework; minimal deps is the standard).

## Config

Environment / flags. Phase 1 needs none. `--db` (default `data/apigod.db`), `--gateways` (override the
IPFS pool). P2: `HELIUS_API_KEY`, `XAI_API_KEY`.

## Repository Layout

```
API-God/
├── cmd/apigod/main.go        # CLI entry, subcommand dispatch
├── internal/
│   ├── source/               # Source interface + pumpportal.go
│   ├── enrich/               # metadata fetch, gateway pool, socials classify
│   ├── resolve/              # Resolver interface + syndication.go (+ token.go)
│   ├── score/                # signal scorer + verification
│   ├── store/                # sqlite schema + queries
│   └── pipeline/             # wiring, channels, lifecycle
├── testdata/                 # golden fixtures (captured frame, metadata JSON, syndication id=20)
├── legacy/                   # retired Node/Playwright implementation
├── go.mod
└── docs/superpowers/specs/
```

## Error Handling & Operational Discipline

- PumpPortal: exactly one socket; backoff reconnect; never spam (ban). Tolerant decode.
- IPFS: gateway pool + retry on 429/timeout; cache-forever by CID.
- Syndication: correct token algorithm; status/shape branching; tombstone & empty-body handling; behind an
  interface with a fallback hook; this is the most fragile dependency, wrap accordingly.
- Socials are untrusted claims → impersonation flagging is a core signal, not an afterthought.
- All stages take a `context.Context`; clean shutdown drains channels and closes the DB.

## Testing

- Each stage unit-tested through its interface with golden fixtures in `testdata/`:
  a real PumpPortal `subscribeNewToken` frame, a real pump.fun metadata JSON (flat socials), and the
  syndication response for `id=20`.
- `resolve` and `enrich` subcommands double as manual/integration surfaces.
- The token algorithm gets a dedicated test against the known vector `id=20 → token=6dq1a2xwd93`.

## Build Order

- **Phase 1 (this spec):** source(PumpPortal) → enrich → resolve(syndication) → score v1 → SQLite + query
  CLI. Zero keys. Done when `apigod run` populates all three tables from live mints and
  `apigod query signals --min-score` returns real, verified rows.
- **Phase 2:** Helius DAS by-mint backfill; xAI `x_search` discovery + watchlist → citation status ids →
  syndication resolve; profile-link tweet pull. Own spec.
- **Phase 3:** the "20 nets" synthesis layer as concurrent pluggable scorers feeding `signals`. Own spec.

---

## Appendix — Verified External Contracts (as of 2026-05-29)

Researched and, where marked, verified against live endpoints. These are the exact shapes the
implementation depends on; do not re-derive them.

### A. PumpPortal new-token websocket — confidence HIGH, live-verified

- `wss://pumpportal.fun/api/data` — `subscribeNewToken` is FREE, no api-key (verified: connected keyless,
  got ack `"Successfully subscribed to token creation events."` + live events).
- Subscribe frame: `{"method":"subscribeNewToken"}`. Multiplex all subscriptions on the ONE socket.
- Event fields (verbatim, live 2026-05-28): `signature`, `mint`, `traderPublicKey` (creator; there is no
  `creator`/`dev` field), `txType:"create"`, `initialBuy`, `solAmount` (SOL dev buy), `bondingCurveKey`,
  `vTokensInBondingCurve`, `vSolInBondingCurve`, `marketCapSol`, `name`, `symbol`, `uri` (metadata URI),
  `is_mayhem_mode`, `pool:"pump"`.
- HARD RULE: one connection only; reconnect-spam triggers hourly IP bans. Decode tolerantly (shape drifts:
  `uri`/`solAmount`/`is_mayhem_mode`/`pool` are newer than old readmes show).
- Lower-latency alternatives for later: Helius Geyser/gRPC or `logsSubscribe` on program
  `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P` ("Program log: Instruction: Create"); Bitquery (richer,
  more lag). PumpPortal ships fastest.

### B. Helius DAS — confidence HIGH (P2 only)

- JSON-RPC POST `https://mainnet.helius-rpc.com/?api-key=<KEY>` (key is a URL query param, not a header).
- `getAsset` params `{id:<mint>, options:{showFungible:true}}`. Fields: `result.id`, `result.content.metadata.name`,
  `.symbol`, `result.content.links.image`, `result.content.json_uri`.
- **DAS does NOT carry twitter/telegram/website.** `content.links` has only `image` + `external_url`.
  Socials live only in the off-chain JSON at `content.json_uri`. So DAS is not the socials source; it is
  P2 by-mint backfill. Free tier caps DAS methods at 2 req/sec.

### C. Off-chain token metadata JSON — confidence HIGH

- The `uri` from the mint event (or `json_uri` from DAS) is a public HTTP(S) URL (pump.fun Pinata gateway /
  ipfs.io / arweave). No auth.
- pump.fun/Bonk/Moonshot put socials FLAT at top level: `twitter`, `telegram`, `website` (plus `name`,
  `symbol`, `description`, `image`, `showName`, `createdOn`). NOT under an `extensions` object.
- The SDK writes `twitter/telegram/website` with `|| ""`, so absent links are usually present-but-empty.
  Treat `""` and missing identically.
- `twitter` is NOT normalized: it can be a profile (`x.com/<handle>`), a status (`x.com/<handle>/status/<id>`),
  a search (`x.com/search?q=...`), or a community URL. Resolver must branch by kind. Both `x.com` and
  `twitter.com` appear; strip `t.co`/UTM.
- Robust order: read top-level `twitter` first, fall back to `external_url`. Never assume `extensions`.
- Socials are attacker-controlled — a claim to verify, not ground truth (impersonation is documented).

### D. Twitter/X syndication CDN — confidence HIGH, live-verified

- GET `https://cdn.syndication.twimg.com/tweet-result?id=<tweet_id>&token=<derived>&lang=en`
  (verified live: `id=20` → HTTP 200 + full Tweet JSON). `features` param not required.
- No auth/cookie/key. CORS pinned to `platform.twitter.com` (irrelevant server-side; a Go client gets the
  body fine).
- `id` must be numeric (`^[0-9]+$`); a username → HTTP 400. Resolves a SPECIFIC tweet only — a bare profile
  link yields nothing here (needs a status id).
- **Token algorithm** (replicate react-tweet's `getToken`, verified `id=20 → 6dq1a2xwd93`):
  JS: `((Number(id)/1e15)*Math.PI).toString(36).replace(/(0+|\.)/g,'')`.
  Go: compute `(float64(id)/1e15)*math.Pi`, format that float in base-36 the way JS
  `Number.prototype.toString(36)` does (integer part base36, `.`, fractional base36 digits), then regex-strip
  all runs of `0` and all `.`. A naive int base-36 is WRONG. (Observed quirk: the server did not strictly
  validate the token for `id=20` — any non-empty token returned the body — but derive it correctly for
  future-proofing; an empty token returns `{}`.)
- Response fields (live): `__typename:"Tweet"`, `id_str`, `text`, `created_at` (ISO-8601), `lang`,
  `favorite_count`, `conversation_count`, `entities.{urls,user_mentions,hashtags}`, `mediaDetails` (only if
  media), and author under `user`: `user.id_str`, `user.name`, `user.screen_name`, `user.verified`,
  `user.is_blue_verified`, `user.profile_image_url_https`. NO retweet/view/follower counts.
- Failure modes to branch on: 404 → HTML error page (not JSON); protected/suspended → `__typename
  "TweetTombstone"`; empty token → `{}`. Undocumented & unstable — single biggest deprecation risk; wrap +
  fallback. Cache-Control was `max-age=60`.

### E. xAI X Search — confidence MEDIUM (P2 only), changes fast

- The old Live Search (`/v1/chat/completions` + `search_parameters`) was RETIRED 2026-01-12 → 410 Gone.
- New: `POST https://api.x.ai/v1/responses` with a server-side tool `{"type":"x_search", ...}` in `tools`.
  Auth `Authorization: Bearer $XAI_API_KEY` (developer API only; consumer SuperGrok has no programmatic key).
- Tool params: `allowed_x_handles` (≤20, mutually exclusive with `excluded_x_handles`), `from_date`/`to_date`
  (ISO `YYYY-MM-DD`), `enable_image_understanding`, `enable_video_understanding`. NO engagement filters,
  no result-count knob.
- Returns model PROSE + `citations` (array of URL strings, e.g. `x.com/.../status/<id>`); inline citations
  are `{type:"url_citation", url, start_index, end_index, title}`. NOT structured post records — no author/
  text/counts in the response. So: use it to surface candidate status URLs, then resolve each through the
  free syndication path (C/D) for structured data.
- Cost: `$5 / 1,000` tool calls ($0.005/call) PLUS token usage; the model may fire several calls per query,
  so budget per query. Multi-second latency — async/cached, off the hot path. Re-verify docs.x.ai before
  committing a cost model (an unconfirmed third-party claim of a ~50% price cut exists; official page still
  shows $5/1k).
