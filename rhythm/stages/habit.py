"""Stage 4 — habit test (ARCHITECTURE_PLAN.md §4.2 step 4).

For every candidate cluster × weekday, on the user's exposed days:
  p ~ Beta(α₀ + h, β₀ + n − h)
with each day weighted by status (hit) and by age (decay, half-life 4 weeks). The prior is the
population's rate at the same lot and weekday (empirical Bayes), worth `prior_strength_weeks`
weeks of evidence. A weekday is a habit day if P(p > 0.5) is high enough **and** a binomial
test says the hits are unlikely for a user booking random days at their own rate. A cluster's
habit days form a routine if it has enough hits across enough weeks and a tight enough time.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import beta, binom

from rhythm.stages.common import circ_stats

KEYS = ["user_id", "location_id", "cluster"]
RECENT_WEEKS = 8


def _decay(days: pd.Series, end: pd.Timestamp, half_life_weeks: float, on: bool) -> np.ndarray:
    if not on:
        return np.ones(len(days))
    age_weeks = (end - days).dt.days.to_numpy() / 7
    return 0.5 ** (age_weeks / half_life_weeks)


def population_rates(df: pd.DataFrame, exposure: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """→ (mean status-weighted booking rate per lot × weekday across users who use the lot,
          each user's plain booking rate per exposed day at each lot)."""
    pairs = df[["user_id", "location_id"]].drop_duplicates()
    hits = df.groupby(["user_id", "location_id", "day"])["weight"].max().rename("hit")
    t = pairs.merge(exposure[["user_id", "day", "weekday"]], on="user_id")
    t = t.join(hits, on=["user_id", "location_id", "day"])
    t["hit"] = t["hit"].fillna(0.0)
    t["any"] = (t["hit"] > 0).astype(float)
    per_user = t.groupby(["user_id", "location_id", "weekday"])["hit"].mean()
    prior = per_user.groupby(["location_id", "weekday"]).mean().rename("prior_rate")
    own = t.groupby(["user_id", "location_id"])["any"].mean().rename("own_rate")
    return prior, own


def habit(df: pd.DataFrame, exposure: pd.DataFrame, cutoff: date, cfg: dict, use_prior: bool = True,
          use_decay: bool = True, use_chance: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (day_stats: one row per cluster × weekday, routines: one row per routine)."""
    h = cfg["habit"]
    end = pd.Timestamp(cutoff)
    prior, own = population_rates(df, exposure)

    cl = df[df["cluster"] >= 0].copy()
    # Time-of-day estimates use their own (longer) memory: a median needs more points than a rate.
    cl["w"] = _decay(cl["day"], end, h.get("time_half_life_weeks", h["decay_half_life_weeks"]), use_decay)         * cl["weight"]
    hits = cl.groupby(KEYS + ["day"])["weight"].max().rename("hit")
    t = cl[KEYS].drop_duplicates().merge(exposure, on="user_id").join(hits, on=KEYS + ["day"])
    t["hit"] = t["hit"].fillna(0.0)
    t["any"] = t["hit"] > 0
    t["w"] = _decay(t["day"], end, h["decay_half_life_weeks"], use_decay)
    t["wh"] = t["w"] * t["hit"]
    ds = t.groupby(KEYS + ["weekday"]).agg(n=("w", "sum"), h=("wh", "sum"), trials=("w", "size"),
                                           k=("hit", "sum"), hit_days=("any", "sum")).reset_index()

    if h.get("prior_source", "clusters") == "clusters":
        # Prior among routine *candidates* (time clusters) at the lot × weekday rather than
        # everyone who ever parked there: one-off visitors would drag every prior towards 0.
        prior = (ds["h"] / ds["n"]).groupby([ds["location_id"], ds["weekday"]]).mean().rename("prior_rate")
    ds = ds.join(prior, on=["location_id", "weekday"]).join(own, on=["user_id", "location_id"])
    r = ds["prior_rate"].fillna(0.5).clip(0.02, 0.98)
    s = h["prior_strength_weeks"]
    a0, b0 = (s * r, s * (1 - r)) if use_prior else (1.0, 1.0)
    a, b = a0 + ds["h"], b0 + ds["n"] - ds["h"]
    ds["post_mean"] = a / (a + b)
    ds["p_gt_half"] = beta.sf(0.5, a, b)
    null = ds["own_rate"].fillna(0.0).clip(1e-3, h["chance_rate_cap"])
    k = ds["k"].round().astype(int)
    ds["chance_p"] = binom.sf(k - 1, ds["trials"], null) if use_chance else 0.0
    ds["passes"] = (ds["p_gt_half"] >= h["min_posterior_p_gt_half"]) & (ds["chance_p"] < h["max_chance_pvalue"])

    # Time per cluster × weekday (falls back to the cluster's time when the weekday has no hits).
    centers = circ_stats(cl, KEYS + ["weekday"]).rename(columns={"center": "center_min", "spread": "spread_min"})
    overall = circ_stats(cl, KEYS).rename(columns={"center": "cluster_center", "spread": "cluster_spread"})
    ds = ds.merge(centers, on=KEYS + ["weekday"], how="left").merge(overall, on=KEYS, how="left")
    ds["center_min"] = ds["center_min"].fillna(ds["cluster_center"])

    routines = _routines(cl, t, ds[ds["passes"]], end, h)
    ds["is_habit"] = ds.set_index(KEYS).index.isin(routines.set_index(KEYS).index) & ds["passes"]
    return ds, routines


def _routines(cl: pd.DataFrame, t: pd.DataFrame, passing: pd.DataFrame, end: pd.Timestamp, h: dict) -> pd.DataFrame:
    cols = KEYS + ["days", "center", "spread", "support", "distinct_weeks", "last_day",
                   "recent_hits", "recent_slots", "mean_p", "expected_per_week"]
    if passing.empty:
        return pd.DataFrame(columns=cols)
    sel = passing[KEYS + ["weekday"]]
    rows = cl.merge(sel, on=KEYS + ["weekday"])
    rows["week"] = rows["day"] - pd.to_timedelta(rows["weekday"], unit="D")
    stats = rows.groupby(KEYS).agg(support=("day", "nunique"), distinct_weeks=("week", "nunique"),
                                   last_day=("day", "max"))
    time = circ_stats(rows, KEYS).set_index(KEYS)
    recent = t.merge(sel, on=KEYS + ["weekday"])
    recent = recent[recent["day"] > end - pd.Timedelta(weeks=RECENT_WEEKS)]
    rec = recent.groupby(KEYS).agg(recent_hits=("any", "sum"), recent_slots=("any", "size"))
    p = passing.groupby(KEYS).agg(days=("weekday", lambda x: tuple(sorted(x))),
                                  mean_p=("post_mean", "mean"), expected_per_week=("post_mean", "sum"))
    out = p.join(time).join(stats).join(rec).reset_index()
    out[["recent_hits", "recent_slots"]] = out[["recent_hits", "recent_slots"]].fillna(0).astype(int)
    keep = ((out["support"] >= h["min_hits"]) & (out["distinct_weeks"] >= h["min_distinct_weeks"])
            & (out["spread"] <= h["max_spread_minutes"]))
    return out.loc[keep, cols].sort_values(["user_id", "expected_per_week"], ascending=[True, False]) \
        .reset_index(drop=True)
