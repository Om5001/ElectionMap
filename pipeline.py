"""End-to-end run: fetch everything -> train forest -> predict -> ensemble -> live counts -> simulate -> write JSON.

    python -m forecast.pipeline                 # live: all APIs
    python -m forecast.pipeline --offline       # bundled + manual data only
    python -m forecast.pipeline --demo          # synthetic market fixtures (clearly labelled in the UI)
    python -m forecast.pipeline --demo --simulate-night 40   # + fake partial counts, to exercise layers 2-3
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import config as C
from .features import race_features, macro_features, macro_series, FEATURE_LABELS, FEATURES
from .data import macro as macro_mod, polls as polls_mod, markets as markets_mod, early_vote as ev_mod, results as results_mod
from .model.train import train, loco_cv
from .model.forest import norm_cdf
from .live import blend_race, bootstrap_weights, simulate
from pm.common import STATES, SENATE_2026, SENATE_SPECIAL_2026, GOVERNOR_2026
from pm.consolidate import tier

MODEL_VERSION = "1.0"


def _cv_cached(hist_hash: str):
    p = C.CACHE / "cv.json"
    if p.exists():
        d = json.loads(p.read_text())
        if d.get("hash") == hist_hash:
            return d["cv"]
    return None


def _hist_hash():
    h = hashlib.sha1(MODEL_VERSION.encode())
    for f in sorted(C.REF.glob("*.csv")) + sorted(C.CACHE.glob("fred_*.csv")):
        h.update(f.name.encode() + str(f.stat().st_size).encode())
    return h.hexdigest()[:12]


def race_universe(markets: dict, polls: pd.DataFrame, results: dict, district_lean: dict) -> list[dict]:
    keys = {f"senate:{s}" for s in SENATE_2026} | {f"senate:{s}:special" for s in SENATE_SPECIAL_2026}
    keys |= {f"governor:{s}" for s in GOVERNOR_2026}
    keys |= {k for k in markets if k.startswith("house:")}
    keys |= {k for k in results if k.startswith("house:")}
    if not polls.empty:
        keys |= {k for k in polls.race.unique() if str(k).startswith("house:")}
    keys |= set(district_lean)
    out = []
    for k in sorted(keys):
        parts = k.split(":")
        office, rest = parts[0], parts[1]
        st, dist = (rest.split("-") + [None])[:2]
        if st not in STATES:
            continue
        out.append({"key": k, "office": office, "state": st, "stateName": STATES[st][0], "district": dist,
                    "special": k.endswith(":special")})
    return out


def load_district_lean() -> dict:
    p = C.REF / "house_district_lean.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    return dict(zip(df.race, df.lean))


def simulate_night(results: dict, races: list[dict], pct: float, seed=7) -> dict:
    """Synthetic partial counts (testing / demo only)."""
    rng = np.random.default_rng(seed)
    out = dict(results)
    for r in races:
        if r["office"] == "house" and rng.random() < .5:
            continue
        true = r["prior"]["margin"] + rng.normal(0, r["sigma"])
        p_in = float(np.clip(pct + rng.normal(0, 25), 0, 100))
        if p_in <= 0:
            continue
        counted = true + rng.normal(0, 6 * (1 - p_in / 100) + 0.3)
        tot = 1_000_000 * p_in / 100
        d = tot * (0.5 + counted / 200)
        called = p_in > 80 and abs(counted) > 4
        out[r["key"]] = results_mod.Result(r["key"], d, tot - d, 0, p_in, called,
                                           ("I" if r["challenger"] == "I" else "D") if counted > 0 and called else ("R" if called else None),
                                           "SIMULATED", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    return out


def run(args) -> dict:
    t0 = time.time()
    online = not (args.offline or args.demo)
    status = {}

    # ---------------- data
    status["macro"] = macro_mod.refresh(offline=not online)
    series = macro_series()
    polls, status["polls"] = polls_mod.load(online=online)
    fixtures = C.ROOT / "tests" / "fixtures" if args.demo else None
    markets, control, status["markets"] = markets_mod.load(online=online, fixtures=fixtures)
    early, status["earlyVote"] = ev_mod.load(online=online)
    results, status["results"] = results_mod.load(online=online)
    district_lean = load_district_lean()
    gb_rows = polls[polls.race == "generic"] if not polls.empty else pd.DataFrame()
    generic = float(gb_rows.margin.mean()) if len(gb_rows) else C.GENERIC_BALLOT_DEFAULT

    # ---------------- model (re-fit every run; seed changes so the forest is re-grown each time)
    hh = _hist_hash()
    cv = _cv_cached(hh)
    seed = int(args.seed) if args.seed is not None else int(time.time() // 60) % 100000
    model, diag, hist = train(seed=seed, n_trees=args.trees, cv=cv)
    if cv is None:
        C.CACHE.mkdir(parents=True, exist_ok=True)
        (C.CACHE / "cv.json").write_text(json.dumps({"hash": hh, "cv": {k: diag[k] for k in diag if k not in ("weights", "trees", "nTrees", "features", "anchorCoef")}}))
    diag["seed"] = seed
    diag["outcomeCorrelation"] = {f: float(pd.Series(hist[f]).corr(hist.margin_actual, method="spearman"))
                                  for f in FEATURES if hist[f].notna().sum() > 20 and hist[f].std() > 0}
    sig_nat, sig_race, sig_np = diag["sigmaNat"], diag["sigmaRace"], diag["sigmaNoPoll"]

    # ---------------- per-race features + forest
    macro_now = macro_features(C.CYCLE, series, as_of=str(pd.Timestamp.today().date()))
    races = race_universe(markets, polls, results, district_lean)
    rows = []
    for r in races:
        pr = polls_mod.for_race(polls, r["key"])
        f = race_features(office=r["office"], state=r["state"], cycle=C.CYCLE, polls=pr if len(pr) else None,
                          generic_ballot=generic, macro=macro_now)
        if r["office"] == "house" and r["key"] in district_lean:
            f["state_lean"] = district_lean[r["key"]]
        r["features"] = f
        r["polls"] = pr[["pollster", "margin", "source"]].to_dict("records") if len(pr) else []
        mk = markets.get(r["key"])
        probs = mk["probs"] if mk else {}
        r["challenger"] = "I" if (probs.get("I", 0) > probs.get("D", 0) or r["key"] in ("senate:NE",)) else "D"
        if mk and probs.get("R") is not None and probs.get(r["challenger"]) is not None:
            r["marketProb"] = probs[r["challenger"]] / (probs[r["challenger"]] + probs["R"])
            r["markets"] = [{"source": s["source"], "p": (s["probs"].get(r["challenger"], 0) /
                             max(s["probs"].get(r["challenger"], 0) + s["probs"].get("R", 0), 1e-9)),
                             "url": s["url"], "title": s["title"], "volume": s["volume"]} for s in mk["sources"]]
            r["marketSpread"] = mk["spread"]
            r["marketVolume"] = sum(s["volume"] or 0 for s in mk["sources"])
        r["candidates"] = C.CANDIDATES.get(r["key"], {})
        r["result"] = results[r["key"]].to_dict() if r["key"] in results else None
        rows.append(f)
    df = pd.DataFrame(rows)
    tm = model.tree_margins(df)
    for i, r in enumerate(races):
        polled = r["features"]["poll_margin"] is not None
        r["forestMargin"] = float(tm[i].mean())
        r["treeSd"] = float(tm[i].std())
        r["anchor"] = float(model.anchor(df.iloc[[i]])[0])
        r["sigma"] = float(math.hypot(sig_race[r["office"]], sig_nat) if polled else sig_np)
        r["polled"] = polled

    # ---------------- layer 2 prior pass (no results), then learn from decided races
    w0 = bootstrap_weights([])
    for r in races:
        snap = {k: r.get(k) for k in ("result",)}
        r["result"] = None
        blend_race(r, w0["weights"], 0.0, early.get(r["state"]))
        r["prior"] = {"margin": r["margin"], "p": r["pChallenger"], "components": r["components"]}
        r.update(snap)
    if args.simulate_night is not None:
        results = simulate_night(results, races, args.simulate_night)
        for r in races:
            r["result"] = results[r["key"]].to_dict() if r["key"] in results else None
        status["results"]["simulated"] = f"SYNTHETIC counts at ~{args.simulate_night}% (demo)"
    # market-implied calls
    for r in races:
        if r.get("marketProb") is not None and (r["marketProb"] >= .99 or r["marketProb"] <= .01):
            res = r.get("result") or {"pctIn": 0, "margin": None}
            if not res.get("called"):
                res = dict(res, marketCall=("challenger" if r["marketProb"] >= .99 else "R"))
                r["result"] = res
    resolved = [r for r in races if r.get("result") and r["result"].get("margin") is not None
                and (r["result"].get("pctIn", 0) >= C_RESOLVED or r["result"].get("called"))]
    boot_rows = [{"forest": r["forestMargin"], "markets": r["prior"]["components"].get("markets"),
                  "actual": r["result"]["margin"]} for r in resolved]
    wlive = bootstrap_weights(boot_rows, seed=seed)
    n_res = len(resolved)
    miss = float(np.mean([r["result"]["margin"] - r["prior"]["margin"] for r in resolved])) if n_res else 0.0
    shift = miss * n_res / (n_res + 5)
    sig_nat_live = sig_nat * math.sqrt(5 / (n_res + 5))
    for r in races:
        blend_race(r, wlive["weights"], 0.0 if r in resolved else shift, early.get(r["state"]))
        p = r["pChallenger"]
        lead = r["challenger"] if p >= .5 else "R"
        r["leader"], r["leaderProb"] = lead, max(p, 1 - p)
        r["tier"] = tier(r["leaderProb"])
        r["status"] = ("called" if r.get("result") and r["result"].get("called") else
                       "counting" if r.get("result") and r["result"].get("pctIn", 0) > 0 else "pre")

    sims = simulate(races, sig_nat_live, seed=seed)

    # ---------------- tracker (10-15 intensely followed, close races)
    def intensity(r):
        close = 1 - abs(2 * r["pChallenger"] - 1)
        attn = 1 + math.log10(1 + (r.get("marketVolume") or 0)) / 6 + 0.15 * len(r["polls"])
        return close * attn + (0.6 if r["key"] in C.PINNED else 0)
    tracker = [r["key"] for r in sorted(races, key=intensity, reverse=True)[: C.TRACKER_SIZE]]
    trees_idx = [t["i"] for t in diag["trees"]]
    for r in races:
        if r["key"] in tracker or r["office"] != "house":
            i = races.index(r)
            r["treeMargins"] = np.round(tm[i], 1).tolist()
            r["sampleTreeMargins"] = [round(float(tm[i][j]), 1) for j in trees_idx]
        r["features"] = {k: (None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), 3))
                         for k, v in r["features"].items()}

    mode = "live" if any(r["status"] != "pre" for r in races) else "pre"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    forecast = {
        "generatedAt": now, "mode": mode, "demo": bool(args.demo), "simulatedNight": args.simulate_night is not None,
        "electionDate": C.ELECTION_DATE, "genericBallot": generic, "status": status,
        "macro": macro_mod.dashboard(), "macroFeatures": macro_now, "earlyVote": early,
        "marketControl": control, "sims": sims, "tracker": tracker,
        "live": {"nResolved": n_res, "nationalShift": round(shift, 2), "rawMiss": round(miss, 2),
                 "sigmaNat": round(sig_nat_live, 2), "ensemble": wlive},
        "calendar": {"senate": sorted(SENATE_2026 | SENATE_SPECIAL_2026), "governor": sorted(GOVERNOR_2026),
                     "houseSeats": {k: v[2] for k, v in STATES.items()},
                     "states": {k: {"name": v[0], "fips": v[1]} for k, v in STATES.items()}},
        "senateMath": {"notUp": C.SENATE_NOT_UP, "tie": C.SENATE_TIE_GOES_TO},
        "races": races,
    }
    diag.update({"featureLabels": FEATURE_LABELS | {"anchor": "Anchor margin"}, "modelVersion": MODEL_VERSION,
                 "trainedAt": now, "runSeconds": round(time.time() - t0, 1)})
    return forecast, diag


C_RESOLVED = 95


def append_history(forecast: dict, path: Path):
    hist = []
    remote = os.getenv("HISTORY_URL")
    if remote:
        try:
            hist = requests.get(remote, timeout=20).json()
        except Exception:  # noqa: BLE001
            hist = []
    if not hist and path.exists():
        try:
            hist = json.loads(path.read_text())
        except ValueError:
            hist = []
    pt = {"t": forecast["generatedAt"], "mode": forecast["mode"],
          "senateD": forecast["sims"].get("senate", {}).get("pDemControl"),
          "races": {r["key"]: round(r["pChallenger"], 3) for r in forecast["races"] if r["key"] in forecast["tracker"] or r["office"] != "house"}}
    hist.append(pt)
    path.write_text(json.dumps(hist[-600:], separators=(",", ":")))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--demo", action="store_true", help="synthetic market fixtures")
    ap.add_argument("--simulate-night", type=float, default=None, help="synthetic partial counts at ~N%% in")
    ap.add_argument("--trees", type=int, default=400)
    ap.add_argument("--seed", default=None)
    ap.add_argument("--out", default=str(C.SITE_DATA))
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    fc, diag = run(a)
    (out / "forecast.json").write_text(json.dumps(fc, separators=(",", ":"), default=float))
    (out / "model.json").write_text(json.dumps(diag, separators=(",", ":"), default=float))
    append_history(fc, out / "history.json")
    s = fc["sims"].get("senate", {})
    print(f"[pipeline] {len(fc['races'])} races, mode={fc['mode']}, Senate P(D control)={s.get('pDemControl')}, "
          f"resolved={fc['live']['nResolved']}, {diag['runSeconds']}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
