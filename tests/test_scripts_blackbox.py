"""Black-box characterization of the scripts. Runs replay.py as a subprocess on the committed
_ts fixture and asserts its summary shape. This locks current behavior BEFORE the import refactor
(Task 2), so the refactor cannot silently change what the script produces. Offline: the fixture's
four mints stay in the green zone (fewer than the 5-sample warmup), so no IPFS/syndication call fires."""
import subprocess, sys, os, pathlib, re

ENG = pathlib.Path(__file__).parent.parent / "engine"
TD = pathlib.Path(__file__).parent.parent / "testdata"


def test_replay_blackbox_runs():
    n = sum(1 for l in open(TD / "mints_ts.jsonl") if l.strip())
    assert n < 5, "fixture grew past the 5-sample warmup; black-box subprocess would hit the network"
    out = subprocess.run(
        [sys.executable, str(ENG / "replay.py"), str(TD / "mints_ts.jsonl")],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": str(ENG)},
    )
    assert out.returncode == 0, out.stderr
    assert "REPLAY (core)" in out.stdout
    assert re.search(r"dedup \d+", out.stdout), out.stdout
