"""Stage 5 — drift (ARCHITECTURE_PLAN.md §4.2 step 5).

Two parts:

* `change_points` (runs *before* the habit test): every cluster × weekday is a series of exposed
  days (hit = status weight). CUSUM picks the most likely change point (argmax |S_k|, S_k = running
  sum of x − mean); a two-segment Bernoulli likelihood-ratio test decides whether it is real
  (LR ≥ `min_lr` and the rate moved by ≥ `min_shift`). Applied twice (the second time on the
  newer segment), so a fade followed by a stop still ends up with the latest regime. The habit
  test then scores only the current regime: a new routine isn't diluted by the months before it
  started, and an old one stops counting once it has visibly ended.
* `label_states` (runs *after* the habit test): each routine becomes `emerging | active | fading
  | ended` with a `change_date`, and a user whose routine ended while another one started
  around the same time is flagged as a routine switch.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SERIES = ["user_id", "location_id", "cluster", "weekday"]
REGIME_COLS = SERIES + ["regime_start", "direction", "pre_rate", "post_rate", "change_lr"]


def _ll(n: np.ndarray, s: np.ndarray) -> np.ndarray:
    """Bernoulli log-likelihood of `s` (fractional) successes in `n` trials at the MLE rate."""
    p = np.clip(np.divide(s, n, out=np.zeros_like(s, dtype=float), where=n > 0), 1e-9, 1 - 1e-9)
    return s * np.log(p) + (n - s) * np.log1p(-p)


def _one_pass(t: pd.DataFrame, min_seg: int, min_down: int, min_lr: float, min_shift: float) -> pd.DataFrame:
    """Best single change point per series of `t` (sorted by series, day). → one row per series
    that has a significant change: [SERIES, regime_start, direction, pre_rate, post_rate, change_lr]."""
    g = t.groupby(SERIES, sort=False)
    x = t["hit"].to_numpy(float)
    n1 = g.cumcount().to_numpy() + 1
    size = g["hit"].transform("size").to_numpy()
    s1 = g["hit"].cumsum().to_numpy()
    tot = g["hit"].transform("sum").to_numpy()
    n2, s2 = size - n1, tot - s1
    cusum = np.abs(s1 - n1 * tot / size)
    ok = (n1 >= min_seg) & (n2 >= min_seg)
    f = pd.DataFrame({"cusum": np.where(ok, cusum, -1.0)}, index=t.index)
    best = f.groupby([t[c] for c in SERIES], sort=False)["cusum"].idxmax()
    best = best[f.loc[best.to_numpy(), "cusum"].to_numpy() > 0]
    if best.empty:
        return pd.DataFrame(columns=REGIME_COLS)
    i = t.index.get_indexer(best.to_numpy())
    lr = 2 * (_ll(n1[i], s1[i]) + _ll(n2[i], s2[i]) - _ll(size[i], tot[i]))
    pre, post = s1[i] / n1[i], s2[i] / n2[i]
    out = pd.DataFrame({c: t[c].to_numpy()[i] for c in SERIES})
    out["regime_start"] = t["day"].to_numpy()[i + 1]          # first day of the newer segment
    out["direction"] = np.sign(post - pre).astype(int)
    out["pre_rate"], out["post_rate"], out["change_lr"] = pre, post, lr
    # A drop needs a longer newer segment than a rise: three unlucky misses shouldn't end a day.
    keep = (lr >= min_lr) & (np.abs(post - pre) >= min_shift) & ((post > pre) | (n2[i] >= min_down))
    return out[keep].reset_index(drop=True)


def change_points(t: pd.DataFrame, cfg: dict, enabled: bool = True) -> pd.DataFrame:
    """`t`: one row per cluster × exposed day [SERIES, day, hit]. → latest significant change per
    series (series without one are absent). Switched off: no change points (whole history = one regime)."""
    d = cfg["drift"]
    if not enabled or t.empty:
        return pd.DataFrame(columns=REGIME_COLS)
    t = t.sort_values(SERIES + ["day"]).reset_index(drop=True)
    args = (d["min_segment"], d.get("min_segment_down", d["min_segment"]), d["min_lr"], d["min_shift"])
    found = _one_pass(t, *args)
    for _ in range(d.get("passes", 2) - 1):
        if found.empty:
            break
        newer = t.merge(found[SERIES + ["regime_start"]], on=SERIES)
        newer = newer[newer["day"] >= newer["regime_start"]].drop(columns="regime_start").reset_index(drop=True)
        again = _one_pass(newer, *args)
        if again.empty:
            break
        found = pd.concat([found.set_index(SERIES).drop(again.set_index(SERIES).index, errors="ignore"),
                           again.set_index(SERIES)]).reset_index()
    return found[REGIME_COLS]


def label_states(routines: pd.DataFrame, cutoff, cfg: dict, enabled: bool = True) -> pd.DataFrame:
    """Adds `state`, `change_date` and `switch_with` (index of the routine it replaced / was replaced by).

    * ended: none of the routine's days is still on now; they were before a downward change.
    * emerging: most of its days started recently (upward change within `emerging_weeks`), or the
      routine itself has fewer than `emerging_min_weeks` weeks of history (new users).
    * fading: most of its days had a downward change that left them still above the habit bar,
      or the last `recent_weeks` weeks hit less than `fade_ratio` × the regime's rate.
    * active: otherwise.
    """
    d = cfg["drift"]
    r = routines.copy()
    if r.empty:
        return r.assign(state=pd.Series(dtype=object), change_date=pd.Series(dtype="datetime64[ns]"),
                        switch_with=pd.Series(dtype=float))
    end = pd.Timestamp(cutoff)
    recent_start = end - pd.Timedelta(weeks=d["emerging_weeks"])
    young = r["distinct_weeks"] < d["emerging_min_weeks"]
    emerging = (r["up_days"] * 2 >= r["n_days"]) & (r["up_since"] >= recent_start)
    fading = (r["down_days"] * 2 >= r["n_days"]) | (
        (r["regime_weeks"] >= 2 * d["recent_weeks"]) & (r["recent_slots"] > 0)
        & (r["recent_rate"] < d["fade_ratio"] * r["regime_rate"]))
    if not enabled:
        emerging, fading = pd.Series(False, index=r.index), pd.Series(False, index=r.index)
    r["state"] = np.select([r["ended"].astype(bool), emerging | young, fading],
                           ["ended", "emerging", "fading"], "active")
    up, down = pd.to_datetime(r["up_since"]), pd.to_datetime(r["down_since"])
    # ended / fading: when it dropped; emerging: when it started; active: its last upward change, if any.
    change = up.copy()
    st = r["state"]
    change[st == "emerging"] = up.fillna(pd.to_datetime(r["first_day"]))[st == "emerging"]
    change[st == "fading"] = down.fillna(end - pd.Timedelta(weeks=d["recent_weeks"]))[st == "fading"]
    change[st == "ended"] = down[st == "ended"]
    r["change_date"] = change
    # Routine switch: an ended routine and a new (emerging / recently started) one of the same user
    # whose change dates are within `switch_window_days` of each other.
    r["switch_with"] = np.nan
    old = r.loc[r["state"] == "ended", ["user_id", "change_date"]].reset_index(names="old")
    new = r.loc[(r["state"] != "ended") & r["up_since"].notna(), ["user_id", "up_since"]].reset_index(names="new")
    pairs = old.merge(new, on="user_id")
    pairs["gap"] = (pd.to_datetime(pairs["up_since"]) - pairs["change_date"]).abs()
    pairs = pairs[pairs["gap"] <= pd.Timedelta(days=d["switch_window_days"])].sort_values("gap")
    pairs = pairs.drop_duplicates("old").drop_duplicates("new")
    r.loc[pairs["old"].to_numpy(), "switch_with"] = pairs["new"].to_numpy()
    r.loc[pairs["new"].to_numpy(), "switch_with"] = pairs["old"].to_numpy()
    return r
