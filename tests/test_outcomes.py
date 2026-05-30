"""Outcome-loop fixes, all offline (RPC/DexScreener monkeypatched):
#3 zero-supply, #8 UNKNOWN recheck, #9 DEX whitelist, #10 migrated+concentrated,
#13 Retry-After, #15 price-0."""
import time
import outcomes
from conftest import FakeResp


def test_label_migrated_concentrated_not_good():            # #10 (red today: labeled MOON)
    lab = outcomes.label({"migrated": 1, "liq_usd": 25000, "vol24h": 80000, "mcap": 120000, "top1_pct": 96.0})
    assert lab == "ALIVE-CONCENTRATED"


def test_label_migrated_clean_is_moon():                    # #10 regression: clean migrated still MOON
    lab = outcomes.label({"migrated": 1, "liq_usd": 25000, "vol24h": 80000, "mcap": 120000, "top1_pct": 10.0})
    assert lab == "MOON"


def test_dexscreener_oncurve_not_migrated(fake_http, td):   # #9 regression: pumpfun stays on-curve
    fake_http({"dexscreener": FakeResp(200, td("dexscreener_oncurve.json"))})
    assert outcomes._dexscreener("Mint")["migrated"] == 0


def test_dexscreener_migrated_whitelisted(fake_http, td):   # #9: raydium is a real DEX
    fake_http({"dexscreener": FakeResp(200, td("dexscreener_migrated.json"))})
    assert outcomes._dexscreener("Mint")["migrated"] == 1


def test_dexscreener_unknown_dexid_not_migrated(fake_http):  # #9 (red today: blocklist misses "pump.fun")
    body = {"pairs": [{"dexId": "pump.fun", "priceUsd": "0.001", "liquidity": {"usd": 100}, "volume": {"h24": 10}, "marketCap": 1000}]}
    fake_http({"dexscreener": FakeResp(200, body)})
    assert outcomes._dexscreener("Mint")["migrated"] == 0


def test_price_zero_preserved(fake_http):                   # #15 (red today: 0 -> None)
    body = {"pairs": [{"dexId": "raydium", "priceUsd": "0", "liquidity": {"usd": 5}, "volume": {"h24": 0}, "marketCap": 0}]}
    fake_http({"dexscreener": FakeResp(200, body)})
    assert outcomes._dexscreener("Mint")["price_usd"] == 0.0


def test_zero_supply_no_garbage(monkeypatch):               # #3 (red today: top1_pct = 10000.0)
    def fake_rpc(method, params, retries=4):
        if method == "getSignaturesForAddress": return [{"blockTime": time.time()}]
        if method == "getTokenSupply": return {"value": {"amount": "0"}}
        if method == "getTokenLargestAccounts": return {"value": [{"amount": "100"}]}
        return None
    monkeypatch.setattr(outcomes, "_rpc", fake_rpc)
    monkeypatch.setattr(outcomes, "_dexscreener", lambda m: {"migrated": 0})
    o = outcomes.collect_outcome("Mint")
    assert o["top1_pct"] is None


def test_unknown_is_rechecked(tmp_path, monkeypatch):       # #8 (red today: UNKNOWN never re-queried)
    monkeypatch.setattr(outcomes, "DB", str(tmp_path / "o.db"))
    monkeypatch.setattr(outcomes, "collect_outcome",
                        lambda m: {"last_trade_age_h": 1.0, "migrated": 0, "top1_pct": None, "top5_pct": None,
                                   "price_usd": None, "liq_usd": None, "vol24h": None, "mcap": None})
    monkeypatch.setattr(outcomes, "label", lambda o: "ALIVE")
    c = outcomes._db()
    c.execute("INSERT INTO scored(mint,score,scored_at,outcome) VALUES('m',1,?,'UNKNOWN')", (time.time() - 7 * 3600,))
    c.commit(); c.close()
    outcomes.run()
    c = outcomes._db(); lab = c.execute("SELECT outcome FROM scored WHERE mint='m'").fetchone()[0]; c.close()
    assert lab == "ALIVE"


def test_rpc_honors_retry_after(monkeypatch):               # #13 (red today: ignores header)
    calls = {"n": 0}; slept = []
    class R429:
        status_code = 429; headers = {"Retry-After": "2"}
        def json(self): return {}
    class ROK:
        status_code = 200; headers = {}
        def json(self): return {"result": "ok"}
    def fake_post(url, *a, **k):
        calls["n"] += 1
        return R429() if calls["n"] == 1 else ROK()
    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(outcomes.time, "sleep", lambda s: slept.append(s))
    assert outcomes._rpc("m", []) == "ok" and 2.0 in slept


def test_record_closes_connection_on_error(monkeypatch):    # #12
    import pytest
    closed = {"v": False}
    class C:
        def execute(self, *a, **k): raise RuntimeError("boom")
        def close(self): closed["v"] = True
    monkeypatch.setattr(outcomes, "_db", lambda: C())
    with pytest.raises(RuntimeError):
        outcomes.record({"mint": "m", "features": {}})
    assert closed["v"] is True       # try/finally closed the connection despite the error
