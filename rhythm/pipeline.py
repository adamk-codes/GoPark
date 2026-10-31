"""Run the pattern engine end to end and write a run folder.

P0: delegates to baseline B2. P3 (Rhythm owner) replaces `_patterns` with the
full pipeline from ARCHITECTURE_PLAN.md §4.2 without changing the outputs' shape.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from rhythm import baselines
from rhythm.io import load_bookings, load_config, new_run_id, runs_dir, write_run


def default_cutoff(df: pd.DataFrame) -> date:
    """Last day that has finished (non-upcoming) bookings."""
    past = df[df["status"] != "booked"]
    return past["start_time"].max().date()


def _patterns(df: pd.DataFrame, cutoff: date, cfg: dict, run_id: str) -> list[dict]:
    hist = df[(df["start_time"].dt.date <= cutoff) & (df["status"] != "booked")]
    return [baselines.user_patterns(uid, ub, cutoff, cfg, run_id) for uid, ub in hist.groupby("user_id")]


def run(input_path: str | Path, cutoff: date | None = None, out_dir: Path | None = None) -> dict:
    cfg = load_config()
    df = load_bookings(input_path)
    cutoff = cutoff or default_cutoff(df)
    run_id = new_run_id()
    patterns = _patterns(df, cutoff, cfg, run_id)
    priors, forecast = baselines.priors_and_forecast(patterns, cutoff)
    info = {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cutoff": cutoff.isoformat(), "input": str(input_path),
            "model_version": baselines.MODEL_VERSION, "params": cfg,
            "counts": {"users": len(patterns), "recurring": sum(p["pattern_type"] == "recurring" for p in patterns),
                       "priors": len(priors)}}
    write_run(out_dir or runs_dir(), info, patterns, priors, forecast)
    return info
