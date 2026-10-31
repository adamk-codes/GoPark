"""Read access to rhythm run folders (runs/<run_id>/), cached by file modification time."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from rhythm.io import load_bookings, resolve_run


@lru_cache(maxsize=32)
def _read_json(path: str, mtime: float):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_run_file(run_id: str, name: str):
    path = resolve_run(run_id) / name
    if not path.exists():
        raise FileNotFoundError(f"{name} not found in run {run_id}")
    return _read_json(str(path), path.stat().st_mtime)


def latest_priors() -> list[dict]:
    """Arrival priors of the newest run, or [] if no run exists yet."""
    try:
        return read_run_file("latest", "arrival_priors.json")
    except FileNotFoundError:
        return []


@lru_cache(maxsize=4)
def _bookings(path: str, mtime: float):
    return load_bookings(path)


def run_bookings(run_id: str):
    info = read_run_file(run_id, "run.json")
    p = Path(info["input"])
    return _bookings(str(p), p.stat().st_mtime)
