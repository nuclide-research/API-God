# API-God

API-God is a free X (Twitter) intelligence tool. 
Core function: give it any search term (topic, person, company, ticker, hashtag, event) and it returns a structured list of who on 
X is posting about it, what they said, and engagement numbers, sortable and saveable.

The point is doing this without X's paid API, which runs tens of thousands a month for this access tier.
  
Find who's talking about anything on X (Twitter), for free. No API key. No $30,000-a-month data bill.

## What it does

Give it a search: a topic, a person, a company, a coin, a hashtag, an event, anything. It finds the
people on X posting about it and hands you a clean list: who they are, what they said, and how much
engagement each post got. Sortable, saveable, structured.

It does this without X's paid API, which costs tens of thousands of dollars a month for this kind of
access.

## The tool: xsearch

```
cd search
pip install -r requirements.txt
python xsearch.py --login          # one-time login, for the free backend
python xsearch.py "any topic"      # then search anything
python xsearch.py '$TICKER'
python xsearch.py "a person or company"
```

Three ways to run it:

| Backend | Cost | Needs | Account risk |
|---------|------|-------|--------------|
| `session` | Free | Your own X login | Yes. Drives your logged-in account, so heavy use can get it rate-limited or suspended. |
| `xai` | ~$0.005 per search | An xAI key | None. No X login, nothing tied to your account. |
| `both` | Sum of the two | Both of the above | Same as `session`, since it runs that path too. |

> **Warning: the `session` and `both` backends can get your X account suspended.** They drive your own
> logged-in account through X's web search. Heavy or fast use looks like automation and X can
> rate-limit or ban the account. The `xai` backend carries no such risk: it uses no X login.

Pick `session` to pay nothing and accept the account risk. Pick `xai` to pay half a cent and carry no
risk. Pick `both` when you want the widest result set and have already accepted the `session` risk.

### session vs xai: two different keys, neither is X's paid API

Neither backend uses X's official paid API. That is the whole point. The two paths get the same data a
different way.

- **`session` uses no key at all.** It uses your own X login cookies and drives x.com's web search like
  a logged-in person, automated. That automation is what breaks X's terms, and that is why the account
  can get suspended. Free, risky.
- **`xai` uses an xAI key, not an X key.** xAI is a separate company. The key is for Grok's search
  service, not for X's data API. There is no X login behind it, so there is no X account to suspend.
  Cheap, safe.

So the free path is cookie automation and carries ban risk. The paid path runs through a different
vendor and carries none. The official X API, the one that does not get you banned, is the
tens-of-thousands-a-month bill this tool exists to skip.

Full guide: `search/README.md`.

## How it works

Two free pieces, glued together.

1. **Finding posts.** To find who is posting about something, the tool either drives X's own search
   through your logged-in session (the `session` backend) or asks xAI's search service (the `xai`
   backend). Either way it ends up with a set of post links.
2. **Reading posts.** Each post is read through `cdn.syndication.twimg.com`, the public endpoint that
   powers embedded tweets across the web. It returns a post's text, author, and engagement as clean
   JSON, with no login and no key. That is the part that makes it free.

Find the posts, then read each one through the free public endpoint. No paid X API anywhere in the loop.

## Where the idea came from

The data you want, who is saying what on X, sits behind X's official API. That API costs tens of
thousands of dollars a month at the volume you would actually need.

You do not need it. X already hands the same data out for free in two places. Its own web app reads
posts through endpoints that work as long as you are logged in, no key. And every tweet embedded on any
website is served by a public endpoint that needs no login at all. Both were sitting in plain sight.

### Why those endpoints stay open

X gates this data behind the paid API, but it cannot close the two free doors without breaking its own
product. The logged-in web endpoints are what x.com itself runs on. Kill them and the website dies. The
syndication endpoint at `cdn.syndication.twimg.com` renders embedded tweets on every news site, blog,
and forum. Kill it and tweet embeds break everywhere. The data leaks through the parts of X that have to
stay public. The tool reads the same doors the browser already uses.

We tested the idea on the noisiest thing we could find: the flood of new Solana coins minting every
minute, each with an X link attached. The free path worked end to end, find the posts, read them through
the public endpoint, all for free. Then the obvious part, the same find-and-read works for anything: a
person, a company, an event, not just coins. That is the tool.

## How it compares

The two doors are not secret. Other tools use them. `twscrape` and `twikit` drive a logged-in account
against X's internal endpoints, the same primitive as the `session` backend. The
`cdn.syndication.twimg.com` read trick has been written up for years and runs in deployed services like
`xreader`. The primitives are known. What is built on them here is the part that is not.

- **Two backends, one tool.** The other scrapers pick one path and live on it. This one runs a
  logged-in path and a no-login paid path as named, swappable backends.
- **`both` merges them.** Run `session` and `xai` together and combine the results. One sourcing path
  misses what the other catches. Merging widens the net in a single search.
- **`xai` needs no X account.** The logged-in scrapers all carry account-ban risk. They all run on a
  logged-in account. The `xai` backend sources post links through xAI's search service with no X login,
  so there is no account to lose.
- **Topic first, not object first.** Most tools archive a known handle or a known tweet. This one starts
  from a topic and finds who is posting about it.

One known limit comes with the read door. `cdn.syndication.twimg.com` returns HTTP 200 with an empty
body when it fails. Cloud IPs see frequent blanks and 404s. Running from a logged-in user's own machine
stays close to the case that works. Heavy cloud-side use does not.

## One example of what you can build on it: a Solana coin tracker

`engine/` is a prototype that points the same idea at one specific use: the flood of new Solana coins
launching every minute. It is an **example**, one application, not the purpose of the project. The
search tool above is the general thing.

Given the firehose, the engine:
1. Drops the ~95% that is junk with a self-calibrating filter (it reads the live stream's own
   distribution instead of a fixed cutoff) plus name dedup, before any network call.
2. For the survivors, finds the X account behind each coin, checks whether the linked tweet really
   mentions the coin or is riding a stranger's, and flags wallets spraying many coins.
3. Ranks what is left.

It also has a **learning loop** (`outcomes.py`, `outcomes_calibrate.py`): it logs every coin it scored,
hours later reads on-chain what actually happened (died, still trading, graduated to a real DEX), labels
the outcome, and a calibrator learns which signals predicted the good ones and proposes weight changes.
That is what lets it improve instead of staying a fixed guess. Honest limit: the loop only has a verdict
once coins have aged a few hours. Details in `docs/` and `engine/README.md`.

## Layout

```
search/   xsearch: find who's talking about anything on X (the product)
engine/   example app: a Solana coin tracker + a learning loop that scores its own picks against outcomes
legacy/   retired Node browser-capture tool
docs/     design notes (engine design, outcome-feedback-loop spec)
```
