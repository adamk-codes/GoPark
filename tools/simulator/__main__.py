"""Replay driver/camera traces into the ops API and score evidence fusion.

    uv run python -m tools.simulator replay --url http://127.0.0.1:8001 --lot AUB --scenario clean --speed 60
    uv run python -m tools.simulator replay --local --speed 0 --out runs/sim_decisions.json
    uv run python -m tools.simulator replay --dry-run --booking B016522
    uv run python -m tools.simulator evaluate [--tune --write-config]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from tools.simulator.timeline import add_gate_sensors, build_timeline, load_traces, parse

ROOT = Path(__file__).resolve().parents[2]


def _data_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("GOPARK_DATA", ROOT / "data"))


def _runs_dir() -> Path:
    return Path(os.environ.get("GOPARK_RUNS", ROOT / "runs"))


def _filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("--data", help="folder with bookings.csv and traces/ (default data/)")
    p.add_argument("--lot", action="append", help="only these lots (repeatable)")
    p.add_argument("--scenario", action="append",
                   help="clean, weak_gps, underground, phone_dead, contradiction, no_show (repeatable)")
    p.add_argument("--booking", action="append", help="only these booking ids (repeatable)")
    p.add_argument("--since", help="bookings starting at/after this ISO time")
    p.add_argument("--until", help="bookings starting before this ISO time")
    p.add_argument("--limit", type=int, help="first N bookings after filtering")
    p.add_argument("--tick-min", type=float, default=2.0, help="demo minutes between /dev/tick calls (0 = none)")
    p.add_argument("--driver-confirm", type=float, default=0.0, metavar="P",
                   help="share of drivers answering an 'arrived?' prompt 1-5 min later, truthfully (default 0)")
    p.add_argument("--gate-sensors", action="store_true",
                   help="synthesize gate_sensor enter/exit events next to the plate reads (traces lack them)")


def _load(args):
    traces = load_traces(_data_dir(args.data), lots=args.lot, scenarios=args.scenario, bookings=args.booking,
                         since=parse(args.since) if args.since else None,
                         until=parse(args.until) if args.until else None, limit=args.limit)
    if args.gate_sensors:
        add_gate_sensors(traces)
    return traces


def _progress(i, n, step):
    print(f"  {i}/{n} steps, demo clock {step.at.isoformat()}", file=sys.stderr)


def _summary(records: list[dict]) -> str:
    table: dict[str, dict[str, int]] = {}
    for r in records:
        outcome = ("confirmed" if r["confirmed_at"] else "arriving" if r["arriving_at"] else "no stay")
        table.setdefault(r["scenario"], {}).setdefault(outcome, 0)
        table[r["scenario"]][outcome] += 1
    return "\n".join(f"  {s:<14} {dict(sorted(v.items()))}" for s, v in sorted(table.items()))


def cmd_replay(args) -> None:
    from tools.simulator import replay as rp
    traces = _load(args)
    if not traces:
        sys.exit("no traces match the filters")
    if args.dry_run:
        print(rp.describe(build_timeline(traces, tick_min=args.tick_min), traces))
        return
    started = time.time()
    if args.local:
        db_path = _runs_dir() / "sim" / "replay.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with rp.local_client(db_path, _data_dir(args.data)) as client:
            records = rp.replay(client, traces, speed=args.speed, tick_min=args.tick_min,
                                reset=not args.no_reset, progress=_progress, driver_confirm=args.driver_confirm)
    else:
        records = rp.replay(rp.HttpClient(args.url), traces, speed=args.speed, tick_min=args.tick_min,
                            reset=not args.no_reset, progress=_progress, driver_confirm=args.driver_confirm)
    out = Path(args.out) if args.out else _runs_dir() / "sim_decisions.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(records, indent=1), encoding="utf-8")
    print(f"{len(records)} bookings replayed in {time.time() - started:.0f}s -> {out}")
    print(_summary(records))


def cmd_evaluate(args) -> None:
    from tools.simulator import evaluate as ev
    from tools.simulator import replay as rp
    data_dir = _data_dir(args.data)
    runs = _runs_dir()
    db_path = runs / "sim" / "fusion_eval.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    traces = _load(args)
    by_id = {t.booking_id: t for t in traces}
    with rp.local_client(db_path, data_dir) as client:
        from api.ops import fusion
        from api.rhythm_api.store import latest_priors
        priors = latest_priors()
        before = copy.deepcopy(fusion.CONFIG)

        def run(cfg: dict, label: str) -> list[dict]:
            started = time.time()
            if args.offline:
                records = [ev.emulate(t, cfg, priors, args.driver_confirm) for t in traces]
            else:
                fusion.use_config(cfg)
                records = rp.replay(client, traces, tick_min=args.tick_min, progress=_progress,
                                    driver_confirm=args.driver_confirm)
            for r in records:
                r["false_evidence_confirm"] = ev.false_evidence_confirm(by_id[r["booking_id"]], r, cfg, priors)
            print(f"{label}: {len(records)} bookings in {time.time() - started:.0f}s", file=sys.stderr)
            return records

        before_records = run(before, "before")
        result = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "mode": "offline" if args.offline else "replay", "traces": str(data_dir / "traces"),
                  "gate_sensors": bool(args.gate_sensors), "driver_confirm": args.driver_confirm,
                  "bookings": len(traces), "priors": len(priors),
                  "split": {"method": "sha1(booking_id)[0] % 2 (even = tune)",
                            "tune": sum(ev.split_of(t.booking_id) == "tune" for t in traces),
                            "test": sum(ev.split_of(t.booking_id) == "test" for t in traces)},
                  "objective": ev.OBJECTIVE, "config_before": before, "config_after": None,
                  "tuning": None, "before": ev.report(before_records), "after": None}
        if args.tune:
            after, steps = ev.tune(traces, before, priors, log=lambda m: print(m, file=sys.stderr))
            changes = ev.changed_paths(before, after)
            after_records = run(after, "after")
            result.update(config_after=after, after=ev.report(after_records),
                          tuning={"changed": changes, "steps": steps,
                                  "objective_tune_before": ev.objective(result["before"]["tune"]),
                                  "objective_tune_after": ev.objective(ev.report(after_records)["tune"]),
                                  "objective_test_before": ev.objective(result["before"]["test"]),
                                  "objective_test_after": ev.objective(ev.report(after_records)["test"])})
            if args.write_config and changes:
                ev.write_config(fusion.CONFIG_PATH, changes)
                print(f"wrote {len(changes)} tuned values to {fusion.CONFIG_PATH}", file=sys.stderr)
            fusion.use_config(before)
    out = Path(args.out) if args.out else runs / "fusion_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(table(result))
    print(f"-> {out}")


def table(result: dict) -> str:
    """Markdown table of the headline numbers per scenario and lot kind (after tuning if present)."""
    def row(name, m):
        d = m["departure"]
        f = lambda v: "-" if v is None else f"{v:.2f}" if isinstance(v, float) else str(v)  # noqa: E731
        return (f"| {name} | {m['bookings']} | {f(m['auto_confirm_rate'])} | {f(m['driver_confirm_rate'])} | "
                f"{f(m['ask_driver_rate'])} | "
                f"{f(m['missed_arrival_rate'])} | {f(m['false_confirm_rate'])} | {f(m['median_time_to_confirm_min'])} | "
                f"{f(d['auto_checkout_rate'])} | {f(d['inferred_checkout_rate'])} | {f(d['median_out_at_error_min'])} | "
                f"{m['low_confidence_alerts']} | {m['anomaly_alerts']} |")
    lines = []
    for label in ("before", "after"):
        rep = result.get(label)
        if not rep:
            continue
        lines += [f"\n{label} ({result['mode']})",
                  "| group | n | auto | driver | ask | missed | false confirm | median ttc min | auto out | inferred out | "
                  "out err min | low conf | anomaly |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        lines += [row("all", rep["all"]), row("test split", rep["test"])]
        lines += [row(k, m) for k, m in rep["by_scenario"].items()]
        lines += [row(f"kind: {k}", m) for k, m in rep["by_lot_kind"].items()]
    return "\n".join(lines)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m tools.simulator", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("replay", help="replay traces into POST /signals on the demo clock")
    _filters(p)
    p.add_argument("--url", default="http://127.0.0.1:8001", help="API base URL (default %(default)s)")
    p.add_argument("--local", action="store_true", help="run the API in-process on runs/sim/replay.sqlite")
    p.add_argument("--speed", type=float, default=60.0, help="demo seconds per real second (0 = no waiting)")
    p.add_argument("--no-reset", action="store_true", help="keep the DB (default: /dev/reset?prepark=false)")
    p.add_argument("--dry-run", action="store_true", help="print the schedule, send nothing")
    p.add_argument("--out", help="decisions JSON (default runs/sim_decisions.json)")
    p.set_defaults(func=cmd_replay)
    p = sub.add_parser("evaluate", help="fusion accuracy vs the traces' truth -> runs/fusion_metrics.json")
    _filters(p)
    p.add_argument("--tune", action="store_true", help="tune config/fusion.yaml on the tune split, report before/after")
    p.add_argument("--write-config", action="store_true", help="with --tune: write tuned values to config/fusion.yaml")
    p.add_argument("--offline", action="store_true", help="score with the pure fusion core only (fast, no API)")
    p.add_argument("--out", help="metrics JSON (default runs/fusion_metrics.json)")
    p.set_defaults(func=cmd_evaluate)
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
