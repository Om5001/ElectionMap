"""Absentee / early-vote returns and turnout projection.

Inputs (either or both):
  data/manual/early_vote.csv   — one row per state (see header). Typical sources: Secretary of State
                                 absentee files, the University of Florida Election Lab early-vote tracker.
  EARLY_VOTE_CSV_URL (env)     — a published CSV with the same columns (e.g. a Google Sheet you update).

Derived per state:
  turnout_index   = early votes now / early votes at the same point in 2022
  party_swing     = (D share - R share of early ballots by party registration) minus the same in 2022
  projected_total = early votes / expected early share of the total (EARLY_SHARE, default 0.55)
These feed the ensemble as a small, live-reweighted adjustment - they have no history in the training
data, so the forest never sees them directly.
"""
from __future__ import annotations

import io
import os

import pandas as pd
import requests

from ..config import MANUAL

COLUMNS = ["state", "early_votes", "early_votes_2022", "dem_share", "rep_share", "dem_share_2022",
           "rep_share_2022", "total_2022", "as_of", "source"]
EARLY_SHARE = float(os.getenv("EARLY_SHARE", "0.55"))


def load(online=True) -> tuple[dict, str]:
    frames = []
    p = MANUAL / "early_vote.csv"
    if p.exists():
        frames.append(pd.read_csv(p))
    url = os.getenv("EARLY_VOTE_CSV_URL")
    status = f"manual rows: {len(frames[0]) if frames else 0}"
    if online and url:
        try:
            r = requests.get(url, timeout=30)
            r.raise_for_status()
            frames.append(pd.read_csv(io.StringIO(r.text)))
            status += f"; url rows: {len(frames[-1])}"
        except Exception as e:  # noqa: BLE001
            status += f"; url failed: {str(e)[:80]}"
    if not frames:
        return {}, status
    df = pd.concat(frames, ignore_index=True).drop_duplicates("state", keep="last")
    out = {}
    for _, r in df.iterrows():
        if pd.isna(r.get("early_votes")):
            continue
        row = {"state": r.state, "earlyVotes": int(r.early_votes), "asOf": str(r.get("as_of", "")),
               "source": str(r.get("source", ""))}
        if pd.notna(r.get("early_votes_2022")) and r.early_votes_2022:
            row["turnoutIndex"] = round(r.early_votes / r.early_votes_2022, 3)
        if pd.notna(r.get("dem_share")) and pd.notna(r.get("rep_share")):
            row["partyGap"] = round(100 * (r.dem_share - r.rep_share), 2)
            if pd.notna(r.get("dem_share_2022")) and pd.notna(r.get("rep_share_2022")):
                row["partySwing"] = round(row["partyGap"] - 100 * (r.dem_share_2022 - r.rep_share_2022), 2)
        row["projectedTotal"] = int(r.early_votes / EARLY_SHARE)
        if pd.notna(r.get("total_2022")) and r.total_2022:
            row["projectedVs2022"] = round(row["projectedTotal"] / r.total_2022, 3)
        out[r.state] = row
    return out, status
