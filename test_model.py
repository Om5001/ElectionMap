import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forecast.model.forest import CorrelationWeightedForest  # noqa: E402
from forecast.live import bootstrap_weights, blend_race, market_margin  # noqa: E402
from forecast.features import aggregate_polls  # noqa: E402
from forecast.data.history import build  # noqa: E402


def test_forest_prefers_informative_factor():
    rng = np.random.default_rng(0)
    n = 600
    X = rng.normal(size=(n, 4))
    y = 5 * X[:, 0] + rng.normal(size=n)
    f = CorrelationWeightedForest(n_trees=60, random_state=1, mask_poll_frac=0, cycle_features=()).fit(X, y, ["a", "b", "c", "d"])
    w = {r["feature"]: r["mean"] for r in f.weight_summary()}
    assert w["a"] > 0.5 and w["a"] > 3 * w["b"]
    mu, sd = f.predict(X[:5], ["a", "b", "c", "d"])
    assert np.corrcoef(mu, y[:5])[0, 1] > 0.8


def test_cycle_level_factor_is_penalised():
    rng = np.random.default_rng(1)
    cyc = np.repeat(np.arange(6), 100)
    macro = rng.normal(size=6)[cyc]           # only 6 distinct values
    x = rng.normal(size=600)
    y = 3 * x + rng.normal(size=600) + 0.5 * macro
    X = np.column_stack([x, macro])
    pen = CorrelationWeightedForest(n_trees=40, random_state=0, mask_poll_frac=0, cycle_features=("macro",)).fit(X, y, ["x", "macro"])
    raw = CorrelationWeightedForest(n_trees=40, random_state=0, mask_poll_frac=0, cycle_features=()).fit(X, y, ["x", "macro"])
    wp = {r["feature"]: r["mean"] for r in pen.weight_summary()}
    wr = {r["feature"]: r["mean"] for r in raw.weight_summary()}
    assert wp["macro"] < wr["macro"] and wp["x"] > wp["macro"]


def test_history_is_real_and_sane():
    h = build()
    assert len(h) > 700 and set(h.office) == {"senate", "governor", "house"}
    assert h.margin_actual.abs().max() < 100
    assert h.poll_margin.corr(h.margin_actual) > 0.9


def test_poll_aggregation_weights_recent_large_polls():
    df = pd.DataFrame({"margin": [10, 0], "samplesize": [2000, 300], "days": [1, 20], "grade": [3, 1], "partisan": [False, True]})
    a = aggregate_polls(df)
    assert a["poll_margin"] > 8 and a["poll_sd"] > 0


def test_bootstrap_weights_learn_from_decided_races():
    rows = [{"forest": a + 0.5, "markets": a + 6, "actual": a} for a in np.linspace(-20, 20, 30)]
    w = bootstrap_weights(rows)
    assert w["weights"]["forest"] > w["prior"]["forest"]
    assert bootstrap_weights([])["weights"] == bootstrap_weights([])["prior"]


def test_counted_votes_dominate_late():
    base = {"office": "senate", "forestMargin": 5.0, "sigma": 6.0, "marketProb": 0.7}
    early = blend_race(dict(base, result={"margin": -10, "pctIn": 10}), {"forest": .5, "markets": .5}, 0, None)
    late = blend_race(dict(base, result={"margin": -10, "pctIn": 98}), {"forest": .5, "markets": .5}, 0, None)
    assert early["margin"] > late["margin"] and late["margin"] < -9 and late["pChallenger"] < 0.01
    assert abs(market_margin(0.5, 6)) < 1e-9


def test_pipeline_demo_end_to_end(tmp_path):
    subprocess.run([sys.executable, "tests/make_fixtures.py"], cwd=ROOT, check=True, capture_output=True)
    for night in (None, "60"):
        args = [sys.executable, "-m", "forecast.pipeline", "--demo", "--seed", "1", "--trees", "80", "--out", str(tmp_path)]
        if night:
            args += ["--simulate-night", night]
        subprocess.run(args, cwd=ROOT, check=True, capture_output=True)
        fc = json.loads((tmp_path / "forecast.json").read_text())
        md = json.loads((tmp_path / "model.json").read_text())
        assert 10 <= len(fc["tracker"]) <= 15
        assert {r["office"] for r in fc["races"]} == {"senate", "governor", "house"}
        assert sum(r["office"] == "senate" for r in fc["races"]) == 35
        assert 0 <= fc["sims"]["senate"]["pDemControl"] <= 1
        assert md["table"][0]["model"] == "Correlation-weighted forest"
        for r in fc["races"]:
            assert 0 <= r["pChallenger"] <= 1
        if night:
            assert fc["mode"] == "live" and fc["live"]["nResolved"] > 0
    hist = json.loads((tmp_path / "history.json").read_text())
    assert len(hist) == 2
