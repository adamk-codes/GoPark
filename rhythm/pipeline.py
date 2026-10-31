"""Run the pattern engine end to end and write a run folder.

`analyse` chains the stages of ARCHITECTURE_PLAN.md §4.2 (clean → active weeks → discover →
habit → predict); `pattern_rows` turns the result into `pattern_row`s (one primary row per
user + one per extra routine) and `priors_and_forecast` into the C1 feeds. P2 adds drift,
calibration, reminders and richer explanations on top without changing the output shapes.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pandas as pd

from rhythm import baselines
from rhythm.baselines import TZ
from rhythm.io import load_bookings, load_calendar, load_config, new_run_id, runs_dir, write_run
from rhythm.stages import activity, clean, discover, habit
from rhythm.stages.common import DAY_FULL, DAY_NAMES, hhmm, switch
from rhythm.stages.predict import first_booking, next_per_user, slots

MODEL_VERSION = "rhythm-1.0-p1"
NO_REMINDER = {"recommend": False, "tier": "none", "at": None}


def default_cutoff(df: pd.DataFrame) -> date:
    """Last day that has finished (non-upcoming) bookings."""
    past = df[df["status"] != "booked"]
    return past["start_time"].max().date()


def history(df: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Rows usable for training at `cutoff`: on or before it and not still upcoming."""
    return df[(df["start_time"].dt.date <= cutoff) & (df["status"] != "booked")]


def analyse(df: pd.DataFrame, cutoff: date, cfg: dict, calendar: dict | None = None) -> dict:
    """All stage outputs at `cutoff` (pure: same input → same result)."""
    c = clean(history(df, cutoff), cfg, merge_rebook=switch(cfg, "merge_rebook"))
    exposure, users = activity(c, cutoff, cfg, calendar, enabled=switch(cfg, "activity"))
    c = discover(c, cfg, enabled=switch(cfg, "discover"))
    day_stats, routines = habit(c, exposure, cutoff, cfg, use_prior=switch(cfg, "prior"),
                                use_decay=switch(cfg, "decay"), use_chance=switch(cfg, "chance_test"))
    sl = slots(day_stats, users, cutoff, calendar, cfg["predict"]["horizon_days"])
    return {"cutoff": cutoff, "clean": c, "exposure": exposure, "users": users,
            "day_stats": day_stats, "routines": routines, "slots": sl}


def _at(day: pd.Timestamp, minute: float) -> datetime:
    m = int(round(minute)) % 1440
    return datetime.combine(day.date(), time(m // 60, m % 60), TZ)


def _slot_label(days: tuple[int, ...]) -> str:
    return f"{DAY_FULL[days[0]]}s" if len(days) == 1 else f"{baselines.day_label([DAY_NAMES[d] for d in days])} slots"


def _routine_reason(r, u) -> str:
    lo, hi = hhmm(r.center - r.spread), hhmm(r.center + r.spread)
    text = (f"Booked {r.location_id} on {r.recent_hits} of the last {r.recent_slots} {_slot_label(r.days)}, "
            f"usually {lo}–{hi}.") if r.recent_slots else \
        f"Booked {r.location_id} {r.support} times on {_slot_label(r.days)}, usually {lo}–{hi}."
    if u.inactive:
        text += f" Inactive {u.days_since_last} days (usual gap {u.median_gap_days:g}); confidence lowered in P2."
    return text


def routine_ids(routines: pd.DataFrame) -> pd.Series:
    """`<user>-P<k>` per routine, strongest first (k = 1 is the primary row)."""
    rank = routines.groupby("user_id", sort=False).cumcount() + 1
    return pd.Series((routines["user_id"] + "-P" + rank.astype(str)).to_numpy(),
                     index=pd.MultiIndex.from_frame(routines[["user_id", "location_id", "cluster"]]))


def pattern_rows(an: dict, cfg: dict, run_id: str) -> list[dict]:
    cutoff, c, users = an["cutoff"], an["clean"], an["users"].set_index("user_id")
    routines, sl = an["routines"], an["slots"]
    habit_slots = sl[sl["is_habit"]]
    own_next = first_booking(habit_slots, by=["user_id", "location_id", "cluster"]) \
        .set_index(["user_id", "location_id", "cluster"])
    guesses = next_per_user(sl[~sl["user_id"].isin(routines["user_id"])]).set_index("user_id")
    done = {uid: g for uid, g in c[c["status"] != "cancelled"].groupby("user_id", sort=False)}
    empty = c.iloc[:0]
    per_user = {uid: g for uid, g in routines.groupby("user_id", sort=False)}
    cancel = c.groupby("user_id")["cancel_rate"].first()
    rows = []
    for uid, u in users.iterrows():
        mine = done.get(uid, empty)
        base = {"user_id": uid, "active_weeks": int(u.active_weeks),
                "inactivity_detected": bool(u.inactive), "cancel_rate": round(float(cancel.get(uid, 0.0)), 3),
                "change_date": None, "run_id": run_id, "model_version": MODEL_VERSION}
        rs = per_user.get(uid)
        if rs is None:
            rows.append({**base, **_no_routine_row(uid, mine, u, guesses, cutoff, cfg)})
            continue
        for k, r in enumerate(rs.itertuples(index=False), start=1):
            nxt = own_next.loc[(uid, r.location_id, r.cluster)] if (uid, r.location_id, r.cluster) in own_next.index else None
            at = _at(nxt["day"], nxt["minute"]) if nxt is not None else None
            conf = round(float(nxt["p"]), 3) if nxt is not None else round(float(r.mean_p), 3)
            days = [DAY_NAMES[d] for d in r.days]
            rows.append({**base, "pattern_id": f"{uid}-P{k}", "is_primary": k == 1,
                         "pattern_type": "recurring", "state": "active", "days": days,
                         "label": baselines.day_label(days),
                         "time_window": {"center": hhmm(r.center), "spread_min": round(float(r.spread), 1)},
                         "location_id": r.location_id, "support": int(r.support),
                         "recurrence_prob": round(float(r.mean_p), 3),
                         "next_booking": {"at": at.isoformat(), "location_id": r.location_id} if at else None,
                         "confidence": conf,
                         "reminder": baselines._reminder(conf, at, r.center, cfg) if at else NO_REMINDER,
                         "last_booking_date": r.last_day.date().isoformat(),
                         "frequency_per_week": round(r.support / max(1, u.active_weeks), 2),
                         "reason": _routine_reason(r, u)})
    return rows


def _no_routine_row(uid: str, mine: pd.DataFrame, u, guesses: pd.DataFrame, cutoff: date, cfg: dict) -> dict:
    weeks = max(1, int(u.active_weeks))
    last = mine["day"].max() if len(mine) else None
    common = {"pattern_id": f"{uid}-P1", "is_primary": True, "state": None, "days": [], "label": "Occasional",
              "time_window": None, "support": int(len(mine)), "recurrence_prob": None,
              "location_id": mine["location_id"].mode().iat[0] if len(mine) else None,
              "reminder": NO_REMINDER, "frequency_per_week": round(len(mine) / weeks, 2),
              "last_booking_date": last.date().isoformat() if last is not None else None}
    if len(mine) < cfg["habit"]["min_hits"]:
        return {**common, "pattern_type": "insufficient_history", "next_booking": None, "confidence": 0.1,
                "reason": f"Only {len(mine)} bookings so far; not enough history for a routine."}
    reason = f"No steady routine: {len(mine)} bookings over {weeks} active weeks."
    if uid in guesses.index:
        g = guesses.loc[uid]
        at = _at(g["day"], g["minute"])
        reason += f" Best guess {DAY_NAMES[at.weekday()]} ~{at:%H:%M} at {g['location_id']} ({g['p']:.0%})."
        return {**common, "pattern_type": "occasional", "next_booking": {"at": at.isoformat(),
                "location_id": g["location_id"]}, "confidence": round(float(g["p"]), 3), "reason": reason}
    return {**common, "pattern_type": "occasional", "next_booking": None, "confidence": 0.1, "reason": reason}


def priors_and_forecast(an: dict, days_ahead: int = 7) -> tuple[list[dict], list[dict]]:
    """Arrival priors (→ C1 fusion) and lot forecast from the habit slots of the next `days_ahead` days.

    Same shapes as `baselines.priors_and_forecast`, but each day uses its own probability and
    time, and holidays / sat-out term breaks get no prior.
    """
    cutoff = an["cutoff"]
    pid = routine_ids(an["routines"])
    rt = an["routines"].set_index(["user_id", "location_id", "cluster"])
    end = pd.Timestamp(cutoff + timedelta(days=days_ahead))
    sl = an["slots"]
    sl = sl[sl["is_habit"] & (sl["day"] <= end)]
    priors, forecast = [], {}
    for s in sl.itertuples(index=False):
        r = rt.loc[(s.user_id, s.location_id, s.cluster)]
        half = max(30.0, 2 * float(r["spread"]))
        center = _at(s.day, s.minute)
        priors.append({"user_id": s.user_id, "lot_id": s.location_id, "date": center.date().isoformat(),
                       "window_start": (center - timedelta(minutes=half)).isoformat(),
                       "window_end": (center + timedelta(minutes=half)).isoformat(),
                       "p": round(float(s.p), 3), "pattern_id": pid[(s.user_id, s.location_id, s.cluster)]})
        fk = (s.location_id, center.date().isoformat(), center.hour)
        forecast[fk] = forecast.get(fk, 0.0) + float(s.p)
    rows_out = [{"lot_id": lot, "date": d, "hour": h, "expected_arrivals": round(v, 2)}
                for (lot, d, h), v in sorted(forecast.items())]
    return priors, rows_out


def run(input_path: str | Path, cutoff: date | None = None, out_dir: Path | None = None) -> dict:
    cfg = load_config()
    df = load_bookings(input_path)
    cutoff = cutoff or default_cutoff(df)
    run_id = new_run_id()
    an = analyse(df, cutoff, cfg, load_calendar())
    patterns = pattern_rows(an, cfg, run_id)
    priors, forecast = priors_and_forecast(an)
    info = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cutoff": cutoff.isoformat(), "input": str(input_path),
            "model_version": MODEL_VERSION, "params": cfg,
            "counts": {"users": len({p["user_id"] for p in patterns}),
                       "rows": len(patterns),
                       "recurring": sum(p["pattern_type"] == "recurring" for p in patterns),
                       "priors": len(priors)}}
    write_run(out_dir or runs_dir(), info, patterns, priors, forecast)
    return info
