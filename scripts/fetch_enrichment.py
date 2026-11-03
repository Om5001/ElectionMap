#!/usr/bin/env python3
"""Download the extra reference data, then re-run the backtest to see if it helps.

    python scripts/fetch_enrichment.py                 # all four sources
    python scripts/fetch_enrichment.py medsl acs       # just some
    python scripts/fetch_enrichment.py --backtest      # all four, then compare the backtest before/after

Sources (credit them if you publish): MIT Election Data + Science Lab, The Downballot,
Silver Bulletin (natesilver.net), U.S. Census Bureau ACS. Optional env: CENSUS_API_KEY,
DOWNBALLOT_CSV_URL, SB_RAW_POLLS_URL. Manual fallbacks go in data/manual/ (see README).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forecast.data.enrichment import ALL  # noqa: E402

CV = ROOT / "data" / "cache" / "cv.json"


def backtest_summary():
    out = subprocess.run([sys.executable, "-m", "forecast.model.train"], cwd=ROOT, capture_output=True, text=True)
    txt = out.stdout
    try:
        d = json.loads(txt[: txt.rfind("}") + 1])
        return {t["model"]: (round(t["rmse"], 2), round(100 * t["winnerAccuracy"], 1)) for t in d["table"]}, txt[txt.rfind("}") + 1:]
    except Exception:  # noqa: BLE001
        return {}, out.stderr[-800:]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_bt = "--backtest" in sys.argv
    names = args or list(ALL)
    before = backtest_summary()[0] if do_bt else None
    ok = 0
    for n in names:
        try:
            print(f"  {n:11s} OK   {ALL[n]()}")
            ok += 1
        except Exception as e:  # noqa: BLE001
            print(f"  {n:11s} FAIL {str(e)[:300]}")
    if CV.exists():
        CV.unlink()           # force the leave-one-cycle-out backtest to re-run on the new data
    print(f"\n{ok}/{len(names)} sources updated.")
    if do_bt:
        after, weights = backtest_summary()
        print("\nBacktest (RMSE pts, winner %):")
        for m in after:
            print(f"  {m:30s} before {before.get(m, ('-', '-'))}   after {after[m]}")
        print("\nFactor weights after:" + weights)
    print("Restart the live server to use the new data:  python3 scripts/live_server.py --every 120")


if __name__ == "__main__":
    main()
