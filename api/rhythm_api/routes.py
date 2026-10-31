"""Rhythm (Challenge 2) API (CONTRACT.md §8). Owner: P3."""
from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from api.rhythm_api.store import read_run_file, run_bookings
from rhythm.io import ROOT, _flatten
from rhythm.pipeline import run as run_pipeline

router = APIRouter(prefix="/rhythm")


def _read(run_id: str, name: str):
    try:
        return read_run_file(run_id, name)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))


class RunIn(BaseModel):
    input: str | None = None
    cutoff: date | None = None


@router.post("/runs")
def create_run(body: RunIn):
    path = Path(body.input) if body.input else ROOT / "data" / "bookings.csv"
    if not path.exists():
        raise HTTPException(400, f"Input file {path} does not exist")
    return run_pipeline(path, body.cutoff)


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    return _read(run_id, "run.json")


@router.get("/runs/{run_id}/patterns")
def patterns(run_id: str, format: str = "json", user_id: str | None = None, state: str | None = None,
             min_conf: float | None = None, primary_only: bool = False):
    rows = _read(run_id, "patterns.json")
    if user_id:
        rows = [r for r in rows if r["user_id"] == user_id]
    if state:
        rows = [r for r in rows if r["state"] == state]
    if min_conf is not None:
        rows = [r for r in rows if r["confidence"] >= min_conf]
    if primary_only:
        rows = [r for r in rows if r["is_primary"]]
    if format == "csv":
        buf = io.StringIO()
        flat = [_flatten(r) for r in rows]
        if flat:
            w = csv.DictWriter(buf, fieldnames=list(flat[0]))
            w.writeheader()
            w.writerows(flat)
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="patterns.csv"'})
    return rows


@router.get("/runs/{run_id}/priors")
def priors(run_id: str, lot_id: str | None = None, date: str | None = None):
    rows = _read(run_id, "arrival_priors.json")
    return [r for r in rows if (not lot_id or r["lot_id"] == lot_id) and (not date or r["date"] == date)]


@router.get("/runs/{run_id}/forecast")
def forecast(run_id: str, lot_id: str | None = None, date: str | None = None):
    rows = _read(run_id, "lot_forecast.json")
    return [r for r in rows if (not lot_id or r["lot_id"] == lot_id) and (not date or r["date"] == date)]


@router.get("/runs/{run_id}/evaluation")
def evaluation(run_id: str):
    return _read(run_id, "metrics.json")


@router.get("/users/{user_id}")
def user_rhythm(user_id: str, run_id: str = "latest"):
    rows = [r for r in _read(run_id, "patterns.json") if r["user_id"] == user_id]
    df = run_bookings(run_id)
    ub = df[df["user_id"] == user_id]
    if not rows and ub.empty:
        raise HTTPException(404, f"Unknown user {user_id}")
    heat = [[0] * 24 for _ in range(7)]
    for ts in ub.loc[ub["status"] == "completed", "start_time"]:
        heat[ts.weekday()][ts.hour] += 1
    history = [{"booking_id": b.booking_id, "user_id": b.user_id, "location_id": b.location_id,
                "start_time": b.start_time.isoformat(), "end_time": b.end_time.isoformat(), "status": b.status,
                "created_at": b.created_at.isoformat() if "created_at" in ub.columns else None,
                "plate": getattr(b, "plate", None)}
               for b in ub.itertuples()]
    return {"user_id": user_id, "patterns": rows, "history": history, "heatmap": heat}


@router.post("/datasets")
def upload_dataset():
    raise HTTPException(501, "Dataset upload is not built yet (phase P3/P4).")
