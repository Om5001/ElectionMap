#!/usr/bin/env python3
"""Build data/reference/house_district_lean.csv from the MIT Election Lab U.S. House returns (1976-2024).

Once this file exists, the pipeline models all 435 House districts, not just the ones with polls or
markets, and the House control probability comes from simulating every seat.

lean = district D-R margin in 2024 minus the national House D-R margin in 2024 (points).
Uncontested races are capped at +/-40.

Source: MIT Election Data and Science Lab, "U.S. House 1976-2024", Harvard Dataverse doi:10.7910/DVN/IG0UN2
(Runs fine in GitHub Actions; some sandboxes block dataverse.harvard.edu.)
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pm.common import STATES  # noqa: E402

DOI = "doi:10.7910/DVN/IG0UN2"
API = "https://dataverse.harvard.edu/api"
YEAR = 2024


def main():
    meta = requests.get(f"{API}/datasets/:persistentId/", params={"persistentId": DOI}, timeout=60).json()
    files = meta["data"]["latestVersion"]["files"]
    f = next(x for x in files if "house" in x["dataFile"]["filename"].lower()
             and x["dataFile"]["filename"].lower().endswith((".tab", ".csv")))
    fid = f["dataFile"]["id"]
    raw = requests.get(f"{API}/access/datafile/{fid}", params={"format": "original"}, timeout=300).content
    try:
        df = pd.read_csv(io.BytesIO(raw), low_memory=False)
    except Exception:  # noqa: BLE001
        df = pd.read_csv(io.BytesIO(raw), sep="\t", low_memory=False)
    df = df[(df.year == YEAR) & (df.stage.str.upper() == "GEN") & (~df.get("special", False).astype(bool))]
    df["party"] = df.party.str.upper().map(lambda p: "D" if "DEMOCRAT" in str(p) else "R" if "REPUBLICAN" in str(p) else "O")
    g = df.groupby(["state_po", "district", "party"]).candidatevotes.sum().unstack(fill_value=0)
    g["total"] = g.sum(axis=1)
    nat = 100 * (g.get("D", 0).sum() - g.get("R", 0).sum()) / g.total.sum()
    rows = []
    for (st, dist), r in g.iterrows():
        if st not in STATES:
            continue
        m = 100 * (r.get("D", 0) - r.get("R", 0)) / r.total if r.total else 0
        m = max(-40, min(40, m))
        d = "AL" if STATES[st][2] == 1 or int(dist) == 0 else f"{int(dist):02d}"
        rows.append({"race": f"house:{st}-{d}", "lean": round(m - nat, 2), "margin2024": round(m, 2)})
    out = pd.DataFrame(rows)
    path = ROOT / "data" / "reference" / "house_district_lean.csv"
    out.to_csv(path, index=False)
    print(f"wrote {path} ({len(out)} districts; national House margin {nat:+.1f})")


if __name__ == "__main__":
    main()
