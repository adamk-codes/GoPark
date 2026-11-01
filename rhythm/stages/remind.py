"""Stage 8 — remind (ARCHITECTURE_PLAN.md §4.2 step 8).

* τ: the lowest threshold whose precision on the calibration (validation) slots reaches
  `target_precision` (so reminders are right at least that often while catching as many
  bookings as possible); `default_threshold` if none does. The whole precision/recall curve is
  kept for the dashboard slider.
* Tiers per routine row (p = calibrated probability of its next slot):
  `locked_in` (active, didn't miss its last slot, and p ≥ `locked_in_threshold` or p ≥ τ with a
  streak of ≥ `locked_in_streak` booked slots: light touch, no reminder) · `emerging` (new
  routine with p ≥ `emerging_min_p`: cement the habit) · `at_risk` (p ≥ τ otherwise: fading,
  recent misses, or just not certain: the best target) · `none`.
* Timing: the user's median booking lead time (start − `created_at`) + 30 min before the slot;
  without `created_at`, 20:00 the evening before for morning slots, else `lead_hours_default`
  before. Never earlier than the run's "now" (the cutoff day at `evening_before_hour`).
* Suppressed if the slot is already booked, or within `cooldown_hours` of the user's previous
  reminder in this run.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

import numpy as np
import pandas as pd

from rhythm.baselines import TZ
from rhythm.stages.common import circ_diff

GRID = [round(x, 2) for x in np.arange(0.05, 1.0, 0.05)]


def pr_curve(p: np.ndarray, y: np.ndarray, n_actual: int | None = None) -> list[dict]:
    """Precision / recall of "remind when p ≥ τ" per τ. Recall is over all booked slots
    (`n_actual`, default: the positives among the scored slots)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    pos = n_actual if n_actual is not None else y.sum()
    out = []
    for tau in GRID:
        m = p >= tau
        tp = float(y[m].sum())
        out.append({"tau": tau, "precision": round(tp / m.sum(), 4) if m.any() else None,
                    "recall": round(tp / pos, 4) if pos else None, "reminders": int(m.sum())})
    return out


def choose_tau(curve: list[dict], cfg: dict) -> float:
    target = cfg["reminder"]["target_precision"]
    ok = [c["tau"] for c in curve if c["precision"] is not None and c["precision"] >= target and c["reminders"] >= 10]
    return float(min(ok)) if ok else float(cfg["reminder"]["default_threshold"])


def lead_minutes(df: pd.DataFrame, cutoff: date, weeks: int = 8) -> pd.Series:
    """Median minutes between creating a booking and its start, per user (recent kept bookings)."""
    if "created_at" not in df or df["created_at"].isna().all():
        return pd.Series(dtype=float)
    recent = df[(df["status"] != "cancelled") & (df["day"] > pd.Timestamp(cutoff) - pd.Timedelta(weeks=weeks))]
    lead = (recent["start_time"] - recent["created_at"]).dt.total_seconds() / 60
    lead = lead[lead > 0]
    g = lead.groupby(recent.loc[lead.index, "user_id"])
    return g.median()[g.size() >= 3]


def booked_slots(df: pd.DataFrame, cutoff: date) -> pd.DataFrame:
    """Upcoming bookings already made by the run's "now" (created on or before the cutoff when
    `created_at` exists; otherwise every `booked` row after the cutoff)."""
    future = df[df["start_time"].dt.date > cutoff]
    if "created_at" in future and future["created_at"].notna().any():
        future = future[future["created_at"].dt.date <= cutoff]
    else:
        future = future[future["status"].astype(str).str.lower() == "booked"]
    future = future[future["status"].astype(str).str.lower() != "cancelled"]
    local = future["start_time"].dt.tz_localize(None)
    return pd.DataFrame({"user_id": future["user_id"].to_numpy(), "day": local.dt.normalize().to_numpy(),
                         "location_id": future["location_id"].to_numpy(),
                         "minute": (local.dt.hour * 60 + local.dt.minute).to_numpy()})


def tier(p: float | None, state: str | None, streak: int, tau: float, cfg: dict) -> str:
    r = cfg["reminder"]
    if p is None or state in (None, "ended"):
        return "none"
    if state == "emerging" and p >= r["emerging_min_p"]:
        return "emerging"
    if state == "active" and streak > 0 and (p >= r["locked_in_threshold"]
                                             or (p >= tau and streak >= r["locked_in_streak"])):
        return "locked_in"
    return "at_risk" if p >= tau else "none"


def when(next_at: datetime, lead_min: float | None, now: datetime, cfg: dict) -> datetime:
    r = cfg["reminder"]
    if lead_min is not None and not np.isnan(lead_min):
        at = next_at - timedelta(minutes=float(lead_min) + 30)
    elif next_at.hour < r["morning_cutoff_hour"]:
        at = datetime.combine(next_at.date() - timedelta(days=1), time(r["evening_before_hour"]), TZ)
    else:
        at = next_at - timedelta(hours=r["lead_hours_default"])
    return max(at, now)


def plan(rows: list[dict], df: pd.DataFrame, clean_df: pd.DataFrame, cutoff: date, tau: float, cfg: dict,
         tol: float) -> list[dict]:
    """Fills `reminder` on each candidate row (dicts with user_id, state, confidence, next_at
    (datetime | None), location_id, streak = booked routine slots in a row up to the last one). Adds `_reminder_note` for the explanation."""
    r = cfg["reminder"]
    now = datetime.combine(cutoff, time(r["evening_before_hour"]), TZ)
    leads = lead_minutes(clean_df, cutoff)
    booked = booked_slots(df, cutoff)
    booked = {k: g["minute"].to_numpy(float) for k, g in booked.groupby(["user_id", "day", "location_id"])}
    for row in rows:
        at = row.get("next_at")
        t = tier(row.get("confidence") if at is not None else None, row.get("state"), row.get("streak", 0), tau, cfg)
        row["reminder"] = {"recommend": False, "tier": t, "at": None}
        row["_reminder_note"] = {"locked_in": f"locked in ({row.get('streak', 0)} slots in a row), no reminder needed"}.get(t)
        if t not in ("at_risk", "emerging") or at is None:
            continue
        mins = booked.get((row["user_id"], pd.Timestamp(at.date()), row["location_id"]))
        if mins is not None and (np.abs(circ_diff(mins, at.hour * 60 + at.minute)) <= tol).any():
            row["_reminder_note"] = "already booked, no reminder"
            continue
        lead = leads.get(row["user_id"])
        send = when(at, lead, now, cfg).replace(second=0, microsecond=0)
        if send >= at:
            row["_reminder_note"] = "too close to the slot to remind"
            continue
        row["reminder"] = {"recommend": True, "tier": t, "at": send.isoformat()}
        row["_reminder_note"] = (f"{t.replace('_', '-')}: remind {send:%a %H:%M}"
                                 + (f" (usual lead {lead / 60:.0f} h + 30 min)" if lead is not None else ""))
    # 48 h cooldown between one user's reminders (the earliest one wins).
    cool = timedelta(hours=r["cooldown_hours"])
    by_user: dict[str, datetime] = {}
    for row in sorted((x for x in rows if x["reminder"]["recommend"]), key=lambda x: x["reminder"]["at"]):
        at = datetime.fromisoformat(row["reminder"]["at"])
        last = by_user.get(row["user_id"])
        if last is not None and at - last < cool:
            row["reminder"] = {"recommend": False, "tier": row["reminder"]["tier"], "at": None}
            row["_reminder_note"] = f"another reminder within {r['cooldown_hours']} h"
            continue
        by_user[row["user_id"]] = at
    return rows
