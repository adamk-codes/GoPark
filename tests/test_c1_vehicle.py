from __future__ import annotations

from datetime import datetime

import jsonschema
import numpy as np

from api import db
from api.ops.fusion import decide
from edge.camera.vehicle_matcher import VehicleMatcher, vehicle_prompt
from tests.conftest import FIXED_FUSION, booking_row, schema


class StubModel:
    def __init__(self, scores):
        self.scores = scores
        self.prompts = None

    def similarities(self, image_bgr, prompts):
        self.prompts = prompts
        return self.scores


def test_matcher_ranks_vehicle_profiles_and_reports_margin():
    expected = [
        {"booking_id": "B1", "user_id": "U1", "vehicle": {"color": "blue", "body": "suv", "make": "BMW", "model": None}, "p": 0.7},
        {"booking_id": "B2", "user_id": "U2", "vehicle": {"color": "white", "body": "sedan", "make": "Kia", "model": None}, "p": 0.6},
    ]
    model = StubModel([0.71, 0.42])
    result = VehicleMatcher(model, model_name="stub").match(np.zeros((8, 8, 3), dtype=np.uint8), expected)
    assert result is not None
    assert result.candidates[0]["booking_id"] == "B1"
    assert result.top_margin == 0.29
    assert "blue BMW suv" in model.prompts[0]
    jsonschema.validate(result.as_payload("stub"), schema("camera_vehicle_payload"))
    assert VehicleMatcher(model).match(np.zeros((0, 0, 3), dtype=np.uint8), expected) is None
    assert VehicleMatcher(model).match(np.zeros((8, 8, 3), dtype=np.uint8), []) is None


def test_vehicle_only_fusion_stays_below_auto_threshold():
    cfg = {**FIXED_FUSION, "camera_vehicle_max_probability": 0.94}
    signal = {"id": 12, "booking_id": "B1", "source": "camera_vehicle", "kind": "enter",
              "lot_id": "AUB", "observed_at": datetime.fromisoformat("2026-11-16T08:00:00+02:00"),
              "payload": {"top_margin": 0.8, "candidates": [
                  {"booking_id": "B1", "score": 0.95,
                   "vehicle": {"color": "blue", "body": "suv", "make": "BMW", "model": None}}
              ]}}
    result = decide(None, [signal], signal["observed_at"], 0.9, cfg)
    assert result["arrival_p"] == 0.94
    assert result["state"] == "ARRIVING"
    assert result["vehicle_suggestion"]["booking_id"] == "B1"
    assert all(point["probability"] < 0.95 for point in result["arrival_points"])


def test_vehicle_only_exit_does_not_auto_checkout():
    cfg = {**FIXED_FUSION, "camera_vehicle_max_probability": 0.94}
    now = datetime.fromisoformat("2026-11-16T08:30:00+02:00")
    signal = {"id": 13, "booking_id": "B1", "source": "camera_vehicle", "kind": "exit",
              "lot_id": "AUB", "observed_at": now,
              "payload": {"top_margin": 0.8, "candidates": [
                  {"booking_id": "B1", "score": 0.95,
                   "vehicle": {"color": "blue", "body": "suv", "make": "BMW", "model": None}}
              ]}}
    stay = {"state": "CHECKED_IN", "in_at": now.replace(minute=20), "inferred": False}
    result = decide(stay, [signal], now, 0.3, cfg)
    assert result["state"] == "CHECKED_IN"
    assert result["departure"] is None and result["departure_p"] <= 0.94


def test_expected_vehicles_and_camera_signal_are_contract_valid(ops):
    booking = booking_row("AUB")
    around = db.local(booking["start_at"])
    ops.post("/dev/clock", json={"now": around})
    conn = db.connect()
    try:
        conn.execute("UPDATE users SET vehicle_color='white', vehicle_body='sedan', vehicle_make='Kia' "
                     "WHERE id != ?", (booking["user_id"],))
        conn.execute("UPDATE users SET vehicle_color='beige', vehicle_body='van', vehicle_make='BMW' WHERE id=?",
                     (booking["user_id"],))
        conn.commit()
    finally:
        conn.close()
    response = ops.get("/lots/AUB/expected_vehicles", params={"around": around, "window_min": 20})
    assert response.status_code == 200, response.text
    candidates = response.json()
    assert candidates
    for candidate in candidates:
        jsonschema.validate(candidate, schema("expected_vehicle"))
    target = next(item for item in candidates if item["booking_id"] == booking["id"])
    payload = {"candidates": [{"booking_id": target["booking_id"], "score": 0.95,
                                          "vehicle": target["vehicle"]}],
               "top_margin": 0.75, "model": "stub"}
    jsonschema.validate(payload, schema("camera_vehicle_payload"))
    result = ops.post("/signals", json={"client_event_id": "vehicle-match-signal-001", "device_id": "cam-gate-1",
                                        "lot_id": "AUB", "source": "camera_vehicle", "kind": "enter",
                                        "observed_at": around, "payload": payload})
    assert result.status_code == 200, result.text
    rows = ops.get("/signals", params={"lot_id": "AUB", "source": "camera_vehicle", "limit": 10}).json()["signals"]
    stored = next(row for row in rows if row["client_event_id"] == "vehicle-match-signal-001")
    assert stored["matched_booking_id"] == booking["id"]
    assert stored["payload"]["candidates"][0]["vehicle"] == target["vehicle"]
    assert stored["payload"]["top_margin"] == 0.75
    assert stored["payload"]["appearance_count"] == 1
    stay = next((row for row in ops.get("/stays", params={"lot_id": "AUB"}).json()
                 if row["booking_id"] == booking["id"]), None)
    assert stay is not None and stay["state"] == "ARRIVING"
    suggestions = [alert for alert in ops.get("/alerts", params={"lot_id": "AUB"}).json()
                   if alert["booking_id"] == booking["id"] and alert["type"] == "low_confidence"]
    assert suggestions and "beige BMW van" in suggestions[0]["message"]


def test_camera_vehicle_rejects_non_expected_booking(ops):
    result = ops.post("/signals", json={"client_event_id": "vehicle-invalid-signal-001", "device_id": "cam-gate-1",
                                        "lot_id": "AUB", "source": "camera_vehicle", "kind": "enter",
                                        "observed_at": "2026-11-16T08:00:00+02:00",
                                        "payload": {"candidates": [{"booking_id": "B-NOT-EXPECTED", "score": 0.9,
                                                                        "vehicle": {"color": "blue", "body": "suv",
                                                                                    "make": "BMW"}}],
                                                    "top_margin": 0.5}})
    assert result.status_code == 422
