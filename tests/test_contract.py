"""Every producer must emit shapes that match contract/schemas. Run: uv run pytest"""
from __future__ import annotations

import csv
import json

import jsonschema
import pytest

from tests.conftest import schema


def _validate_all(rows, name):
    validator = jsonschema.Draft202012Validator(schema(name))
    for row in rows:
        validator.validate(row)


def test_generator_bookings_match_schema(workdir):
    with open(workdir / "bookings.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows
    _validate_all(rows[:500], "booking")
    assert {r["status"] for r in rows} <= {"booked", "completed", "cancelled", "no_show"}


def test_generator_is_deterministic(workdir, tmp_path):
    import subprocess
    import sys
    from tests.conftest import ROOT
    subprocess.run([sys.executable, "-m", "tools.generator", "--users", "60", "--weeks", "12", "--seed", "3",
                    "--out", str(tmp_path)], cwd=ROOT, check=True, capture_output=True)
    assert (tmp_path / "bookings.csv").read_bytes() == (workdir / "bookings.csv").read_bytes()


@pytest.mark.parametrize("file,name", [("patterns.json", "pattern_row"),
                                       ("arrival_priors.json", "arrival_prior"),
                                       ("lot_forecast.json", "lot_forecast")])
def test_rhythm_outputs_match_schema(run_dir, file, name):
    rows = json.loads((run_dir / file).read_text(encoding="utf-8"))
    assert rows
    _validate_all(rows, name)


def test_one_primary_row_per_user(run_dir):
    rows = json.loads((run_dir / "patterns.json").read_text(encoding="utf-8"))
    primaries = [r["user_id"] for r in rows if r["is_primary"]]
    assert len(primaries) == len(set(primaries)) == len({r["user_id"] for r in rows})


def test_overview_and_scan_match_schema(client):
    ov = client.get("/lots/AUB/overview").json()
    jsonschema.validate(ov, schema("lot_overview"))
    booking = next(e for e in ov["expected_soon"] if e["booking_id"])
    body = {"lot_id": "AUB", "device_id": "dev-t", "client_event_id": "t-1", "booking_id": booking["booking_id"]}
    first = client.post("/scan", json=body).json()
    jsonschema.validate(first, schema("scan_result"))
    assert first["result"] == "checked_in"
    assert client.post("/scan", json=body).json() == first          # retry is idempotent
    again = client.post("/scan", json={**body, "client_event_id": "t-2"}).json()
    assert again["result"] == "already_in"                           # double scan is not a check-out


def test_unknown_plate_suggests_walk_in(client):
    r = client.post("/scan", json={"lot_id": "AUB", "device_id": "d", "plate": "Z 0"}).json()
    assert r["result"] == "not_found" and r["suggest"] == "register_walk_in"


def test_signals_are_idempotent(client):
    sig = {"client_event_id": "sig-test-0001", "device_id": "d", "lot_id": "AUB", "source": "geofence",
           "kind": "enter", "observed_at": "2026-11-16T07:55:00+02:00"}
    jsonschema.validate(sig, schema("signal"))
    assert client.post("/signals", json={"signals": [sig, sig]}).json() == {"accepted": 1, "duplicates": 1}


def test_rhythm_endpoints(client):
    rows = client.get("/rhythm/runs/latest/patterns", params={"primary_only": True}).json()
    assert rows
    user = client.get(f"/rhythm/users/{rows[0]['user_id']}").json()
    assert len(user["heatmap"]) == 7 and len(user["heatmap"][0]) == 24
    assert client.get("/rhythm/runs/latest/patterns", params={"format": "csv"}).text.startswith("user_id,")
