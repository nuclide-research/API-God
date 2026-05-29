# engine: Solana coin tracker (example app)

One application of the API-God idea: watch new Solana coins as they launch and rank the few worth a
look. This is a prototype and an example, not the main product (the search tool in `../search` is).

## Pipeline (`live.py`)

A new coin mints, and it runs through:

1. **gate** a self-calibrating filter (zones drawn from the live stream's own distribution, not a fixed
   cutoff) plus name dedup. Drops ~95% as junk before any network call.
2. **enrich** fetch the coin's metadata, pull its X link.
3. **resolve** read the linked tweet for free (the `cdn.syndication.twimg.com` endpoint) and check: does
   the tweet actually mention the coin, or is it riding a stranger's? does the handle match the author?
4. **score** plus a cluster pass that penalizes one wallet or one account spraying many coins.
5. **discovery** (optional, needs an xAI key) count independent accounts posting the coin's contract.

## Learning loop (`outcomes.py`, `outcomes_calibrate.py`)

The scorer is a guess until it learns whether its picks pan out. The loop closes that, on free data:

- **record** log every coin it scored (wired into the engine).
- **collect + label** hours later, read on-chain what happened (free Solana RPC + DexScreener): dead,
  still trading, graduated to a real DEX. Labels `DEAD` / `ALIVE` / `FLAT` / `MOON`.
- **calibrate** once enough coins are labeled, a logistic regression learns which signals predicted the
  good outcomes and proposes capped weight changes (interpretable, not a black box).

## Run

```
pip install -r requirements.txt
python live.py                 # watch + score live (~9 min); records its picks
python outcomes.py run          # hours later: label the recorded picks
python outcomes_calibrate.py    # once enough labels exist: learn weight changes
```

## Status

Built and reviewed. Honest gaps: the loop needs coins to age a few hours before it says anything; rug
detection (liquidity dropping over time) and wiring the learned weights back into the scorer are the
remaining steps. Design notes in `../docs/superpowers/specs/`.
