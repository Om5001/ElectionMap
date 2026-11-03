"""Polymarket via the public Gamma API (no key needed).

GET https://gamma-api.polymarket.com/events?closed=false&tag_slug=...&limit=&offset=
Each event has markets[]; each market has `groupItemTitle` (outcome label, e.g.
"Democratic" or a candidate) and `outcomePrices` (JSON string, index 0 = Yes).
"""
from __future__ import annotations

import json

from ..common import Quote, classify, party_of, normalise, get_json, fnum

NAME = "Polymarket"
BASE = "https://gamma-api.polymarket.com"
TAGS = ["midterms", "us-elections", "elections", "politics", "senate", "governor", "house"]
PAGE = 500
MAX_PAGES_PER_TAG = 10
MAX_PAGES_FALLBACK = 40


def _page(params) -> list:
    out = []
    for i in range(params.pop("_max", 10)):
        data = get_json(f"{BASE}/events", {**params, "limit": PAGE, "offset": i * PAGE})
        if not isinstance(data, list) or not data:
            break
        out.extend(data)
        if len(data) < PAGE:
            break
    return out


def fetch_raw() -> list:
    seen, events = set(), []
    for tag in TAGS:
        try:
            for ev in _page({"closed": "false", "tag_slug": tag, "_max": MAX_PAGES_PER_TAG}):
                if ev.get("id") not in seen:
                    seen.add(ev.get("id"))
                    events.append(ev)
        except RuntimeError:
            continue
    if len(events) < 20:  # tag slugs changed? fall back to a full scan of open events
        for ev in _page({"closed": "false", "_max": MAX_PAGES_FALLBACK}):
            if ev.get("id") not in seen:
                seen.add(ev.get("id"))
                events.append(ev)
    return events


def _yes_price(m: dict):
    bid, ask = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
    if bid and ask and 0 < ask - bid <= 0.10:
        return (bid + ask) / 2
    prices = m.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except ValueError:
            prices = None
    if prices:
        return fnum(prices[0])
    return fnum(m.get("lastTradePrice"))


def parse(events: list) -> list[Quote]:
    out = []
    for ev in events:
        title = ev.get("title") or ""
        slug = ev.get("slug") or ""
        info = classify(title, hint=slug)
        if not info:
            continue
        markets = [m for m in ev.get("markets", []) if not m.get("closed")]
        probs, cands, vol = {}, [], 0.0
        for m in markets:
            pr = _yes_price(m)
            if pr is None:
                continue
            label = m.get("groupItemTitle") or m.get("question") or ""
            party = party_of(label, m.get("question") or "")
            vol += fnum(m.get("volumeNum") or m.get("volume")) or 0
            cands.append({"name": label, "party": party, "prob": round(pr, 4)})
            if party:
                probs[party] = probs.get(party, 0) + pr
        if not probs and len(cands) == 1:
            party = party_of(title, markets[0].get("question", ""))
            if party:
                p = cands[0]["prob"]
                probs = {party: p, ("R" if party == "D" else "D"): 1 - p}
        probs = normalise(probs)
        if not probs:
            continue
        out.append(Quote(
            source="polymarket", title=title, url=f"https://polymarket.com/event/{slug}",
            probs=probs, volume=vol or (fnum(ev.get("volume")) or 0), candidates=cands, **info,
        ))
    return out
