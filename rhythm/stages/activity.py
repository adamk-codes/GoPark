"""Stage 2 — active weeks (ARCHITECTURE_PLAN.md §4.2 step 2).

A user is "exposed" on a day if a routine of theirs could have produced a booking that day:
from their first booking to the cutoff, minus public holidays, minus term breaks they
visibly sit out, minus inactive gaps longer than `max(21 d, 3 × median gap)`.
Misses are only counted on exposed days, so a student's summer doesn't count against them.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

USER_COLS = ["user_id", "first_day", "last_day", "median_gap_days", "gap_threshold_days",
             "days_since_last", "inactive", "skips_breaks", "exposed_days", "active_weeks"]


def calendar_arrays(calendar: dict | None) -> tuple[np.ndarray, list[tuple[np.datetime64, np.datetime64]]]:
    calendar = calendar or {}
    hol = np.array([np.datetime64(d, "D") for d in calendar.get("public_holidays") or []], "datetime64[D]")
    breaks = [(np.datetime64(b["start"], "D"), np.datetime64(b["end"], "D"))
              for b in calendar.get("term_breaks") or []]
    return hol, breaks


def activity(df: pd.DataFrame, cutoff: date, cfg: dict, calendar: dict | None = None,
             enabled: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (exposure: one row per user × exposed day [user_id, day, weekday, week], users summary)."""
    hol, breaks = calendar_arrays(calendar)
    end = np.datetime64(cutoff, "D")
    gap_min, gap_mult = cfg["inactive_gap_days_min"], cfg["inactive_gap_multiplier"]
    skip_ratio = cfg.get("term_break_skip_ratio", 0.5)
    exp_users, exp_days, summary = [], [], []
    day = df["day"].to_numpy().astype("datetime64[D]")
    kept = (df["status"] != "cancelled").to_numpy()
    for uid, ix in sorted(df.groupby("user_id").indices.items()):
        all_days = np.unique(day[ix])
        booked = np.unique(day[ix[kept[ix]]])
        booked = booked if len(booked) else all_days   # cancelled-only users: use what they tried
        span = np.arange(all_days[0], end + 1, dtype="datetime64[D]")
        gaps = np.diff(booked).astype(int)
        median_gap = float(np.median(gaps)) if len(gaps) else 0.0
        threshold = max(gap_min, gap_mult * median_gap)
        mask = np.ones(len(span), bool)
        skips = False
        if enabled:
            mask &= ~np.isin(span, hol)
            on = np.isin(span, booked)
            for s, e in breaks:
                inside = (span >= s) & (span <= e)
                if inside.any() and (~inside).any():
                    rate_in, rate_out = on[inside].mean(), on[~inside].mean()
                    if rate_in < skip_ratio * rate_out:
                        mask &= ~inside
                        skips = True
            for a, b, n in zip(booked[:-1], booked[1:], gaps):
                if n > threshold:
                    mask &= ~((span > a) & (span < b))
        days = span[mask]
        last = booked[-1]
        since = int((end - last).astype(int))
        exp_users.append(np.full(len(days), uid, dtype=object))
        exp_days.append(days)
        weeks = np.unique((days.view("int64") + 3) // 7)   # 1970-01-01 was a Thursday
        summary.append((uid, pd.Timestamp(all_days[0]), pd.Timestamp(last), median_gap, threshold, since,
                        since > threshold, skips, len(days), len(weeks)))

    exposure = pd.DataFrame({"user_id": np.concatenate(exp_users) if exp_users else [],
                             "day": pd.to_datetime(np.concatenate(exp_days)) if exp_days else []})
    exposure["weekday"] = exposure["day"].dt.weekday
    exposure["week"] = exposure["day"] - pd.to_timedelta(exposure["weekday"], unit="D")
    return exposure, pd.DataFrame(summary, columns=USER_COLS)
