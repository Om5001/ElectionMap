"""Election-night vote counts. Every adapter returns {race_key: Result}; later adapters override earlier ones
only when they report a higher share of the expected vote.

  1. AP Elections API      — needs AP_API_KEY (paid; what most newsrooms use). Most complete: all 435 House seats,
                             expected-vote-in percentage (eevp) and AP race calls.
  2. Clarity / Scytl ENR   — many states and counties publish live JSON at results.enr.clarityelections.com.
                             List the state-level summary.json URLs in data/manual/clarity_sources.csv.
  3. RESULTS_JSON_URL      — any JSON you control in this shape: {"races":[{"race":"senate:TX","dem":..,"rep":..,
                             "other":..,"pctIn":..,"called":true,"winner":"D"}]} (e.g. a Google Apps Script).
  4. data/manual/results.csv / RESULTS_CSV_URL — type numbers in by hand; always wins when it has more vote in.
  5. Market-implied calls  — when Kalshi/Polymarket consensus prices one side at >= 99c (added in the pipeline).
"""
from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass, asdict

import pandas as pd
import requests

from ..config import MANUAL, AP_API_KEY, ELECTION_DATE
from pm.common import classify, party_of, STATES


@dataclass
class Result:
    race: str
    dem: float = 0
    rep: float = 0
    other: float = 0
    pctIn: float = 0          # % of expected vote counted (0-100)
    called: bool = False
    winner: str | None = None  # D / R / I
    source: str = ""
    updated: str = ""

    @property
    def margin(self):
        tot = self.dem + self.rep + self.other
        return 100 * (self.dem - self.rep) / tot if tot else None

    def to_dict(self):
        d = asdict(self)
        d["margin"] = self.margin
        return d


def _merge(into: dict, new: dict):
    for k, r in new.items():
        cur = into.get(k)
        if cur is None or r.pctIn >= cur.pctIn or (r.called and not cur.called):
            into[k] = r


# ---------------------------------------------------------------- AP
def ap() -> dict:
    if not AP_API_KEY:
        return {}
    url = f"https://api.ap.org/v2/elections/{ELECTION_DATE}"
    params = {"apikey": AP_API_KEY, "format": "json", "level": "state", "officeID": "S,G,H", "resultsType": "l"}
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    out = {}
    for race in r.json().get("races", []):
        office = {"S": "senate", "G": "governor", "H": "house"}.get(race.get("officeID"))
        if not office or race.get("raceTypeID") not in (None, "G"):
            continue
        for ru in race.get("reportingUnits", []):
            st = ru.get("statePostal")
            if st not in STATES or ru.get("level") not in (None, "state", "district"):
                continue
            key = f"{office}:{st}"
            if office == "house":
                seat = str(race.get("seatNum") or "1")
                key += "-" + ("AL" if STATES[st][2] == 1 else f"{int(seat):02d}")
            elif "special" in (race.get("description", "") + race.get("seatName", "")).lower():
                key += ":special"
            votes = {"D": 0, "R": 0, "O": 0}
            winner = None
            for c in ru.get("candidates", []):
                p = {"Dem": "D", "GOP": "R", "Rep": "R"}.get(c.get("party"), "O")
                votes[p] += c.get("voteCount", 0) or 0
                if c.get("winner") == "X":
                    winner = p if p != "O" else "I"
            pct = ru.get("eevp") or ru.get("precinctsReportingPct") or 0
            out[key] = Result(key, votes["D"], votes["R"], votes["O"], float(pct), winner is not None, winner,
                              "AP", ru.get("lastUpdated", ""))
    return out


# ---------------------------------------------------------------- Clarity ENR
def clarity() -> dict:
    p = MANUAL / "clarity_sources.csv"
    if not p.exists():
        return {}
    out = {}
    for _, row in pd.read_csv(p).iterrows():
        try:
            data = requests.get(row.url, timeout=30).json()
        except Exception as e:  # noqa: BLE001
            print(f"[results] clarity {row.state}: {e}", file=sys.stderr)
            continue
        contests = data if isinstance(data, list) else data.get("Contests", data.get("contests", []))
        for c in contests:
            name = c.get("C") or c.get("ContestName") or c.get("name") or ""
            info = classify(f"{name} {row.state} 2026" if STATES[row.state][0].lower() not in name.lower()
                            else name)
            if not info and "senat" in name.lower():
                info = {"office": "senate", "state": row.state, "district": None, "special": "special" in name.lower()}
            if not info and "governor" in name.lower():
                info = {"office": "governor", "state": row.state, "district": None, "special": False}
            if not info:
                continue
            names, parties, votes = c.get("CH", []), c.get("P", []), c.get("V", [])
            v = {"D": 0, "R": 0, "O": 0}
            for i, n in enumerate(names):
                party = party_of(parties[i] if i < len(parties) else "", n) or "O"
                v[party if party in v else "O"] += float(votes[i]) if i < len(votes) else 0
            pr, tp = c.get("PR") or 0, c.get("TP") or 0
            key = f"{info['office']}:{info['state']}" + (f"-{info['district']}" if info["office"] == "house" else "") + \
                  (":special" if info.get("special") else "")
            out[key] = Result(key, v["D"], v["R"], v["O"], 100 * pr / tp if tp else 0, False, None, f"Clarity {row.state}")
    return out


# ---------------------------------------------------------------- JSON / CSV feeds
def json_feed() -> dict:
    url = os.getenv("RESULTS_JSON_URL")
    if not url:
        return {}
    out = {}
    for r in requests.get(url, timeout=30).json().get("races", []):
        out[r["race"]] = Result(r["race"], r.get("dem", 0), r.get("rep", 0), r.get("other", 0), r.get("pctIn", 0),
                                bool(r.get("called")), r.get("winner"), r.get("source", "json-feed"), r.get("updated", ""))
    return out


def csv_feed() -> dict:
    frames = []
    p = MANUAL / "results.csv"
    if p.exists():
        frames.append(pd.read_csv(p))
    if os.getenv("RESULTS_CSV_URL"):
        frames.append(pd.read_csv(io.StringIO(requests.get(os.getenv("RESULTS_CSV_URL"), timeout=30).text)))
    out = {}
    for df in frames:
        for _, r in df.iterrows():
            if pd.isna(r.get("race")):
                continue
            out[r.race] = Result(r.race, float(r.get("dem_votes") or 0), float(r.get("rep_votes") or 0),
                                 float(r.get("other_votes") or 0), float(r.get("pct_in") or 0),
                                 str(r.get("called")).lower() in ("1", "true", "yes"),
                                 None if pd.isna(r.get("winner")) else r.get("winner"),
                                 str(r.get("source") or "manual"), str(r.get("updated") or ""))
    return out


ADAPTERS = {"ap": ap, "clarity": clarity, "json": json_feed, "csv": csv_feed}


def load(online=True) -> tuple[dict, dict]:
    results, status = {}, {}
    for name, fn in ADAPTERS.items():
        if not online and name != "csv":
            continue
        try:
            got = fn()
            _merge(results, got)
            status[name] = f"{len(got)} races"
        except Exception as e:  # noqa: BLE001
            status[name] = f"failed: {str(e)[:100]}"
            print(f"[results] {name}: {e}", file=sys.stderr)
    return results, status
