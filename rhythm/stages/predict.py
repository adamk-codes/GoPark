"""Stage 6 (P1 version) — candidate slots and the next likely booking.

Every cluster × weekday becomes a dated slot over the horizon, scored with its posterior mean
(P2 adds the activity factor, the occasional-user gap model and calibration). Holidays are
skipped, and term-break days for users who sit breaks out. The next booking is the slot most
likely to be the *first* one: p_i × Π_{earlier j} (1 − p_j).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from rhythm.stages.activity import calendar_arrays

SLOT_COLS = ["user_id", "location_id", "cluster", "day", "minute", "p", "is_habit"]


def slots(day_stats: pd.DataFrame, users: pd.DataFrame, cutoff: date, calendar: dict | None,
          horizon_days: int) -> pd.DataFrame:
    if day_stats.empty:
        return pd.DataFrame(columns=SLOT_COLS)
    hol, breaks = calendar_arrays(calendar)
    days = np.arange(np.datetime64(cutoff + timedelta(days=1)),
                     np.datetime64(cutoff + timedelta(days=horizon_days + 1)), dtype="datetime64[D]")
    in_break = np.zeros(len(days), bool)
    for s, e in breaks:
        in_break |= (days >= s) & (days <= e)
    cal = pd.DataFrame({"day": pd.to_datetime(days), "holiday": np.isin(days, hol), "in_break": in_break})
    cal["weekday"] = cal["day"].dt.day_of_week
    s = day_stats[["user_id", "location_id", "cluster", "weekday", "center_min", "post_mean", "is_habit"]] \
        .merge(cal, on="weekday")
    skips = s["user_id"].map(users.set_index("user_id")["skips_breaks"]).fillna(False).astype(bool)
    s = s[~s["holiday"] & ~(s["in_break"] & skips)]
    s = s.rename(columns={"center_min": "minute", "post_mean": "p"})
    return s[SLOT_COLS].sort_values(["user_id", "day", "minute"]).reset_index(drop=True)


def first_booking(sl: pd.DataFrame, by: str | list[str] = "user_id") -> pd.DataFrame:
    """One row per group `by`: the slot most likely to be the group's first booking."""
    if sl.empty:
        return sl.assign(p_first=pd.Series(dtype=float))
    by = [by] if isinstance(by, str) else by
    sl = sl.sort_values(by + ["day", "minute"]).copy()
    log_miss = np.log1p(-sl["p"].clip(upper=0.999))
    before = log_miss.groupby([sl[c] for c in by]).cumsum() - log_miss
    sl["p_first"] = sl["p"] * np.exp(before)
    return sl.loc[sl.groupby(by)["p_first"].idxmax()].reset_index(drop=True)


def next_per_user(sl: pd.DataFrame) -> pd.DataFrame:
    """User-level top-1 over all candidate slots (the habit test labels routines; it doesn't
    gate prediction: a 60 % Monday still beats a 75 % Wednesday as the *next* booking)."""
    return first_booking(sl)
