"""Politically-predictive economic series.

Primary: FRED (St. Louis Fed). The `fredgraph.csv` endpoint needs no key; if FRED_API_KEY is set
the JSON API is used instead (higher rate limits). Fallback: bundled monthly CPI + WTI files
(datasets/cpi-us, datasets/oil-prices on GitHub) so training always works offline.

Series used by the model: CPI (CPIAUCSL), WTI crude (MCOILWTICO), unemployment (UNRATE).
Display-only context: regular gasoline (GASREGW), consumer sentiment (UMCSENT), 10y yield (DGS10).
"""
from __future__ import annotations

import io
import sys
from functools import lru_cache

import pandas as pd
import requests

from ..config import REF, CACHE, FRED_API_KEY

FRED_SERIES = {
    "cpi": "CPIAUCSL", "wti": "MCOILWTICO", "unrate": "UNRATE",
    "gas": "GASREGW", "sentiment": "UMCSENT", "t10y": "DGS10",
}
MODEL_SERIES = ("cpi", "wti", "unrate")
LABELS = {"cpi": "CPI inflation (y/y)", "wti": "WTI crude ($/bbl)", "unrate": "Unemployment rate",
          "gas": "Gasoline, regular ($/gal)", "sentiment": "Consumer sentiment (UMich)", "t10y": "10-yr Treasury yield"}


def _fred(series_id: str) -> pd.Series:
    if FRED_API_KEY:
        r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                         params={"series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json",
                                 "observation_start": "1995-01-01"}, timeout=30)
        r.raise_for_status()
        obs = r.json()["observations"]
        s = pd.Series({pd.Timestamp(o["date"]): pd.to_numeric(o["value"], errors="coerce") for o in obs})
    else:
        r = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id}, timeout=30)
        r.raise_for_status()
        df = pd.read_csv(io.StringIO(r.text))
        df.columns = ["date", "value"]
        s = pd.Series(pd.to_numeric(df["value"], errors="coerce").values, index=pd.to_datetime(df["date"]))
    return s.dropna()


def _monthly(s: pd.Series) -> pd.Series:
    return s.resample("MS").mean().dropna()


def _bundled() -> dict:
    out = {}
    cpi = pd.read_csv(REF / "cpi.csv", parse_dates=["Date"])
    out["cpi"] = _monthly(pd.Series(cpi["Index"].values, index=cpi["Date"]))
    wti = pd.read_csv(REF / "wti.csv", parse_dates=["Date"])
    out["wti"] = _monthly(pd.Series(wti["Price"].values, index=wti["Date"]))
    return out


def refresh(offline: bool = False) -> dict:
    """Pull FRED into data/cache (best-effort). Returns status per series."""
    status = {}
    if offline:
        return {k: "offline" for k in FRED_SERIES}
    CACHE.mkdir(parents=True, exist_ok=True)
    for key, sid in FRED_SERIES.items():
        try:
            s = _fred(sid)
            s.rename("value").to_csv(CACHE / f"fred_{key}.csv", index_label="date")
            status[key] = f"FRED {sid} ok ({s.index[-1].date()})"
        except Exception as e:  # noqa: BLE001
            status[key] = f"FRED {sid} failed: {str(e)[:120]}"
            print(f"[macro] {status[key]}", file=sys.stderr)
    load_all.cache_clear()
    return status


@lru_cache(None)
def load_all() -> dict:
    out = _bundled()
    for key in FRED_SERIES:
        p = CACHE / f"fred_{key}.csv"
        if p.exists():
            df = pd.read_csv(p, parse_dates=["date"])
            s = _monthly(pd.Series(df["value"].values, index=df["date"]))
            if key in out:  # splice: keep bundled history, prefer FRED where both exist
                s = s.combine_first(out[key])
            out[key] = s
    return out


def dashboard() -> list[dict]:
    """Latest reading + 12-month change for the UI."""
    rows = []
    for key, s in load_all().items():
        if s.empty:
            continue
        last_t, last = s.index[-1], float(s.iloc[-1])
        prev = s[s.index <= last_t - pd.DateOffset(months=12)]
        row = {"key": key, "label": LABELS.get(key, key), "asOf": str(last_t.date()), "value": round(last, 2)}
        if not prev.empty:
            p = float(prev.iloc[-1])
            row["yoyPct"] = round(100 * (last / p - 1), 2) if key != "unrate" else None
            row["yoyChg"] = round(last - p, 2)
        if key == "cpi" and "yoyPct" in row:
            row["display"] = f"{row['yoyPct']:.1f}% y/y"
        rows.append(row)
    order = ["cpi", "wti", "gas", "unrate", "sentiment", "t10y"]
    return sorted(rows, key=lambda r: order.index(r["key"]) if r["key"] in order else 99)
