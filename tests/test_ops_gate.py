"""Gate presence sensor (break-beam pair) + lane plate camera: pairing, unidentified cars, occupancy."""
from __future__ import annotations

import itertools
from datetime import timedelta

from api import db
from tests.conftest import at, booking_row

_ids = itertools.count()


def post(client, lot_id, source, kind, when, booking=None, **payload):
    sig = {"client_event_id": f"gate-{next(_ids):06d}", "device_id": f"gate-{lot_id}-1" if source == "gate_sensor"
           else f"cam-{lot_id}-gate", "lot_id": lot_id, "source": source, "kind": kind, "observed_at": when,
           "payload": payload}
    if booking:
        sig.update(booking_id=booking["id"], user_id=booking["user_id"])
    client.post("/dev/clock", json={"now": when})
    response = client.post("/signals", json={"signals": [sig]})
    assert response.status_code == 200, response.text


def sensor(client, lot_id, kind, when):
    post(client, lot_id, "gate_sensor", kind, when, gate_id=f"{lot_id}-1", lane=1)


def plate(client, b, kind, when, plate_norm=None):
    post(client, b["lot_id"], "camera_plate", kind, when, plate_norm=plate_norm or b["plate_norm"], ocr_conf=0.93,
         camera="gate", direction="in" if kind == "enter" else "out")


def tick(client, when):
    client.post("/dev/clock", json={"now": when})
    client.post("/dev/tick")


def seconds(when: str, s: float) -> str:
    return (db.parse(when) + timedelta(seconds=s)).isoformat()


def occupied(client, lot_id):
    return client.get(f"/lots/{lot_id}/overview").json()["counts"]["occupied"]


def stays(client, lot_id):
    return {s["id"]: s for s in client.get("/stays", params={"lot_id": lot_id}).json()}


def alerts(client, lot_id, kind="low_confidence"):
    return [a for a in client.get("/alerts", params={"lot_id": lot_id}).json() if a["type"] == kind]


def test_sensor_and_plate_read_pair_into_one_strong_identified_arrival(ops):
    b = booking_row()
    t = at(b["start_at"], -3)
    sensor(ops, b["lot_id"], "enter", t)
    plate(ops, b, "enter", seconds(t, 3))
    stay = stays(ops, b["lot_id"])[f"S-{b['id']}"]
    assert stay["state"] == "CHECKED_IN" and stay["in_conf"] > 0.99   # plate 3.91 + pairing 2.0 on a 0.30 prior
    evidence = ops.get(f"/stays/{stay['id']}/evidence").json()["evidence"]
    assert {e["source"] for e in evidence} == {"gate_sensor", "camera_plate"}
    tick(ops, seconds(t, 60))
    assert not alerts(ops, b["lot_id"]) and occupied(ops, b["lot_id"]) == 1
    assert not any(sid.startswith("G-") for sid in stays(ops, b["lot_id"]))


def test_plate_read_without_sensor_still_counts(ops):
    b = booking_row()
    plate(ops, b, "enter", at(b["start_at"], -3))
    tick(ops, at(b["start_at"], -2))
    stay = stays(ops, b["lot_id"])[f"S-{b['id']}"]
    assert stay["state"] == "CHECKED_IN" and 0.95 <= stay["in_conf"] < 0.99
    assert occupied(ops, b["lot_id"]) == 1 and not alerts(ops, b["lot_id"])


def test_sensor_only_entry_is_counted_as_unknown_car_until_identified(ops):
    b = booking_row()
    lot = b["lot_id"]
    t = at(b["start_at"], -3)
    sensor(ops, lot, "enter", t)
    assert occupied(ops, lot) == 0             # still inside the pairing window
    tick(ops, seconds(t, 10))
    unknown = [s for sid, s in stays(ops, lot).items() if sid.startswith("G-")]
    assert len(unknown) == 1 and unknown[0]["state"] == "CHECKED_IN" and unknown[0]["booking_id"] is None
    alert = alerts(ops, lot)
    assert len(alert) == 1 and alert[0]["stay_id"] == unknown[0]["id"] and "identity unknown" in alert[0]["message"]
    assert occupied(ops, lot) == 1

    # The attendant scans the car: it was this booking. The placeholder goes, the count stays 1.
    scan = ops.post("/scan", json={"lot_id": lot, "device_id": "att-1", "client_event_id": "gate-scan-0001",
                                   "booking_id": b["id"], "observed_at": seconds(t, 60)}).json()
    assert scan["result"] == "checked_in"
    assert stays(ops, lot)[unknown[0]["id"]]["state"] == "CANCELLED"
    assert not alerts(ops, lot) and occupied(ops, lot) == 1


def test_late_plate_upload_turns_an_unknown_entry_into_the_booking(ops):
    b = booking_row()
    t = at(b["start_at"], -3)
    sensor(ops, b["lot_id"], "enter", t)
    tick(ops, seconds(t, 30))
    assert occupied(ops, b["lot_id"]) == 1 and alerts(ops, b["lot_id"])
    ops.post("/dev/clock", json={"now": seconds(t, 90)})
    ops.post("/signals", json={"signals": [{
        "client_event_id": "gate-late-plate-01", "device_id": f"cam-{b['lot_id']}-gate", "lot_id": b["lot_id"],
        "source": "camera_plate", "kind": "enter", "observed_at": seconds(t, 2),
        "payload": {"plate_norm": b["plate_norm"], "ocr_conf": 0.9}}]})
    all_stays = stays(ops, b["lot_id"])
    assert all_stays[f"S-{b['id']}"]["state"] == "CHECKED_IN"
    assert [s["state"] for sid, s in all_stays.items() if sid.startswith("G-")] == ["CANCELLED"]
    assert not alerts(ops, b["lot_id"]) and occupied(ops, b["lot_id"]) == 1


def test_wrong_direction_is_not_paired(ops):
    b = booking_row()
    lot = b["lot_id"]
    t = at(b["start_at"], -3)
    sensor(ops, lot, "exit", t)
    plate(ops, b, "enter", seconds(t, 2))
    evidence = ops.get(f"/stays/S-{b['id']}/evidence").json()["evidence"]
    assert "gate_sensor" not in {e["source"] for e in evidence}
    tick(ops, seconds(t, 20))
    out = alerts(ops, lot)
    assert len(out) == 1 and out[0]["stay_id"] is None and out[0]["message"].startswith("Car out")
    assert occupied(ops, lot) == 0   # one identified car in, one unknown car out


def test_unidentified_exit_is_claimed_by_the_silent_departure(ops):
    b = booking_row()
    lot = b["lot_id"]
    plate(ops, b, "enter", at(b["start_at"], -3))
    sensor(ops, lot, "enter", seconds(at(b["start_at"], -3), 1))
    exit_at = at(b["end_at"], -4)
    sensor(ops, lot, "exit", exit_at)                       # plate not read on the way out
    tick(ops, at(b["end_at"], -3))
    assert occupied(ops, lot) == 0 and len(alerts(ops, lot)) == 1
    tick(ops, at(b["end_at"], 31))
    stay = stays(ops, lot)[f"S-{b['id']}"]
    assert stay["state"] == "CHECKED_OUT" and stay["inferred"] and stay["out_at"] == exit_at
    assert not alerts(ops, lot) and occupied(ops, lot) == 0
