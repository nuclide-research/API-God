#!/usr/bin/env python3
"""Full live test of the engine.

Runs the live pipeline for a short budget against the real PumpPortal firehose, replays the capture
it produced, and asserts the two agree on dedup and zoning. Those are the deterministic stages, so a
faithful replay must reproduce them exactly. This is the regression guard for the live/replay dedup
reconciliation (engine_core.dedup_name + the _ts stamp).

  python engine/livetest.py                 # ~120s live capture, then replay + reconcile
  ENGINE_BUDGET=60 python engine/livetest.py

Keyless and network-dependent (PumpPortal + IPFS + syndication). Exit 0 = PASS, 1 = FAIL.

What it does NOT assert: the resolve/verify/cluster counts. Those hit live X state that drifts between
the live run and the replay (a tweet can be deleted or change between the two), so they are expected to
differ. The dedup and zone counts are computed purely from the capture and must match to the digit.
"""
import os, re, sys, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = "/tmp/mints2.jsonl"          # live.py RAW
SUMMARY = "/tmp/run2_summary.txt"      # live.py SUMMARY
BUDGET = os.environ.get("ENGINE_BUDGET", "120")

def zones(s):
    """Pull (green, amber, red, dedup) from either the live summary or the replay output line."""
    m = (re.search(r"green (\d+) amber (\d+) red (\d+) \| dedup (\d+)", s)
         or re.search(r"green (\d+) \| amber (\d+) \| red (\d+) \| dedup (\d+)", s))
    return tuple(map(int, m.groups())) if m else None

def main():
    env = {**os.environ, "ENGINE_BUDGET": BUDGET, "PYTHONPATH": HERE}

    print(f"[livetest] 1/2 live capture against the firehose, ENGINE_BUDGET={BUDGET}s ...")
    live = subprocess.run([sys.executable, os.path.join(HERE, "live.py")],
                          env=env, capture_output=True, text=True)
    if live.returncode != 0:
        print(f"[livetest] FAIL: live.py exited {live.returncode}\n{live.stderr[-600:]}")
        return 1
    n = sum(1 for _ in open(CAPTURE)) if os.path.exists(CAPTURE) else 0
    if n == 0:
        print("[livetest] FAIL: no mints captured (firehose unreachable?)")
        return 1
    live_z = zones(open(SUMMARY).read()) if os.path.exists(SUMMARY) else None
    print(f"[livetest]     captured {n} mints; live  (green,amber,red,dedup) = {live_z}")

    print("[livetest] 2/2 replay the same capture ...")
    rep = subprocess.run([sys.executable, os.path.join(HERE, "replay.py"), CAPTURE],
                         env=env, capture_output=True, text=True)
    rep_z = zones(rep.stdout)
    print(f"[livetest]     replay (green,amber,red,dedup) = {rep_z}")

    if live_z and rep_z and live_z == rep_z:
        print("[livetest] PASS: live and replay agree on dedup + zoning (reconciliation holds)")
        return 0
    print("[livetest] FAIL: dedup/zone reconciliation mismatch")
    print(f"  live  : {live_z}\n  replay: {rep_z}")
    if rep.returncode != 0:
        print(f"  replay stderr: {rep.stderr[-400:]}")
    return 1

if __name__ == "__main__":
    sys.exit(main())
