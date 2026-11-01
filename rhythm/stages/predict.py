"""Stage 6 — candidate slots, the next likely booking, and the occasional-user gap model.

Every cluster × weekday becomes a dated slot over the horizon, scored with
`posterior mean × activity factor` (the factor halves every `inactivity_half_life_weeks` once the
user has been away longer than 2× their typical gap). Calibration (stage 7) turns the score into a
probability. Holidays are skipped, and term-break days for users who sit breaks out. The next
booking is the slot most likely to be the *first* one: p_i × Π_{earlier j} (1 − p_j).

Users without a routine also get a gap model: their recent booking rate per exposed day gives
P(at least one booking in the next 7 days) = 1 − exp(−7 λ), at their most frequent recent lot.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from rhythm.stages.activity import calendar_arrays

SLOT_COLS = ["user_id", "location_id", "cluster", "day", "minute", "score", "p", "is_habit"]


def activity_factor(users: pd.DataFrame, cfg: dict, enabled: bool = True) -> pd.Series:
    """Per user: 1, or less once they've been away longer than 2× their usual gap."""
    if not enabled:
        return pd.Series(1.0, index=users["user_id"])
    hl = cfg["predict"]["inactivity_half_life_weeks"] * 7
    over = users["days_since_last"] - 2 * users["median_gap_days"].clip(lower=1)
    return pd.Series(0.5 ** (over.clip(lower=0) / hl).to_numpy(), index=users["user_id"].to_numpy())


def slots(day_stats: pd.DataFrame, users: pd.DataFrame, cutoff: date, calendar: dict | None,
          horizon_days: int, factor: pd.Series | None = None) -> pd.DataFrame:
    """Dated candidate slots; `score` is the uncalibrated score, `p` starts equal to it."""
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
    f = s["user_id"].map(factor).fillna(1.0) if factor is not None else 1.0
    s = s.rename(columns={"center_min": "minute"}).assign(score=s["post_mean"] * f)
    s["p"] = s["score"]
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


def gap_model(df: pd.DataFrame, exposure: pd.DataFrame, cutoff: date, cfg: dict) -> pd.DataFrame:
    """Per user: kept bookings per exposed day over the last `gap_window_weeks` weeks (λ), the
    chance of at least one booking in the next 7 days, and the lot they use most in that window."""
    weeks = cfg["predict"]["gap_window_weeks"]
    start = pd.Timestamp(cutoff) - pd.Timedelta(weeks=weeks)
    kept = df[(df["status"] != "cancelled") & (df["day"] > start)]
    n = kept.groupby("user_id")["day"].nunique().rename("bookings")
    exp = exposure[exposure["day"] > start].groupby("user_id").size().rename("exposed")
    lot = kept.groupby("user_id")["location_id"].agg(lambda s: s.mode().iat[0]).rename("gap_lot")
    out = pd.concat([n, exp, lot], axis=1)
    out["bookings"] = out["bookings"].fillna(0)
    out["rate_per_day"] = out["bookings"] / out["exposed"].clip(lower=7).fillna(7 * weeks)
    out["p_week"] = 1 - np.exp(-7 * out["rate_per_day"])
    return out.rename_axis("user_id").reset_index()
