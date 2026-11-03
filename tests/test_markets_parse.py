import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pm.common import classify, party_of  # noqa: E402

CASES = [
    ("Texas Senate Election Winner", "", ("senate", "TX", None, False)),
    ("Who will win the Senate race in West Virginia?", "", ("senate", "WV", None, False)),
    ("Virginia Senate winner?", "", ("senate", "VA", None, False)),
    ("Ohio Senate special election winner", "", ("senate", "OH", None, True)),
    ("Florida Senate Election Winner", "", ("senate", "FL", None, True)),
    ("Which party will win the 2026 Georgia gubernatorial race?", "", ("governor", "GA", None, False)),
    ("Arizona Governor Election Winner", "", ("governor", "AZ", None, False)),
    ("PA-07 House race winner?", "", ("house", "PA", "07", False)),
    ("Who will win NY-17?", "", ("house", "NY", "17", False)),
    ("Iowa 1st District House Election Winner", "", ("house", "IA", "01", False)),
    ("Alaska House race winner 2026", "", ("house", "AK", "AL", False)),
    ("Which party will win the U.S. Senate?", "", ("control-senate", None, None, False)),
    ("Which party will control the House after the 2026 midterms?", "", ("control-house", None, None, False)),
    ("Senate Election Winner", "usse-me-2026-11-03", ("senate", "ME", None, False)),
]
REJECT = [
    "Georgia Republican Senate primary winner?",
    "Texas Senate race margin of victory",
    "Pennsylvania Senate winner 2028?",
    "Michigan State Senate control",
    "Will Dems win 51+ Senate seats?",
    "Fed rate in December?",
    "PA-25 House race",  # PA has 17 districts
    "Lieutenant Governor of Texas winner",
]


@pytest.mark.parametrize("text,hint,exp", CASES)
def test_classify(text, hint, exp):
    r = classify(text, hint=hint)
    assert r is not None, text
    assert (r["office"], r["state"], r["district"], r["special"]) == exp


@pytest.mark.parametrize("text", REJECT)
def test_reject(text):
    assert classify(text) is None


@pytest.mark.parametrize("label,exp", [
    ("Democratic party", "D"), ("Republican", "R"), ("Jane Doe (D)", "D"), ("GOP", "R"),
    ("Independent", "I"), ("Rep. Colin Allred", None), ("Dan Osborn", None),
])
def test_party(label, exp):
    assert party_of(label) == exp


