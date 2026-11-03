"""Shared helpers: state metadata, race classification, party detection, HTTP."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests

ELECTION_YEAR = 2026

# ---------------------------------------------------------------- states
STATES = {
    "AL": ("Alabama", "01", 7), "AK": ("Alaska", "02", 1), "AZ": ("Arizona", "04", 9),
    "AR": ("Arkansas", "05", 4), "CA": ("California", "06", 52), "CO": ("Colorado", "08", 8),
    "CT": ("Connecticut", "09", 5), "DE": ("Delaware", "10", 1), "FL": ("Florida", "12", 28),
    "GA": ("Georgia", "13", 14), "HI": ("Hawaii", "15", 2), "ID": ("Idaho", "16", 2),
    "IL": ("Illinois", "17", 17), "IN": ("Indiana", "18", 9), "IA": ("Iowa", "19", 4),
    "KS": ("Kansas", "20", 4), "KY": ("Kentucky", "21", 6), "LA": ("Louisiana", "22", 6),
    "ME": ("Maine", "23", 2), "MD": ("Maryland", "24", 8), "MA": ("Massachusetts", "25", 9),
    "MI": ("Michigan", "26", 13), "MN": ("Minnesota", "27", 8), "MS": ("Mississippi", "28", 4),
    "MO": ("Missouri", "29", 8), "MT": ("Montana", "30", 2), "NE": ("Nebraska", "31", 3),
    "NV": ("Nevada", "32", 4), "NH": ("New Hampshire", "33", 2), "NJ": ("New Jersey", "34", 12),
    "NM": ("New Mexico", "35", 3), "NY": ("New York", "36", 26), "NC": ("North Carolina", "37", 14),
    "ND": ("North Dakota", "38", 1), "OH": ("Ohio", "39", 15), "OK": ("Oklahoma", "40", 5),
    "OR": ("Oregon", "41", 6), "PA": ("Pennsylvania", "42", 17), "RI": ("Rhode Island", "44", 2),
    "SC": ("South Carolina", "45", 7), "SD": ("South Dakota", "46", 1), "TN": ("Tennessee", "47", 9),
    "TX": ("Texas", "48", 38), "UT": ("Utah", "49", 4), "VT": ("Vermont", "50", 1),
    "VA": ("Virginia", "51", 11), "WA": ("Washington", "53", 10), "WV": ("West Virginia", "54", 2),
    "WI": ("Wisconsin", "55", 8), "WY": ("Wyoming", "56", 1),
}
NAME_TO_ABBR = {v[0].lower(): k for k, v in STATES.items()}
# Longest names first so "West Virginia" wins over "Virginia".
_STATE_NAME_RE = re.compile(
    r"\b(" + "|".join(sorted((re.escape(n) for n in NAME_TO_ABBR), key=len, reverse=True)) + r")\b",
    re.I,
)

# 2026 cycle: Senate class II + specials (OH, FL); 36 governorships.
SENATE_2026 = set("AL AK AR CO DE GA ID IL IA KS KY LA ME MA MI MN MS MT NE NH NJ NM NC OK OR RI SC SD TN TX VA WV WY".split())
SENATE_SPECIAL_2026 = {"OH", "FL"}
GOVERNOR_2026 = set("AL AK AZ AR CA CO CT FL GA HI ID IL IA KS ME MD MA MI MN NE NV NH NM NY OH OK OR PA RI SC SD TN TX VT WI WY".split())

# ---------------------------------------------------------------- model


@dataclass
class Quote:
    """One source's view of one race, normalised to party probabilities."""
    source: str
    office: str               # senate | house | governor | control-senate | control-house
    state: Optional[str]      # 2-letter, None for national control
    district: Optional[str]   # "07", "AL" (at-large) for House
    special: bool
    title: str
    url: str
    probs: dict = field(default_factory=dict)   # {"D": .55, "R": .43, "I": .02}
    volume: float = 0.0
    candidates: list = field(default_factory=list)  # [{"name","party","prob"}]

    @property
    def key(self) -> str:
        if self.office.startswith("control"):
            return self.office
        k = f"{self.office}:{self.state}"
        if self.office == "house":
            k += f"-{self.district}"
        if self.special:
            k += ":special"
        return k

    def to_dict(self):
        d = asdict(self)
        d["key"] = self.key
        return d


# ---------------------------------------------------------------- classification

_EXCLUDE = re.compile(
    r"primary|nominee|nomination|runoff|margin|by how much|vote share|popular vote|turnout|"
    r"percent|%|recount|concede|debate|endorse|approval|mention|seats? will|how many|"
    r"state senate|state house|state legislat|lieutenant|lt\.? governor|speaker|majority leader|"
    r"before|by (?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)|announce|resign|"
    r"recall|impeach|tipping|flip.*\bseats\b|balance of power|trifecta|sweep",
    re.I,
)
_OTHER_YEAR = re.compile(r"\b(20(?:2[0-5]|2[7-9]|3\d))\b")
_SENATE = re.compile(r"\bsenat(?:e|orial)\b", re.I)
_GOV = re.compile(r"\bgovern(?:or|orship)\b|\bgubernatorial\b", re.I)
_HOUSE = re.compile(r"\bhouse\b|\bcongressional district\b|\bcongress(?:ional)?\b", re.I)
_CONTROL = re.compile(r"\b(control|which party will win the (?:u\.?s\.? )?(senate|house))\b", re.I)
_DIST_CODE = re.compile(r"\b([A-Z]{2})[- ]?(\d{1,2}|AL)\b")
_DIST_ORD = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b(?:\s+congressional)?(?:\s+district)?", re.I)
_DIST_WORD = re.compile(r"\bdistrict\s+(\d{1,2})\b", re.I)
_AT_LARGE = re.compile(r"\bat[- ]large\b", re.I)


def classify(text: str, *, hint: str = "") -> Optional[dict]:
    info = _classify(text, hint=hint)
    if info and info["office"] == "senate" and info["state"] in SENATE_SPECIAL_2026 and info["state"] not in SENATE_2026:
        info["special"] = True
    return info


def _classify(text: str, *, hint: str = "") -> Optional[dict]:
    """Return {office, state, district, special} for a general-election winner market, else None.

    `text` is the human title/question; `hint` may hold tickers/slugs that help (e.g. 'usse-ak').
    """
    if not text:
        return None
    full = f"{text} {hint}"
    years = {int(y) for y in _OTHER_YEAR.findall(full)}
    if years and str(ELECTION_YEAR) not in full:
        return None

    control = _CONTROL.search(text)
    if control and not _STATE_NAME_RE.search(text):
        if _SENATE.search(text):
            return {"office": "control-senate", "state": None, "district": None, "special": False}
        if _HOUSE.search(text):
            return {"office": "control-house", "state": None, "district": None, "special": False}
        return None

    if _EXCLUDE.search(text):
        return None

    special = bool(re.search(r"\bspecial\b", text, re.I))

    # Slug hints like Polymarket US "usse-ak-2026-11-03" / "usgo-ga-..." / "usho-pa07-..."
    m = re.search(r"\bus(se|go|ho)-([a-z]{2})(\d{1,2})?\b", hint.lower())
    if m and m.group(2).upper() in STATES:
        office = {"se": "senate", "go": "governor", "ho": "house"}[m.group(1)]
        st = m.group(2).upper()
        dist = _fmt_dist(m.group(3), st) if office == "house" else None
        if office != "house" or dist:
            return {"office": office, "state": st, "district": dist, "special": special}

    if _GOV.search(text):
        st = find_state(text)
        return {"office": "governor", "state": st, "district": None, "special": special} if st else None

    if _SENATE.search(text):
        st = find_state(text)
        if not st:
            return None
        return {"office": "senate", "state": st, "district": None, "special": special}

    # House: needs a state + district
    code = None
    for mm in _DIST_CODE.finditer(text):
        if mm.group(1) in STATES:
            code = mm
            break
    if code and (_HOUSE.search(text) or re.search(r"\b[A-Z]{2}-\d{1,2}\b", text)):
        st = code.group(1)
        dist = _fmt_dist(code.group(2), st)
        if dist:
            return {"office": "house", "state": st, "district": dist, "special": special}
    if _HOUSE.search(text):
        st = find_state(text)
        if st:
            if _AT_LARGE.search(text) or STATES[st][2] == 1:
                return {"office": "house", "state": st, "district": "AL", "special": special}
            mm = _DIST_ORD.search(text) or _DIST_WORD.search(text)
            if mm:
                dist = _fmt_dist(mm.group(1), st)
                if dist:
                    return {"office": "house", "state": st, "district": dist, "special": special}
    return None


def _fmt_dist(raw, st) -> Optional[str]:
    seats = STATES[st][2]
    if raw is None:
        return "AL" if seats == 1 else None
    if str(raw).upper() in ("AL", "0", "00"):
        return "AL"
    n = int(raw)
    if seats == 1:
        return "AL"
    if 1 <= n <= seats:
        return f"{n:02d}"
    return None


def find_state(text: str) -> Optional[str]:
    m = _STATE_NAME_RE.search(text)
    if m:
        return NAME_TO_ABBR[m.group(1).lower()]
    return None


# ---------------------------------------------------------------- parties

_D = re.compile(r"\bdem(?:ocrat(?:ic|s)?)?\b|\(d\)|\bdfl\b", re.I)
_R = re.compile(r"\brepublican(?:s)?\b|\bgop\b|\(r\)", re.I)  # not bare "Rep." (= Representative)
_I = re.compile(r"\bindependent\b|\(i\)|\bother\b|\bthird[- ]party\b|\blibertarian\b|\bgreen\b|\bforward\b", re.I)


def party_of(*texts: str) -> Optional[str]:
    """Detect a party label from outcome/candidate text. Returns D/R/I or None."""
    for t in texts:
        if not t:
            continue
        d, r, i = bool(_D.search(t)), bool(_R.search(t)), bool(_I.search(t))
        if d and not r:
            return "D"
        if r and not d:
            return "R"
        if i and not d and not r:
            return "I"
    return None


def normalise(probs: dict) -> dict:
    tot = sum(v for v in probs.values() if v and v > 0)
    if tot <= 0:
        return {}
    return {k: round(v / tot, 4) for k, v in probs.items() if v and v > 0}


# ---------------------------------------------------------------- http

UA = "election-markets-map/1.0 (+https://github.com)"


def get_json(url: str, params: dict | None = None, *, retries: int = 3, timeout: int = 30):
    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": UA, "Accept": "application/json"})
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def fnum(x) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None
