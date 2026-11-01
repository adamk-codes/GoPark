"""Stage 4 — habit test (ARCHITECTURE_PLAN.md §4.2 step 4), scored per drift regime.

For every candidate cluster × weekday, on the user's exposed days of the *current regime* (after
the latest change point from `drift.change_points`; the whole history if there is none):
  p ~ Beta(α₀ + h, β₀ + n − h)
with each day weighted by status (hit) and by age (decay, half-life 4 weeks). The prior is the
population's rate at the same lot and weekday (empirical Bayes), worth `prior_strength_weeks`
weeks of evidence, so a short history shrinks towards the population instead of being rejected.

A weekday is "on" if P(p > `min_day_rate`) ≥ `min_posterior_p_on`. A cluster's on-days form a
routine if, together, they have enough hits across enough weeks, a tight enough time, and pass a
binomial chance test against a user booking random days at their own rate (one test per routine,
not per day: a 5-day commuter's Monday is not "unusual" against their own 70 % rate, but their
weekday set is). Days that were on *before* a downward change and are off now make an ended
routine (kept so the drift stage can report it and pair it with its replacement).
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from scipy.stats import beta, binom

from rhythm.stages import drift as drift_stage
from rhythm.stages.common import circ_stats

KEYS = ["user_id", "location_id", "cluster"]
SERIES = KEYS + ["weekday"]
RECENT_WEEKS = 8
ROUTINE_COLS = KEYS + ["days", "center", "spread", "support", "distinct_weeks", "first_day", "last_day",
                       "recent_hits", "recent_slots", "mean_p", "expected_per_week", "chance_p",
                       "regime_hits", "regime_slots", "regime_rate", "regime_weeks", "recent_rate",
                       "n_days", "up_days", "up_since", "down_days", "down_since", "pre_hits", "pre_slots",
                       "post_hits", "post_slots", "last_slot_day", "streak", "ended"]


def _decay(days: pd.Series, end, half_life_weeks: float, on: bool) -> np.ndarray:
    if not on:
        return np.ones(len(days))
    age_weeks = (np.asarray(end, dtype="datetime64[ns]") - days.to_numpy(dtype="datetime64[ns]")) \
        / np.timedelta64(7, "D")
    return 0.5 ** (np.maximum(age_weeks, 0) / half_life_weeks)


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


def _counts(t: pd.DataFrame, end, h: dict, use_decay: bool) -> pd.DataFrame:
    w = _decay(t["day"], end, h["decay_half_life_weeks"], use_decay)
    f = pd.DataFrame({"w": w, "wh": w * t["hit"].to_numpy(), "hit": t["hit"].to_numpy(),
                      "any": t["any"].to_numpy()}, index=t.index)
    return f.groupby([t[c] for c in SERIES]).agg(n=("w", "sum"), h=("wh", "sum"), trials=("w", "size"),
                                                 k=("hit", "sum"), hit_days=("any", "sum"))


def _prior_mean(ds: pd.DataFrame, prior: pd.Series, h: dict) -> pd.Series:
    """Prior rate per cluster × weekday: the population's rate at the lot × weekday, blended with
    the same cluster's rate on its *other* weekdays that have hits (leave-one-out). A new user's
    Tuesday with 1 hit in 2 weeks borrows strength from their Mon/Wed/Thu; a coincidence's lone
    weekday has no siblings and keeps the population prior."""
    pop = ds.join(prior, on=["location_id", "weekday"])["prior_rate"].fillna(0.5)
    hit = (ds["k"] > 0).to_numpy()
    g = [ds.index.get_level_values(c) for c in KEYS]
    hh = pd.Series(np.where(hit, ds["h"], 0.0), index=ds.index)
    nn = pd.Series(np.where(hit, ds["n"], 0.0), index=ds.index)
    loo_h = hh.groupby(g).transform("sum") - hh
    loo_n = nn.groupby(g).transform("sum") - nn
    own = (loo_h / loo_n).where(loo_n >= 1.0)
    wt = h.get("prior_cluster_weight", 0.0)
    return (wt * own + (1 - wt) * pop).fillna(pop).clip(0.02, 0.98)


def _posterior(ds: pd.DataFrame, prior: pd.Series, h: dict, use_prior: bool, suffix: str = "") -> pd.DataFrame:
    r = _prior_mean(ds, prior, h)
    s = h["prior_strength_weeks"]
    a0, b0 = (s * r, s * (1 - r)) if use_prior else (1.0, 1.0)
    a, b = a0 + ds["h"], b0 + ds["n"] - ds["h"]
    # A day needs at least one booking of its own to be "on"; the prior only decides how much one counts.
    p_on = np.where(ds["k"] > 0, beta.sf(h["min_day_rate"], a, b), 0.0)
    return pd.DataFrame({f"post_mean{suffix}": a / (a + b), f"p_on{suffix}": p_on}, index=ds.index)


def habit(df: pd.DataFrame, exposure: pd.DataFrame, cutoff: date, cfg: dict, use_prior: bool = True,
          use_decay: bool = True, use_chance: bool = True, use_drift: bool = True
          ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """→ (day_stats: one row per cluster × weekday, routines: one row per routine)."""
    h = cfg["habit"]
    end = pd.Timestamp(cutoff)
    prior, own = population_rates(df, exposure)

    cl = df[df["cluster"] >= 0].copy()
    hits = cl.groupby(KEYS + ["day"])["weight"].max().rename("hit")
    t = cl[KEYS].drop_duplicates().merge(exposure, on="user_id").join(hits, on=KEYS + ["day"])
    t["hit"] = t["hit"].fillna(0.0)
    t["any"] = t["hit"] > 0

    regimes = drift_stage.change_points(t, cfg, enabled=use_drift)
    t = t.merge(regimes, on=SERIES, how="left")
    t["current"] = t["regime_start"].isna() | (t["day"] >= t["regime_start"])

    cur = _counts(t[t["current"]], end, h, use_decay)
    if h.get("prior_source", "clusters") == "clusters":
        # Prior among routine *candidates* (time clusters) at the lot × weekday rather than
        # everyone who ever parked there: one-off visitors would drag every prior towards 0.
        rate = cur["h"] / cur["n"]
        prior = rate.groupby(level=["location_id", "weekday"]).mean().rename("prior_rate")
    ds = cur.join(_posterior(cur, prior, h, use_prior))
    # The regime before the latest change, decayed from the change date (ended routines).
    old = t[~t["current"]]
    if len(old):
        pre = _counts(old, old["regime_start"], h, use_decay)
        ds = ds.join(_posterior(pre, prior, h, use_prior, "_pre").join(pre[["k"]].rename(columns={"k": "k_pre"})))
    else:
        ds = ds.assign(post_mean_pre=np.nan, p_on_pre=np.nan, k_pre=np.nan)
    ds = ds.reset_index().merge(regimes, on=SERIES, how="left").join(own, on=["user_id", "location_id"])
    ds["passes"] = ds["p_on"] >= h["min_posterior_p_on"]
    ds["was_on"] = (~ds["passes"] & (ds["direction"] < 0) & (ds["p_on_pre"] >= h["min_posterior_p_on"]))
    ds["up"] = ds["direction"] > 0
    ds["down"] = ds["direction"] < 0

    # Time per cluster × weekday from the current regime (time memory is longer than the rate's:
    # a median needs more points), falling back to the cluster's time when the weekday has no hits.
    cl = cl.merge(regimes[SERIES + ["regime_start"]], on=SERIES, how="left")
    now = cl["regime_start"].isna() | (cl["day"] >= cl["regime_start"])
    cl["w"] = _decay(cl["day"], end, h.get("time_half_life_weeks", h["decay_half_life_weeks"]), use_decay) * cl["weight"]
    centers = circ_stats(cl[now], SERIES).rename(columns={"center": "center_min", "spread": "spread_min"})
    overall = circ_stats(cl, KEYS).rename(columns={"center": "cluster_center", "spread": "cluster_spread"})
    ds = ds.merge(centers, on=SERIES, how="left").merge(overall, on=KEYS, how="left")
    ds["center_min"] = ds["center_min"].fillna(ds["cluster_center"])

    routines = _routines(cl, t, ds, end, h, use_chance)
    cur_keys = routines.loc[~routines["ended"], KEYS].set_index(KEYS).index if len(routines) else pd.Index([])
    ds["is_habit"] = ds.set_index(KEYS).index.isin(cur_keys) & ds["passes"]
    return ds, routines


def _routine_stats(cl: pd.DataFrame, t: pd.DataFrame, sel: pd.DataFrame, end: pd.Timestamp, h: dict,
                   use_chance: bool, ended: bool) -> pd.DataFrame:
    """One row per cluster for the selected (cluster, weekday) days, scored on the regime they
    belong to (the current one, or the one before the change for ended routines)."""
    if sel.empty:
        return pd.DataFrame(columns=ROUTINE_COLS)
    tt = t.merge(sel[SERIES], on=SERIES)
    tt = tt[~tt["current"]] if ended else tt[tt["current"]]
    rows = cl.merge(sel[SERIES + ["regime_start"]], on=SERIES, suffixes=("_x", ""))
    rows = rows[(rows["day"] < rows["regime_start"]) if ended else
                (rows["regime_start"].isna() | (rows["day"] >= rows["regime_start"]))]
    if rows.empty or tt.empty:
        return pd.DataFrame(columns=ROUTINE_COLS)
    rows = rows.assign(week=rows["day"] - pd.to_timedelta(rows["weekday"], unit="D"))
    stats = rows.groupby(KEYS).agg(support=("day", "nunique"), distinct_weeks=("week", "nunique"),
                                   first_day=("day", "min"), last_day=("day", "max"))
    time = circ_stats(rows, KEYS).set_index(KEYS)
    reg = tt.groupby(KEYS).agg(regime_hits=("any", "sum"), regime_slots=("any", "size"), regime_from=("day", "min"),
                               regime_to=("day", "max"), own_rate=("own_rate", "first"))
    reg["last_slot_day"] = reg["regime_to"]
    # Streak: booked routine slots in a row, counting back from the latest one.
    tt = tt.sort_values(KEYS + ["day"])
    miss = (~tt["any"]).astype(int).groupby([tt[c] for c in KEYS]).cumsum()
    last_miss = miss.groupby([tt[c] for c in KEYS]).transform("max")
    reg["streak"] = (tt["any"] & (miss == last_miss)).groupby([tt[c] for c in KEYS]).sum()
    reg["regime_rate"] = reg["regime_hits"] / reg["regime_slots"]
    reg["regime_weeks"] = ((reg["regime_to"] - reg["regime_from"]).dt.days // 7 + 1)
    last_week = tt["day"] > (tt.groupby(KEYS)["day"].transform("max") - pd.Timedelta(weeks=h["recent_weeks"]))
    rr = tt[last_week].groupby(KEYS).agg(recent_rate=("any", "mean"))
    rec = tt[tt["day"] > end - pd.Timedelta(weeks=RECENT_WEEKS)].groupby(KEYS).agg(
        recent_hits=("any", "sum"), recent_slots=("any", "size"))
    p_col = "post_mean_pre" if ended else "post_mean"
    agg = sel.groupby(KEYS).agg(days=("weekday", lambda x: tuple(sorted(x))), mean_p=(p_col, "mean"),
                                expected_per_week=(p_col, "sum"), n_days=("weekday", "size"),
                                up_days=("up", "sum"), down_days=("down", "sum"),
                                pre_hits=("k_pre", "sum"), post_hits=("k", "sum"), post_slots=("trials", "sum"))
    up = sel[sel["up"]].groupby(KEYS)["regime_start"].median().rename("up_since")
    down = sel[sel["down"]].groupby(KEYS)["regime_start"].median().rename("down_since")
    pre_slots = t[~t["current"]].merge(sel[SERIES], on=SERIES).groupby(KEYS).size().rename("pre_slots")
    out = agg.join([time, stats, reg, rr, rec, up, down, pre_slots]).reset_index()
    out[["recent_hits", "recent_slots", "pre_slots"]] = out[["recent_hits", "recent_slots", "pre_slots"]].fillna(0).astype(int)
    out["recent_rate"] = out["recent_rate"].fillna(0.0)
    null = out["own_rate"].fillna(0.0).clip(1e-3, h["chance_rate_cap"])
    out["chance_p"] = binom.sf(out["regime_hits"] - 1, out["regime_slots"], null) if use_chance else 0.0
    out["ended"] = ended
    # A brand-new user's first week can be a routine already (it shows up as `emerging`).
    keep = ((out["support"] >= h["min_hits"]) & (out["distinct_weeks"] >= np.minimum(h["min_distinct_weeks"], out["regime_weeks"]))
            & (out["spread"] <= h["max_spread_minutes"]) & (out["chance_p"] < h["max_chance_pvalue"]))
    return out.loc[keep, ROUTINE_COLS]


def _routines(cl: pd.DataFrame, t: pd.DataFrame, ds: pd.DataFrame, end: pd.Timestamp, h: dict,
              use_chance: bool) -> pd.DataFrame:
    t = t.join(ds.set_index(KEYS)["own_rate"].groupby(level=KEYS).first(), on=KEYS)
    current = _routine_stats(cl, t, ds[ds["passes"]], end, h, use_chance, ended=False)
    # Ended: clusters with no current routine whose days were on before a downward change.
    old = ds[ds["was_on"] & ~ds.set_index(KEYS).index.isin(current.set_index(KEYS).index)]
    ended = _routine_stats(cl, t, old, end, h, use_chance, ended=True)
    parts = [p for p in (current, ended) if len(p)]
    if not parts:
        return pd.DataFrame(columns=ROUTINE_COLS)
    out = pd.concat(parts, ignore_index=True)
    out["_order"] = out["ended"].astype(int)
    return out.sort_values(["user_id", "_order", "expected_per_week"], ascending=[True, True, False]) \
        .drop(columns="_order").reset_index(drop=True)
