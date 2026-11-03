"""Extra reference data that makes the model more current. Run via `python scripts/fetch_enrichment.py`.

  1. MIT Election Lab certified presidential returns by state  -> data/reference/pres_by_state.csv
     (replaces the incomplete 2012/2016 county-sum figures; Harvard Dataverse doi:10.7910/DVN/42MVDX)
  2. The Downballot: 2024 presidential results on the 2026 district lines -> data/reference/house_district_lean.csv
     (10 states redrew for 2026; credit The Downballot, the-downballot.com)
  3. Silver Bulletin raw polls (Feb 2025, includes 2024 results)       -> data/reference/sb_raw_polls.csv
     (free for any use with attribution to Silver Bulletin, natesilver.net)
  4. Census ACS 5-year demographics, states + congressional districts   -> data/reference/acs_state.csv, acs_cd.csv

Every fetcher has a local-file fallback in data/manual/ (see README) and a pure parser that is unit-tested,
because source formats change. Nothing here is required: the model runs without any of these files.
"""
from __future__ import annotations

import io
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from ..config import REF, MANUAL
from pm.common import STATES

UA = {"User-Agent": "election-forest/1.1 (open-source election model)"}
DATAVERSE = "https://dataverse.harvard.edu/api"
FIPS_TO_ABBR = {v[1]: k for k, v in STATES.items()}
ABBR_TO_FIPS = {k: v[1] for k, v in STATES.items()}


def _log(msg):
    print(f"[enrich] {msg}", file=sys.stderr)


def _get(url, **kw):
    r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 120), **kw)
    r.raise_for_status()
    return r


def _read_table(raw: bytes, name: str = "") -> pd.DataFrame:
    if name.lower().endswith((".xlsx", ".xls")) or raw[:2] == b"PK":
        return pd.read_excel(io.BytesIO(raw), sheet_name=0)
    for sep in (",", "\t"):
        try:
            df = pd.read_csv(io.BytesIO(raw), sep=sep, low_memory=False)
            if df.shape[1] > 2:
                return df
        except Exception:  # noqa: BLE001
            pass
    raise ValueError(f"could not parse table {name}")


def dataverse_file(doi: str, must: tuple[str, ...]) -> tuple[pd.DataFrame, str]:
    meta = _get(f"{DATAVERSE}/datasets/:persistentId/", params={"persistentId": doi}).json()
    files = meta["data"]["latestVersion"]["files"]
    def ok(fn):
        fn = fn.lower()
        return (all(m in fn for m in must) and fn.endswith((".tab", ".csv", ".xlsx"))
                and not any(x in fn for x in ("codebook", "readme", "notes")))
    cands = [f for f in files if ok(f["dataFile"]["filename"])]
    if not cands:
        raise RuntimeError(f"no file matching {must} in {doi}: {[f['dataFile']['filename'] for f in files]}")
    f = sorted(cands, key=lambda x: x["dataFile"]["filename"])[-1]  # latest year range sorts last
    raw = _get(f"{DATAVERSE}/access/datafile/{f['dataFile']['id']}", params={"format": "original"}, timeout=300).content
    return _read_table(raw, f["dataFile"]["filename"]), f["dataFile"]["filename"]


# =========================================================== 1. certified state presidential results
def parse_medsl_president(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=lambda c: str(c).strip().strip('"').lower())
    if "year" not in df:
        raise ValueError(f"not a returns file; columns were {list(df.columns)[:12]}")
    df = df[pd.to_numeric(df.year, errors="coerce") >= 2008]
    if "writein" in df:
        df = df[~df.writein.astype(str).str.upper().eq("TRUE")]
    party = df.get("party_simplified", df.get("party_detailed", "")).astype(str).str.upper()
    df = df.assign(p=np.where(party.str.contains("DEMOCRAT"), "dem", np.where(party.str.contains("REPUBLICAN"), "gop", "oth")))
    df["fips"] = df["state_fips"].astype(int).astype(str).str.zfill(2)
    votes = df.pivot_table(index=["year", "fips"], columns="p", values="candidatevotes", aggfunc="sum", fill_value=0)
    total = df.groupby(["year", "fips"]).totalvotes.max()
    out = votes.join(total).reset_index()
    out = out.rename(columns={"totalvotes": "total"})
    return out[["year", "fips", "dem", "gop", "total"]]


def fetch_medsl_president() -> str:
    local = MANUAL / "medsl_president.csv"
    if local.exists():
        df, name = pd.read_csv(local), local.name
    else:
        df, name = dataverse_file("doi:10.7910/DVN/42MVDX", ("president",))
    new = parse_medsl_president(df)
    path = REF / "pres_by_state.csv"
    old = pd.read_csv(path, dtype={"fips": str})
    backup = REF / "pres_by_state_countysum.csv"
    if not backup.exists():
        old.to_csv(backup, index=False)
    keep = old[~old.year.isin(new.year.unique())]          # e.g. keep 2024 if MIT file stops at 2020
    merged = pd.concat([new, keep], ignore_index=True).sort_values(["year", "fips"])
    merged.to_csv(path, index=False)
    return f"certified results for {sorted(new.year.unique().tolist())} from {name}; kept {sorted(keep.year.unique().tolist())} from county sums"


# =========================================================== 2. Downballot: 2024 president on 2026 lines
_DIST = re.compile(r"^\s*([A-Z]{2})[-\s]?(\d{1,2}|AL)\s*$")


def _pct(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    s = str(x).strip().replace("%", "").replace(",", "")
    if s.upper() in ("EVEN", "TIE", "0"):
        return 0.0
    m = re.match(r"^(D|R|H|T|DEM|REP|HARRIS|TRUMP|BIDEN)\s*\+\s*([\d.]+)$", s, re.I)
    if m:
        return float(m.group(2)) * (1 if m.group(1).upper()[0] in "DHB" else -1)
    try:
        return float(s.replace("+", ""))
    except ValueError:
        return None


def _is_margin_series(vals) -> bool:
    txt = [str(v) for v in vals if str(v).strip() not in ("", "nan", "None")]
    if not txt:
        return False
    signed = sum(bool(re.match(r"^\s*([DRHT]|Dem|Rep|Harris|Trump)?\s*[+-]\s*\d", t, re.I)) for t in txt)
    return signed / len(txt) > 0.6


def parse_downballot(df: pd.DataFrame) -> pd.DataFrame:
    """Find the district column and Harris/Trump shares (or a D+/R+ margin) wherever they are in the sheet."""
    # locate header row: first row (within 15) that contains a cell like 'District' / 'CD'
    hdr = re.compile(r"^\s*(district|cd|congressional district|seat|district code)\s*$", re.I)
    if not any(hdr.match(str(c)) for c in df.columns):
        for i in range(min(15, len(df))):
            row = [str(v) for v in df.iloc[i].tolist()]
            if any(hdr.match(v) for v in row):
                df = df.iloc[i + 1:].reset_index(drop=True)
                df.columns = row
                break
    # two-row header (e.g. "2024" spanning "Harris | Trump | Margin"): merge the sub-header row in
    cols = [str(c) for c in df.columns]
    if len(df):
        sub = [str(v) for v in df.iloc[0].tolist()]
        if any(re.match(r"^\s*(harris|trump|biden|margin|dem|rep|other)\s*%?\s*$", v, re.I) for v in sub):
            top, merged = "", []
            for c, sv in zip(cols, sub):
                if not re.match(r"^(nan|none|unnamed.*|\d+)?$", c, re.I) or re.match(r"^20\d\d$", c):
                    top = c
                merged.append(f"{top} {sv}".strip() if sv not in ("nan", "None") else c)
            df = df.iloc[1:].reset_index(drop=True)
            df.columns = merged
            cols = merged
    df.columns = cols
    dcol = None
    for c in cols:
        vals = df[c].astype(str).head(60)
        if vals.str.match(_DIST).mean() > 0.6:
            dcol = c
            break
    if dcol is None:
        raise ValueError(f"no district column found in {cols[:15]}")
    lc = {c: str(c).lower() for c in cols}
    harris = next((c for c in cols if "harris" in lc[c] and "trump" not in lc[c]), None)
    trump = next((c for c in cols if "trump" in lc[c] and "harris" not in lc[c]), None)
    margs = [c for c in cols if "margin" in lc[c]]
    marg = next((c for c in margs if "2024" in lc[c] or "pres" in lc[c] or "harris" in lc[c]), margs[0] if margs else None)
    if not marg and not (harris and trump):
        # a bare "2024" (or "Pres") column holding D+5 / R+3 / Harris +5 style margins
        for c in cols:
            if c != dcol and re.search(r"2024|pres", lc[c]) and _is_margin_series(df[c].head(80)):
                marg = c
                break
    rows = []
    for _, r in df.iterrows():
        m = _DIST.match(str(r[dcol]))
        if not m or m.group(1) not in STATES:
            continue
        st, d = m.group(1), m.group(2)
        n = STATES[st][2]
        dist = "AL" if n == 1 or d in ("AL", "0", "00") else f"{int(d):02d}"
        if harris and trump:
            h, t = _pct(r[harris]), _pct(r[trump])
            if h is None or t is None:
                continue
            if h <= 1 and t <= 1:
                h, t = 100 * h, 100 * t
            margin = h - t
        elif marg:
            margin = _pct(r[marg])
            if margin is None:
                continue
            if abs(margin) <= 1:
                margin *= 100
        else:
            preview = df.head(3).to_dict("records")
            raise ValueError(f"no Harris/Trump or margin columns in {cols[:15]}; first rows: {str(preview)[:300]}")
        rows.append({"race": f"house:{st}-{dist}", "margin2024": round(margin, 2)})
    out = pd.DataFrame(rows).drop_duplicates("race", keep="first")
    if len(out) < 300:
        raise ValueError(f"only {len(out)} districts parsed")
    return out


def _national_2024() -> float:
    df = pd.read_csv(REF / "pres_by_state.csv")
    g = df[df.year == 2024]
    return float(100 * (g.dem.sum() - g.gop.sum()) / g.total.sum()) if len(g) else -1.48


def fetch_downballot(url: str | None = None) -> str:
    url = url or os.getenv("DOWNBALLOT_CSV_URL")
    local = MANUAL / "downballot_2026_districts.csv"
    tried = []
    if local.exists():
        out = parse_downballot(pd.read_csv(local, header=None).pipe(lambda d: d.rename(columns=d.iloc[0]).iloc[1:]))
        src = local.name
    else:
        urls = [url] if url else []
        if not urls:   # discover Google Sheets linked from the announcement post
            page = _get("https://www.the-downballot.com/p/the-downballot-releases-presidential").text
            ids = list(dict.fromkeys(re.findall(r"docs\.google\.com/spreadsheets/d/([\w-]{20,})", page)))
            urls = [f"https://docs.google.com/spreadsheets/d/{i}/export?format=csv" for i in ids]
        out, src = None, None
        for u in urls:
            try:
                raw = _get(u).content
                df = pd.read_csv(io.BytesIO(raw), header=None)
                df = df.rename(columns=df.iloc[0]).iloc[1:]
                out, src = parse_downballot(df), u
                break
            except Exception as e:  # noqa: BLE001
                tried.append(f"{u[:80]}: {str(e)[:80]}")
        if out is None:
            raise RuntimeError("could not parse a Downballot sheet. Tried: " + " | ".join(tried) +
                               ". Download the sheet as CSV to data/manual/downballot_2026_districts.csv or set DOWNBALLOT_CSV_URL.")
    nat = _national_2024()
    out["lean"] = (out.margin2024 - nat).round(2)
    out["source"] = "downballot-2026-lines"
    path = REF / "house_district_lean.csv"
    if path.exists():
        old = pd.read_csv(path)
        old = old[~old.race.isin(out.race)]
        if len(old):
            old["source"] = old.get("source", "medsl-house-2024")
            out = pd.concat([out, old], ignore_index=True)
    out.to_csv(path, index=False)
    return f"{(out.source == 'downballot-2026-lines').sum()} districts on 2026 lines from {src[:70]} (national 2024 margin {nat:+.2f})"


# =========================================================== 3. Silver Bulletin raw polls (adds 2024)
SYN = {
    "cycle": ["cycle", "year", "election_year"],
    "type_simple": ["type_simple", "type", "race_type", "office_type"],
    "location": ["location", "state", "geography"],
    "race": ["race", "race_id", "raceid"],
    "pollster": ["pollster", "pollster_name", "pollster_display_name"],
    "pollster_rating_id": ["pollster_rating_id", "pollster_id"],
    "samplesize": ["samplesize", "sample_size", "n"],
    "partisan": ["partisan", "sponsor_partisan"],
    "polldate": ["polldate", "end_date", "poll_date", "enddate"],
    "electiondate": ["electiondate", "election_date"],
    "time_to_election": ["time_to_election", "days_to_election", "days_before_election"],
    "cand1_name": ["cand1_name"], "cand2_name": ["cand2_name"],
    "cand1_party": ["cand1_party"], "cand2_party": ["cand2_party"],
    "cand1_pct": ["cand1_pct"], "cand2_pct": ["cand2_pct"],
    "cand1_actual": ["cand1_actual"], "cand2_actual": ["cand2_actual"],
    "margin_poll": ["margin_poll", "poll_margin"], "margin_actual": ["margin_actual", "actual_margin"],
}


def _norm_type(v: str) -> str:
    s = str(v).strip()
    if s in ("Sen-G", "Gov-G", "House-G", "House-G-US", "Pres-G"):
        return s
    t = s.lower()
    gen = "-p" not in t and "prim" not in t
    if not gen:
        return s
    if "house" in t and ("us" in t or "generic" in t or "national" in t):
        return "House-G-US"
    for k, v2 in (("sen", "Sen-G"), ("gov", "Gov-G"), ("house", "House-G"), ("pres", "Pres-G")):
        if k in t:
            return v2
    return s


def normalize_sb(df: pd.DataFrame) -> pd.DataFrame:
    low = {str(c).lower().strip(): c for c in df.columns}
    out = pd.DataFrame(index=df.index)
    for k, names in SYN.items():
        src = next((low[n] for n in names if n in low), None)
        if src is not None:
            out[k] = df[src]
    if "margin_poll" not in out and {"cand1_pct", "cand2_pct"} <= set(out):
        out["margin_poll"] = pd.to_numeric(out.cand1_pct, errors="coerce") - pd.to_numeric(out.cand2_pct, errors="coerce")
    if "margin_actual" not in out and {"cand1_actual", "cand2_actual"} <= set(out):
        out["margin_actual"] = pd.to_numeric(out.cand1_actual, errors="coerce") - pd.to_numeric(out.cand2_actual, errors="coerce")
    if "time_to_election" not in out and {"polldate", "electiondate"} <= set(out):
        out["time_to_election"] = (pd.to_datetime(out.electiondate, errors="coerce") - pd.to_datetime(out.polldate, errors="coerce")).dt.days
    need = {"cycle", "type_simple", "location", "margin_poll", "margin_actual", "cand1_party", "cand2_party"}
    missing = need - set(out)
    if missing:
        raise ValueError(f"Silver Bulletin file is missing {sorted(missing)}; columns were {list(df.columns)[:40]}")
    out["type_simple"] = out.type_simple.map(_norm_type)
    for c in ("cand1_party", "cand2_party"):
        out[c] = out[c].astype(str).str.upper().str[:3].replace({"DFL": "DEM", "GOP": "REP"})
    out["cycle"] = pd.to_numeric(out.cycle, errors="coerce").astype("Int64")
    if "race" not in out:
        out["race"] = out.cycle.astype(str) + "_" + out.type_simple + "_" + out.location.astype(str)
    out["race"] = "sb_" + out.race.astype(str)
    for c in ("samplesize", "time_to_election", "margin_poll", "margin_actual"):
        if c in out:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    if "time_to_election" not in out:
        out["time_to_election"] = 14
    for c in ("pollster_rating_id", "partisan", "samplesize", "pollster", "cand1_name", "cand2_name"):
        if c not in out:
            out[c] = np.nan
    return out.dropna(subset=["margin_poll", "margin_actual", "cycle"])


def fetch_silver_bulletin(url: str | None = None) -> str:
    url = url or os.getenv("SB_RAW_POLLS_URL")
    local = next((p for p in (MANUAL / "silver_bulletin_raw_polls.xlsx", MANUAL / "silver_bulletin_raw_polls.csv") if p.exists()), None)
    if local:
        raw, name = local.read_bytes(), local.name
    else:
        if not url:
            page = _get("https://www.natesilver.net/p/pollster-ratings-archive-2025").text
            links = re.findall(r"https://[^\"'\s>]+?\.xlsx", page)
            links = [l for l in links if "raw" in l.lower()] or links
            if not links:
                raise RuntimeError("no .xlsx link found on the Silver Bulletin page; download 'Rawpolls' to "
                                   "data/manual/silver_bulletin_raw_polls.xlsx or set SB_RAW_POLLS_URL")
            url = links[0]
        try:
            raw, name = _get(url, timeout=300).content, url
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (401, 403):
                raise RuntimeError("Silver Bulletin requires you to be logged in. Log in (free) at natesilver.net, download "
                                   "'Rawpolls' from /p/pollster-ratings-archive-2025, and drag it into data/manual/ "
                                   "named silver_bulletin_raw_polls.xlsx") from None
            raise
    if raw[:2] != b"PK" and b"<html" in raw[:2000].lower():
        raise RuntimeError("Silver Bulletin requires you to be logged in to download this file. Log in (free) at "
                           "natesilver.net, download 'Rawpolls' from /p/pollster-ratings-archive-2025, and drag it into "
                           "data/manual/ named silver_bulletin_raw_polls.xlsx")
    out = normalize_sb(_read_table(raw, name))
    out.to_csv(REF / "sb_raw_polls.csv", index=False)
    cyc = out.groupby("cycle").race.nunique().to_dict()
    return f"{len(out)} polls, races per cycle {cyc}"


# =========================================================== 4. Census ACS demographics
ACS_VARS = {
    "B01003_001E": "pop", "B15002_001E": "pop25",
    **{f"B15002_0{n}E": f"ba_m{n}" for n in (15, 16, 17, 18)},
    **{f"B15002_0{n}E": f"ba_f{n}" for n in (32, 33, 34, 35)},
    "B03003_001E": "eth_total", "B03003_003E": "hispanic",
    "B02001_001E": "race_total", "B02001_003E": "black",
    "B01002_001E": "median_age", "B19013_001E": "median_income",
}


def parse_acs(rows: list[list], year: int, level: str) -> pd.DataFrame:
    head, data = rows[0], rows[1:]
    df = pd.DataFrame(data, columns=head)
    for v in ACS_VARS:
        df[v] = pd.to_numeric(df[v], errors="coerce")
        df.loc[df[v] < 0, v] = np.nan     # Census sentinels like -666666666
    df = df.rename(columns=ACS_VARS)
    ba = df[[c for c in df.columns if c.startswith("ba_")]].sum(axis=1)
    out = pd.DataFrame({
        "year": year, "state": df["state"].map(FIPS_TO_ABBR),
        "pct_college": 100 * ba / df.pop25, "pct_hispanic": 100 * df.hispanic / df.eth_total,
        "pct_black": 100 * df.black / df.race_total, "median_age": df.median_age,
        "log_income": np.log(df.median_income), "pop": df["pop"],
    })
    if level == "cd":
        cd = df["congressional district"].astype(str)
        out["district"] = np.where(cd.isin(["00", "0", "98"]), "AL", cd.str.zfill(2))
        out = out[~cd.isin(["ZZ"])]
    return out.dropna(subset=["state"]).round(3)


def _census(year: int, params: dict) -> list:
    url = f"https://api.census.gov/data/{year}/acs/acs5"
    r = requests.get(url, params=params, headers=UA, timeout=60)
    try:
        return r.json()
    except ValueError:
        msg = re.sub(r"<[^>]+>", " ", r.text)
        msg = re.sub(r"\s+", " ", msg).strip()[:140]
        bad = re.findall(r"unknown variable '([^']+)'", r.text)
        if bad:   # drop unsupported variables (e.g. an education table missing in that year) and retry once
            keep = [v for v in params["get"].split(",") if v not in bad]
            r2 = requests.get(url, params={**params, "get": ",".join(keep)}, headers=UA, timeout=60)
            rows = r2.json()
            for v in bad:
                rows[0].append(v)
                for row in rows[1:]:
                    row.append(None)
            return rows
        raise ValueError(f"HTTP {r.status_code}: {msg or 'empty reply'}")


def fetch_acs(years=None) -> str:
    key = os.getenv("CENSUS_API_KEY", "")
    years = years or list(range(2010, 2026, 2)) + [2023, 2025]
    get = "NAME," + ",".join(ACS_VARS)
    st_frames, cd_frames, notes = [], [], []
    for y in sorted(set(years)):
        for level, geo in (("state", {"for": "state:*"}), ("cd", {"for": "congressional district:*", "in": "state:*"})):
            params = {"get": get, **geo}
            if key:
                params["key"] = key
            try:
                rows = _census(y, params)
                (st_frames if level == "state" else cd_frames).append(parse_acs(rows, y, level))
            except Exception as e:  # noqa: BLE001
                notes.append(f"{y}/{level}: {str(e)[:160]}")
    if not st_frames:
        raise RuntimeError("no ACS data fetched. First errors: " + " | ".join(notes[:3]) +
                           ". If it mentions a key, get a free one at api.census.gov/data/key_signup.html and "
                           "run: export CENSUS_API_KEY=yourkey")
    pd.concat(st_frames).to_csv(REF / "acs_state.csv", index=False)
    if cd_frames:
        pd.concat(cd_frames).to_csv(REF / "acs_cd.csv", index=False)
    got = sorted({int(f.year.iloc[0]) for f in st_frames})
    return f"ACS 5-year for {got}" + (f" (skipped: {len(notes)} not yet published/failed)" if notes else "")


ALL = {"medsl": fetch_medsl_president, "acs": fetch_acs, "sb": fetch_silver_bulletin, "downballot": fetch_downballot}
