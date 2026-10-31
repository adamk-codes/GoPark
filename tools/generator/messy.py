"""--messy: inject the dirt real booking exports have, so the engine's cleaning is tested.

Only history rows are touched (never the live "booked" week the ops demo loads).
Each corrupted row gets one kind of damage; duplicates are copied after that.
"""
from __future__ import annotations

import random

STATUS_NOISE = {"completed": ["Completed", "COMPLETED", " completed", "completed "],
                "cancelled": ["Cancelled", " cancelled ", "CANCELLED", "canceled"],
                "no_show": ["No_Show", "NO_SHOW", " no_show"]}
RATES = {"status_noise": 0.03, "missing_end_time": 0.005, "end_before_start": 0.003,
         "no_offset": 0.005, "duplicate": 0.01}
COLS = {"booking_id": 0, "start_time": 3, "end_time": 4, "status": 5}


def _strip_offset(ts: str) -> str:
    return ts[:19] if len(ts) > 19 else ts


def inject(rows: list[list[str]], seed: int) -> tuple[list[list[str]], dict]:
    """rows are bookings.csv rows (no header). Returns (new rows, {kind: {count, booking_ids}})."""
    rng = random.Random(seed * 104729 + 3)
    log = {k: [] for k in RATES}
    out: list[list[str]] = []
    for row in rows:
        row = list(row)
        bid, status = row[COLS["booking_id"]], row[COLS["status"]]
        if status != "booked":
            r = rng.random()
            edge = 0.0
            for kind in ("status_noise", "missing_end_time", "end_before_start", "no_offset"):
                edge += RATES[kind]
                if r < edge:
                    if kind == "status_noise":
                        row[COLS["status"]] = rng.choice(STATUS_NOISE[status])
                    elif kind == "missing_end_time":
                        row[COLS["end_time"]] = ""
                    elif kind == "end_before_start":
                        row[COLS["start_time"]], row[COLS["end_time"]] = row[COLS["end_time"]], row[COLS["start_time"]]
                    else:
                        row[COLS["start_time"]] = _strip_offset(row[COLS["start_time"]])
                        row[COLS["end_time"]] = _strip_offset(row[COLS["end_time"]])
                    log[kind].append(bid)
                    break
        out.append(row)
        if status != "booked" and rng.random() < RATES["duplicate"]:
            out.append(list(row))
            log["duplicate"].append(bid)
    return out, {k: {"count": len(v), "booking_ids": v} for k, v in log.items()}
