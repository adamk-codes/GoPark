"""WS /live?lot_id= pushes {type: "stay"|"alert", data} when stays or alerts change."""
from __future__ import annotations

import jsonschema

from tests.conftest import at, booking_row, schema


def _signal(b, cid, source, kind, when, **payload):
    return {"client_event_id": cid, "device_id": "live-test", "lot_id": b["lot_id"], "source": source,
            "kind": kind, "observed_at": when, "payload": payload}


def _until(ws, done, limit=200) -> list[dict]:
    """Messages received up to and including the first one that satisfies `done`."""
    seen = []
    while len(seen) < limit:
        seen.append(ws.receive_json())
        if done(seen[-1]):
            return seen
    raise AssertionError(f"no matching message in {limit}")


def test_live_pushes_stay_and_alert_changes_for_its_lot_only(ops):
    b = booking_row()
    other = next(lot["id"] for lot in ops.get("/lots").json() if lot["id"] != b["lot_id"])
    when = at(b["start_at"], -3)
    ops.post("/dev/clock", json={"now": when})
    with ops.websocket_connect(f"/live?lot_id={other}") as elsewhere, \
            ops.websocket_connect(f"/live?lot_id={b['lot_id']}") as ws:
        ops.post("/signals", json={"signals": [
            _signal(b, "live-0001", "camera_plate", "enter", when, plate_norm=b["plate_norm"], ocr_conf=0.95)]})
        message = _until(ws, lambda m: m["type"] == "stay")[-1]
        assert message["data"]["booking_id"] == b["id"] and message["data"]["state"] == "CHECKED_IN"

        ops.post("/dev/clock", json={"now": at(b["end_at"], 5)})
        ops.post("/dev/tick")
        # The tick moves the stay to UNCONFIRMED_DEPARTURE and opens an alert (other lot alerts may come too).
        seen = _until(ws, lambda m: m["type"] == "alert" and m["data"]["booking_id"] == b["id"])
        alert = seen[-1]["data"]
        jsonschema.validate(alert, schema("alert"))
        assert alert["type"] == "unconfirmed_departure" and alert["lot_id"] == b["lot_id"]
        stays = [m["data"] for m in seen if m["type"] == "stay" and m["data"]["booking_id"] == b["id"]]
        assert stays and stays[-1]["state"] == "UNCONFIRMED_DEPARTURE"

        ops.post("/walk_ins", json={"lot_id": other, "plate": "Z 112233", "device_id": "live-test",
                                    "client_event_id": "live-walk-in-01"})
        seen = _until(elsewhere, lambda m: m["type"] == "stay")
        assert seen[-1]["data"]["plate_norm"] == "Z112233" and seen[-1]["data"]["lot_id"] == other
        assert all(m["data"]["lot_id"] == other for m in seen)
