# API-God Session Log

## 2026-05-27

### What was built
- **Phase 1 bones**: Playwright persistent context + CDP interception (`src/interceptor.js`), SQLite storage (`src/storage.js`), visible browser launcher (`src/browser.js`), CLI entry point (`src/index.js`)
- **Plugin system**: `src/plugins.js` — `loadPlugins()`, `runOnResponse()`, `runOnWebSocket()`, `runOnPageLoad()`
- **X.com plugin** (`src/plugins/x.js`): parses SearchTimeline GraphQL responses into structured `x-tweet` records. Ported from `api-god-x.py`
- **axiom.trade plugin** (`src/plugins/axiom.js`): matches `:3001` / `axiom.trade` WebSockets, parses socket.io frames, extracts trending token data into `axiom-token` / `axiom-alert` records
- **Shodan plugin** (`src/plugins/shodan.js`): passive — parses server-rendered HTML search results into `shodan-host` records (ip, org, country, city, tags, banner). No API key needed, rides authenticated session
- **`scripts/nav.js`**: navigate to a URL and report what was captured
- **`scripts/shodan-search.js`**: standalone Playwright Shodan scraper (discussed but agreed passive plugin is the right model)

### Plugin interface
Each plugin exports a default object with:
- `name`: string
- `match(url)`: HTTP URL matcher
- `matchWS(url)`: optional WS URL matcher (falls back to `match`)
- `onResponse(url, body)`: returns records array or null
- `onWebSocket(frame)`: returns records array or null (frame = `{ dir, url, data }`)
- `onPageLoad(url, page)`: returns records array or null — has live Playwright page handle

### DB schema
`data/captures.db` — table `captures(id, ts, domain, type, url, status, method, data)`
All plugin-specific fields packed into `data` JSON. Query with `--pretty` to expand.

### CLI
```
node src/index.js               # start browser
node src/index.js query [domain] [--type X] [--limit N] [--pretty]
node src/index.js stats
```

### Repo
- `Nicholas-Kloster/API-God` — **PRIVATE** as of 2026-05-27
- `data/` and `*.db` are gitignored — no credentials or captures ever committed

### What's next
- **axiom.trade**: open the target in the browser and let the plugin capture the `:3001` socket.io trending feed
- **O'Reilly plugin**: authenticated session → capture content API responses
- **Pagination**: Shodan passive plugin currently gets 10 results per page view; consider auto-clicking next page on load
- **`--slim` flag**: condensed NDJSON output for piping into jq / aimap
- **WebSocket plugin validation**: need live axiom.trade session to confirm `onWebSocket` parse path end-to-end
