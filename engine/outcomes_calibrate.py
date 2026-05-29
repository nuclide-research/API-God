"""Outcome-loop P3: the weight calibrator.

Reads the labeled scored coins, learns which FEATURES actually predicted a good outcome (interpretable
logistic regression + per-feature information gain), and writes capped weight updates to
config/weights.json. This is what turns the engine from a static rule set into one that learns. It runs
once the loop has labeled enough coins (it refuses to act on too little data).

    python outcomes_calibrate.py             # train on labeled history -> propose weight updates
    python outcomes_calibrate.py --selftest  # run on synthetic data to prove the pipeline works now
"""
import sqlite3, json, os, sys
import numpy as np
from outcomes import DB

WEIGHTS = os.path.join(os.path.dirname(__file__), "config", "weights.json")
GOOD = {"MOON", "FLAT", "ALIVE"}   # "survived / good"; DEAD and ALIVE-CONCENTRATED (rug-risk) are bad
MIN_SAMPLES = 30
DELTA_CAP = 0.10                                         # +/-10% weight change per cycle (no whiplash)
FEATURES = ["zone_red", "verified", "independent", "serial"]

def _featurize(features_json):
    f = json.loads(features_json or "{}")
    return [1.0 if f.get("zone") == "red" else 0.0,
            1.0 if f.get("verified") else 0.0,
            float(f.get("independent") or 0),
            1.0 if (f.get("serial") or 1) > 1 else 0.0]

def _load(db):
    c = sqlite3.connect(db)
    try:
        rows = c.execute("SELECT features, outcome FROM scored WHERE outcome IS NOT NULL AND outcome != 'UNKNOWN'").fetchall()
    except sqlite3.OperationalError:        # no ledger / no scored table yet
        rows = []
    c.close()
    X = np.array([_featurize(f) for f, _ in rows]) if rows else np.empty((0, len(FEATURES)))
    y = np.array([1 if o in GOOD else 0 for _, o in rows])
    return X, y

def calibrate(db=DB, out=WEIGHTS):
    X, y = _load(db)
    n = len(y)
    if n < MIN_SAMPLES or len(set(y.tolist())) < 2:
        print(f"not enough labeled data to calibrate (have {n}, need >={MIN_SAMPLES} with both outcomes). "
              f"Keeping current weights.")
        return None
    from sklearn.linear_model import LogisticRegression
    from sklearn.feature_selection import mutual_info_classif
    from sklearn.model_selection import train_test_split
    strat = y if min(np.bincount(y)) > 1 else None
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=strat)
    m = LogisticRegression(class_weight="balanced", max_iter=2000).fit(Xtr, ytr)
    tr, te = m.score(Xtr, ytr), m.score(Xte, yte)
    mi = mutual_info_classif(X, y, discrete_features=[True, True, True, True], random_state=42)  # independent is a small count
    coefs = m.coef_[0]; mx = max(abs(coefs)) or 1.0
    flags = []
    if tr - te > 0.15: flags.append("overfit/survivorship-bias: test acc >15% below train -> do not trust update")
    report = {"samples": n, "train_acc": round(float(tr), 3), "test_acc": round(float(te), 3),
              "good_rate": round(float(y.mean()), 3), "features": {}, "flags": flags}
    for i, feat in enumerate(FEATURES):
        report["features"][feat] = {"coef": round(float(coefs[i]), 3),
                                    "info_gain": round(float(mi[i]), 3),
                                    "weight_delta": round(DELTA_CAP * coefs[i] / mx, 4)}  # capped, scaled by relative coef
    if out and not flags:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        json.dump(report, open(out, "w"), indent=1); print(f"weights updated -> {out}")
    elif flags:
        print("NOT writing weights:", "; ".join(flags))
    print(json.dumps(report, indent=1))
    return report

def selftest():
    """Synthetic data where a good outcome is driven by verified + independent posters and hurt by serial
    farming. The calibrator should recover those signs (positive verified/independent, negative serial)."""
    import tempfile, random
    p = tempfile.mktemp(suffix=".db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE scored(features TEXT, outcome TEXT)")
    rng = random.Random(1)
    for _ in range(200):
        verified = rng.random() < 0.4; indep = rng.randint(0, 5); serial = rng.randint(1, 4); red = rng.random() < 0.3
        good = (verified or indep >= 2) and not (serial > 1)
        if rng.random() < 0.15: good = not good            # 15% label noise
        feats = json.dumps({"zone": "red" if red else "amber", "verified": verified,
                            "independent": indep, "serial": serial})
        c.execute("INSERT INTO scored(features, outcome) VALUES(?,?)", (feats, "MOON" if good else "DEAD"))
    c.commit(); c.close()
    print("=== selftest: synthetic truth = verified+independent help, serial hurts ===")
    calibrate(p, out=None)

if __name__ == "__main__":
    if "--selftest" in sys.argv: selftest()
    else: calibrate()
