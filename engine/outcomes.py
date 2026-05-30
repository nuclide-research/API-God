"""Outcome-feedback loop (P1+P2): the engine's learning layer.

The engine scores coins but never learns if its picks were right. This logs every scored coin, then
later checks on-chain what actually happened, and labels it. P3 (the weight calibrator) is in
outcomes_calibrate.py and tunes the engine's weights from these labels.

Free data:
  - Solana public RPC: last on-chain activity (dead/alive), holder concentration.
  - DexScreener (free, public): price / liquidity / volume for LISTED coins -> RUG vs MOON.

Usage:
    python outcomes.py collect <mint>     # one-shot: check + label a single mint (test surface)
    python outcomes.py run                 # check every recorded coin past its window
    python outcomes.py stats               # label distribution + survival/moon-rate by score band
record(coin) is called by the engine at scoring time (engine/live.py).
"""
import sqlite3, json, time, sys, os
import requests

RPC = os.environ.get("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
DB  = os.environ.get("OUTCOMES_DB", os.path.join(os.path.dirname(__file__), "outcomes.db"))
CHECK_AFTER_H  = 6          # first outcome check at T+6h
DEAD_SILENCE_H = 6          # no on-chain activity in this many hours = abandoned
GRADUATED_DEXES = {"raydium", "raydium-clmm", "orca", "meteora", "jupiter", "fluxbeam"}  # real DEXes off the curve (#9)

def _db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS scored (
        mint TEXT PRIMARY KEY, creator TEXT, score REAL, features TEXT, scored_at REAL,
        outcome TEXT, checked_at REAL, last_trade_age_h REAL, top1_pct REAL, top5_pct REAL,
        migrated INTEGER, price_usd REAL, liq_usd REAL, vol24h REAL, mcap REAL, raw TEXT)""")
    for col, typ in [("migrated","INTEGER"),("price_usd","REAL"),("liq_usd","REAL"),("vol24h","REAL"),("mcap","REAL")]:
        try: c.execute(f"ALTER TABLE scored ADD COLUMN {col} {typ}")   # tolerate older DBs
        except sqlite3.OperationalError: pass
    return c

def record(coin):
    """Called by the engine when it scores a coin. coin = {mint, creator, score, features dict}."""
    c = _db()
    try:
        c.execute("INSERT OR IGNORE INTO scored(mint,creator,score,features,scored_at) VALUES(?,?,?,?,?)",
                  (coin.get("mint"), coin.get("creator"), coin.get("score"),
                   json.dumps(coin.get("features", {})), time.time()))
        c.commit()
    finally:
        c.close()                                        # always close, even on error (#12)

def _rpc(method, params, retries=4):
    for i in range(retries):
        try:
            r = requests.post(RPC, timeout=15, json={"jsonrpc":"2.0","id":1,"method":method,"params":params})
            if r.status_code == 429: time.sleep(float(r.headers.get("Retry-After") or 1.5*(i+1))); continue   # honor Retry-After (#13)
            res = r.json()
            if "result" in res: return res["result"]
            time.sleep(1.0*(i+1))
        except Exception: time.sleep(1.0*(i+1))
    return None

def _dexscreener(mint):
    """Free price/liquidity for LISTED coins. No pairs returned -> the coin never migrated off the curve."""
    try:
        pairs = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=12).json().get("pairs") or []
    except Exception:
        return {}
    if not pairs: return {"migrated": 0}
    real = [p for p in pairs if (p.get("dexId") or "").lower() in GRADUATED_DEXES]  # whitelist real DEXes (#9)
    use = real or pairs
    deepest = max(use, key=lambda x: (x.get("liquidity") or {}).get("usd") or 0)
    pu = deepest.get("priceUsd")
    return {"migrated": 1 if real else 0,            # 1 only if listed on a real DEX, not the pump.fun curve
            "dex": deepest.get("dexId"),
            "price_usd": float(pu) if pu not in (None, "") else None,   # preserve a real 0 (#15)
            "liq_usd": round(sum((p.get("liquidity") or {}).get("usd") or 0 for p in use), 2),
            "vol24h": round(sum((p.get("volume") or {}).get("h24") or 0 for p in use), 2),
            "mcap": deepest.get("marketCap")}

def collect_outcome(mint):
    out = {"last_trade_age_h":None,"top1_pct":None,"top5_pct":None,
           "migrated":0,"price_usd":None,"liq_usd":None,"vol24h":None,"mcap":None}
    sigs = _rpc("getSignaturesForAddress", [mint, {"limit":1}])
    if sigs and sigs[0].get("blockTime"):
        out["last_trade_age_h"] = round((time.time() - sigs[0]["blockTime"]) / 3600, 2)
    sup = _rpc("getTokenSupply", [mint]); largest = _rpc("getTokenLargestAccounts", [mint])
    if sup and largest and largest.get("value"):
        total = float(sup["value"]["amount"])
        amts = sorted((float(a["amount"]) for a in largest["value"]), reverse=True)
        if total > 0 and amts:                       # guard zero supply -> no garbage percentage (#3)
            out["top1_pct"] = round(100*amts[0]/total, 1); out["top5_pct"] = round(100*sum(amts[:5])/total, 1)
    out.update(_dexscreener(mint))
    return out

def label(o):
    """Listed coins (DexScreener pair) are labeled by liquidity/volume/mcap; coins still on the curve by
    on-chain activity. A single snapshot can't tell 'rugged' from 'never took off' (both read low-liquidity),
    so we don't claim RUG here. Real RUG = liquidity DROPPING across the T+6/24/72h checks, which the
    repeated-check design enables (P2.5)."""
    liq = o.get("liq_usd") or 0; vol = o.get("vol24h") or 0; mc = o.get("mcap") or 0
    concentrated = (o.get("top1_pct") or 0) > 90 or (o.get("top5_pct") or 0) > 98
    if o.get("migrated"):                                # has a tradeable pair
        if liq < 1000 and vol < 100: return "DEAD"       # listed but empty (dead or already drained)
        if concentrated: return "ALIVE-CONCENTRATED"     # graduated but whale-held -> not "good" (#10)
        if vol > 5000 and mc > 50000 and liq > 10000: return "MOON"   # real liquidity + volume + cap
        return "FLAT"                                    # listed, middling
    age = o.get("last_trade_age_h")
    if age is None: return "UNKNOWN"
    if age > DEAD_SILENCE_H: return "DEAD"               # died on the bonding curve (the usual fate)
    if concentrated: return "ALIVE-CONCENTRATED"
    return "ALIVE"

def _apply(mint, c):
    o = collect_outcome(mint); lab = label(o)
    c.execute("""UPDATE scored SET outcome=?,checked_at=?,last_trade_age_h=?,top1_pct=?,top5_pct=?,
                 migrated=?,price_usd=?,liq_usd=?,vol24h=?,mcap=?,raw=? WHERE mint=?""",
              (lab, time.time(), o["last_trade_age_h"], o["top1_pct"], o["top5_pct"],
               o["migrated"], o["price_usd"], o["liq_usd"], o["vol24h"], o["mcap"], json.dumps(o), mint))
    c.commit(); return lab, o

def run():
    c = _db()
    try:
        now = time.time()
        due = c.execute("SELECT mint FROM scored WHERE (outcome IS NULL OR outcome='UNKNOWN') AND scored_at <= ?",
                        (now - CHECK_AFTER_H*3600,)).fetchall()   # also recheck UNKNOWN (#8)
        print(f"{len(due)} coins due for outcome check")
        for (mint,) in due:
            lab, o = _apply(mint, c)
            print(f"  {mint[:14]}.. -> {lab}  (age {o['last_trade_age_h']}h, migrated {o['migrated']}, liq ${o['liq_usd']})")
    finally:
        c.close()

def stats():
    """ALIVE-CONCENTRATED is intentionally NOT counted as survived: it is a rug-risk outcome,
    matching the calibrator's GOOD set ({MOON, FLAT, ALIVE})."""
    c = _db()
    try:
        rows = c.execute("SELECT outcome, COUNT(*) FROM scored WHERE outcome IS NOT NULL GROUP BY outcome").fetchall()
        print("label distribution:", dict(rows))
        bands = c.execute("""SELECT CASE WHEN score>=4 THEN 'high(>=4)' WHEN score>=1 THEN 'mid(1-3)' ELSE 'low(<1)' END band,
                                    COUNT(*) n,
                                    SUM(outcome IN ('MOON','FLAT','ALIVE')) survived,
                                    SUM(outcome='MOON') mooned
                             FROM scored WHERE outcome IS NOT NULL GROUP BY band""").fetchall()
        print("by engine score band (the backtest: does a higher score predict a better outcome?):")
        for band, n, surv, moon in bands:
            print(f"  {band:10} n={n}  survived {100*(surv or 0)//max(n,1)}%  mooned {moon or 0}")
    finally:
        c.close()

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "collect" and len(sys.argv) > 2:
        o = collect_outcome(sys.argv[2]); print(json.dumps({**o, "label": label(o)}, indent=1))
    elif cmd == "run": run()
    elif cmd == "stats": stats()
    else: print(__doc__)
