"""Run the pattern engine end to end and write a run folder.

`analyse` chains the stages of ARCHITECTURE_PLAN.md §4.2 (clean → active weeks → discover →
drift change points → habit → drift states → predict, + calibrate when a fitted map is given);
`fit_calibration` fits the isotonic map and the reminder threshold τ on the weeks before a
cutoff; `pattern_rows` turns an analysis into `pattern_row`s (one primary row per user + one per
extra routine, with tiers and explanations) and `priors_and_forecast` into the C1 feeds.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from rhythm import baselines
from rhythm.baselines import TZ
from rhythm.io import load_bookings, load_calendar, load_config, new_run_id, runs_dir, write_run
from rhythm.stages import activity, calibrate, clean, discover, drift, explain, habit, remind
from rhythm.stages.clean import normalise_status
from rhythm.stages.common import DAY_NAMES, hhmm, switch
from rhythm.stages.predict import activity_factor, first_booking, gap_model, next_per_user, slots

MODEL_VERSION = "rhythm-2.0-p2"
NO_REMINDER = {"recommend": False, "tier": "none", "at": None}


def default_cutoff(df: pd.DataFrame) -> date:
    """Last day that has finished (non-upcoming) bookings."""
    past = df[df["status"] != "booked"]
    return past["start_time"].max().date()


def history(df: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Rows usable for training at `cutoff`: on or before it and not still upcoming."""
    return df[(df["start_time"].dt.date <= cutoff) & (df["status"] != "booked")]


def kept_bookings(df: pd.DataFrame, cutoff: date, window_days: int) -> pd.DataFrame:
    """Bookings the user kept (completed or no-show) in (cutoff, cutoff + window] → [user_id, day, location_id, minute]."""
    d = df["start_time"].dt.date
    status = normalise_status(df["status"])
    sel = df[(d > cutoff) & (d <= cutoff + timedelta(days=window_days)) & status.isin(["completed", "no_show"])]
    sel = sel.sort_values("start_time")
    local = sel["start_time"].dt.tz_localize(None)
    return pd.DataFrame({"user_id": sel["user_id"].to_numpy(), "day": local.dt.normalize().to_numpy(),
                         "location_id": sel["location_id"].to_numpy(),
                         "minute": (local.dt.hour * 60 + local.dt.minute).to_numpy()})


def analyse(df: pd.DataFrame, cutoff: date, cfg: dict, calendar: dict | None = None,
            calibration: dict | None = None) -> dict:
    """All stage outputs at `cutoff` (pure: same input → same result). Slot `p` is calibrated
    when a fitted `calibration` map is given, else it equals the raw `score`."""
    c = clean(history(df, cutoff), cfg, merge_rebook=switch(cfg, "merge_rebook"))
    exposure, users = activity(c, cutoff, cfg, calendar, enabled=switch(cfg, "activity"))
    c = discover(c, cfg, enabled=switch(cfg, "discover"))
    day_stats, routines = habit(c, exposure, cutoff, cfg, use_prior=switch(cfg, "prior"),
                                use_decay=switch(cfg, "decay"), use_chance=switch(cfg, "chance_test"),
                                use_drift=switch(cfg, "drift"))
    routines = drift.label_states(routines, cutoff, cfg, enabled=switch(cfg, "drift"))
    factor = activity_factor(users, cfg, enabled=switch(cfg, "activity"))
    sl = slots(day_stats, users, cutoff, calendar, cfg["predict"]["horizon_days"], factor)
    if calibration and switch(cfg, "calibrate"):
        sl["p"] = calibrate.apply(sl["score"], calibration)
    return {"cutoff": cutoff, "clean": c, "exposure": exposure, "users": users, "day_stats": day_stats,
            "routines": routines, "slots": sl, "factor": factor, "gaps": gap_model(c, exposure, cutoff, cfg),
            "calibration": calibration}


# ── calibration + reminder threshold, fitted on the weeks before a cutoff ─────

def week_slots(an: dict, window_days: int) -> pd.DataFrame:
    sl = an["slots"]
    return sl[sl["day"] <= pd.Timestamp(an["cutoff"] + timedelta(days=window_days))]


def scored_slots(df: pd.DataFrame, cutoffs: list[date], cfg: dict, calendar: dict | None,
                 analyses: dict | None = None) -> pd.DataFrame:
    """Every candidate slot of the week after each cutoff with its raw score and outcome `y`."""
    window, tol = cfg["evaluate"]["window_days"], cfg["predict"]["hit_tolerance_minutes"]
    parts = []
    for c in cutoffs:
        an = analyses[c] if analyses and c in analyses else analyse(df, c, cfg, calendar)
        ws = week_slots(an, window)
        actual = kept_bookings(df, c, window)
        parts.append(ws.assign(y=calibrate.label_slots(ws, actual, tol), cutoff=c, n_actual=len(actual)))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def fit_from_slots(s: pd.DataFrame, cfg: dict) -> dict:
    """Isotonic map + reminder τ (+ the curves behind them) from labelled validation slots."""
    on = switch(cfg, "calibrate")
    model = calibrate.fit(s["score"].to_numpy(), s["y"].to_numpy(), enabled=on) if len(s) else \
        {**calibrate.IDENTITY, "n": 0, "fitted": False}
    p = calibrate.apply(s["score"], model) if len(s) else np.zeros(0)
    n_actual = int(s.groupby("cutoff")["n_actual"].first().sum()) if len(s) else 0
    curve = remind.pr_curve(p, s["y"].to_numpy() if len(s) else np.zeros(0), n_actual)
    return {**model, "tau": remind.choose_tau(curve, cfg), "curve": curve,
            "fitted_on": sorted({c.isoformat() for c in s["cutoff"]}) if len(s) else []}


def fit_calibration(df: pd.DataFrame, cutoff: date, cfg: dict, calendar: dict | None) -> dict:
    """Fit on the `calibrate.weeks` weekly cutoffs before `cutoff` (outcomes known by `cutoff`)."""
    weeks, window = cfg["calibrate"]["weeks"], cfg["evaluate"]["window_days"]
    cutoffs = [cutoff - timedelta(days=window) - timedelta(weeks=k) for k in range(weeks - 1, -1, -1)]
    start = df["start_time"].min().date()
    cutoffs = [c for c in cutoffs if c > start + timedelta(weeks=2)]
    return fit_from_slots(scored_slots(df, cutoffs, cfg, calendar), cfg)


# ── pattern rows ──────────────────────────────────────────────────────────────

def _at(day: pd.Timestamp, minute: float) -> datetime:
    m = int(round(minute)) % 1440
    return datetime.combine(day.date(), time(m // 60, m % 60), TZ)


def routine_ids(routines: pd.DataFrame) -> pd.Series:
    """`<user>-P<k>` per routine, strongest first (k = 1 is the primary row)."""
    rank = routines.groupby("user_id", sort=False).cumcount() + 1
    return pd.Series((routines["user_id"] + "-P" + rank.astype(str)).to_numpy(),
                     index=pd.MultiIndex.from_frame(routines[["user_id", "location_id", "cluster"]]))


def pattern_rows(an: dict, cfg: dict, run_id: str, bookings: pd.DataFrame | None = None) -> list[dict]:
    """One primary row per user + one per extra routine, with tiers and reasons.
    `bookings` (the full input, incl. upcoming rows) lets reminders skip already-booked slots."""
    cutoff, c, users = an["cutoff"], an["clean"], an["users"].set_index("user_id")
    routines, sl = an["routines"].reset_index(drop=True), an["slots"]
    cal = an.get("calibration") or {}
    tau = cal.get("tau", cfg["reminder"]["default_threshold"])
    habit_slots = sl[sl["is_habit"]]
    own_next = first_booking(habit_slots, by=["user_id", "location_id", "cluster"]) \
        .set_index(["user_id", "location_id", "cluster"])
    current = routines[routines["state"] != "ended"]
    guesses = next_per_user(sl[~sl["user_id"].isin(current["user_id"])]).set_index("user_id")
    gaps = an["gaps"].set_index("user_id")
    done = {uid: g for uid, g in c[c["status"] != "cancelled"].groupby("user_id", sort=False)}
    empty = c.iloc[:0]
    per_user = {uid: g for uid, g in routines.groupby("user_id", sort=False)}
    cancel = c.groupby("user_id")["cancel_rate"].first()
    factor = an["factor"]
    rows = []
    for uid, u in users.iterrows():
        mine = done.get(uid, empty)
        f = float(factor.get(uid, 1.0))
        base = {"user_id": uid, "active_weeks": int(u.active_weeks),
                "inactivity_detected": bool(u.inactive), "cancel_rate": round(float(cancel.get(uid, 0.0)), 3),
                "change_date": None, "run_id": run_id, "model_version": MODEL_VERSION}
        idle = explain.inactivity(u, f) if u.inactive or f < 0.99 else ""
        rs = per_user.get(uid)
        if rs is None:
            rows.append({**base, **_no_routine_row(uid, mine, u, guesses, gaps, idle)})
            continue
        for k, (ix, r) in enumerate(rs.iterrows(), start=1):
            key = (uid, r.location_id, r.cluster)
            nxt = own_next.loc[key] if r.state != "ended" and key in own_next.index else None
            at = _at(nxt["day"], nxt["minute"]) if nxt is not None else None
            conf = float(nxt["p"]) if nxt is not None else 0.0
            partner = routines.loc[int(r.switch_with)] if pd.notna(r.switch_with) else None
            days = [DAY_NAMES[d] for d in r.days]
            rows.append({**base, "pattern_id": f"{uid}-P{k}", "is_primary": k == 1,
                         "pattern_type": "recurring", "state": r.state, "days": days,
                         "label": baselines.day_label(days),
                         "time_window": {"center": hhmm(r.center), "spread_min": round(float(r.spread), 1)},
                         "location_id": r.location_id, "support": int(r.support),
                         "recurrence_prob": round(float(r.mean_p), 3),
                         "next_booking": {"at": at.isoformat(), "location_id": r.location_id} if at else None,
                         "confidence": round(min(1.0, max(0.0, conf)), 3),
                         "reminder": NO_REMINDER,
                         "last_booking_date": r.last_day.date().isoformat(),
                         "frequency_per_week": round(r.support / max(1, u.active_weeks), 2),
                         "change_date": r.change_date.date().isoformat() if pd.notna(r.change_date) else None,
                         "reason": explain.routine(r, u, partner) + (idle if r.state != "ended" else ""),
                         "next_at": at, "streak": int(r.streak)})
    if bookings is not None:
        remind.plan(rows, bookings, c, cutoff, tau, cfg, cfg["predict"]["hit_tolerance_minutes"])
    for row in rows:
        row["reason"] += explain.reminder(row.pop("_reminder_note", None))
        row.pop("next_at", None)
        row.pop("streak", None)
    return rows


def _no_routine_row(uid: str, mine: pd.DataFrame, u, guesses: pd.DataFrame, gaps: pd.DataFrame, idle: str) -> dict:
    weeks = max(1, int(u.active_weeks))
    last = mine["day"].max() if len(mine) else None
    gap = gaps.loc[uid] if uid in gaps.index else None
    common = {"pattern_id": f"{uid}-P1", "is_primary": True, "state": None, "days": [], "label": "Occasional",
              "time_window": None, "support": int(len(mine)), "recurrence_prob": None,
              "location_id": mine["location_id"].mode().iat[0] if len(mine) else None,
              "reminder": NO_REMINDER, "frequency_per_week": round(len(mine) / weeks, 2),
              "last_booking_date": last.date().isoformat() if last is not None else None}
    if len(mine) < 3:
        return {**common, "pattern_type": "insufficient_history", "next_booking": None, "confidence": 0.1,
                "reason": explain.insufficient(len(mine))}
    if uid in guesses.index:
        g = guesses.loc[uid]
        at = _at(g["day"], g["minute"])
        guess = {"at": at, "location_id": g["location_id"], "p": float(g["p"])}
        return {**common, "pattern_type": "occasional", "next_booking": {"at": at.isoformat(),
                "location_id": g["location_id"]}, "confidence": round(min(1.0, float(g["p"])), 3),
                "reason": explain.occasional(len(mine), weeks, gap, guess) + idle}
    return {**common, "pattern_type": "occasional", "next_booking": None,
            "confidence": round(float(gap.p_week), 3) if gap is not None else 0.1,
            "reason": explain.occasional(len(mine), weeks, gap) + idle}


# ── C1 feeds ──────────────────────────────────────────────────────────────────

def base_rate(an: dict, weeks: int = 8) -> pd.DataFrame:
    """Expected non-routine arrivals per lot × weekday × hour: bookings outside current routines
    in the last `weeks` weeks, divided by `weeks` (the "unpredictable users" part of the forecast)."""
    c = an["clean"]
    c = c[(c["status"] != "cancelled") & (c["day"] > pd.Timestamp(an["cutoff"]) - pd.Timedelta(weeks=weeks))]
    ds = an["day_stats"]
    habit_keys = ds.loc[ds["is_habit"], ["user_id", "location_id", "cluster", "weekday"]]
    c = c.merge(habit_keys.assign(_routine=True), on=["user_id", "location_id", "cluster", "weekday"], how="left")
    c = c[c["_routine"].isna()]
    hour = c["start_time"].dt.hour
    return (c.groupby([c["location_id"], c["weekday"], hour.rename("hour")]).size() / weeks).rename("base")


def priors_and_forecast(an: dict, days_ahead: int = 7) -> tuple[list[dict], list[dict]]:
    """Arrival priors (→ C1 fusion) from the habit slots of the next `days_ahead` days, and the lot
    forecast = Σ calibrated slot probabilities + the non-routine base rate.

    Same shapes as `baselines.priors_and_forecast`; each day uses its own (calibrated) probability
    and time, and holidays / sat-out term breaks get no prior.
    """
    cutoff = an["cutoff"]
    pid = routine_ids(an["routines"].reset_index(drop=True))
    rt = an["routines"].set_index(["user_id", "location_id", "cluster"])
    end = pd.Timestamp(cutoff + timedelta(days=days_ahead))
    sl = an["slots"]
    sl = sl[sl["is_habit"] & (sl["day"] <= end)]
    priors, forecast = [], {}
    for s in sl.itertuples(index=False):
        r = rt.loc[(s.user_id, s.location_id, s.cluster)]
        half = max(30.0, 2 * float(r["spread"]))
        center = _at(s.day, s.minute)
        p = round(min(1.0, max(0.0, float(s.p))), 3)
        priors.append({"user_id": s.user_id, "lot_id": s.location_id, "date": center.date().isoformat(),
                       "window_start": (center - timedelta(minutes=half)).isoformat(),
                       "window_end": (center + timedelta(minutes=half)).isoformat(),
                       "p": p, "pattern_id": pid[(s.user_id, s.location_id, s.cluster)]})
        fk = (s.location_id, center.date().isoformat(), center.hour)
        forecast[fk] = forecast.get(fk, 0.0) + float(s.p)
    base = base_rate(an)
    for k in range(1, days_ahead + 1):
        d = cutoff + timedelta(days=k)
        for (lot, wd, hour), v in base.items():
            if wd == d.weekday():
                fk = (lot, d.isoformat(), int(hour))
                forecast[fk] = forecast.get(fk, 0.0) + float(v)
    rows_out = [{"lot_id": lot, "date": d, "hour": h, "expected_arrivals": round(v, 2)}
                for (lot, d, h), v in sorted(forecast.items()) if v >= 0.005]
    return priors, rows_out


def run(input_path: str | Path, cutoff: date | None = None, out_dir: Path | None = None) -> dict:
    cfg = load_config()
    calendar = load_calendar()
    df = load_bookings(input_path)
    cutoff = cutoff or default_cutoff(df)
    run_id = new_run_id()
    cal = fit_calibration(df, cutoff, cfg, calendar)
    an = analyse(df, cutoff, cfg, calendar, calibration=cal)
    patterns = pattern_rows(an, cfg, run_id, bookings=df)
    priors, forecast = priors_and_forecast(an)
    info = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cutoff": cutoff.isoformat(), "input": str(input_path),
            "model_version": MODEL_VERSION, "params": cfg,
            "calibration": {k: cal[k] for k in ("x", "y", "n", "fitted", "tau", "fitted_on")},
            "counts": {"users": len({p["user_id"] for p in patterns}),
                       "rows": len(patterns),
                       "recurring": sum(p["pattern_type"] == "recurring" for p in patterns),
                       "states": {s: sum(p["state"] == s for p in patterns)
                                  for s in ("emerging", "active", "fading", "ended")},
                       "reminders": sum(p["reminder"]["recommend"] for p in patterns),
                       "priors": len(priors)}}
    write_run(out_dir or runs_dir(), info, patterns, priors, forecast)
    return info
