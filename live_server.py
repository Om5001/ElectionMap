#!/usr/bin/env python3
"""Election-night mode on your own machine: re-run the pipeline every N seconds and serve the site.

    python scripts/live_server.py                 # every 90 s, http://localhost:8000
    python scripts/live_server.py --every 60 --port 8080

GitHub's scheduled Actions can lag by 10+ minutes on busy nights. This loop has no such limit,
and you can type numbers into data/manual/results.csv as they come in.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def serve(port):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT / "site"))
    http.server.ThreadingHTTPServer(("0.0.0.0", port), handler).serve_forever()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=90)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    threading.Thread(target=serve, args=(a.port,), daemon=True).start()
    print(f"serving http://localhost:{a.port}  (refresh every {a.every}s, Ctrl-C to stop)")
    while True:
        t = time.time()
        cmd = [sys.executable, "-m", "forecast.pipeline"] + (["--offline"] if a.offline else [])
        subprocess.run(cmd, cwd=ROOT)
        time.sleep(max(5, a.every - (time.time() - t)))


if __name__ == "__main__":
    main()
