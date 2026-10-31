"""Synthetic GoPark booking generator (CONTRACT.md §3).

    uv run python -m tools.generator --users 300 --weeks 26 --seed 42 --out data

Writes bookings.csv, truth.json, stays.csv and traces/ (one signal trace per
live-week booking). Bookings after --history-end get status "booked" so the ops
demo has upcoming reservations. See tools/generator/README.md for the options.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml

from tools.generator import messy
from tools.generator.personas import PERSONAS, TZ, Calendar
from tools.generator.traces import write_traces

ROOT = Path(__file__).resolve().parents[2]
PLATE_LETTERS = "BGMNOSTZ"
BUCKET_S = 15 * 60
SHIFTS_MIN = (15, -15, 30, -30)


def load_calendar(history_end: date, weeks: int, future_days: int) -> Calendar:
    cfg = yaml.safe_load((ROOT / "config" / "holidays.yaml").read_text(encoding="utf-8"))
    start = history_end - timedelta(weeks=weeks) + timedelta(days=1)
    start -= timedelta(days=start.weekday())  # align to Monday
    return Calendar(
        start=start,
        history_end=history_end,
        end=history_end + timedelta(days=future_days),
        holidays={date.fromisoformat(str(d)) for d in cfg.get("public_holidays", [])},
        term_breaks=[(date.fromisoformat(str(b["start"])), date.fromisoformat(str(b["end"])))
                     for b in cfg.get("term_breaks", [])],
    )


def load_lots() -> dict:
    cfg = yaml.safe_load((ROOT / "config" / "lots.yaml").read_text(encoding="utf-8"))
    return {lot["id"]: lot for lot in cfg["lots"]}


def parse_mix(spec: str | None, registry: dict) -> dict[str, float]:
    """'fader=2,lapsed,occasional=0.5' -> weights. None -> the registry's default shares."""
    if not spec:
        return {name: share for name, (_, share) in registry.items()}
    mix = {}
    for part in spec.split(","):
        name, _, weight = part.strip().partition("=")
        if name not in registry:
            raise SystemExit(f"Unknown persona {name!r}. Known: {', '.join(registry)}")
        mix[name] = float(weight) if weight else 1.0
    return mix


def enforce_capacity(rows: list[dict], lots: dict) -> dict:
    """First come, first served by created_at: a booking that doesn't fit moves ±15/30 min or is dropped.

    A booking fits if the lot has a free space in every 15-minute slot it covers, so
    no booking ever starts in a full lot. Cancelled bookings don't hold a space.
    Mutates rows in place and returns counts.
    """
    occupied: dict[tuple[str, int], int] = defaultdict(int)
    shifted = dropped = 0
    keep = []

    def slots(start, end) -> range:
        return range(int(start.timestamp()) // BUCKET_S, -(-int(end.timestamp()) // BUCKET_S))

    for r in sorted(rows, key=lambda r: (r["created_at"], r["user_id"], r["start"])):
        if r["status"] == "cancelled":
            keep.append(r)
            continue
        lot, cap = r["location_id"], lots[r["location_id"]]["capacity"]
        for delta in (0, *SHIFTS_MIN):
            shift = timedelta(minutes=delta)
            if all(occupied[(lot, b)] < cap for b in slots(r["start"] + shift, r["end"] + shift)):
                break
        else:
            dropped += 1
            continue
        if delta:
            shifted += 1
            r["start"] += shift
            r["end"] += shift
            r["created_at"] = min(r["created_at"], r["start"] - timedelta(minutes=5))
        for b in slots(r["start"], r["end"]):
            occupied[(lot, b)] += 1
        keep.append(r)
    rows[:] = keep
    return {"shifted": shifted, "dropped": dropped}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", type=int, default=300)
    ap.add_argument("--weeks", type=int, default=26)
    ap.add_argument("--history-end", type=date.fromisoformat, default=date(2026, 11, 15))
    ap.add_argument("--future-days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=ROOT / "data")
    ap.add_argument("--mix", help="persona weights, e.g. 'fader=2,lapsed,occasional=0.5' (default: built-in shares)")
    ap.add_argument("--messy", action="store_true", help="inject duplicates, status noise and broken timestamps")
    ap.add_argument("--include-hidden", action="store_true", help="add the hidden personas (hidden_personas.py)")
    ap.add_argument("--no-traces", action="store_true", help="skip data/traces/")
    ap.add_argument("--no-cameras", action="store_true", help="traces without camera_plate/camera_spot signals")
    args = ap.parse_args()

    registry = dict(PERSONAS)
    if args.include_hidden:
        try:
            from tools.generator.hidden_personas import HIDDEN_PERSONAS
        except ImportError:
            raise SystemExit("--include-hidden needs tools/generator/hidden_personas.py (kept out of git)")
        registry.update(HIDDEN_PERSONAS)
    mix = parse_mix(args.mix, registry)
    names = [n for n in mix if mix[n] > 0]

    rng = random.Random(args.seed)
    cal = load_calendar(args.history_end, args.weeks, args.future_days)
    lots = load_lots()
    history_cutoff = cal.history_end + timedelta(days=1)

    rows, truth, stays = [], {}, []
    for i in range(1, args.users + 1):
        user_id = f"U{i:04d}"
        persona = rng.choices(names, weights=[mix[n] for n in names])[0]
        plan = registry[persona][0](rng, cal, lots)
        plate = f"{rng.choice(PLATE_LETTERS)} {rng.randint(1, 999999)}"
        truth[user_id] = {"persona": persona, "routines": plan.routines, "plate": plate, **plan.extra}
        for b in plan.bookings:
            if b.start.date() >= history_cutoff:
                if b.status == "cancelled":
                    continue
                status = "booked"
            elif b.status:
                status = b.status
            else:
                r = rng.random()
                status = ("cancelled" if r < plan.cancel_rate
                          else "no_show" if r < plan.cancel_rate + plan.no_show_rate
                          else "completed")
            rows.append({"user_id": user_id, "location_id": b.location_id, "start": b.start,
                         "end": b.end, "status": status,
                         "created_at": b.start - timedelta(hours=b.lead_hours), "plate": plate})

    capacity = enforce_capacity(rows, lots)
    rows.sort(key=lambda r: (r["start"], r["user_id"]))
    out_rows, live = [], []
    for n, r in enumerate(rows, 1):
        bid = f"B{n:06d}"
        out_rows.append([bid, r["user_id"], r["location_id"], r["start"].isoformat(), r["end"].isoformat(),
                         r["status"], r["created_at"].isoformat(timespec="seconds"), r["plate"]])
        if r["status"] == "completed":
            arrive = r["start"] + timedelta(minutes=rng.gauss(-5, 8))
            overstay = rng.random() < 0.08
            leave = r["end"] + timedelta(minutes=rng.uniform(20, 60) if overstay else rng.gauss(-5, 15))
            stays.append([bid, arrive.isoformat(timespec="seconds"), leave.isoformat(timespec="seconds")])
        elif r["status"] == "booked":
            live.append({"booking_id": bid, **r})

    messy_log = None
    if args.messy:
        out_rows, messy_log = messy.inject(out_rows, args.seed)

    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "bookings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["booking_id", "user_id", "location_id", "start_time", "end_time",
                    "status", "created_at", "plate"])
        w.writerows(out_rows)

    with open(args.out / "stays.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["booking_id", "actual_arrival", "actual_departure"])
        w.writerows(stays)

    scenarios = None
    if not args.no_traces:
        scenarios = write_traces(args.out, lots, live, args.seed, cameras=not args.no_cameras)

    persona_counts: dict[str, int] = defaultdict(int)
    for u in truth.values():
        persona_counts[u["persona"]] += 1
    meta = {"seed": args.seed, "users": args.users, "weeks": args.weeks,
            "history_start": cal.start.isoformat(), "history_end": cal.history_end.isoformat(),
            "future_days": args.future_days, "timezone": str(TZ), "generator_version": "v1",
            "persona_mix": mix, "personas": dict(sorted(persona_counts.items())),
            "include_hidden": args.include_hidden, "capacity": capacity,
            "messy": {k: v["count"] for k, v in messy_log.items()} if messy_log else None,
            "trace_scenarios": scenarios}
    (args.out / "truth.json").write_text(json.dumps({"generated_with": meta, "users": truth}, indent=1),
                                         encoding="utf-8")
    if messy_log:
        (args.out / "messy_log.json").write_text(json.dumps(messy_log, indent=1), encoding="utf-8")
    elif (args.out / "messy_log.json").exists():
        (args.out / "messy_log.json").unlink()

    counts = {s: sum(r["status"] == s for r in rows) for s in ("completed", "cancelled", "no_show", "booked")}
    print(f"{len(out_rows)} bookings for {args.users} users -> {args.out}  {counts}")
    print(f"capacity: {capacity['shifted']} shifted, {capacity['dropped']} dropped")
    if messy_log:
        print("messy: " + ", ".join(f"{k} {v['count']}" for k, v in messy_log.items()))
    if scenarios:
        print(f"traces: {sum(scenarios.values())} live bookings  {scenarios}")


if __name__ == "__main__":
    main()
