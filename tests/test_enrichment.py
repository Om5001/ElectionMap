import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forecast.data import enrichment as E  # noqa: E402
from forecast import features as F  # noqa: E402
from forecast.data import history as H  # noqa: E402
from pm.common import STATES  # noqa: E402


def test_parse_medsl_president():
    df = pd.DataFrame({
        "year": [2016] * 3 + [2004], "state_po": ["CA"] * 4, "state_fips": [6] * 4,
        "party_simplified": ["DEMOCRAT", "REPUBLICAN", "LIBERTARIAN", "DEMOCRAT"],
        "candidatevotes": [8753788, 4483810, 478500, 1], "totalvotes": [14181595] * 4, "writein": [False] * 4,
    })
    out = E.parse_medsl_president(df)
    r = out.iloc[0]
    assert len(out) == 1 and r.fips == "06" and r.dem == 8753788 and r.total == 14181595
    assert round(100 * (r.dem - r.gop) / r.total, 1) == 30.1


def _fake_downballot(style):
    rows = []
    for st, v in STATES.items():
        for d in range(1, v[2] + 1):
            code = f"{st}-{'AL' if v[2] == 1 else f'{d:02d}'}"
            h = 50 + (hash(code) % 30 - 15)
            rows.append((code, h, 100 - h - 2))
    if style == "shares":
        df = pd.DataFrame(rows, columns=["District", "Harris %", "Trump %"])
        df["Harris %"] = df["Harris %"].map(lambda x: f"{x}.0%")
    else:
        df = pd.DataFrame({"CD": [r[0] for r in rows], "2024 Pres Margin": [f"{'D' if r[1] > r[2] else 'R'}+{abs(r[1] - r[2])}" for r in rows]})
    # mimic a sheet with title rows above the header
    top = pd.DataFrame([["2024 presidential results by 2026 district"] + [None] * (df.shape[1] - 1)], columns=df.columns)
    full = pd.concat([top, pd.DataFrame([df.columns.tolist()], columns=df.columns), df], ignore_index=True)
    full.columns = range(full.shape[1])
    return full, rows


@pytest.mark.parametrize("style", ["shares", "margin"])
def test_parse_downballot_finds_columns(style):
    df, rows = _fake_downballot(style)
    out = E.parse_downballot(df)
    assert len(out) == 435
    code, h, t = rows[0]
    st, d = code.split("-")
    got = out.set_index("race").loc[f"house:{st}-{d}", "margin2024"]
    assert abs(got - (h - t)) < 0.01


def test_normalize_sb_538_style_and_variants():
    df = pd.DataFrame({
        "Year": [2024, 2024, 2024], "Type": ["Senate General", "Sen-G", "House-G-US"], "Location": ["PA", "PA", "US"],
        "Race_ID": [1, 1, 2], "Pollster": ["NYT/Siena", "Emerson", "YouGov"], "Sample_Size": [1000, 800, 1500],
        "Poll_Date": ["2024-10-30", "2024-10-28", "2024-10-30"], "Election_Date": ["2024-11-05"] * 3,
        "cand1_party": ["Dem", "DEM", "DEM"], "cand2_party": ["Rep", "REP", "REP"],
        "cand1_pct": [48, 47, 49], "cand2_pct": [46, 48, 47], "cand1_actual": [48.6] * 2 + [47.2], "cand2_actual": [48.9] * 2 + [49.8],
    })
    out = E.normalize_sb(df)
    assert set(out.type_simple) == {"Sen-G", "House-G-US"}
    assert out.margin_poll.tolist() == [2, -1, 2]
    assert out.time_to_election.tolist() == [6, 8, 6]
    assert (out.cand1_party == "DEM").all()
    with pytest.raises(ValueError):
        E.normalize_sb(pd.DataFrame({"x": [1]}))


def test_parse_acs():
    head = ["NAME"] + list(E.ACS_VARS) + ["state", "congressional district"]
    vals = {"B01003_001E": 760000, "B15002_001E": 500000, "B03003_001E": 760000, "B03003_003E": 76000,
            "B02001_001E": 760000, "B02001_003E": 152000, "B01002_001E": 39.5, "B19013_001E": 70000}
    row = ["X"] + [str(vals.get(v, 25000)) for v in E.ACS_VARS] + ["42", "07"]
    al = list(row); al[-2:] = ["02", "00"]
    bad = list(row); bad[head.index("B19013_001E")] = "-666666666"
    out = E.parse_acs([head, row, al, bad], 2022, "cd")
    r = out.iloc[0]
    assert r.state == "PA" and r.district == "07" and r.pct_college == 40.0 and r.pct_black == 20.0
    assert out.iloc[1].district == "AL" and np.isnan(out.iloc[2].log_income)


def test_demographics_flow_into_training(tmp_path, monkeypatch):
    for f in (ROOT / "data" / "reference").glob("*.csv"):
        if f.name not in ("acs_state.csv", "acs_cd.csv", "sb_raw_polls.csv"):
            shutil.copy(f, tmp_path / f.name)
    rng = np.random.default_rng(0)
    st_rows = [{"year": y, "state": s, "pct_college": rng.uniform(20, 45), "pct_hispanic": rng.uniform(2, 40),
                "pct_black": rng.uniform(1, 35), "median_age": rng.uniform(32, 44), "log_income": rng.uniform(10.6, 11.4)}
               for y in range(2010, 2025, 2) for s in STATES]
    pd.DataFrame(st_rows).to_csv(tmp_path / "acs_state.csv", index=False)
    monkeypatch.setattr(F, "REF", tmp_path)
    monkeypatch.setattr(H, "REF", tmp_path)
    F._acs.cache_clear(); F.pres_lean.cache_clear(); F.pollster_grades.cache_clear()
    try:
        h = H.build()
        assert h.pct_college.notna().mean() > 0.95
        d = F.demographics(2026, "TX")
        assert d["pct_college"] is not None          # 2026 falls back to the latest year (2024)
        assert F.demographics(2008, "TX")["pct_college"] is None
    finally:
        F._acs.cache_clear(); F.pres_lean.cache_clear(); F.pollster_grades.cache_clear()
