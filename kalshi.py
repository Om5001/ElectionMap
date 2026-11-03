"""Kalshi public market data (no API key needed for reads).

Docs: https://docs.kalshi.com  —  GET /events?status=open&with_nested_markets=true
Prices come either as integer cents (yes_bid, yes_ask, last_price) or, on newer
API versions, as dollar strings (yes_bid_dollars, ...). Both are handled.
"""
from __future__ import annotations

from ..common import Quote, classify, party_of, normalise, get_json, fnum

NAME = "Kalshi"
BASE = "https://api.elections.kalshi.com/trade-api/v2"
CATEGORIES = {"elections", "politics", "us elections"}
MAX_PAGES = 60


def fetch_raw() -> list:
    events, cursor = [], None
    for _ in range(MAX_PAGES):
        params = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = get_json(f"{BASE}/events", params)
        events.extend(data.get("events", []))
        cursor = data.get("cursor")
        if not cursor:
            break
    return events


def _price(m: dict):
    """Yes probability 0..1: bid/ask midpoint when the book is tight, else last trade."""
    def p(field):
        v = fnum(m.get(f"{field}_dollars"))
        if v is not None:
            return v
        v = fnum(m.get(field))
        return v / 100 if v is not None else None

    bid, ask, last = p("yes_bid"), p("yes_ask"), p("last_price")
    if bid and ask and ask >= bid and (ask - bid) <= 0.10:
        return (bid + ask) / 2
    if last:
        return last
    if bid and ask:
        return (bid + ask) / 2
    return bid or ask


def parse(events: list) -> list[Quote]:
    out = []
    for ev in events:
        cat = (ev.get("category") or "").lower()
        if cat and cat not in CATEGORIES:
            continue
        title = ev.get("title") or ""
        sub = ev.get("sub_title") or ""
        tick = ev.get("event_ticker") or ""
        info = classify(f"{title} {sub}".strip(), hint=tick)
        if not info:
            continue
        markets = [m for m in ev.get("markets", []) if (m.get("status") in (None, "active", "open", "initialized"))]
        probs, cands, vol = {}, [], 0.0
        for m in markets:
            pr = _price(m)
            if pr is None:
                continue
            label = m.get("yes_sub_title") or m.get("subtitle") or m.get("title") or ""
            party = party_of(label, m.get("ticker", "").rsplit("-", 1)[-1] in ("D", "DEM") and "Democratic" or "",
                             m.get("ticker", "").rsplit("-", 1)[-1] in ("R", "REP", "GOP") and "Republican" or "",
                             m.get("rules_primary") or "")
            vol += fnum(m.get("volume")) or 0
            cands.append({"name": label, "party": party, "prob": round(pr, 4)})
            if party:
                probs[party] = probs.get(party, 0) + pr
        if not probs and len(markets) == 1 and cands:
            # single binary market like "Will Democrats win X?"
            party = party_of(title)
            if party:
                p = cands[0]["prob"]
                probs = {party: p, ("R" if party == "D" else "D"): 1 - p}
        probs = normalise(probs)
        if not probs:
            continue
        series = ev.get("series_ticker") or tick.split("-")[0]
        out.append(Quote(
            source="kalshi", title=title, url=f"https://kalshi.com/markets/{series.lower()}/{tick.lower()}" if tick else "https://kalshi.com",
            probs=probs, volume=vol, candidates=cands, **info,
        ))
    return out
