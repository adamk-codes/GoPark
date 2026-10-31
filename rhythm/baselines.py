"""Baseline B2: most frequent weekdays + median time + most frequent location.

This is the P0 stand-in for the full engine (ARCHITECTURE_PLAN.md §4.2) and stays
afterwards as a baseline to beat in the evaluation. It emits contract-valid rows
so every consumer (API, dashboard, C1 fusion) can be built against real shapes.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TZ = ZoneInfo("Asia/Beirut")
LOOKBACK_WEEKS = 8
MODEL_VERSION = "baseline-b2 (P0 stub)"


def day_label(days: list[str]) -> str:
    if days == DAY_NAMES:
        return "Daily"
    if days == DAY_NAMES[:5]:
        return "Weekdays"
    if days == ["Sat", "Sun"]:
        return "Weekends"
    return " / ".join(days)


def _hhmm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def _local(d: date, minutes: float) -> datetime:
    m = int(round(minutes))
    return datetime.combine(d, time(m // 60, m % 60), TZ)


def _reminder(conf: float, next_at: datetime | None, center_min: float, cfg: dict) -> dict:
    r = cfg["reminder"]
    if next_at is None or conf < r["default_threshold"]:
        return {"recommend": False, "tier": "none", "at": None}
    tier = "locked_in" if conf >= r["locked_in_threshold"] else "at_risk"
    if center_min < r["morning_cutoff_hour"] * 60:
        at = datetime.combine(next_at.date() - timedelta(days=1), time(r["evening_before_hour"]), TZ)
    else:
        at = next_at - timedelta(hours=r["lead_hours_default"])
    return {"recommend": True, "tier": tier, "at": at.isoformat()}


def user_patterns(user_id: str, ub: pd.DataFrame, cutoff: date, cfg: dict, run_id: str) -> dict:
    done = ub[ub["status"] == "completed"]
    window_start = cutoff - timedelta(weeks=LOOKBACK_WEEKS) + timedelta(days=1)
    recent = done[done["start_time"].dt.date >= window_start]
    last = done["start_time"].max() if len(done) else None
    weeks_seen = max(1, min(LOOKBACK_WEEKS, ((cutoff - ub["start_time"].min().date()).days + 1) // 7))
    cancel_rate = float((ub["status"] == "cancelled").mean()) if len(ub) else 0.0
    base = {
        "user_id": user_id, "pattern_id": f"{user_id}-P1", "is_primary": True,
        "support": int(len(recent)), "active_weeks": int(weeks_seen),
        "last_booking_date": last.date().isoformat() if last is not None else None,
        "frequency_per_week": round(len(recent) / weeks_seen, 2),
        "inactivity_detected": bool(last is None or (cutoff - last.date()).days > 21),
        "cancel_rate": round(cancel_rate, 3), "change_date": None,
        "run_id": run_id, "model_version": MODEL_VERSION,
    }
    if len(recent) < 3:
        return {**base, "pattern_type": "insufficient_history" if len(done) < 3 else "occasional",
                "state": None, "days": [], "label": "Occasional", "time_window": None,
                "location_id": recent["location_id"].mode().iat[0] if len(recent) else None,
                "recurrence_prob": None, "next_booking": None, "confidence": 0.1,
                "reminder": {"recommend": False, "tier": "none", "at": None},
                "reason": f"Only {len(recent)} completed bookings in the last {weeks_seen} weeks."}

    location = recent["location_id"].mode().iat[0]
    at_loc = recent[recent["location_id"] == location]
    hits = at_loc["start_time"].dt.weekday.value_counts()
    days_idx = sorted(int(d) for d, n in hits.items() if n / weeks_seen >= 0.5)
    if not days_idx:
        return {**base, "pattern_type": "occasional", "state": None, "days": [], "label": "Occasional",
                "time_window": None, "location_id": location, "recurrence_prob": None,
                "next_booking": None, "confidence": 0.2,
                "reminder": {"recommend": False, "tier": "none", "at": None},
                "reason": f"{len(recent)} bookings in {weeks_seen} weeks with no weekday repeated in most weeks."}

    on_days = at_loc[at_loc["start_time"].dt.weekday.isin(days_idx)]
    minutes = on_days["start_time"].dt.hour * 60 + on_days["start_time"].dt.minute
    center, spread = float(minutes.median()), float(minutes.std() or 0)
    conf = round(min(0.99, len(on_days) / (weeks_seen * len(days_idx))), 3)
    next_day = next(cutoff + timedelta(days=k) for k in range(1, 8)
                    if (cutoff + timedelta(days=k)).weekday() in days_idx)
    next_at = _local(next_day, center)
    days = [DAY_NAMES[d] for d in days_idx]
    return {**base, "pattern_type": "recurring", "state": "active", "days": days, "label": day_label(days),
            "time_window": {"center": _hhmm(center), "spread_min": round(spread, 1)},
            "location_id": location, "recurrence_prob": conf,
            "next_booking": {"at": next_at.isoformat(), "location_id": location},
            "confidence": conf, "reminder": _reminder(conf, next_at, center, cfg),
            "reason": (f"Booked {location} on {len(on_days)} of {weeks_seen * len(days_idx)} "
                       f"{day_label(days)} slots in the last {weeks_seen} weeks, usually around {_hhmm(center)}.")}


def priors_and_forecast(patterns: list[dict], cutoff: date, days_ahead: int = 7):
    priors, forecast = [], {}
    for p in patterns:
        if p["pattern_type"] != "recurring":
            continue
        h, m = map(int, p["time_window"]["center"].split(":"))
        half = max(30, p["time_window"]["spread_min"] * 2)
        for k in range(1, days_ahead + 1):
            d = cutoff + timedelta(days=k)
            if DAY_NAMES[d.weekday()] not in p["days"]:
                continue
            center = datetime.combine(d, time(h, m), TZ)
            priors.append({"user_id": p["user_id"], "lot_id": p["location_id"], "date": d.isoformat(),
                           "window_start": (center - timedelta(minutes=half)).isoformat(),
                           "window_end": (center + timedelta(minutes=half)).isoformat(),
                           "p": p["confidence"], "pattern_id": p["pattern_id"]})
            key = (p["location_id"], d.isoformat(), h)
            forecast[key] = forecast.get(key, 0.0) + p["confidence"]
    rows = [{"lot_id": lot, "date": d, "hour": h, "expected_arrivals": round(v, 2)}
            for (lot, d, h), v in sorted(forecast.items())]
    return priors, rows
