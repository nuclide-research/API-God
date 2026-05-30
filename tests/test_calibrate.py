"""Calibrator fixes: #5 no crash on extreme class imbalance, #16 bin the independent count so it
matches discrete_features for mutual-info. Plus a functional check that the calibrator recovers
the right sign (serial farming should read negative) on synthetic data."""
import sqlite3, json, random
import outcomes_calibrate as oc


def _imbalanced_db(path, n_bad, n_good):
    c = sqlite3.connect(path); c.execute("CREATE TABLE scored(features TEXT, outcome TEXT)")
    for _ in range(n_bad):  c.execute("INSERT INTO scored VALUES(?,?)", (json.dumps({"zone": "amber"}), "DEAD"))
    for _ in range(n_good): c.execute("INSERT INTO scored VALUES(?,?)", (json.dumps({"verified": True}), "MOON"))
    c.commit(); c.close()


def test_extreme_imbalance_no_crash(tmp_path):            # #5
    _imbalanced_db(str(tmp_path / "o.db"), 28, 2)
    oc.calibrate(str(tmp_path / "o.db"), out=None)        # must not raise


def test_featurize_bins_independent():                    # #16
    assert oc._featurize('{"independent": 5}')[2] == 1.0
    assert oc._featurize('{"independent": 1}')[2] == 0.0


def test_recovers_serial_negative(tmp_path):              # functional sanity
    p = str(tmp_path / "s.db")
    c = sqlite3.connect(p); c.execute("CREATE TABLE scored(features TEXT, outcome TEXT)")
    rng = random.Random(1)
    for _ in range(200):
        verified = rng.random() < 0.4; indep = rng.randint(0, 5); serial = rng.randint(1, 4)
        good = (verified or indep >= 2) and not (serial > 1)
        if rng.random() < 0.15: good = not good
        c.execute("INSERT INTO scored VALUES(?,?)",
                  (json.dumps({"verified": verified, "independent": indep, "serial": serial}), "MOON" if good else "DEAD"))
    c.commit(); c.close()
    rep = oc.calibrate(p, out=None)
    assert rep is not None and rep["features"]["serial"]["coef"] < 0


def test_mutual_info_marks_all_features_discrete(tmp_path, monkeypatch):   # #16 second half
    import sklearn.feature_selection as fs
    captured = {}
    orig = fs.mutual_info_classif
    def spy(X, y, **kw):
        captured["df"] = kw.get("discrete_features")
        return orig(X, y, **kw)
    monkeypatch.setattr(fs, "mutual_info_classif", spy)
    p = str(tmp_path / "c.db")
    c = sqlite3.connect(p); c.execute("CREATE TABLE scored(features TEXT, outcome TEXT)")
    rng = random.Random(2)
    for _ in range(60):
        good = rng.random() < 0.5
        c.execute("INSERT INTO scored VALUES(?,?)",
                  (json.dumps({"verified": good, "independent": rng.randint(0, 4), "serial": rng.randint(1, 3)}),
                   "MOON" if good else "DEAD"))
    c.commit(); c.close()
    oc.calibrate(p, out=None)
    assert captured["df"] == [True, True, True, True]    # independent stays declared-discrete
