from __future__ import annotations

import json

import jsonschema

from tests.conftest import FIXED_FUSION, schema


def _upcoming_booking(client):
    """Any lot's expected booking with no stay yet; the generated test data decides which lot has one."""
    for lot in client.get("/lots").json():
        active = {s["booking_id"] for s in client.get("/stays", params={"lot_id": lot["id"]}).json()}
        for item in client.get(f"/lots/{lot['id']}/overview").json()["expected_soon"]:
            if item["booking_id"] and item["booking_id"] not in active:
                return lot["id"], item["booking_id"]
    return None


def test_signal_batch_fuses_into_stay_with_saved_evidence(client, monkeypatch):
    from api.ops import fusion
    monkeypatch.setattr(fusion, "CONFIG", FIXED_FUSION)   # one geofence = ARRIVING under the starting weights
    found = _upcoming_booking(client)
    assert found, "test data has no upcoming booking in any lot"
    lot_id, booking_id = found
    now = client.get("/dev/clock").json()["now"]
    signal = {"client_event_id": "fusion-signal-0001", "device_id": "sensor-1", "lot_id": lot_id,
              "booking_id": booking_id, "source": "geofence", "kind": "enter", "observed_at": now}
    response = client.post("/signals", json={"signals": [signal]})
    assert response.status_code == 200
    stay = client.get("/stays", params={"lot_id": lot_id}).json()
    fused = next(row for row in stay if row["booking_id"] == booking_id)
    assert fused["state"] in {"ARRIVING", "CHECKED_IN"}
    evidence = client.get(f"/stays/{fused['id']}/evidence").json()
    jsonschema.validate(evidence, schema("stay_evidence"))
    assert evidence["evidence"]
    assert evidence["evidence"][0]["direction"] == "arrival"
    assert json.loads(json.dumps(evidence)) == evidence
