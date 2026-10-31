"""Rhythm CLI.

    uv run python -m rhythm run --input data/bookings.csv [--cutoff 2026-11-15]
    uv run python -m rhythm evaluate ...   (P3)
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from rhythm.io import ROOT
from rhythm.pipeline import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="rhythm")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="detect patterns and write runs/<run_id>/")
    r.add_argument("--input", type=Path, default=ROOT / "data" / "bookings.csv")
    r.add_argument("--cutoff", type=date.fromisoformat, default=None)
    r.add_argument("--out", type=Path, default=None)
    sub.add_parser("evaluate", help="backtest against truth (not built yet)")
    args = ap.parse_args()

    if args.cmd == "run":
        info = run(args.input, args.cutoff, args.out)
        print(json.dumps({k: info[k] for k in ("run_id", "cutoff", "model_version", "counts")}, indent=1))
    else:
        raise SystemExit("evaluate is not built yet (Rhythm owner, phase P2)")


if __name__ == "__main__":
    main()
