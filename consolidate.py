"""Merge per-source quotes into one consensus number per race."""
from __future__ import annotations

from collections import defaultdict

from .common import Quote, STATES, SENATE_2026, SENATE_SPECIAL_2026, GOVERNOR_2026

# Real-money markets count fully; PredictIt has small position caps; Manifold is play money.
WEIGHTS = {"kalshi": 1.0, "polymarket": 1.0, "predictit": 0.75, "manifold": 0.5}


def tier(p_leader: float) -> str:
    if p_leader >= 0.90:
        return "safe"
    if p_leader >= 0.75:
        return "likely"
    if p_leader >= 0.60:
        return "lean"
    if p_leader >= 0.525:
        return "tilt"
    return "tossup"


def _quality(q: Quote) -> tuple:
    both = int("D" in q.probs and "R" in q.probs)
    return (both, q.volume, len(q.candidates))


def consolidate(quotes: list[Quote], weights: dict | None = None) -> list[dict]:
    weights = weights or WEIGHTS
    by_key: dict[str, dict[str, Quote]] = defaultdict(dict)
    for q in quotes:
        cur = by_key[q.key].get(q.source)
        if cur is None or _quality(q) > _quality(cur):
            by_key[q.key][q.source] = q

    races = []
    for key, per_src in by_key.items():
        tot_w, acc = 0.0, defaultdict(float)
        for src, q in per_src.items():
            w = weights.get(src, 0.5)
            tot_w += w
            for party, p in q.probs.items():
                acc[party] += w * p
        probs = {k: round(v / tot_w, 4) for k, v in acc.items()} if tot_w else {}
        if not probs:
            continue
        leader = max(probs, key=probs.get)
        ranked = sorted(probs.values(), reverse=True)
        margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0)
        dvals = [q.probs.get("D", 0) for q in per_src.values()]
        any_q = next(iter(per_src.values()))
        races.append({
            "key": key,
            "office": any_q.office,
            "state": any_q.state,
            "stateName": STATES[any_q.state][0] if any_q.state else None,
            "district": any_q.district,
            "special": any_q.special,
            "probs": probs,
            "leader": leader,
            "leaderProb": round(probs[leader], 4),
            "margin": round(margin, 4),
            "tier": tier(probs[leader]),
            "spread": round(max(dvals) - min(dvals), 4) if len(dvals) > 1 else 0,
            "nSources": len(per_src),
            "sources": [
                {"source": s, "probs": q.probs, "url": q.url, "title": q.title,
                 "volume": round(q.volume, 2), "candidates": q.candidates[:8], "weight": weights.get(s, 0.5)}
                for s, q in sorted(per_src.items())
            ],
        })
    races.sort(key=lambda r: (r["office"], r["state"] or "", r["district"] or "", r["special"]))
    return races


def calendar() -> dict:
    return {
        "senate": sorted(SENATE_2026 | SENATE_SPECIAL_2026),
        "senateSpecial": sorted(SENATE_SPECIAL_2026),
        "governor": sorted(GOVERNOR_2026),
        "houseSeats": {k: v[2] for k, v in STATES.items()},
        "states": {k: {"name": v[0], "fips": v[1]} for k, v in STATES.items()},
    }
