# xsearch: find who's talking about anything on X

`xsearch` finds the people posting about a topic, a coin, or a contract address on X (Twitter). You get
back a clean list: who they are, what they said, and how much engagement each post got.

It does this **without paying for X's API**. That API runs into tens of thousands of dollars a month for
this kind of access. You get the same results for free, or for a fraction of a cent.

## Two ways to search

You choose how it searches with `--backend`:

| Backend | Cost | Uses your X account? | Best for |
|---------|------|----------------------|----------|
| `session` (default) | Free | Yes (you log in once) | Your own searches |
| `xai` | about $0.005 per search | No | Running it a lot, or hands-off |
| `both` | about $0.005 per search | Yes | When you want the most complete results |

- **session** uses your own X login to read X's normal search. It is free. It runs on your account, so
  keep your searches occasional. X flags accounts that automate too hard.
- **xai** uses a paid search service (about half a cent per search) and never touches your account.
- **both** runs the two together and merges the results. Anything both of them found is marked `[SX]`.
  Those are the ones most likely to matter.

## Setup

Install once:

```
pip install -r requirements.txt
playwright install chromium
```

To use the free `session` backend, log in once:

```
python xsearch.py --login
```

A browser window opens. Log into X normally, then come back and press Enter. Your session is saved, so
you never have to log in again.

To use the `xai` backend, set a key (get one at console.x.ai):

```
export XAI_API_KEY=your_key_here
```

You only need to set up the backend you want to use.

## Use it

```
python xsearch.py "solana depin"                     # a topic
python xsearch.py '$GIGA'                             # a coin ticker
python xsearch.py "<contract address>"               # a contract address (the most precise search)

python xsearch.py "solana" --backend xai             # use the paid, no-account-risk search
python xsearch.py "solana" --backend both            # use both and merge
python xsearch.py "solana" --json --out results.jsonl   # save full data instead of the table
```

By default you get a readable list of accounts, ranked by engagement, like this:

```
42 accounts for 'solana'  (backend=both: 30 session, 18 xai, 6 in both)

[SX] @someone       ♥ 820 ↻ 140  the post text...
[S ] @another        ♥   4 ↻   0  the post text...
[ X] @third          ♥  50 ↻   2  the post text...
```

The tag shows who found each account: `S` = the free search, `X` = the paid search, `SX` = both.

## Options

- `--backend session|xai|both`: which search to use (default: `session`)
- `--tab live|top`: newest posts, or most-engaged (default: `live`)
- `--pages N`: how many times to scroll for more results (default: 5)
- `--sort engagement|recent`: how to rank the output
- `--out FILE` / `--json`: write or print every post as structured data
- `--limit N`: how many accounts to show in the readable list

## Tips

- A **contract address** gives the cleanest results. Broad words like "solana" return mostly promo spam.
  That is what people post under broad crypto terms.
- For your own occasional searches, the free `session` backend is all you need.
- If you want to run searches constantly or unattended, use `--backend xai`. It costs about half a cent
  each and keeps your account out of it.
