"""GET /signals: filtered, oldest first, polled with a cursor (the edge camera's --trigger api)."""
from __future__ import annotations

import jsonschema

from tests.conftest import at, booking_row, schema


def _gate(lot_id, n, kind, when):
    return {"client_event_id": f"list-gate-{n:04d}", "device_id": f"gate-{lot_id}-1", "lot_id": lot_id,
            "source": "gate_sensor", "kind": kind, "observed_at": when, "payload": {"gate_id": f"{lot_id}-1"}}


def test_signal_list_filters_and_cursor(ops):
    b = booking_row()
    lot = b["lot_id"]
    t0 = at(b["start_at"], -10)
    ops.post("/dev/clock", json={"now": t0})
    start = ops.get("/signals", params={"source": "gate_sensor", "lot_id": lot, "limit": 1}).json()
    jsonschema.validate(start, schema("signal_list"))
    assert start == {"signals": [], "cursor": 0}

    ops.post("/signals", json={"signals": [
        _gate(lot, 1, "enter", at(b["start_at"], -9)),
        {"client_event_id": "list-geo-0001", "device_id": "dev", "lot_id": lot, "booking_id": b["id"],
         "source": "geofence", "kind": "enter", "observed_at": at(b["start_at"], -8), "payload": {}},
        _gate(lot, 2, "exit", at(b["start_at"], -7))]})
    latest = ops.get("/signals", params={"source": "gate_sensor", "lot_id": lot, "limit": 1}).json()
    assert [s["kind"] for s in latest["signals"]] == ["exit"]          # newest only, to start polling from
    everything = ops.get("/signals", params={"lot_id": lot}).json()
    jsonschema.validate(everything, schema("signal_list"))
    assert [s["source"] for s in everything["signals"]] == ["gate_sensor", "geofence", "gate_sensor"]  # oldest first
    assert everything["signals"][0]["payload"] == {"gate_id": f"{lot}-1"}

    first = ops.get("/signals", params={"source": "gate_sensor", "lot_id": lot, "after_id": 0, "limit": 1}).json()
    assert [s["client_event_id"] for s in first["signals"]] == ["list-gate-0001"]
    rest = ops.get("/signals", params={"source": "gate_sensor", "lot_id": lot, "after_id": first["cursor"]}).json()
    assert [s["client_event_id"] for s in rest["signals"]] == ["list-gate-0002"]
    nothing = ops.get("/signals", params={"source": "gate_sensor", "lot_id": lot, "after_id": rest["cursor"]}).json()
    assert nothing == {"signals": [], "cursor": rest["cursor"]}

    since = ops.get("/signals", params={"lot_id": lot, "since": at(b["start_at"], -8)}).json()
    assert [s["source"] for s in since["signals"]] == ["geofence", "gate_sensor"]
    assert ops.get("/signals", params={"since": "yesterday"}).status_code == 422
