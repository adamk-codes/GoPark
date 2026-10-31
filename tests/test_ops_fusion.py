from __future__ import annotations

import json

import jsonschema

from tests.conftest import schema


def test_signal_batch_fuses_into_stay_with_saved_evidence(client):
    overview = client.get("/lots/AUB/overview").json()
    active = {stay["booking_id"] for stay in client.get("/stays", params={"lot_id": "AUB"}).json()}
    candidates = [item["booking_id"] for item in overview["expected_soon"]
                  if item["booking_id"] and item["booking_id"] not in active]
    assert candidates
    booking_id = candidates[0]
    now = client.get("/dev/clock").json()["now"]
    signal = {"client_event_id": "fusion-signal-0001", "device_id": "sensor-1", "lot_id": "AUB",
              "booking_id": booking_id, "source": "geofence", "kind": "enter", "observed_at": now}
    response = client.post("/signals", json={"signals": [signal]})
    assert response.status_code == 200
    stay = client.get("/stays", params={"lot_id": "AUB"}).json()
    fused = next(row for row in stay if row["booking_id"] == booking_id)
    assert fused["state"] in {"ARRIVING", "CHECKED_IN"}
    evidence = client.get(f"/stays/{fused['id']}/evidence").json()
    jsonschema.validate(evidence, schema("stay_evidence"))
    assert evidence["evidence"]
    assert evidence["evidence"][0]["direction"] == "arrival"
    assert json.loads(json.dumps(evidence)) == evidence
