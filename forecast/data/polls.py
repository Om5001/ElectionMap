"""2026 polls.

Adapters (all optional, merged and de-duplicated):
  1. data/manual/polls.csv          — hand-entered / seeded final polls with source URLs (always loaded)
  2. Wikipedia polling tables       — every "2026 United States Senate/gubernatorial election in <State>" page,
                                      parsed from the public REST HTML (no key). Columns whose header contains
                                      (D)/(R)/(I) or a known nominee name are mapped to parties.
  3. POLLS_CSV_URL (env)            — any published CSV with the same columns as polls.csv (e.g. a Google Sheet)

Margin convention: challenger-to-Republican (usually D) minus Republican, in points.
"""
from __future__ import annotations

import io
import os
import re
import sys
from datetime import date

import pandas as pd
import requests

from ..config import MANUAL, CACHE, ELECTION_DATE, CANDIDATES
from ..features import pollster_grades
from pm.common import STATES, SENATE_2026, SENATE_SPECIAL_2026, GOVERNOR_2026

WIKI = "https://en.wikipedia.org/api/rest_v1/page/html/{}"
UA = {"User-Agent": "election-forest/1.0 (open-source election model; github)"}


# ---------------------------------------------------------------- pollster grades
def _grade_lookup():
    try:
        df = pd.read_csv(MANUAL.parent / "reference" / "538_pollster_ratings.csv")
    except FileNotFoundError:
        return lambda name: None
    names = [(str(n).lower(), g) for n, g in zip(df.pollster, df.numeric_grade)]

    def look(name: str):
        n = str(name).lower().replace("/", " ").replace(".", "")
        toks = [t for t in re.split(r"\W+", n) if len(t) > 2]
        best, score = None, 0
        for full, g in names:
            s = sum(t in full for t in toks)
            if s > score:
                best, score = g, s
        return best if score else None
    return look


# ---------------------------------------------------------------- adapters
def manual() -> pd.DataFrame:
    p = MANUAL / "polls.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)
    df["origin"] = "manual"
    return df


def from_url() -> pd.DataFrame:
    url = os.getenv("POLLS_CSV_URL")
    if not url:
        return pd.DataFrame()
    r = requests.get(url, timeout=30, headers=UA)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df["origin"] = "csv-url"
    return df


def _wiki_titles():
    for st in sorted(SENATE_2026 | SENATE_SPECIAL_2026):
        name = STATES[st][0]
        key = f"senate:{st}" + (":special" if st in SENATE_SPECIAL_2026 and st not in SENATE_2026 else "")
        suffix = "_special" if key.endswith("special") else ""
        yield key, f"2026_United_States_Senate{suffix}_election_in_{name.replace(' ', '_')}"
    for st in sorted(GOVERNOR_2026):
        yield f"governor:{st}", f"2026_{STATES[st][0].replace(' ', '_')}_gubernatorial_election"


_PARTY_HDR = re.compile(r"\((D|R|I|DFL|Ind\.?|Rep\.?|Dem\.?)\)", re.I)


def _party_for_header(h: str, key: str):
    m = _PARTY_HDR.search(h)
    if m:
        t = m.group(1).upper()[0]
        return {"D": "D", "R": "R", "I": "I"}.get(t)
    for party, name in CANDIDATES.get(key, {}).items():
        if name and name.split()[-1].lower() in h.lower():
            return party
    return None


def _num(x):
    m = re.search(r"-?\d+(\.\d+)?", str(x).replace(",", ""))
    return float(m.group()) if m else None


def _end_date(s: str):
    s = re.sub(r"\[.*?\]", "", str(s))
    m = re.findall(r"([A-Z][a-z]{2,8})\.?\s+(?:\d{1,2}\s*[–-]\s*)?(\d{1,2}),?\s*(\d{4})?", s)
    if not m:
        return None
    mon, day, yr = m[-1]
    try:
        return pd.Timestamp(f"{mon[:3]} {day} {yr or '2026'}").date()
    except ValueError:
        return None


def wikipedia(keys: set | None = None, limit: int = 80) -> pd.DataFrame:
    rows = []
    for key, title in list(_wiki_titles())[:limit]:
        if keys and key not in keys:
            continue
        try:
            r = requests.get(WIKI.format(title), timeout=25, headers=UA)
            if r.status_code != 200:
                continue
            tables = pd.read_html(io.StringIO(r.text))
        except Exception as e:  # noqa: BLE001
            print(f"[polls] wikipedia {title}: {e}", file=sys.stderr)
            continue
        for t in tables:
            if isinstance(t.columns, pd.MultiIndex):
                t.columns = [" ".join(str(c) for c in col if "Unnamed" not in str(c)).strip() for col in t.columns]
            cols = [str(c) for c in t.columns]
            src = next((c for c in cols if "poll source" in c.lower()), None)
            if not src:
                continue
            party_cols = {c: _party_for_header(c, key) for c in cols}
            dcol = next((c for c, p in party_cols.items() if p in ("D", "I")), None)
            rcol = next((c for c, p in party_cols.items() if p == "R"), None)
            if not dcol or not rcol:
                continue
            datec = next((c for c in cols if "date" in c.lower()), None)
            nc = next((c for c in cols if "sample" in c.lower()), None)
            for _, row in t.iterrows():
                d, rr = _num(row[dcol]), _num(row[rcol])
                if d is None or rr is None:
                    continue
                end = _end_date(row[datec]) if datec else None
                if end and end < date(2026, 9, 1):
                    continue
                rows.append({"race": key, "pollster": re.sub(r"\[.*?\]", "", str(row[src])).strip(),
                             "margin": d - rr, "dem_pct": d, "rep_pct": rr,
                             "samplesize": _num(row[nc]) if nc else None, "end_date": end,
                             "partisan": 1 if re.search(r"\((D|R)\)|PAC|campaign", str(row[src])) else 0,
                             "source": f"https://en.wikipedia.org/wiki/{title}", "origin": "wikipedia"})
            break  # first polling table on the page = general election
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- public API
def load(online: bool = True) -> tuple[pd.DataFrame, dict]:
    frames, status = [manual()], {"manual": f"{len(manual())} polls"}
    if online:
        for name, fn in (("wikipedia", wikipedia), ("csv-url", from_url)):
            try:
                df = fn()
                frames.append(df)
                status[name] = f"{len(df)} polls"
                if name == "wikipedia" and len(df):
                    CACHE.mkdir(parents=True, exist_ok=True)
                    df.to_csv(CACHE / "polls_wikipedia.csv", index=False)
            except Exception as e:  # noqa: BLE001
                status[name] = f"failed: {str(e)[:100]}"
        if not any(len(f) for f in frames[1:]) and (CACHE / "polls_wikipedia.csv").exists():
            frames.append(pd.read_csv(CACHE / "polls_wikipedia.csv"))
            status["wikipedia"] += " (using cache)"
    df = pd.concat([f for f in frames if len(f)], ignore_index=True) if any(len(f) for f in frames) else pd.DataFrame()
    if df.empty:
        return df, status
    df = df.drop_duplicates(subset=["race", "pollster", "margin"], keep="first")
    look = _grade_lookup()
    df["grade"] = df["pollster"].map(look)
    ed = pd.to_datetime(df.get("end_date"), errors="coerce")
    df["days"] = (pd.Timestamp(ELECTION_DATE) - ed).dt.days.fillna(7).clip(lower=0)
    df["partisan"] = df.get("partisan", 0).fillna(0).astype(bool)
    return df, status


def for_race(df: pd.DataFrame, key: str) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df.race == key]
