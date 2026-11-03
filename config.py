"""Static configuration. Anything here can be overridden in config.yaml-style env vars or data/manual/*.csv."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
REF = DATA / "reference"
MANUAL = DATA / "manual"
LIVE = DATA / "live"
CACHE = DATA / "cache"
SITE_DATA = ROOT / "site" / "data"

CYCLE = 2026
ELECTION_DATE = "2026-11-03"

# President's party on Election Day of each cycle (drives midterm-penalty and economy features).
PRES_PARTY = {
    1998: "D", 2000: "D", 2002: "R", 2004: "R", 2006: "R", 2008: "R", 2010: "D", 2012: "D",
    2014: "D", 2016: "D", 2018: "R", 2020: "R", 2022: "D", 2024: "D", 2026: "R",
}
# Presidential election used as the "state lean" baseline for each cycle (most recent BEFORE the cycle).
LEAN_YEAR = {2010: 2008, 2012: 2008, 2014: 2012, 2016: 2012, 2018: 2016, 2020: 2016, 2022: 2020, 2024: 2020, 2026: 2024}

# Senate arithmetic (119th Congress -> 2026). Seats NOT up in 2026, by caucus.
SENATE_NOT_UP = {"D": 34, "R": 31}          # D count includes King (ME) and Sanders (VT)
SENATE_TIE_GOES_TO = "R"                     # VP Vance breaks ties
# How an independent winner would caucus (None = neither / count separately).
INDEPENDENT_CAUCUS = {"NE": None}
HOUSE_SEATS = 435

# National environment when no live generic-ballot source is reachable.
# Silver Bulletin average, final week: Democrats "nearly 9 points" ahead (Yahoo News, Nov 2026).
GENERIC_BALLOT_DEFAULT = float(os.getenv("GENERIC_BALLOT_D_MARGIN", "8.7"))

# Intensely-followed races always shown in the tracker (others are added by closeness x attention).
PINNED = [
    "senate:TX", "senate:OH:special", "senate:MI", "senate:ME", "senate:NC", "senate:GA",
    "senate:IA", "senate:NE", "senate:AK", "senate:KS", "senate:SC", "senate:NH",
    "governor:OH", "governor:GA", "governor:AZ", "governor:MI", "governor:WI", "governor:NV",
]
TRACKER_SIZE = int(os.getenv("TRACKER_SIZE", "15"))

# Known nominees for key races (display only; parties drive the math).
CANDIDATES = {
    "senate:TX": {"D": "James Talarico", "R": "Ken Paxton"},
    "senate:MI": {"D": "Abdul El-Sayed"},
    "senate:ME": {"D": "Jackson", "R": "Susan Collins"},
    "senate:OH:special": {"D": "Sherrod Brown", "R": "Jon Husted"},
    "senate:AK": {"D": "Mary Peltola"},
    "senate:KS": {"D": "Hamilton", "R": "Roger Marshall"},
    "senate:IA": {"D": "Turek", "R": "Ashley Hinson"},
    "senate:NE": {"I": "Dan Osborn", "R": "Pete Ricketts"},
    "senate:SC": {"D": "Andrews", "R": "Lindsey Graham"},
    "senate:NC": {"D": "Roy Cooper"},
    "senate:GA": {"D": "Jon Ossoff"},
    "senate:NH": {"D": "Chris Pappas"},
    "governor:OH": {"R": "Vivek Ramaswamy"},
}

# Ensemble priors (Layer 2) before any 2026 results are in. Updated live from called races.
ENSEMBLE_PRIOR = {"forest": 0.55, "markets": 0.45}
ENSEMBLE_PRIOR_STRENGTH = 8      # pseudo-races of evidence behind the prior

# Optional keys (set as GitHub Actions secrets). Everything works without them.
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
AP_API_KEY = os.getenv("AP_API_KEY", "")
