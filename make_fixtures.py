#!/usr/bin/env python3
"""Generate SYNTHETIC raw payloads shaped like each API, for offline tests and the demo map.

The numbers are random (seeded) — they are NOT real market prices.
"""
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pm.common import STATES, SENATE_2026, SENATE_SPECIAL_2026, GOVERNOR_2026  # noqa: E402

rng = random.Random(2026)
OUT = Path(__file__).parent / "fixtures"  # noqa
OUT.mkdir(exist_ok=True)

# lean per state in [-1 (R), +1 (D)]
LEAN = {s: rng.uniform(-1, 1) for s in STATES}
for s in "CA NY MA IL MD HI VT RI WA OR NJ CT DE".split():
    LEAN[s] = rng.uniform(0.6, 1)
for s in "WY WV ID OK AL AR KY LA MS ND SD TN UT NE MT KS IN MO SC".split():
    LEAN[s] = rng.uniform(-1, -0.55)
for s in "GA MI NC NH ME AZ NV PA WI".split():
    LEAN[s] = rng.uniform(-0.25, 0.25)


def pd(state, noise=0.08):
    x = LEAN[state] * 3 + rng.gauss(0, 0.6)
    p = 1 / (1 + 2.718 ** (-x))
    return min(0.99, max(0.01, p + rng.gauss(0, noise)))


# Keep the synthetic Senate prices loosely consistent with the real seeded polls so the demo isn't absurd.
import csv, math
_pm = {}
_pp = ROOT / "data" / "manual" / "polls.csv"
if _pp.exists():
    for row in csv.DictReader(open(_pp)):
        _pm.setdefault(row["race"].split(":")[1], []).append(float(row["margin"]))
POLL_P = {s: 1 / (1 + math.exp(-(sum(v) / len(v)) / 4)) for s, v in _pm.items()}
_pd = pd
def pd(state, noise=0.08):
    if state in POLL_P and noise < 0.15:
        return min(0.97, max(0.03, POLL_P[state] + rng.gauss(0, 0.05)))
    return _pd(state, noise)

senate = sorted(SENATE_2026 | SENATE_SPECIAL_2026)
gov = sorted(GOVERNOR_2026)
house = []
for s, (_, _, n) in STATES.items():
    for d in range(1, n + 1):
        if rng.random() < (0.35 if n > 1 else 0.6):
            house.append((s, d, n))

# ---------- Kalshi
kalshi = []
def k_market(tk, sub, p):
    c = round(p * 100)
    return {"ticker": tk, "yes_sub_title": sub, "status": "active",
            "yes_bid": max(1, c - 1), "yes_ask": min(99, c + 1), "last_price": c,
            "volume": rng.randint(5_000, 2_000_000)}
for s in senate:
    p = pd(s); tk = f"SENATE{s}-26"
    kalshi.append({"event_ticker": tk, "series_ticker": f"SENATE{s}", "category": "Elections",
                   "title": f"{STATES[s][0]} Senate winner?", "sub_title": "Nov 3, 2026",
                   "markets": [k_market(tk + "-D", "Democratic party", p), k_market(tk + "-R", "Republican party", 1 - p)]})
for s in gov:
    p = pd(s); tk = f"GOVPARTY{s}-26"
    kalshi.append({"event_ticker": tk, "series_ticker": f"GOVPARTY{s}", "category": "Elections",
                   "title": f"Which party will win the {STATES[s][0]} governor race?", "sub_title": "2026",
                   "markets": [k_market(tk + "-D", "Democratic party", p), k_market(tk + "-R", "Republican party", 1 - p)]})
for s, d, n in house:
    p = pd(s, 0.2); code = f"{s}-{d:02d}" if n > 1 else f"{s}-AL"; tk = f"HOUSE{s}{d}-26"
    kalshi.append({"event_ticker": tk, "series_ticker": "HOUSERACE", "category": "Elections",
                   "title": f"{code} House race winner?", "sub_title": "2026 general",
                   "markets": [k_market(tk + "-D", "Democratic party", p), k_market(tk + "-R", "Republican party", 1 - p)]})
kalshi.append({"event_ticker": "CONTROLS-2026", "category": "Elections", "title": "Which party will win the U.S. Senate?",
               "markets": [k_market("CONTROLS-2026-D", "Democratic party", .41), k_market("CONTROLS-2026-R", "Republican party", .59)]})
kalshi.append({"event_ticker": "CONTROLH-2026", "category": "Elections", "title": "Which party will win the U.S. House?",
               "markets": [k_market("CONTROLH-2026-D", "Democratic party", .74), k_market("CONTROLH-2026-R", "Republican party", .26)]})
# noise that must be filtered out
kalshi += [
    {"event_ticker": "SENATEGAPRIM-26", "category": "Elections", "title": "Georgia Republican Senate primary winner?", "markets": [k_market("X-A", "Someone", .5)]},
    {"event_ticker": "FED-26DEC", "category": "Economics", "title": "Fed rate in December?", "markets": [k_market("X-B", "Cut", .5)]},
    {"event_ticker": "SENATEPA-28", "category": "Elections", "title": "Pennsylvania Senate winner 2028?", "markets": [k_market("X-C", "Democratic party", .5)]},
]
(OUT / "kalshi.json").write_text(json.dumps(kalshi))

# ---------- Polymarket (candidate-labelled + party-labelled mix)
poly = []
def p_market(label, q, p):
    return {"groupItemTitle": label, "question": q, "outcomePrices": json.dumps([f"{p:.3f}", f"{1-p:.3f}"]),
            "bestBid": round(p - .005, 3), "bestAsk": round(p + .005, 3), "volumeNum": rng.randint(1_000, 5_000_000), "closed": False}
for s in senate:
    if rng.random() < .85:
        p = pd(s)
        name = STATES[s][0]
        poly.append({"id": f"se{s}", "slug": f"usse-{s.lower()}-2026-11-03", "title": f"{name} Senate Election Winner",
                     "markets": [p_market("Democratic", f"Will the Democrats win the {name} Senate race?", p),
                                 p_market("Republican", f"Will the Republicans win the {name} Senate race?", 1 - p)]})
for s in gov:
    if rng.random() < .8:
        p = pd(s); name = STATES[s][0]
        poly.append({"id": f"go{s}", "slug": f"{name.lower().replace(' ', '-')}-governor-election-winner", "title": f"{name} Governor Election Winner",
                     "markets": [p_market("Jane Doe (D)", f"Will Jane Doe win the {name} governor race?", p),
                                 p_market("John Roe (R)", f"Will John Roe win the {name} governor race?", 1 - p - .01),
                                 p_market("Other", "Other?", .01)]})
for s, d, n in house[::2]:
    p = pd(s, .2)
    poly.append({"id": f"ho{s}{d}", "slug": f"usho-{s.lower()}{d:02d}-2026-11-03", "title": f"{STATES[s][0]} {d}{'st' if d==1 else 'nd' if d==2 else 'rd' if d==3 else 'th'} District House Election Winner",
                 "markets": [p_market("Democratic", "D?", p), p_market("Republican", "R?", 1 - p)]})
poly.append({"id": "ctl-h", "slug": "which-party-controls-the-house-after-2026", "title": "Which party will control the House after the 2026 midterms?",
             "markets": [p_market("Democratic", "D", .72), p_market("Republican", "R", .28)]})
poly.append({"id": "noise", "slug": "texas-senate-margin", "title": "Texas Senate race margin of victory", "markets": [p_market("R+5-10", "x", .4)]})
(OUT / "polymarket.json").write_text(json.dumps(poly))

# ---------- PredictIt
pi = []
for s in senate[::2] + gov[::3]:
    office = "Senate" if s in senate[::2] else "gubernatorial"
    p = pd(s, .1)
    pi.append({"id": len(pi) + 9000, "name": f"Which party will win the 2026 {STATES[s][0]} {office} race?",
               "url": f"https://www.predictit.org/markets/detail/{len(pi)+9000}",
               "contracts": [{"name": "Democratic", "lastTradePrice": round(p, 2), "bestBuyYesCost": round(p + .01, 2), "bestSellYesCost": round(p - .01, 2), "status": "Open"},
                             {"name": "Republican", "lastTradePrice": round(1 - p, 2), "bestBuyYesCost": round(1 - p + .01, 2), "bestSellYesCost": round(1 - p - .01, 2), "status": "Open"}]})
(OUT / "predictit.json").write_text(json.dumps(pi))

# ---------- Manifold
mf = []
for s in senate:
    if rng.random() < .5:
        p = pd(s, .12)
        mf.append({"id": f"m{s}", "question": f"Will a Democrat win the 2026 US Senate election in {STATES[s][0]}?",
                   "outcomeType": "BINARY", "probability": round(p, 3), "url": f"https://manifold.markets/demo/senate-{s.lower()}", "volume": rng.randint(100, 50000)})
for s in gov[::4]:
    p = pd(s, .12)
    mf.append({"id": f"g{s}", "question": f"Who will win the 2026 {STATES[s][0]} gubernatorial election?", "outcomeType": "MULTIPLE_CHOICE",
               "url": f"https://manifold.markets/demo/gov-{s.lower()}", "volume": 1000,
               "answers": [{"text": "Democratic candidate", "probability": p}, {"text": "Republican candidate", "probability": 1 - p}]})
(OUT / "manifold.json").write_text(json.dumps(mf))
print("fixtures written:", [p.name for p in OUT.glob("*.json")])
