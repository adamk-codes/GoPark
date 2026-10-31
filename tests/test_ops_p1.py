from __future__ import annotations

import json

import jsonschema

from tests.conftest import schema


def test_booking_qr_is_signed_and_tampering_is_rejected(client):
    from api import db

    public_key = client.get("/keys/qr").json()
    jsonschema.validate(public_key, schema("qr_public_key"))
    conn = db.connect()
    try:
        now = db.utc(db.now(conn))
        booking = conn.execute("SELECT id, lot_id FROM bookings WHERE status='booked' AND end_at > ? "
                               "ORDER BY start_at LIMIT 1", (now,)).fetchone()
    finally:
        conn.close()
    assert booking
    booking_id = booking["id"]
    response = client.get(f"/bookings/{booking_id}/qr")
    assert response.status_code == 200
    qr = response.json()
    jsonschema.validate(qr, schema("booking_qr"))

    valid = client.post("/scan", json={"lot_id": booking["lot_id"], "device_id": "test", "client_event_id": "qr-valid-0001",
                                       "qr": qr["qr"]}).json()
    assert valid["result"] != "invalid_qr"
    prefix, payload, signature = qr["qr"].split(".")
    tampered = f"{prefix}.{payload}.{('A' if signature[0] != 'A' else 'B')}{signature[1:]}"
    bad = client.post("/scan", json={"lot_id": booking["lot_id"], "device_id": "test", "client_event_id": "qr-bad-00001",
                                     "qr": tampered}).json()
    assert bad["result"] == "invalid_qr"


def test_walk_in_is_idempotent_and_creates_unbooked_stay(client):
    body = {"lot_id": "AUB", "plate": "Z 987654", "device_id": "attendant-1", "client_event_id": "walk-in-000001"}
    result = client.post("/walk_ins", json=body).json()
    jsonschema.validate(result, schema("scan_result"))
    assert result["result"] == "checked_in"
    assert result["stay"]["booking_id"] is None
    assert client.post("/walk_ins", json=body).json() == result


def test_gate_code_is_accepted_as_signal_and_rejects_bad_code(client):
    gate = client.get("/dev/gate/AUB").json()
    jsonschema.validate(gate, schema("gate_code"))
    now = client.get("/dev/clock").json()["now"]
    good = {"client_event_id": "gate-signal-0001", "device_id": "driver", "lot_id": "AUB",
            "source": "gate_qr", "kind": "scan", "observed_at": now, "payload": {"code": gate["code"]}}
    assert client.post("/signals", json=good).status_code == 200
    bad = {**good, "client_event_id": "gate-signal-0002", "payload": {"code": "GPG1.AUB.deadbeef"}}
    assert client.post("/signals", json=bad).status_code == 422


def test_persistent_alerts_can_be_resolved(client):
    rows = client.get("/alerts", params={"lot_id": "AUB"}).json()
    assert all("id" in row and "status" in row and "estimated_fee" in row for row in rows)
    if rows:
        alert = rows[0]
        jsonschema.validate(alert, schema("alert"))
        resolved = client.post(f"/alerts/{alert['id']}/resolve", json={"device_id": "attendant", "note": "checked"})
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "resolved"
        assert all(row["id"] != alert["id"] for row in client.get("/alerts", params={"lot_id": "AUB"}).json())


def test_sync_pull_supports_changed_rows_and_alerts(client):
    response = client.get("/sync/pull", params={"lot_id": "AUB", "since": "2000-01-01T00:00:00+00:00"})
    assert response.status_code == 200
    payload = response.json()
    assert {"cursor", "bookings", "stays", "alerts"} <= payload.keys()
    assert json.loads(json.dumps(payload)) == payload
