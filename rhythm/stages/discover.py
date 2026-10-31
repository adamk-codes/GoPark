"""Stage 3 — discover (ARCHITECTURE_PLAN.md §4.2 step 3).

Per user × lot, DBSCAN on start minute on a circular clock (eps ≈ 45 min, status weights as
sample weights). Each time cluster is a candidate routine; the habit stage decides which of
its weekdays are real. A user can have several clusters, at one lot or several.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.sparse.csgraph import connected_components

from rhythm.stages.common import MINUTES_PER_DAY


def circular_distances(minutes: np.ndarray) -> np.ndarray:
    d = np.abs(minutes[:, None] - minutes[None, :])
    return np.minimum(d, MINUTES_PER_DAY - d)


def dbscan_circular(minutes: np.ndarray, weights: np.ndarray, eps: float, min_samples: float) -> np.ndarray:
    """Weighted DBSCAN on a 24 h clock → labels (-1 = noise).

    Core points have ≥ `min_samples` total weight within `eps` (themselves included); core points
    within `eps` of each other share a cluster; border points join their nearest core point.
    Same result as sklearn's DBSCAN, without its per-call overhead (this runs ~500× per run).
    """
    near = circular_distances(minutes.astype(float)) <= eps
    core = near @ weights >= min_samples
    labels = np.full(len(minutes), -1)
    if not core.any():
        return labels
    ci = np.flatnonzero(core)
    _, comp = connected_components(near[np.ix_(ci, ci)], directed=False)
    labels[ci] = comp
    border = np.flatnonzero(~core & near[:, ci].any(axis=1))
    if len(border):
        d = circular_distances(minutes.astype(float))[np.ix_(border, ci)]
        labels[border] = comp[d.argmin(axis=1)]
    return labels


def discover(df: pd.DataFrame, cfg: dict, enabled: bool = True) -> pd.DataFrame:
    """Adds `cluster` (int, -1 = noise). Switched off: one cluster per user × lot."""
    eps, min_samples = cfg["discover"]["eps_minutes"], cfg["discover"]["min_samples"]
    if not enabled:
        return df.assign(cluster=0)
    labels = np.full(len(df), -1)
    minutes, weights = df["minute"].to_numpy(float), df["weight"].to_numpy(float)
    for ix in df.groupby(["user_id", "location_id"], sort=False).indices.values():
        if weights[ix].sum() >= min_samples:
            labels[ix] = dbscan_circular(minutes[ix], weights[ix], eps, min_samples)
    return df.assign(cluster=labels)
