"""Driver mode (CONTRACT.md §7): one-tap confirm, pending confirmations, rewards.

`POST /stays/{id}/confirm` stores the answer as a `driver_confirm` signal (definitive in
config/fusion.yaml) and lets fusion act on it, so the evidence trail shows the driver's tap
next to the sensors. "arrived" moves an ARRIVING stay to CHECKED_IN (`in_method driver`),
"left" checks out (`out_method driver`), "not_me" puts the stay back to EXPECTED with a
`low_confidence` alert (an auto check-in that the driver disputes raises an `anomaly`).
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api import db
from api.deps import get_conn
from api.ops import fusion, rewards
from api.ops.routes import ACTIVE, _upsert_alert, audit, booking_out, remember, remembered, stay_out

router = APIRouter()
KIND = {"arrived": "confirm", "not_me": "deny", "left": "exit"}


class ConfirmIn(BaseModel):
    device_id: str
    client_event_id: str
    answer: Literal["arrived", "not_me", "left"]
    observed_at: str | None = None


def _result(result: str, message: str, stay, points: int = 0) -> dict:
    return {"result": result, "message": message, "stay": stay_out(stay), "points_awarded": points}


@router.post("/stays/{stay_id}/confirm")
def confirm(stay_id: str, body: ConfirmIn, conn=Depends(get_conn)):
    if (seen := remembered(conn, body.client_event_id)) is not None:
        return seen
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
    if stay is None:
        raise HTTPException(404, "Unknown stay")
    if not stay["booking_id"]:
        raise HTTPException(409, "Only stays with a booking can be confirmed by a driver")
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (stay["booking_id"],)).fetchone()
    now = db.now(conn)
    observed = db.parse(body.observed_at) if body.observed_at else now
    before = stay_out(stay)
    # Anti-fraud: points only when other evidence already agreed with the driver.
    if body.answer == "arrived":
        agreed = stay["state"] in ("ARRIVING",) + ACTIVE
    elif body.answer == "left":
        agreed = conn.execute(
            "SELECT 1 FROM signals WHERE (booking_id = ? OR matched_booking_id = ?) AND source != 'driver_confirm' "
            "AND kind IN ('exit', 'unpark', 'vacant') AND observed_at > ?",
            (booking["id"], booking["id"], stay["in_at"] or "")).fetchone() is not None
    else:
        agreed = False
    conn.execute(
        "INSERT OR IGNORE INTO signals(client_event_id, device_id, user_id, lot_id, booking_id, source, kind, "
        "observed_at, received_at, payload) VALUES (?,?,?,?,?,'driver_confirm',?,?,?,?)",
        (body.client_event_id, body.device_id, booking["user_id"], stay["lot_id"], booking["id"], KIND[body.answer],
         db.utc(observed), db.utc(now), db.dumps({"answer": body.answer, "stay_id": stay_id})))
    conn.commit()
    label = booking["plate_display"] or booking["id"]

    if body.answer == "not_me":
        if stay["state"] == "ARRIVING":
            conn.execute("UPDATE stays SET state = 'EXPECTED', in_method = NULL, in_conf = NULL, updated_at = ? "
                         "WHERE id = ?", (db.utc(now), stay_id))
            _upsert_alert(conn, alert_type="low_confidence", severity="warning", lot_id=stay["lot_id"], stay_id=None,
                          booking_id=booking["id"], at=now,
                          message=f"The driver of {label} says the car seen at {stay['lot_id']} is not theirs.")
            outcome, message = "rejected", "Thanks, we won't check you in for that one."
        else:
            _upsert_alert(conn, alert_type="anomaly", severity="critical", lot_id=stay["lot_id"], stay_id=stay_id,
                          booking_id=booking["id"], at=now,
                          message=f"The driver of {label} disputes the {stay['state']} stay at {stay['lot_id']}.")
            outcome, message = "disputed", "Thanks, an attendant will check it."
        stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
        audit(conn, action="driver_deny", entity="stay", entity_id=stay_id, lot_id=stay["lot_id"], before=before,
              after=stay_out(stay), observed_at=observed, device_id=body.device_id,
              client_event_id=body.client_event_id, actor_type="driver", actor_id=booking["user_id"])
        return remember(conn, body.client_event_id, _result(outcome, message, stay))

    fusion.fuse_booking(conn, booking["id"])
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
    if body.answer == "arrived" and stay["state"] in ACTIVE and before["state"] not in ACTIVE:
        conn.execute("UPDATE stays SET in_method = 'driver', in_conf = 1.0, updated_at = ? WHERE id = ?",
                     (db.utc(now), stay_id))
        outcome, message = "checked_in", f"Checked in {label} at {stay['lot_id']}."
    elif body.answer == "left" and stay["state"] == "CHECKED_OUT" and (
            before["state"] != "CHECKED_OUT" or before["inferred"]):
        conn.execute("UPDATE stays SET out_method = 'driver', out_conf = 1.0, inferred = 0, updated_at = ? WHERE id = ?",
                     (db.utc(now), stay_id))
        outcome, message = "checked_out", f"Checked out {label}. Thanks!"
    else:
        outcome, message = "already", f"{label} is already {stay['state'].replace('_', ' ').lower()}."
        agreed = False
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
    points = rewards.award_confirmation(conn, booking["user_id"], booking["id"], body.answer, agreed, now) \
        if outcome in ("checked_in", "checked_out") else 0
    audit(conn, action="driver_confirm", entity="stay", entity_id=stay_id, lot_id=stay["lot_id"], before=before,
          after=stay_out(stay), observed_at=observed, device_id=body.device_id, client_event_id=body.client_event_id,
          actor_type="driver", actor_id=booking["user_id"])
    return remember(conn, body.client_event_id, _result(outcome, message, stay, points))


@router.get("/users/{user_id}/pending_confirmations")
def pending_confirmations(user_id: str, conn=Depends(get_conn)):
    """Stays waiting for this driver: ARRIVING ("arrived?") and ended-but-unconfirmed ("left?")."""
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(404, f"Unknown user {user_id}")
    rows = conn.execute(
        "SELECT s.*, b.id AS b_id FROM stays s JOIN bookings b ON b.id = s.booking_id WHERE b.user_id = ? "
        "AND s.state IN ('ARRIVING', 'UNCONFIRMED_DEPARTURE', 'OVERSTAY') ORDER BY s.updated_at", (user_id,)).fetchall()
    out = []
    for r in rows:
        booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (r["b_id"],)).fetchone()
        out.append({"stay": stay_out(r), "booking": booking_out(booking),
                    "question": "arrived" if r["state"] == "ARRIVING" else "left",
                    "p": r["in_conf"], "asked_at": db.local(r["updated_at"])})
    return out


@router.get("/users/{user_id}/rewards")
def user_rewards(user_id: str, conn=Depends(get_conn)):
    if conn.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
        raise HTTPException(404, f"Unknown user {user_id}")
    return rewards.summary(conn, user_id, db.now(conn))
