"""PredictIt public market data feed (no key; refreshed by PredictIt ~every 60s).

GET https://www.predictit.org/api/marketdata/all/
markets[]: {id, name, url, contracts[]: {name, lastTradePrice, bestBuyYesCost, bestSellYesCost}}
"""
from __future__ import annotations

from ..common import Quote, classify, party_of, normalise, get_json, fnum

NAME = "PredictIt"
URL = "https://www.predictit.org/api/marketdata/all/"


def fetch_raw() -> list:
    return get_json(URL).get("markets", [])


def _price(c: dict):
    buy, sell, last = fnum(c.get("bestBuyYesCost")), fnum(c.get("bestSellYesCost")), fnum(c.get("lastTradePrice"))
    if buy and sell and 0 <= buy - sell <= 0.10:
        return (buy + sell) / 2
    return last or buy or sell


def parse(markets: list) -> list[Quote]:
    out = []
    for mk in markets:
        title = mk.get("name") or mk.get("shortName") or ""
        info = classify(title)
        if not info:
            continue
        contracts = [c for c in mk.get("contracts", []) if (c.get("status") or "Open").lower() == "open"]
        probs, cands = {}, []
        for c in contracts:
            pr = _price(c)
            if pr is None:
                continue
            label = c.get("name") or c.get("shortName") or ""
            party = party_of(label)
            cands.append({"name": label, "party": party, "prob": round(pr, 4)})
            if party:
                probs[party] = probs.get(party, 0) + pr
        if not probs and len(cands) == 1:
            party = party_of(title)
            if party:
                p = cands[0]["prob"]
                probs = {party: p, ("R" if party == "D" else "D"): 1 - p}
        probs = normalise(probs)
        if not probs:
            continue
        out.append(Quote(
            source="predictit", title=title, url=mk.get("url") or "https://www.predictit.org",
            probs=probs, volume=0, candidates=cands, **info,
        ))
    return out
