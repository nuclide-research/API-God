"""Coverage for live.py's stream loop (the hot path), driven by the fake websocket so it is fully
offline. A single small dev-buy stays in the green zone (warmup), so no IPFS/syndication call fires."""
import json, time
from concurrent.futures import ThreadPoolExecutor
import live
from conftest import FakeWS


async def test_stream_once_processes_a_frame(tmp_path, monkeypatch):
    for g in (live.events, live.devbuf, live.survivors):
        g.clear()
    live.gaps.clear(); live.zone_count.clear(); live.last_name.clear()
    monkeypatch.setattr(live, "RAW", str(tmp_path / "cap.jsonl"))
    frame = {"mint": "M1", "traderPublicKey": "W1", "name": "Alpha", "symbol": "ALP", "solAmount": 0.2, "uri": "ipfs://a"}
    ws = FakeWS([json.dumps({"message": "subscribed"}), json.dumps(frame)])   # ack frame, then one mint
    ex = ThreadPoolExecutor(max_workers=2)
    res = await live.stream_once(ws, ex, time.monotonic() + 5)
    ex.shutdown(wait=True)
    assert res == "done"
    assert len(live.events) == 1 and live.events[0]["_ts"] > 0      # the mint captured + _ts stamped
    assert live.gaps["suppressed_green"] == 1                       # single small buy -> green -> suppressed
    assert sum(1 for _ in open(live.RAW)) == 1                      # written to the capture file
