"""Test setup: generate a small dataset into a temp folder and point the API at it."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "contract" / "schemas"


# Point the API at a temp folder now, before any test module imports `api.db` (it reads these at import),
# so no test can touch the developer's data/gopark.sqlite.
WORKDIR = Path(tempfile.mkdtemp(prefix="gopark-test-"))
(WORKDIR / "runs").mkdir()
os.environ["GOPARK_DATA"] = str(WORKDIR)
os.environ["GOPARK_DB"] = str(WORKDIR / "test.sqlite")
os.environ["GOPARK_RUNS"] = str(WORKDIR / "runs")
os.environ["GOPARK_SCHEDULER"] = "0"   # tests drive time-based rules with /dev/tick, not the 30 s loop


@pytest.fixture(scope="session")
def workdir() -> Path:
    d = WORKDIR
    subprocess.run([sys.executable, "-m", "tools.generator", "--users", "60", "--weeks", "12",
                    "--seed", "3", "--out", str(d)], cwd=ROOT, check=True, capture_output=True)
    return d


@pytest.fixture(scope="session")
def run_dir(workdir) -> Path:
    from rhythm.pipeline import run
    info = run(workdir / "bookings.csv", out_dir=workdir / "runs")
    return workdir / "runs" / info["run_id"]


@pytest.fixture(scope="session")
def client(workdir, run_dir):
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as c:
        c.post("/dev/reset")
        yield c


def schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))


# The starting fusion weights (before tuning), so ops tests don't move when config/fusion.yaml is retuned.
FIXED_FUSION = {
    "thresholds": {"auto_confirm": 0.95, "ask_driver": 0.60, "attendant_after_min": 10},
    "default_prior": 0.30, "max_prior": 0.99, "departure_prior": 0.30, "freshness_half_life_min": 15,
    "min_evidence_log_lr": 0.0, "min_stay_min": 5, "elsewhere_factor": 1.0, "strong_elsewhere_log_lr": 4.0,
    "camera_vehicle_max_probability": 0.94,
    "still_parked_max_min": 180, "overstay_usd_per_15_min": 1.0,
    "matching": {"before_start_min": 90, "after_end_min": 180, "fuzzy_below_conf": 0.8},
    "gate_pairing": {"window_s": 8, "resolve_window_min": 15},
    "signals": {"geofence": {"log_lr": 1.39}, "motion": {"log_lr": 2.08}, "ble": {"log_lr": 2.71},
                "nfc": {"log_lr": 4.6}, "camera_plate": {"log_lr": 3.91, "fuzzy_log_lr": 1.5},
                "camera_spot": {"log_lr": 1.1}, "camera_vehicle": {"log_lr": 3.0, "max_log_lr": 3.0,
                                  "margin_scale": 0.08, "min_score": 0.16},
                "gate_sensor": {"log_lr": 2.0}, "gate_qr": {"log_lr": 5.3}, "car_bt": {"log_lr": 2.08},
                "booking_qr": {"definitive": True}, "attendant": {"definitive": True},
                "driver_confirm": {"definitive": True}},
}


@pytest.fixture
def ops(client, monkeypatch):
    """Empty lots, fixed fusion weights, no C2 priors; restores the default demo DB afterwards."""
    from api.ops import fusion
    monkeypatch.setattr(fusion, "CONFIG", FIXED_FUSION)
    monkeypatch.setattr(fusion, "latest_priors", lambda: [])
    client.post("/dev/reset", params={"prepark": "false"})
    yield client
    client.post("/dev/reset")


def booking_row(lot_id: str | None = None, *, has_space: bool = False, skip: tuple = ()):
    """The earliest upcoming booking with a plate (optionally at a lot / with a space)."""
    from api import db
    conn = db.connect()
    try:
        q = "SELECT * FROM bookings WHERE status = 'booked' AND plate_norm IS NOT NULL"
        args: list = []
        if lot_id:
            q, args = q + " AND lot_id = ?", [lot_id]
        if has_space:
            q += " AND space_id IS NOT NULL"
        rows = conn.execute(q + " ORDER BY start_at, id", args).fetchall()
        return next(dict(r) for r in rows if r["id"] not in skip and r["plate_norm"] not in skip)
    finally:
        conn.close()


def at(ts: str, minutes: float = 0) -> str:
    """UTC DB time + minutes, as a local ISO string for the API."""
    from datetime import timedelta
    from api import db
    return (db.parse(ts) + timedelta(minutes=minutes)).astimezone(db.TZ).isoformat(timespec="seconds")
