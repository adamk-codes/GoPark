"""Helpers shared by the stages: day names, circular clock statistics, switches."""
from __future__ import annotations

import numpy as np
import pandas as pd

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MINUTES_PER_DAY = 1440


def switch(cfg: dict, stage: str) -> bool:
    """Whether a stage is on (`stages: {<stage>: false}` in the config turns it off)."""
    return bool((cfg.get("stages") or {}).get(stage, True))


def circ_diff(a, b):
    """Signed difference a - b on a 24 h clock, in (-720, 720]."""
    return (np.asarray(a) - np.asarray(b) + 720) % MINUTES_PER_DAY - 720


def circ_stats(frame: pd.DataFrame, keys: list[str], minute: str = "minute",
               weight: str = "w") -> pd.DataFrame:
    """Weighted circular median (`center`) and circular std (`spread`) of `minute` per group.

    Times near midnight stay together (23:50 and 00:10 are 20 minutes apart). Vectorised:
    the reference point is the circular mean, offsets from it are ordinary numbers.
    """
    f = frame[keys + [minute, weight]].copy()
    ang = f[minute].to_numpy(float) / MINUTES_PER_DAY * 2 * np.pi
    w = f[weight].to_numpy(float)
    f["_s"], f["_c"] = np.sin(ang) * w, np.cos(ang) * w
    g = f.groupby(keys, sort=False)[["_s", "_c", weight]].sum()
    ref = (np.arctan2(g["_s"], g["_c"]) / (2 * np.pi) * MINUTES_PER_DAY) % MINUTES_PER_DAY
    f = f.join(ref.rename("_ref"), on=keys)
    f["_off"] = circ_diff(f[minute], f["_ref"])
    f["_wo2"] = f[weight] * f["_off"] ** 2
    f = f.sort_values(keys + ["_off"])
    f["_cw"] = f.groupby(keys, sort=False)[weight].cumsum()
    f = f.join(g[weight].rename("_tot"), on=keys)
    med = f[f["_cw"] >= f["_tot"] / 2].groupby(keys, sort=False).first()
    var = f.groupby(keys, sort=False)["_wo2"].sum() / g[weight].where(g[weight] > 0)
    out = pd.DataFrame({"center": (med["_ref"] + med["_off"]) % MINUTES_PER_DAY,
                        "spread": np.sqrt(var.reindex(med.index).fillna(0.0))})
    return out.reset_index()


def hhmm(minutes: float) -> str:
    m = int(round(minutes)) % MINUTES_PER_DAY
    return f"{m // 60:02d}:{m % 60:02d}"
