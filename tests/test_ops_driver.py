"""Driver mode: one-tap confirm through fusion, pending confirmations, rewards ledger."""
from __future__ import annotations

import itertools

import jsonschema

from tests.conftest import at, booking_row, schema

_ids = itertools.count()


def post(client, lot_id, source, kind, when, booking=None, **payload):
    sig = {"client_event_id": f"drv-{next(_ids):06d}", "device_id": f"test-{source}", "lot_id": lot_id,
           "source": source, "kind": kind, "observed_at": when, "payload": payload}
    if booking:
        sig.update(booking_id=booking["id"], user_id=booking["user_id"])
    client.post("/dev/clock", json={"now": when})
    assert client.post("/signals", json={"signals": [sig]}).status_code == 200


def confirm(client, stay_id, answer, cid=None):
    response = client.post(f"/stays/{stay_id}/confirm", json={
        "device_id": "dev-driver", "client_event_id": cid or f"confirm-{next(_ids):06d}", "answer": answer})
    assert response.status_code == 200, response.text
    body = response.json()
    jsonschema.validate(body, schema("confirm_result"))
    return body


def arriving(client, b):
    post(client, b["lot_id"], "geofence", "enter", at(b["start_at"], -3), booking=b, accuracy_m=150)
    stay = next(s for s in client.get("/stays").json() if s["booking_id"] == b["id"])
    assert stay["state"] == "ARRIVING"
    return stay


def test_arrived_turns_an_arriving_stay_into_a_driver_check_in_with_points(ops):
    b = booking_row()
    stay = arriving(ops, b)
    pending = ops.get(f"/users/{b['user_id']}/pending_confirmations").json()
    assert [p["question"] for p in pending] == ["arrived"] and pending[0]["stay"]["id"] == stay["id"]
    jsonschema.validate(pending[0], schema("pending_confirmation"))

    first = confirm(ops, stay["id"], "arrived", cid="confirm-arrived-0001")
    assert first["result"] == "checked_in" and first["points_awarded"] >= 10
    assert first["stay"]["state"] == "CHECKED_IN" and first["stay"]["in_method"] == "driver"
    assert confirm(ops, stay["id"], "arrived", cid="confirm-arrived-0001") == first   # idempotent retry
    assert not ops.get(f"/users/{b['user_id']}/pending_confirmations").json()
    evidence = ops.get(f"/stays/{stay['id']}/evidence").json()["evidence"]
    assert "driver_confirm" in {e["source"] for e in evidence}

    rewards = ops.get(f"/users/{b['user_id']}/rewards").json()
    jsonschema.validate(rewards, schema("rewards"))
    assert rewards["points"] == sum(h["points"] for h in rewards["history"])
    assert "confirm_arrival" in {h["reason"] for h in rewards["history"]}
    assert {p["id"] for p in rewards["perks"]} >= {"grace_bank", "verified"}
    assert ops.get("/users/NOBODY/rewards").status_code == 404


def test_not_me_puts_the_stay_back_to_expected_with_an_alert_and_no_points(ops):
    b = booking_row()
    stay = arriving(ops, b)
    result = confirm(ops, stay["id"], "not_me")
    assert result["result"] == "rejected" and result["points_awarded"] == 0
    assert result["stay"]["state"] == "EXPECTED"
    alerts = [a for a in ops.get("/alerts").json() if a["booking_id"] == b["id"] and a["type"] == "low_confidence"]
    assert len(alerts) == 1
    # Real evidence later still checks the car in.
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], 5), plate_norm=b["plate_norm"], ocr_conf=0.95)
    post(ops, b["lot_id"], "motion", "park", at(b["start_at"], 6), booking=b)
    assert next(s for s in ops.get("/stays").json() if s["booking_id"] == b["id"])["state"] == "CHECKED_IN"


def test_not_me_on_an_auto_check_in_is_disputed(ops):
    b = booking_row()
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], -3), plate_norm=b["plate_norm"], ocr_conf=0.95)
    result = confirm(ops, f"S-{b['id']}", "not_me")
    assert result["result"] == "disputed" and result["stay"]["state"] == "CHECKED_IN"
    assert any(a["type"] == "anomaly" and a["booking_id"] == b["id"] for a in ops.get("/alerts").json())


def test_left_checks_out_an_unconfirmed_departure(ops):
    b = booking_row()
    post(ops, b["lot_id"], "camera_plate", "enter", at(b["start_at"], -3), plate_norm=b["plate_norm"], ocr_conf=0.95)
    post(ops, b["lot_id"], "motion", "unpark", at(b["end_at"], -2), booking=b)
    ops.post("/dev/clock", json={"now": at(b["end_at"], 5)})
    ops.post("/dev/tick")
    pending = ops.get(f"/users/{b['user_id']}/pending_confirmations").json()
    assert [p["question"] for p in pending] == ["left"]
    result = confirm(ops, f"S-{b['id']}", "left")
    assert result["result"] == "checked_out" and result["points_awarded"] >= 10   # the unpark agreed
    assert result["stay"]["out_method"] == "driver" and not result["stay"]["inferred"]
    rewards = ops.get(f"/users/{b['user_id']}/rewards").json()
    assert rewards["grace_minutes"] == 5
    assert next(p for p in rewards["perks"] if p["id"] == "grace_bank")["unlocked"]
