"""Stage 7 — calibrate (ARCHITECTURE_PLAN.md §4.2 step 7).

Slot scores (posterior mean × activity factor) are ranked well but not probabilities. Isotonic
regression maps them to "how often a slot with this score really got booked", fitted on
**validation** cutoffs only: every candidate slot of the week after each cutoff, labelled 1 if
the user kept a booking that day at that lot within ±`hit_tolerance_minutes`.

* `rhythm run` fits on the `calibrate.weeks` weekly cutoffs before its own cutoff (their outcomes
  are known by then);
* `rhythm evaluate` fits once on the validation split and applies it to the test split.

The fitted map is a small table of (score, probability) knots, so it is stored in `run.json`
and `metrics.json` and applied with linear interpolation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import isotonic_regression

from rhythm.stages.common import circ_diff

IDENTITY = {"x": [0.0, 1.0], "y": [0.0, 1.0]}


def label_slots(slots: pd.DataFrame, actual: pd.DataFrame, tol: float) -> np.ndarray:
    """1 where a kept booking matches the slot's user, day and lot with start within ±tol."""
    if slots.empty:
        return np.zeros(0)
    s = slots[["user_id", "day", "location_id", "minute"]].reset_index(drop=True).reset_index(names="si")
    m = s.merge(actual[["user_id", "day", "location_id", "minute"]], on=["user_id", "day", "location_id"],
                suffixes=("", "_a"))
    m = m[np.abs(circ_diff(m["minute"].to_numpy(float), m["minute_a"].to_numpy(float))) <= tol]
    y = np.zeros(len(s))
    y[m["si"].unique()] = 1.0
    return y


def fit(scores: np.ndarray, labels: np.ndarray, enabled: bool = True) -> dict:
    """Isotonic map score → probability as knots {x, y, n}. Identity when off or without data."""
    if not enabled or len(scores) < 20 or labels.min() == labels.max():
        return {**IDENTITY, "n": int(len(scores)), "fitted": False}
    # Pool equal scores first, then PAVA weighted by count; knots at both ends of each block.
    ux, inv, cnt = np.unique(np.asarray(scores, float), return_inverse=True, return_counts=True)
    mean_y = np.bincount(inv, weights=np.asarray(labels, float)) / cnt
    res = isotonic_regression(mean_y, weights=cnt.astype(float), increasing=True)
    fitted, b = np.clip(res.x, 0.0, 1.0), res.blocks
    xs, ys = [], []
    for lo, hi in zip(b[:-1], b[1:] - 1):
        xs += [ux[lo]] if lo == hi else [ux[lo], ux[hi]]
        ys += [fitted[lo]] * (1 if lo == hi else 2)
    return {"x": [round(float(v), 5) for v in xs], "y": [round(float(v), 5) for v in ys],
            "n": int(len(scores)), "fitted": True}


def apply(scores, model: dict) -> np.ndarray:
    return np.interp(np.asarray(scores, float), model["x"], model["y"])


def reliability(p: np.ndarray, y: np.ndarray, bins: int = 10) -> dict:
    """Brier score, expected calibration error and reliability-diagram bins (equal width)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    if len(p) == 0:
        return {"brier": None, "ece": None, "n": 0, "bins": []}
    idx = np.minimum((p * bins).astype(int), bins - 1)
    rows, ece = [], 0.0
    for b in range(bins):
        m = idx == b
        if not m.any():
            continue
        conf, acc = float(p[m].mean()), float(y[m].mean())
        ece += m.sum() / len(p) * abs(conf - acc)
        rows.append({"lo": b / bins, "hi": (b + 1) / bins, "mean_pred": round(conf, 4),
                     "observed": round(acc, 4), "n": int(m.sum())})
    return {"brier": round(float(np.mean((p - y) ** 2)), 4), "ece": round(float(ece), 4), "n": int(len(p)),
            "bins": rows}
