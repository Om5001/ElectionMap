"""Layers 2 and 3 + chamber simulation.

Layer 1 (forest)   -> margin mu_f, error sigma_f per race                      (see model/)
Layer 2 (ensemble) -> blend forest with market-implied margin (+ early-vote nudge).
                      Blend weights start at a prior and are re-estimated on election night by
                      bootstrapping over races that are already decided: each bootstrap resample scores
                      every component by its squared error on those races, weights ~ 1/MSE, then the
                      average over resamples is shrunk toward the prior by n/(n+k).
                      The mean miss on decided races is also applied as a shared "national shift".
Layer 3 (count)    -> fold in counted votes: mu = r*counted + (1-r)*prior, with r = (share of expected
                      vote in)^1.5 so early, unrepresentative batches move the estimate less.
Simulation         -> 20,000 correlated draws (shared national error + race error) -> seat distributions.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import norm

from .config import ENSEMBLE_PRIOR, ENSEMBLE_PRIOR_STRENGTH, SENATE_NOT_UP, SENATE_TIE_GOES_TO, HOUSE_SEATS

EV_K = 0.35          # early-vote party swing (pts) -> margin nudge (pts); heuristic prior, reweighted live
RESOLVED_PCT = 95


def market_margin(p: float, sigma: float) -> float:
    p = min(max(p, 0.01), 0.99)
    return float(sigma * norm.ppf(p))


def bootstrap_weights(rows: list[dict], comps=("forest", "markets"), B=500, seed=0) -> dict:
    """rows: resolved races with component predictions + actual margin."""
    prior = {c: ENSEMBLE_PRIOR.get(c, 0.0) for c in comps}
    s = sum(prior.values())
    prior = {c: v / s for c, v in prior.items()}
    usable = [r for r in rows if all(r.get(c) is not None for c in comps)]
    n = len(usable)
    if n < 3:
        return {"weights": prior, "n": n, "boot": None, "prior": prior}
    rng = np.random.default_rng(seed)
    act = np.array([r["actual"] for r in usable])
    preds = {c: np.array([r[c] for r in usable]) for c in comps}
    W = []
    for _ in range(B):
        idx = rng.integers(0, n, n)
        inv = {c: 1.0 / (np.mean((preds[c][idx] - act[idx]) ** 2) + 1.0) for c in comps}
        t = sum(inv.values())
        W.append([inv[c] / t for c in comps])
    W = np.array(W)
    data_w = dict(zip(comps, W.mean(0)))
    lam = n / (n + ENSEMBLE_PRIOR_STRENGTH)
    post = {c: (1 - lam) * prior[c] + lam * data_w[c] for c in comps}
    return {"weights": post, "n": n, "prior": prior, "data": data_w,
            "boot": {c: {"p05": float(np.quantile(W[:, i], .05)), "p95": float(np.quantile(W[:, i], .95)),
                         "hist": np.histogram(W[:, i], bins=20, range=(0, 1))[0].tolist()} for i, c in enumerate(comps)}}


def blend_race(r: dict, w: dict, shift: float, ev: dict | None) -> dict:
    """r: race dict with forestMargin, sigma, marketProb (challenger), result (optional)."""
    comps = {"forest": r["forestMargin"]}
    if r.get("marketProb") is not None:
        comps["markets"] = market_margin(r["marketProb"], r["sigma"])
    if ev and ev.get("partySwing") is not None and r["office"] != "house":
        r["earlyVoteNudge"] = round(EV_K * ev["partySwing"], 2)
    ww = {c: w.get(c, 0) for c in comps}
    tot = sum(ww.values()) or 1
    ww = {c: v / tot for c, v in ww.items()}
    mu = sum(ww[c] * comps[c] for c in comps) + r.get("earlyVoteNudge", 0) + shift
    sigma = r["sigma"]
    res = r.get("result")
    if res and res.get("margin") is not None and res.get("pctIn", 0) > 0:
        rr = min(res["pctIn"], 100) / 100
        r_eff = rr ** 1.5
        mu = r_eff * res["margin"] + (1 - r_eff) * mu
        sigma = max(0.5, sigma * (1 - r_eff) + 0.5 * r_eff * (1 - rr) * 10)
    p = float(norm.cdf(mu / sigma))
    if res and res.get("called") and res.get("winner"):
        p = 1.0 if res["winner"] in ("D", "I") else 0.0
    r.update({"components": {c: round(v, 2) for c, v in comps.items()}, "componentWeights": {c: round(v, 3) for c, v in ww.items()},
              "margin": round(mu, 2), "sigmaLive": round(sigma, 2), "pChallenger": round(p, 4)})
    return r


def simulate(races: list[dict], sigma_nat: float, n=20000, seed=0) -> dict:
    rng = np.random.default_rng(seed)
    nat = rng.normal(0, sigma_nat, n)
    out = {}
    for office in ("senate", "governor", "house"):
        rs = [r for r in races if r["office"] == office]
        if not rs:
            continue
        wins = np.zeros(n)
        ind = np.zeros(n)
        for r in rs:
            if r["pChallenger"] in (0.0, 1.0):
                win = np.full(n, r["pChallenger"] == 1.0)
            else:
                race_sd = math.sqrt(max(r["sigmaLive"] ** 2 - sigma_nat ** 2, 1.0)) if not r.get("result") else r["sigmaLive"]
                shared = nat if not r.get("result") else 0
                win = (r["margin"] + shared + rng.normal(0, race_sd, n)) > 0
            if r.get("challenger") == "I":
                ind += win
            else:
                wins += win
        o = {"n": len(rs), "expectedChallenger": float((wins + ind).mean()),
             "dist": np.bincount((wins + ind).astype(int), minlength=len(rs) + 1).tolist()}
        if office == "senate":
            d_total = wins + SENATE_NOT_UP["D"]
            r_total = (len(rs) - wins - ind) + SENATE_NOT_UP["R"]
            o["pDemControl"] = float(np.mean(d_total >= 51))
            o["pRepControl"] = float(np.mean((r_total >= 51) | ((r_total == 50) & (SENATE_TIE_GOES_TO == "R"))))
            o["expectedDemSeats"] = float(d_total.mean())
            o["expectedRepSeats"] = float(r_total.mean())
            o["expectedInd"] = float(ind.mean())
            o["seatDist"] = {int(k): int(v) for k, v in zip(*np.unique(d_total, return_counts=True))}
        if office == "house":
            o["complete"] = len(rs) >= HOUSE_SEATS
            if o["complete"]:
                o["pDemControl"] = float(np.mean(wins >= 218))
        out[office] = o
    return out
