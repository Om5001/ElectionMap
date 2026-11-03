"""Feature engineering shared by the historical training set and live 2026 races.

Every feature is expressed so that POSITIVE = good for Democrats, which lets the
correlation weights be read directly ("how much does this factor track the D margin").
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pandas as pd

from .config import REF, PRES_PARTY, LEAN_YEAR
from pm.common import STATES

FEATURES = [
    "poll_margin",        # weighted average of polls, D minus R (pts)
    "poll_n_log",         # log(1 + number of polls)
    "poll_sd",            # disagreement between pollsters (pts)
    "poll_quality",       # mean pollster grade (0-3, 538 numeric grade)
    "state_lean",         # state presidential margin minus national margin (pts)
    "generic_ballot",     # national generic congressional ballot, D minus R (pts)
    "midterm_inparty",    # +1 midterm under R president, -1 under D president, 0 otherwise
    "inflation_inparty",  # CPI y/y % x (+1 if R president, -1 if D) -> inflation hurts the in-party
    "oil_inparty",        # WTI crude y/y % change x in-party sign
    "unemp_inparty",      # unemployment-rate change y/y x in-party sign (FRED; optional)
    "office_senate", "office_governor", "office_house",
]
FEATURE_LABELS = {
    "poll_margin": "Poll average", "poll_n_log": "Poll volume", "poll_sd": "Pollster disagreement",
    "poll_quality": "Pollster quality", "state_lean": "State partisan lean", "generic_ballot": "Generic ballot",
    "midterm_inparty": "Midterm penalty", "inflation_inparty": "Inflation (CPI)", "oil_inparty": "Oil price",
    "unemp_inparty": "Unemployment", "office_senate": "Office: Senate", "office_governor": "Office: Governor",
    "office_house": "Office: House",
}
FIPS_TO_ABBR = {v[1]: k for k, v in STATES.items()}
FIPS_TO_ABBR["11"] = "DC"


def inparty_sign(cycle: int) -> int:
    return 1 if PRES_PARTY.get(cycle) == "R" else -1


# ---------------------------------------------------------------- reference data

@lru_cache(None)
def pollster_grades() -> dict:
    p = REF / "538_pollster_ratings.csv"
    if not p.exists():
        return {}
    df = pd.read_csv(p)
    return dict(zip(df["pollster_rating_id"], df["numeric_grade"].fillna(1.0)))


@lru_cache(None)
def pres_lean() -> dict:
    """{(year, ST): state margin minus national margin} in points."""
    df = pd.read_csv(REF / "pres_by_state.csv", dtype={"fips": str})
    df["fips"] = df["fips"].str.zfill(2)
    out = {}
    for y, g in df.groupby("year"):
        nat = 100 * (g.dem.sum() - g.gop.sum()) / g.total.sum()
        for _, r in g.iterrows():
            st = FIPS_TO_ABBR.get(r.fips)
            if st:
                out[(int(y), st)] = 100 * (r.dem - r.gop) / r.total - nat
    return out


def macro_series() -> dict:
    """Monthly series as pandas Series indexed by month start. Live FRED pulls (if any) override the bundled files."""
    from .data import macro
    return macro.load_all()


def macro_features(cycle: int, series: dict | None = None, as_of: str | None = None) -> dict:
    """Economic factors measured at the September before the election (or latest available)."""
    series = series if series is not None else macro_series()
    s = inparty_sign(cycle)
    ref = pd.Timestamp(as_of) if as_of else pd.Timestamp(f"{cycle}-09-01")
    out = {}

    def yoy(name, pct=True):
        ser = series.get(name)
        if ser is None or ser.empty:
            return None
        ser = ser[ser.index <= ref + pd.offsets.MonthEnd(0)]
        if ser.empty:
            return None
        last_t = ser.index[-1]
        prev = ser[ser.index <= last_t - pd.DateOffset(months=12)]
        if prev.empty:
            return None
        a, b = ser.iloc[-1], prev.iloc[-1]
        return 100 * (a / b - 1) if pct else a - b

    for feat, name, pct in (("inflation_inparty", "cpi", True), ("oil_inparty", "wti", True), ("unemp_inparty", "unrate", False)):
        v = yoy(name, pct)
        out[feat] = None if v is None else s * v
    return out


# ---------------------------------------------------------------- poll aggregation

def aggregate_polls(polls: pd.DataFrame) -> dict:
    """polls: columns margin (D-R), samplesize, days (to election), grade (0-3, optional), partisan (bool)."""
    if polls is None or polls.empty:
        return {"poll_margin": None, "poll_n_log": 0.0, "poll_sd": None, "poll_quality": None}
    n = polls["samplesize"].fillna(600).clip(100, 5000)
    days = polls["days"].fillna(14).clip(0, 120)
    grade = polls.get("grade", pd.Series(1.5, index=polls.index)).fillna(1.5)
    partisan = polls.get("partisan", pd.Series(False, index=polls.index)).fillna(False).astype(bool)
    w = np.sqrt(n) * np.exp(-days / 14.0) * (0.5 + grade / 6.0) * np.where(partisan, 0.5, 1.0)
    m = polls["margin"].astype(float)
    avg = float(np.average(m, weights=w))
    sd = float(np.sqrt(np.average((m - avg) ** 2, weights=w))) if len(m) > 1 else None
    return {"poll_margin": avg, "poll_n_log": math.log1p(len(m)), "poll_sd": sd, "poll_quality": float(grade.mean())}


def office_onehot(office: str) -> dict:
    return {"office_senate": float(office == "senate"), "office_governor": float(office == "governor"),
            "office_house": float(office == "house")}


def race_features(*, office: str, state: str, cycle: int, polls: pd.DataFrame | None,
                  generic_ballot: float | None, macro: dict) -> dict:
    f = {}
    f.update(aggregate_polls(polls))
    ly = LEAN_YEAR.get(cycle)
    f["state_lean"] = pres_lean().get((ly, state)) if ly else None
    f["generic_ballot"] = generic_ballot
    f["midterm_inparty"] = float(inparty_sign(cycle)) if cycle % 4 == 2 else 0.0
    f.update(macro)
    f.update(office_onehot(office))
    return f


def to_matrix(rows: list[dict], features=FEATURES) -> np.ndarray:
    return np.array([[np.nan if r.get(k) is None else r.get(k) for k in features] for r in rows], dtype=float)
