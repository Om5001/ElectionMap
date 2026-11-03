"""Historical training set: every polled Senate / Governor / House general election, 2010-2022.

Source: FiveThirtyEight `raw_polls.csv` (polls in the final weeks + certified results, CC BY 4.0),
538 pollster ratings, county-level presidential results (tonmcg/US_County_Level_Election_Results_08-24),
CPI and WTI monthly series. One row per race, target = actual D-minus-R margin.
"""
from __future__ import annotations

import pandas as pd

from ..config import REF, LEAN_YEAR
from ..features import race_features, pollster_grades, macro_features, macro_series

TYPE_TO_OFFICE = {"Sen-G": "senate", "Gov-G": "governor", "House-G": "house"}
POLL_WINDOW_DAYS = 21


def _polls_frame(g: pd.DataFrame, grades: dict) -> pd.DataFrame:
    return pd.DataFrame({
        "margin": g["margin_poll"].astype(float),
        "samplesize": g["samplesize"],
        "days": g["time_to_election"],
        "grade": g["pollster_rating_id"].map(grades),
        "partisan": g["partisan"].notna() & (g["partisan"].astype(str) != "NA"),
    })


def generic_ballot_by_cycle(df: pd.DataFrame | None = None) -> dict:
    df = df if df is not None else pd.read_csv(REF / "538_raw_polls.csv")
    g = df[(df.type_simple == "House-G-US") & (df.time_to_election <= 30)]
    g = g[(g.cand1_party == "DEM") & (g.cand2_party == "REP")]
    out = {}
    for cyc, gg in g.groupby("cycle"):
        out[int(cyc)] = float((gg.margin_poll * gg.samplesize.fillna(1000) ** 0.5).sum() / (gg.samplesize.fillna(1000) ** 0.5).sum())
    return out


def build() -> pd.DataFrame:
    df = pd.read_csv(REF / "538_raw_polls.csv")
    grades = pollster_grades()
    gb = generic_ballot_by_cycle(df)
    series = macro_series()
    df = df[df.type_simple.isin(TYPE_TO_OFFICE) & df.cycle.isin(LEAN_YEAR.keys())]
    df = df[(df.cand1_party == "DEM") & (df.cand2_party == "REP")]
    df = df[df.time_to_election <= POLL_WINDOW_DAYS]
    macro_cache = {}
    rows = []
    for race_id, g in df.groupby("race"):
        first = g.iloc[0]
        cycle = int(first.cycle)
        st = str(first.location).split("-")[0]
        if len(st) != 2:
            continue
        if cycle not in macro_cache:
            macro_cache[cycle] = macro_features(cycle, series)
        f = race_features(office=TYPE_TO_OFFICE[first.type_simple], state=st, cycle=cycle,
                          polls=_polls_frame(g, grades), generic_ballot=gb.get(cycle), macro=macro_cache[cycle])
        f.update({"race": race_id, "cycle": cycle, "state": st, "office": TYPE_TO_OFFICE[first.type_simple],
                  "location": first.location, "margin_actual": float(first.margin_actual),
                  "dem": first.cand1_name, "rep": first.cand2_name})
        rows.append(f)
    return pd.DataFrame(rows)


if __name__ == "__main__":
    h = build()
    print(h.groupby(["cycle", "office"]).size().unstack())
    print(h.describe().T[["count", "mean", "std"]])
