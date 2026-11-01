"""Fusion P2: camera attribution, departures, arrival elsewhere, contradictions, low confidence."""
from __future__ import annotations

import itertools

from tests.conftest import at, booking_row

_ids = itertools.count()


def post(client, lot_id, source, kind, when, booking=None, now=None, **payload):
    sig = {"client_event_id": f"dep-{next(_ids):06d}", "device_id": f"test-{source}", "lot_id": lot_id,
           "source": source, "kind": kind, "observed_at": when, "payload": payload}
    if booking:
        sig.update(booking_id=booking["id"], user_id=booking["user_id"])
    client.post("/dev/clock", json={"now": now or when})
    response = client.post("/signals", json={"signals": [sig]})
    assert response.status_code == 200, response.text
    return response.json()


def stay_of(client, booking_id):
    return next((s for s in client.get("/stays").json() if s["booking_id"] == booking_id), None)


def open_alerts(client, booking_id, kind):
    return [a for a in client.get("/alerts").json() if a["booking_id"] == booking_id and a["type"] == kind]


def test_plate_camera_attributes_without_booking_id_and_exit_checks_out(ops):
    b = booking_row()
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], -3), plate_norm=b["plate_norm"], ocr_conf=0.95)
    post(ops, b["lot_id"], "motion", "park", at(b["start_at"], -2), booking=b)
    stay = stay_of(ops, b["id"])
    assert stay["state"] == "CHECKED_IN" and stay["in_method"] == "auto" and stay["in_conf"] >= 0.95

    post(ops, b["lot_id"], "motion", "unpark", at(b["end_at"], -4), booking=b)
    assert stay_of(ops, b["id"])["state"] == "CHECKED_IN"   # unpark alone is not enough
    post(ops, b["lot_id"], "camera_plate", "exit", at(b["end_at"], -3), plate_norm=b["plate_norm"], ocr_conf=0.9)
    post(ops, b["lot_id"], "geofence", "exit", at(b["end_at"], -2), booking=b, accuracy_m=10)
    stay = stay_of(ops, b["id"])
    assert stay["state"] == "CHECKED_OUT" and stay["out_method"] == "auto" and not stay["inferred"]
    assert stay["out_at"] == at(b["end_at"], -4)   # the unpark, first exit-like signal of the burst
    evidence = ops.get(f"/stays/{stay['id']}/evidence").json()["evidence"]
    assert {e["direction"] for e in evidence} == {"arrival", "departure"}


def test_misread_plate_matches_with_one_wrong_character_only_at_low_confidence(ops):
    b = booking_row()
    plate = b["plate_norm"]
    wrong = plate[:-1] + str((int(plate[-1]) + 1) % 10)
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], -3), plate_norm=wrong, ocr_conf=0.95)
    assert stay_of(ops, b["id"]) is None   # confident read of another plate: not this car
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], -2), plate_norm=wrong, ocr_conf=0.6)
    post(ops, b["lot_id"], "motion", "park", at(b["start_at"], -1), booking=b)
    stay = stay_of(ops, b["id"])
    assert stay is not None and stay["state"] in ("ARRIVING", "CHECKED_IN")


def test_spot_camera_adds_occupancy_evidence_by_space(ops):
    b = booking_row(has_space=True)
    lot = b["lot_id"]
    post(ops, lot, "geofence", "enter", at(b["start_at"], -3), booking=b, accuracy_m=200)
    assert stay_of(ops, b["id"])["state"] == "ARRIVING"   # 0.30 prior + geofence = 0.63
    post(ops, lot, "camera_spot", "occupied", at(b["start_at"], -1), space_id=b["space_id"])
    post(ops, lot, "motion", "park", at(b["start_at"], 0), booking=b)
    assert stay_of(ops, b["id"])["state"] == "CHECKED_IN"
    evidence = ops.get(f"/stays/S-{b['id']}/evidence").json()["evidence"]
    assert "camera_spot" in {e["source"] for e in evidence}


def test_silent_end_goes_unconfirmed_then_inferred_checkout_at_booking_end(ops):
    b = booking_row()
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], -3), plate_norm=b["plate_norm"], ocr_conf=0.95)
    post(ops, b["lot_id"], "motion", "park", at(b["start_at"], -2), booking=b)
    ops.post("/dev/clock", json={"now": at(b["end_at"], 5)})
    ops.post("/dev/tick")
    assert stay_of(ops, b["id"])["state"] == "UNCONFIRMED_DEPARTURE"
    assert open_alerts(ops, b["id"], "unconfirmed_departure")
    ops.post("/dev/clock", json={"now": at(b["end_at"], 31)})
    ops.post("/dev/tick")
    stay = stay_of(ops, b["id"])
    assert stay["state"] == "CHECKED_OUT" and stay["inferred"] and stay["out_method"] == "inferred"
    assert stay["out_at"] == at(b["end_at"])
    assert not open_alerts(ops, b["id"], "unconfirmed_departure")

    # A late exit signal (overstayer whose phone reports late) corrects the inferred time.
    post(ops, b["lot_id"], "camera_plate", "exit", at(b["end_at"], 40), plate_norm=b["plate_norm"], ocr_conf=0.9)
    post(ops, b["lot_id"], "motion", "unpark", at(b["end_at"], 40), booking=b)
    stay = stay_of(ops, b["id"])
    assert stay["out_at"] == at(b["end_at"], 40) and not stay["inferred"] and stay["out_method"] == "auto"


def test_arrival_elsewhere_closes_the_old_stay(ops):
    b = booking_row()
    other = next(lot["id"] for lot in ops.get("/lots").json() if lot["id"] != b["lot_id"])
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], -3), plate_norm=b["plate_norm"], ocr_conf=0.95)
    post(ops, b["lot_id"], "motion", "park", at(b["start_at"], -2), booking=b)
    assert stay_of(ops, b["id"])["state"] == "CHECKED_IN"
    post(ops, other, "camera_plate", "enter", at(b["start_at"], 60), plate_norm=b["plate_norm"], ocr_conf=0.95)
    stay = stay_of(ops, b["id"])
    assert stay["state"] == "CHECKED_OUT" and stay["inferred"] and stay["out_at"] == at(b["start_at"], 60)
    actions = [a["action"] for a in ops.get("/audit", params={"limit": 20}).json() if a["entity_id"] == stay["id"]]
    assert "closed_by_arrival_elsewhere" in actions


def test_strong_evidence_at_two_lots_is_an_anomaly_and_never_auto_confirmed(ops):
    b = booking_row()
    other = next(lot["id"] for lot in ops.get("/lots").json() if lot["id"] != b["lot_id"])
    post(ops, other, "nfc", "scan", at(b["start_at"], -4), booking=b)
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], -3), plate_norm=b["plate_norm"], ocr_conf=0.95)
    post(ops, b["lot_id"], "motion", "park", at(b["start_at"], -2), booking=b)
    post(ops, b["lot_id"], "geofence", "enter", at(b["start_at"], -2), booking=b, accuracy_m=10)
    stay = stay_of(ops, b["id"])
    assert stay is None or stay["state"] == "ARRIVING"
    alerts = open_alerts(ops, b["id"], "anomaly")
    assert len(alerts) == 1 and alerts[0]["severity"] == "critical" and other in alerts[0]["message"]


def test_weak_evidence_raises_low_confidence_after_ten_minutes(ops):
    b = booking_row()
    post(ops, b["lot_id"], "camera_spot", "occupied", at(b["start_at"], -3), space_id="nowhere")  # unmatched
    # A geofence enter delivered 5 min late has decayed to P = 0.56 < 0.60: no stay yet.
    post(ops, b["lot_id"], "geofence", "enter", at(b["start_at"], -8), booking=b, now=at(b["start_at"], -3),
         accuracy_m=300)
    assert stay_of(ops, b["id"]) is None
    ops.post("/dev/clock", json={"now": at(b["start_at"], 1)})
    ops.post("/dev/tick")
    assert not open_alerts(ops, b["id"], "low_confidence")
    ops.post("/dev/clock", json={"now": at(b["start_at"], 3)})
    ops.post("/dev/tick")
    assert len(open_alerts(ops, b["id"], "low_confidence")) == 1
    # Strong evidence later clears it.
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], 9), plate_norm=b["plate_norm"], ocr_conf=0.95)
    post(ops, b["lot_id"], "motion", "park", at(b["start_at"], 10), booking=b)
    assert stay_of(ops, b["id"])["state"] == "CHECKED_IN"
    assert not open_alerts(ops, b["id"], "low_confidence")
