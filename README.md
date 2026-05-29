# API-God: Solana Memecoin Signal Engine

A headless engine that watches every new Solana memecoin the moment it mints, figures out the X
account/tweet behind it, scores how much it deserves attention, and ranks the survivors. It runs on
**free, key-less public data**: no $30k/mo X API, no paid firehose, no browser.

> Status: **validated Python prototype.** The logic below has been proven on real live mints. The
> production target is a single Go binary (see `docs/superpowers/specs/`); the prototype is the
> reference implementation. The retired Node browser-capture tool lives in `legacy/`.

## The idea (why it costs nothing)

You don't search for the signal, the coin hands it to you. Every pump.fun mint embeds a metadata link;
that metadata carries the project's X URL; and any public tweet resolves through Twitter's own embed CDN
with no auth. Three free hops:

```
pump.fun firehose  ->  token metadata JSON  ->  X syndication CDN
(new mint, WS)         (the twitter: link)      (the actual tweet, no key)
```

## Pipeline

```
source -> gate -> enrich -> resolve -> score -> discovery
```

1. **source**: one PumpPortal websocket (`subscribeNewToken`), free. Mint, creator wallet, name, symbol,
   metadata URI, dev-buy size.
2. **gate**: SPC self-calibrating zones (green/amber/red from the live stream's own p80/p95) + name
   dedup. Suppresses ~75% as noise before any network call. The cutoff is computed, not guessed.
3. **enrich**: fetch the metadata JSON (IPFS gateway pool), pull the `twitter` link.
4. **resolve**: resolve the linked tweet via the syndication CDN. Branches on the failure modes
   (404 / tombstone / empty). Checks: does the tweet reference the coin, or is it riding a stranger's
   tweet (impersonation)? Does the URL handle match the real author (spoof)?
5. **score**: zone + verification + serial-wallet/author cluster penalty (catches farmers spraying many
   coins). Tombstone and big-buy-no-socials are flagged, not rewarded.
6. **discovery**: *(needs `XAI_API_KEY`)* searches the contract address (and ticker) to count
   independent accounts posting it, the one signal the creator does not control. Gated to candidates that
   survived the free stages, so the paid call only fires on ~8% of the firehose.

The control-loop shape (deadband, alarm zones, watchdog, historian) is borrowed from industrial SCADA.

## Run it

```bash
pip install -r engine/requirements.txt
cd engine

python replay.py /path/to/mints.jsonl   # replay scoring over captured mints (no network for source)
python live.py                          # live: stream pump.fun, score in real time (~9 min run)
python stress.py                        # adversarial battery against the decision logic
python solana_search.py                 # standalone: search X for a topic, enrich results for free
```

`discovery` and `solana_search` go live only when `XAI_API_KEY` (developer key from console.x.ai) is set;
without it they cleanly no-op so the rest runs free.

## What it does NOT do

- It does not trade. It produces a ranked list of attention; nothing touches a wallet.
- It does not yet track outcomes (whether a scored coin later rugged or mooned). That feedback loop is
  scoped in `docs/superpowers/specs/` and is the next build.
- It is a strong spam filter, not yet a trust scorer. Every positive signal except `discovery` is
  something the coin's own creator controls, and is therefore forgeable. The independent and outcome
  signals are what turn filtering into trust.

## Layout

```
engine/      validated Python prototype (engine_core + live/replay/discovery/stress/search)
legacy/      retired Node/Playwright browser-capture tool
docs/        design specs (engine design, outcome-loop scope)
```
