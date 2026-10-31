"""Synthetic GoPark booking generator (CONTRACT.md §3).

    uv run python -m tools.generator --users 300 --weeks 26 --seed 42 --out data

Writes bookings.csv, truth.json and stays.csv. Bookings after --history-end get
status "booked" so the ops demo has upcoming reservations.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import string
from datetime import date, timedelta
from pathlib import Path

import yaml

from tools.generator.personas import PERSONAS, TZ, Calendar

ROOT = Path(__file__).resolve().parents[2]
PLATE_LETTERS = "BGMNOSTZ"


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


def pick_persona(rng: random.Random) -> str:
    names = list(PERSONAS)
    return rng.choices(names, weights=[PERSONAS[n][1] for n in names])[0]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", type=int, default=300)
    ap.add_argument("--weeks", type=int, default=26)
    ap.add_argument("--history-end", type=date.fromisoformat, default=date(2026, 11, 15))
    ap.add_argument("--future-days", type=int, default=7)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", type=Path, default=ROOT / "data")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    cal = load_calendar(args.history_end, args.weeks, args.future_days)
    lots = load_lots()
    history_cutoff = cal.history_end + timedelta(days=1)

    rows, truth, stays = [], {}, []
    for i in range(1, args.users + 1):
        user_id = f"U{i:04d}"
        persona = pick_persona(rng)
        plan = PERSONAS[persona][0](rng, cal, lots)
        plate = f"{rng.choice(PLATE_LETTERS)} {rng.randint(1, 999999)}"
        truth[user_id] = {"persona": persona, "routines": plan.routines, "plate": plate}
        for b in plan.bookings:
            if b.start.date() >= history_cutoff:
                status = "booked"
            else:
                r = rng.random()
                status = ("cancelled" if r < plan.cancel_rate
                          else "no_show" if r < plan.cancel_rate + plan.no_show_rate
                          else "completed")
            rows.append({"user_id": user_id, "location_id": b.location_id, "start": b.start,
                         "end": b.end, "status": status,
                         "created_at": b.start - timedelta(hours=b.lead_hours), "plate": plate})

    rows.sort(key=lambda r: (r["start"], r["user_id"]))
    args.out.mkdir(parents=True, exist_ok=True)
    with open(args.out / "bookings.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["booking_id", "user_id", "location_id", "start_time", "end_time",
                    "status", "created_at", "plate"])
        for n, r in enumerate(rows, 1):
            bid = f"B{n:06d}"
            w.writerow([bid, r["user_id"], r["location_id"], r["start"].isoformat(),
                        r["end"].isoformat(), r["status"],
                        r["created_at"].isoformat(timespec="seconds"), r["plate"]])
            if r["status"] == "completed":
                arrive = r["start"] + timedelta(minutes=rng.gauss(-5, 8))
                overstay = rng.random() < 0.08
                leave = r["end"] + timedelta(minutes=rng.uniform(20, 60) if overstay else rng.gauss(-5, 15))
                stays.append([bid, arrive.isoformat(timespec="seconds"), leave.isoformat(timespec="seconds")])

    with open(args.out / "stays.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["booking_id", "actual_arrival", "actual_departure"])
        w.writerows(stays)

    meta = {"seed": args.seed, "users": args.users, "weeks": args.weeks,
            "history_start": cal.start.isoformat(), "history_end": cal.history_end.isoformat(),
            "future_days": args.future_days, "timezone": str(TZ), "generator_version": "v0"}
    (args.out / "truth.json").write_text(json.dumps({"generated_with": meta, "users": truth}, indent=1),
                                         encoding="utf-8")
    counts = {s: sum(r["status"] == s for r in rows) for s in ("completed", "cancelled", "no_show", "booked")}
    print(f"{len(rows)} bookings for {args.users} users -> {args.out}  {counts}")


if __name__ == "__main__":
    main()
