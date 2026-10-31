"""Stage 1 — clean (ARCHITECTURE_PLAN.md §4.2 step 1).

Repairs messy rows, weights statuses, merges a cancel + rebook into one booking intent and
adds the columns later stages use: `weight`, `day` (local midnight), `weekday`, `minute`,
`cancel_rate` (per user, before merging).
"""
from __future__ import annotations

import pandas as pd

STATUS_ALIASES = {"complete": "completed", "done": "completed", "canceled": "cancelled",
                  "cancel": "cancelled", "noshow": "no_show", "missed": "no_show"}
DEFAULT_DURATION = pd.Timedelta(hours=2)


def normalise_status(s: pd.Series) -> pd.Series:
    s = s.astype("string").str.strip().str.lower().str.replace(r"[\s\-]+", "_", regex=True)
    return s.replace(STATUS_ALIASES)


def _repair_end(df: pd.DataFrame) -> pd.Series:
    """Missing end or end before start → start + the user's median good duration."""
    dur = df["end_time"] - df["start_time"]
    bad = dur.isna() | (dur <= pd.Timedelta(0))
    typical = dur.where(~bad).groupby(df["user_id"]).transform("median").fillna(DEFAULT_DURATION)
    return df["end_time"].where(~bad, df["start_time"] + typical)


def _rebooked(df: pd.DataFrame, hours: float) -> pd.Index:
    """Cancelled rows the same user rebooked at the same lot within `hours` (one intent)."""
    cols = ["user_id", "location_id", "start_time"] + (["created_at"] if "created_at" in df else [])
    canc = df.loc[df["status"] == "cancelled", cols].reset_index(names="_idx")
    kept = df.loc[df["status"] != "cancelled", cols]
    if canc.empty or kept.empty:
        return pd.Index([])
    m = canc.merge(kept, on=["user_id", "location_id"], suffixes=("", "_re"))
    close = (m["start_time_re"] - m["start_time"]).abs() < pd.Timedelta(hours=hours)
    if "created_at" in m:   # a rebook is made after the booking it replaces
        close &= ~(m["created_at_re"] < m["created_at"])
    return pd.Index(m.loc[close, "_idx"].unique())


def clean(df: pd.DataFrame, cfg: dict, merge_rebook: bool = True) -> pd.DataFrame:
    weights = cfg["status_weights"]
    out = df.copy()
    out["status"] = normalise_status(out["status"])
    out = out[out["status"].isin(list(weights)) & out["start_time"].notna()
              & out["user_id"].notna() & out["location_id"].notna()]
    out["location_id"] = out["location_id"].astype(str).str.strip()
    cancel_rate = (out["status"] == "cancelled").groupby(out["user_id"]).mean()

    if "end_time" not in out:
        out["end_time"] = pd.NaT
    out["end_time"] = _repair_end(out)
    out["weight"] = out["status"].map(weights).astype(float)
    # Duplicates (same user, lot, start): keep the strongest status.
    out = (out.sort_values("weight", ascending=False, kind="stable")
              .drop_duplicates(["user_id", "location_id", "start_time"]))
    if merge_rebook:
        out = out.drop(_rebooked(out, cfg["rebook_merge_hours"]))

    local = out["start_time"].dt.tz_localize(None)
    out["day"] = local.dt.normalize()
    out["weekday"] = local.dt.weekday
    out["minute"] = local.dt.hour * 60 + local.dt.minute
    out["cancel_rate"] = out["user_id"].map(cancel_rate).astype(float)
    return out.sort_values("start_time").reset_index(drop=True)
