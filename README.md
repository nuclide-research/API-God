# API-God

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

- **session**: free. Uses your own X login.
- **xai**: about half a cent per search. No login, no account risk (needs an xAI key).
- **both**: runs the two together and merges the results.

Full guide: `search/README.md`.

## One example of what you can build on it: a Solana coin tracker

`engine/` is a prototype that points the same idea at one specific use. It watches new Solana coins the
moment they launch, finds the X account behind each, and ranks them. It is an **example**, one
application of the search tool, not the purpose of the project. The search tool is the general thing.

## Layout

```
search/   xsearch: find who's talking about anything on X
engine/   example app: a Solana coin-tracking prototype built on the same idea
legacy/   retired Node browser-capture tool
docs/     design notes
```
