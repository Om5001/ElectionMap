"""Train on 2010-2022 races and measure honestly with leave-one-cycle-out (LOCO) CV.

LOCO = train on every cycle except one, predict the held-out cycle, repeat. It is the right test
for elections because errors are correlated within a cycle (2016, 2020 polling misses).
Outputs error scales used downstream:
  sigma_nat   - national (shared) error: RMS of per-cycle mean errors
  sigma_race  - race-specific error after removing the cycle's shared error, by office
  sigma_nopoll- error for races without polls
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from ..features import FEATURES, to_matrix
from ..data.history import build as build_history
from .election_model import ElectionModel, _hide_polls
from .forest import norm_cdf

SHRINK_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]


def _brier(pred, sigma, y):
    p = np.array([norm_cdf(m / s) for m, s in zip(pred, sigma)])
    return float(np.mean((p - (y > 0)) ** 2))


def loco_cv(hist: pd.DataFrame, n_trees=150, seed=0) -> dict:
    n = len(hist)
    anchor, corr = np.zeros(n), np.zeros(n)
    anchor_np, corr_np = np.zeros(n), np.zeros(n)
    skl = np.zeros(n)
    for cyc in sorted(hist.cycle.unique()):
        tr, te = (hist.cycle != cyc).values, (hist.cycle == cyc).values
        m = ElectionModel(n_trees=n_trees, seed=seed + int(cyc)).fit(hist[tr])
        anchor[te], corr[te] = m.anchor(hist[te]), m.correction(hist[te])
        hidden = _hide_polls(hist[te])
        anchor_np[te], corr_np[te] = m.anchor(hidden), m.correction(hidden)
        Xtr = np.nan_to_num(to_matrix(hist[tr][FEATURES].to_dict("records")), nan=0.0)
        Xte = np.nan_to_num(to_matrix(hist[te][FEATURES].to_dict("records")), nan=0.0)
        skl[te] = RandomForestRegressor(n_estimators=n_trees, min_samples_leaf=6, max_features=0.5,
                                        random_state=seed).fit(Xtr, hist.margin_actual[tr]).predict(Xte)
    y = hist.margin_actual.values
    rmse = lambda p: float(np.sqrt(np.mean((y - p) ** 2)))  # noqa: E731
    grid = [{"shrink": s, "rmse": rmse(anchor + s * corr)} for s in SHRINK_GRID]
    shrink = min(grid, key=lambda g: g["rmse"])["shrink"]
    pred = anchor + shrink * corr
    pred_np = anchor_np + shrink * corr_np

    res = y - pred
    cyc_mean = pd.Series(res).groupby(hist.cycle.values).mean()
    sigma_nat = float(np.sqrt(np.mean(cyc_mean.values ** 2)))
    race_res = res - hist.cycle.map(cyc_mean).values
    sigma_race = {o: float(np.std(race_res[hist.office.values == o])) for o in ("senate", "governor", "house")}
    sigma_nopoll = float(np.std(y - pred_np))
    sig = np.array([np.hypot(sigma_race[o], sigma_nat) for o in hist.office.values])

    def metrics(p, name, s=sig):
        return {"model": name, "rmse": rmse(p), "mae": float(np.mean(np.abs(y - p))),
                "winnerAccuracy": float(np.mean(np.sign(p) == np.sign(y))), "brier": _brier(p, s, y), "n": int(n)}

    table = [metrics(pred, "Correlation-weighted forest"), metrics(hist.poll_margin.values, "Poll average only"),
             metrics(skl, "Standard random forest"), metrics(pred_np, "Forest, polls hidden", np.full(n, np.hypot(sigma_nopoll, 1)))]
    p = np.array([norm_cdf(m / s) for m, s in zip(pred, sig)])
    calib = []
    for a in np.linspace(0, .9, 10):
        msk = (p >= a) & (p < a + .1 + 1e-9)
        if msk.sum() >= 5:
            calib.append({"p": float(p[msk].mean()), "observed": float((y[msk] > 0).mean()), "n": int(msk.sum())})
    return {"table": table, "shrinkGrid": grid, "shrink": shrink, "calibration": calib, "sigmaNat": sigma_nat,
            "sigmaRace": sigma_race, "sigmaNoPoll": sigma_nopoll, "nRaces": int(n),
            "byCycle": [{"cycle": int(c), "bias": float(cyc_mean[c]), "n": int((hist.cycle == c).sum()),
                         "rmse": float(np.sqrt(np.mean(res[hist.cycle.values == c] ** 2)))} for c in cyc_mean.index],
            "cycles": [int(c) for c in sorted(hist.cycle.unique())]}


def train(seed: int = 0, n_trees: int = 400, cv: dict | None = None):
    """Fit the production model. Pass a cached `cv` dict to skip re-running LOCO."""
    hist = build_history()
    diag = cv if cv else loco_cv(hist, seed=seed)
    model = ElectionModel(n_trees=n_trees, shrink=diag["shrink"], seed=seed).fit(hist)
    f = model.forest_
    out = dict(diag)
    out["weights"] = f.weight_summary()
    out["trees"] = f.tree_sample()
    out["nTrees"] = n_trees
    out["features"] = f.features_
    out["anchorCoef"] = dict(zip(["state_lean", "generic_ballot", "intercept"], map(float, model.coef_)))
    return model, out, hist


if __name__ == "__main__":
    import json
    m, d, h = train()
    print(json.dumps({k: d[k] for k in ("table", "shrinkGrid", "sigmaNat", "sigmaRace", "sigmaNoPoll", "anchorCoef")}, indent=1))
    for w in d["weights"]:
        print(f"{w['feature']:18s} rho={w['rho']:+.2f} w={w['mean']:.3f} [{w['p05']:.3f},{w['p95']:.3f}] sel={w['selected']:.2f} imp={w['importance']:.3f}")
