"""Manifold Markets public API (play money — weighted lower in the consensus).

GET https://api.manifold.markets/v0/search-markets?term=...&filter=open&limit=...
Binary markets carry `probability`; multiple-choice need GET /v0/market/{id} for answers[].
"""
from __future__ import annotations

from ..common import Quote, classify, party_of, normalise, get_json, fnum

NAME = "Manifold"
BASE = "https://api.manifold.markets/v0"
TERMS = ["2026 senate", "2026 governor", "2026 house", "2026 gubernatorial", "2026 congressional district", "senate control 2026", "house control 2026"]
MAX_DETAIL = 300


def fetch_raw() -> list:
    seen, rows = set(), []
    for term in TERMS:
        try:
            data = get_json(f"{BASE}/search-markets", {"term": term, "filter": "open", "limit": 1000, "sort": "liquidity"})
        except RuntimeError:
            continue
        for m in data or []:
            if m.get("id") not in seen:
                seen.add(m.get("id"))
                rows.append(m)
    # hydrate multiple-choice markets that look like races
    n = 0
    for m in rows:
        if m.get("outcomeType") == "MULTIPLE_CHOICE" and not m.get("answers") and classify(m.get("question", "")):
            if n >= MAX_DETAIL:
                break
            try:
                m["answers"] = get_json(f"{BASE}/market/{m['id']}").get("answers", [])
            except RuntimeError:
                pass
            n += 1
    return rows


def parse(rows: list) -> list[Quote]:
    out = []
    for m in rows:
        q = m.get("question") or ""
        info = classify(q)
        if not info:
            continue
        probs, cands = {}, []
        if m.get("outcomeType") == "BINARY":
            p = fnum(m.get("probability"))
            party = party_of(q)
            if p is None or not party:
                continue
            probs = {party: p, ("R" if party == "D" else "D"): 1 - p}
            cands = [{"name": q, "party": party, "prob": round(p, 4)}]
        elif m.get("outcomeType") == "MULTIPLE_CHOICE":
            for a in m.get("answers", []):
                p = fnum(a.get("probability"))
                if p is None:
                    continue
                party = party_of(a.get("text", ""))
                cands.append({"name": a.get("text", ""), "party": party, "prob": round(p, 4)})
                if party:
                    probs[party] = probs.get(party, 0) + p
        else:
            continue
        probs = normalise(probs)
        if not probs:
            continue
        out.append(Quote(
            source="manifold", title=q, url=m.get("url") or "https://manifold.markets",
            probs=probs, volume=fnum(m.get("volume")) or 0, candidates=cands, **info,
        ))
    return out
