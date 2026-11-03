"""Prediction markets: Kalshi, Polymarket, PredictIt, Manifold (see pm/sources). Consolidated per race."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pm.sources import ALL
from pm.consolidate import consolidate, WEIGHTS


def load(online=True, fixtures: Path | None = None) -> tuple[dict, list, dict]:
    quotes, status = [], {}
    for name, mod in ALL.items():
        try:
            if fixtures:
                raw = json.loads((fixtures / f"{name}.json").read_text())
            elif online:
                raw = mod.fetch_raw()
            else:
                status[name] = {"name": mod.NAME, "ok": False, "error": "offline", "races": 0}
                continue
            q = mod.parse(raw)
            quotes.extend(q)
            status[name] = {"name": mod.NAME, "ok": True, "races": len(q), "weight": WEIGHTS[name]}
        except Exception as e:  # noqa: BLE001
            status[name] = {"name": mod.NAME, "ok": False, "error": str(e)[:200], "races": 0, "weight": WEIGHTS[name]}
            print(f"[markets] {name}: {e}", file=sys.stderr)
    merged = consolidate(quotes)
    races = {r["key"]: r for r in merged if not r["office"].startswith("control")}
    control = [r for r in merged if r["office"].startswith("control")]
    return races, control, status
