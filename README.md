<h1 align="center">API-God</h1>

<h4 align="center">Keyless X (Twitter) toolkit. Read internal GraphQL off the wire through a saved Playwright session.</h4>

<p align="center">
  <a href="https://github.com/nuclide-research/API-God/blob/main/LICENSE"><img src="https://img.shields.io/github/license/nuclide-research/API-God?style=flat-square" alt="license"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python" alt="python"></a>
  <a href="https://nuclide-research.com"><img src="https://img.shields.io/badge/by-NuClide-blue?style=flat-square" alt="NuClide"></a>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#installation">Installation</a> •
  <a href="#usage">Usage</a> •
  <a href="#rate-limit-map">Rate-limit Map</a> •
  <a href="#the-engine">Engine</a> •
  <a href="#scope">Scope</a>
</p>

---

API-God reads X without the X API. No developer account, no paid tier, no key. It rides a browser session you are already logged into. X's own JavaScript calls X's internal GraphQL endpoints; API-God captures those responses off the wire. The auth is your session cookie, not an API key.

A second path needs no login at all. The syndication CDN (`cdn.syndication.twimg.com/tweet-result?id=`) returns a full tweet by id, keyless, with no per-IP limit observed at 250 requests. API-God uses it to hydrate tweet ids without touching any account rate-limit budget.

The `search/` directory is the reusable layer. The `engine/` directory is a downstream consumer: a Solana memecoin signal engine that scores new launches from the X stream in real time.

# Features

- Keyless reads via authenticated Playwright session: `SearchTimeline`, `UserTweets`, `UserTweetsAndReplies`, `ListLatestTweetsTimeline`, `TweetResultsByRestIds`
- Syndication CDN path: full tweet by id, no login required, no per-IP limit observed
- Rate-limit census of 93 read operations: full per-op bucket map committed as JSON
- Continuous ingest (`ingest.py`): poll-on-interval, dedup across polls, append only new tweets
- Field-exposure diff (`fielddiff.py`): keyless CDN vs authed `TweetResultsByRestIds`, 86 authed-only fields
- xAI backend option (`--backend xai`): paid path, no account risk
- 51 offline pytest tests, no network, no browser, no RPC

# Installation

```bash
cd search
pip install -r requirements.txt
python -m playwright install chromium
python xsearch.py --login          # one-time: opens a browser, saves session
```

Python 3.10+. Dependencies: `playwright`, `requests`, `httpx[http2]`. For the engine: `websockets`, `scikit-learn`, `numpy`.

# Usage

One query tool, several modes. Each reads a different X GraphQL endpoint off the wire.

| Mode | Command | Reads |
|------|---------|-------|
| Search | `python xsearch.py "solana depin"` | `SearchTimeline` |
| Track one account | `python xsearch.py elonmusk --track` | `UserTweets` |
| Track with replies | `python xsearch.py elonmusk --track --replies` | `UserTweetsAndReplies` |
| Track a List | `python xsearch.py --list <listId>` | `ListLatestTweetsTimeline` |
| Hydrate ids (keyless) | `cat ids.txt \| python xsearch.py --hydrate` | syndication CDN |
| Batch hydrate (authed) | `cat ids.txt \| python xsearch.py --batch` | `TweetResultsByRestIds` |
| Probe rate limits | `python xsearch.py "query" --probe` | search or track endpoint |
| xAI backend | `python xsearch.py "query" --backend xai` | xAI x_search + CDN |
| Combined | `python xsearch.py "query" --backend both` | session + xAI, merged |

```bash
python xsearch.py "solana depin"                 # search, default backend
python xsearch.py '$GIGA' --backend xai          # paid, no account risk
python xsearch.py "q" --tab top --pages 5        # Top tab, up to 5 scroll pages
python xsearch.py "q" --json --out results.jsonl # JSONL output to file
python xsearch.py elonmusk --track --pages 5     # timeline, 5 pages
python xsearch.py --list 1283884222881640448     # whole List in one call
python xsearch.py --login                        # one-time: save session
```

Full flags: `--backend {session,xai,both}`, `--tab {live,top}`, `--pages N` (default 5), `--delay N` (ms, default 1300), `--sort {recent,engagement}`, `--limit N` (default 30), `--out PATH`, `--json`, `--headed`, `--track`, `--replies`, `--probe`, `--max-req N`, `--reload`, `--list LISTID`, `--hydrate`, `--batch`.

### Continuous ingestion: ingest.py

```bash
python ingest.py --list <id> --interval 30 --cycles 20 --out stream.jsonl
python ingest.py --search "solana" --interval 60 --cycles 10 --out stream.jsonl
```

Flags: `--list`, `--search`, `--interval` (seconds, default 30), `--cycles` (default 5), `--pages` (default 3), `--out` (default `/tmp/x-stream.jsonl`), `--hydrate` (re-check live engagement per new tweet via CDN).

### Rate-limit census: census.py

```bash
python census.py                              # human table + bucket histogram
python census.py --json map.json              # full per-op map as JSON
python census.py --deep --inventory --json x-op-inventory.json
```

Flags: `--delay N` (seconds between probes, default 0.25), `--include-mutations`, `--json PATH`, `--deep` (walks lazy-loaded webpack chunks, 292 ops total vs 157 in `main.js`), `--inventory` (list ops without probing), `--limit-ops N`.

A 2026-05-30 sweep of 93 read operations: 500 is the default bucket (67 ops). 17 scraping-attractive ops throttled to 50 (`SearchTimeline`, `UserTweets`, `UserTweetsAndReplies`, Followers, Community feeds). Snapshot in `search/x-read-bucket-map.json`. Full inventory in `search/x-op-inventory.json` (292 operations: 161 query, 131 mutation).

### Field-exposure diff: fielddiff.py

```bash
python fielddiff.py <tweet_id>
```

Resolves one tweet through the keyless CDN and the authed `TweetResultsByRestIds`, then diffs the field-name vocabulary. For tweet 20: CDN returned 31 field names, authed path returned 106. The 86 authed-only fields are engagement counts and viewer-relationship fields absent from anonymous responses.

# Rate-limit map

| Endpoint | Limit |
|----------|-------|
| `SearchTimeline` | 50 requests / 15 min rolling, then HTTP 429 |
| `UserTweets` | separate bucket; serves 200 while search is 429'd |
| `ListLatestTweetsTimeline` | 500 / 15 min; one call returns every member |
| `TweetResultsByRestIds` | 500 / 15 min; up to ~100 ids per call |
| syndication CDN | no per-IP limit observed at 250 requests |

The strategy: spend one List or search call to harvest tweet ids, hydrate through the keyless CDN. List is the efficient read frontier: 500-limit, multiplexes all members per call.

# Layout

| Path | What |
|------|------|
| `search/` | `xsearch.py` (X toolkit) and supporting pipeline tools |
| `engine/` | Solana memecoin signal engine that consumes the X stream |
| `legacy/` | retired Node.js interceptor, kept for reference |
| `tests/` | offline pytest suite (no network, no browser) |
| `testdata/` | fixtures for the test suite |

# The engine

The Solana memecoin signal engine watches the pump.fun firehose, scores new launches in real time, and records outcomes for calibration. The pipeline per mint: zone the dev buy by size, enrich from token metadata and any linked tweet via the syndication CDN, verify the social claim against the data layer, discover related accounts, apply a cluster penalty for coordinated wallet farms, score the survivor, and write to the SQLite outcome ledger.

Modules: `live.py` (firehose loop), `engine_core.py` (pure scoring functions), `replay.py` (deterministic re-run over captured logs), `outcomes.py` (label MOON / DEAD / migrated / concentrated), `outcomes_calibrate.py` (fit weights against labels), `discovery.py` (social discovery), `stress.py` (load test), `livetest.py` (regression guard).

# Tests

```bash
pip install -r engine/requirements-dev.txt
pytest
```

51 tests pass offline. Network, browser, and WebSocket firehose are all faked. The suite covers the pipeline logic, the scoring guards, and the calibrator without touching X or any RPC.

# Example

```
$ python xsearch.py elonmusk --track --pages 5
100 tweets from @elonmusk

Sat May 30 15:45  ♥115779 ↻20580 👁 5760940  Release the body camera videos
Tue May 26 15:57  ♥103654 ↻12188 👁15539004  Starlink coming to American Airlines!
```

```
$ python xsearch.py "from:elonmusk" --probe
{ "endpoint": "SearchTimeline", "ok_before_limit": 45, "limit_status": 429, "limit_at_s": 63.3 }
```

# Scope

API-God reads public posts through a logged-in session. It does not bypass authentication for non-public content. It does not forge requests; it reads the responses to requests the browser was going to make anyway. `--probe` mode finds rate-limit walls by tripping them; run it only on a throwaway account. Use it on accounts and data you are authorized to access, and stay inside X's terms for that account.

# Our other projects

- [api-god-x](https://github.com/nuclide-research/api-god-x): single-file extract: `SearchTimeline` only, one Python script
- [osint-platoon](https://github.com/nuclide-research/osint-platoon): multi-agent OSINT framework on ATP 3-21.8 doctrine
- [osint-handbook-2026](https://github.com/nuclide-research/osint-handbook-2026): scraped OSINT tool dataset
- [aimap](https://github.com/nuclide-research/aimap): fingerprint scanner for AI/ML infrastructure

# License

MIT. Part of the NuClide toolchain. Contact: [nuclide-research.com](https://nuclide-research.com)
