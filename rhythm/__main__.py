"""Rhythm CLI.

    uv run python -m rhythm run --input data/bookings.csv [--cutoff 2026-11-15]
    uv run python -m rhythm evaluate [--input data/bookings.csv] [--truth data/truth.json] [--run-id latest] [--no-ablation]
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
    e = sub.add_parser("evaluate", help="backtest vs baselines + pattern recovery → runs/<run_id>/metrics.json")
    e.add_argument("--input", type=Path, default=ROOT / "data" / "bookings.csv")
    e.add_argument("--truth", type=Path, default=None, help="default: truth.json next to the input")
    e.add_argument("--run-id", default=None, help="write into this run (or 'latest'); default: a fresh run")
    e.add_argument("--no-ablation", action="store_true", help="skip the ablation (5 extra engine variants)")
    args = ap.parse_args()

    if args.cmd == "run":
        info = run(args.input, args.cutoff, args.out)
        print(json.dumps({k: info[k] for k in ("run_id", "cutoff", "model_version", "counts")}, indent=1))
    else:
        from rhythm.evaluate import evaluate, headline
        m = evaluate(args.input, args.truth, args.run_id, with_ablation=not args.no_ablation)
        print(f"run {m['run_id']} · test cutoffs {', '.join(m['cutoffs']['test'])}")
        print(headline(m))
        if "recovery" in m:
            print("recovery (engine vs B2, day-set Jaccard / lot accuracy):")
            for p, s in m["recovery"]["engine"]["by_persona"].items():
                b = m["recovery"]["B2"]["by_persona"].get(p, {})
                print(f"  {p}: {s.get('day_jaccard')} / {s.get('lot_accuracy')}  vs  {b.get('day_jaccard')} / "
                      f"{b.get('lot_accuracy')}  false-habit {s.get('false_habit_rate')} vs {b.get('false_habit_rate')}")


if __name__ == "__main__":
    main()
