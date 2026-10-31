"""Loading bookings/config and writing run outputs (CONTRACT.md §8)."""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
TZ = "Asia/Beirut"
REQUIRED = ["booking_id", "user_id", "location_id", "start_time", "end_time", "status"]


def load_config() -> dict:
    return yaml.safe_load((ROOT / "config" / "rhythm.yaml").read_text(encoding="utf-8"))


def load_bookings(path: str | Path) -> pd.DataFrame:
    """Read a bookings CSV/JSON into a frame with local-time datetimes."""
    path = Path(path)
    df = pd.read_json(path) if path.suffix == ".json" else pd.read_csv(path, dtype=str)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        raise ValueError(f"bookings file is missing columns: {', '.join(missing)}")
    df["status"] = df["status"].str.strip().str.lower()
    for col in ("start_time", "end_time", "created_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601").dt.tz_convert(TZ)
    return df.drop_duplicates("booking_id").sort_values("start_time").reset_index(drop=True)


def new_run_id() -> str:
    return "r" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def runs_dir() -> Path:
    return Path(os.environ.get("GOPARK_RUNS", ROOT / "runs"))


def resolve_run(run_id: str) -> Path:
    if run_id == "latest":
        latest = runs_dir() / "latest.txt"
        if not latest.exists():
            raise FileNotFoundError("no runs yet; run `python -m rhythm run` first")
        run_id = latest.read_text(encoding="utf-8").strip()
    path = runs_dir() / run_id
    if not path.is_dir():
        raise FileNotFoundError(f"run {run_id} not found")
    return path


NESTED = {"time_window": ("center", "spread_min"), "next_booking": ("at", "location_id"),
          "reminder": ("recommend", "tier", "at")}


def _flatten(row: dict) -> dict:
    """One CSV row; nested objects become `<field>_<key>` with the same columns even when null."""
    flat = {}
    for k, v in row.items():
        if k in NESTED:
            for k2 in NESTED[k]:
                flat[f"{k}_{k2}"] = (v or {}).get(k2)
        elif isinstance(v, list):
            flat[k] = "/".join(map(str, v))
        else:
            flat[k] = v
    return flat


def write_run(out_dir: Path, run_info: dict, patterns: list[dict], priors: list[dict],
              forecast: list[dict]) -> Path:
    run_path = out_dir / run_info["run_id"]
    run_path.mkdir(parents=True, exist_ok=True)

    def dump(name, obj):
        (run_path / name).write_text(json.dumps(obj, indent=1, default=str), encoding="utf-8")

    dump("run.json", run_info)
    dump("patterns.json", patterns)
    dump("arrival_priors.json", priors)
    dump("lot_forecast.json", forecast)
    flat = [_flatten(p) for p in patterns]
    if flat:
        with open(run_path / "patterns.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(flat[0]))
            w.writeheader()
            w.writerows(flat)
    (out_dir / "latest.txt").write_text(run_info["run_id"], encoding="utf-8")
    return run_path
