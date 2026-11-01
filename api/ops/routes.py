"""Ops API for Challenge 1 (CONTRACT.md §7). Owner: P2.

P0 implements scan/check-in/out, overview, stays, audit, signal storage and
simple on-the-fly alerts. Fusion, QR signatures, rewards and live push are P2+.
"""
from __future__ import annotations

import json
import hashlib
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import yaml

from api import db
from api.deps import get_conn
from api.ops import qr as qr_codes
from api.rhythm_api.store import latest_priors

router = APIRouter()
EARLY_MIN = 30           # scan accepted this many minutes before start
OVERSTAY_GRACE_MIN = 10
EXPECTED_WINDOW_MIN = 30
DOUBLE_SCAN_MIN = 5      # a repeat scan within this window is not treated as a check-out
ACTIVE = ("CHECKED_IN", "OVERSTAY", "UNCONFIRMED_DEPARTURE")
FUSION_CONFIG = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "fusion.yaml").read_text())
PLATE_PATTERN = re.compile(r"^[A-Z]\d{1,6}$")


# ---------- serializers ----------
def lot_out(r) -> dict:
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "capacity": r["capacity"],
            "has_spaces": bool(r["has_spaces"]), "center_lat": r["center_lat"],
            "center_lng": r["center_lng"], "radius_m": r["radius_m"]}


def booking_out(r) -> dict | None:
    if r is None:
        return None
    return {"booking_id": r["id"], "user_id": r["user_id"], "location_id": r["lot_id"],
            "start_time": db.local(r["start_at"]), "end_time": db.local(r["end_at"]),
            "status": r["status"], "created_at": db.local(r["created_at"]), "plate": r["plate_display"]}


def stay_out(r) -> dict | None:
    if r is None:
        return None
    return {"id": r["id"], "booking_id": r["booking_id"], "lot_id": r["lot_id"], "plate_norm": r["plate_norm"],
            "state": r["state"], "in_at": db.local(r["in_at"]), "in_method": r["in_method"],
            "in_conf": r["in_conf"], "out_at": db.local(r["out_at"]), "out_method": r["out_method"],
            "out_conf": r["out_conf"], "inferred": bool(r["inferred"])}


def alert_out(r) -> dict:
    return {"id": r["id"], "type": r["type"], "severity": r["severity"], "lot_id": r["lot_id"],
            "stay_id": r["stay_id"], "booking_id": r["booking_id"], "message": r["message"] or "",
            "estimated_fee": r["estimated_fee"], "status": r["status"], "created_at": db.local(r["created_at"]),
            "resolved_by": r["resolved_by"], "resolved_at": db.local(r["resolved_at"])}


def audit(conn, *, action, entity, entity_id, lot_id, before, after, observed_at, device_id=None,
          client_event_id=None, actor_type="attendant", actor_id=None):
    conn.execute(
        "INSERT INTO audit_log(client_event_id, actor_type, actor_id, action, entity, entity_id, lot_id,"
        " before, after, observed_at, received_at, device_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (client_event_id, actor_type, actor_id or device_id, action, entity, entity_id, lot_id,
         db.dumps(before), db.dumps(after), db.utc(observed_at), db.utc(db.now(conn)), device_id))


def remembered(conn, client_event_id: str | None):
    if not client_event_id:
        return None
    row = conn.execute("SELECT response FROM processed_events WHERE client_event_id = ?",
                       (client_event_id,)).fetchone()
    return json.loads(row["response"]) if row else None


def remember(conn, client_event_id: str | None, response: dict) -> dict:
    if client_event_id:
        conn.execute("INSERT OR IGNORE INTO processed_events VALUES (?,?,?)",
                     (client_event_id, db.dumps(response), db.utc(db.now(conn))))
    conn.commit()
    return response


# ---------- basics ----------
@router.get("/health")
def health(conn=Depends(get_conn)):
    return {"ok": True, "now": db.now(conn).isoformat(timespec="seconds"), "version": "p1"}


@router.get("/lots")
def lots(conn=Depends(get_conn)):
    return [lot_out(r) for r in conn.execute("SELECT * FROM lots ORDER BY id")]


@router.get("/bookings/{booking_id}/qr")
def booking_qr(booking_id: str, conn=Depends(get_conn)):
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id.strip().upper(),)).fetchone()
    if booking is None:
        raise HTTPException(404, "Unknown booking")
    if booking["status"] != "booked":
        raise HTTPException(409, "Only active bookings have a driver QR")
    return {"qr": qr_codes.sign_booking(booking)}


def _lot(conn, lot_id: str):
    row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Unknown lot {lot_id}")
    return row


@router.get("/lots/{lot_id}/overview")
def overview(lot_id: str, conn=Depends(get_conn)):
    lot = _lot(conn, lot_id)
    t = db.now(conn)
    from api.ops.fusion import unidentified_exits
    # Stays include unidentified gate entries (G-…); unidentified gate exits not yet matched to a stay count as gone.
    occupied = conn.execute(f"SELECT COUNT(*) FROM stays WHERE lot_id = ? AND state IN {ACTIVE}",
                            (lot_id,)).fetchone()[0]
    occupied = max(0, occupied - unidentified_exits(conn, lot_id, t))
    upcoming = conn.execute(
        "SELECT b.* FROM bookings b WHERE b.lot_id = ? AND b.status = 'booked' AND b.end_at > ? AND b.start_at <= ?"
        " AND NOT EXISTS (SELECT 1 FROM stays s WHERE s.booking_id = b.id AND s.state != 'EXPECTED') ORDER BY b.start_at",
        (lot_id, db.utc(t), db.utc(t + timedelta(minutes=60)))).fetchall()
    reserved = len(upcoming)

    priors = {(p["user_id"], p["date"]): p for p in latest_priors() if p["lot_id"] == lot_id}
    soon_cut = db.utc(t + timedelta(minutes=EXPECTED_WINDOW_MIN))
    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    # Anyone with a booking here today is covered by that booking; predictions are only for the rest.
    booked_users = {r["user_id"] for r in conn.execute(
        "SELECT DISTINCT user_id FROM bookings WHERE lot_id = ? AND start_at >= ? AND start_at < ?",
        (lot_id, db.utc(day_start), db.utc(day_start + timedelta(days=1))))}
    expected = []
    for b in upcoming:
        if b["start_at"] <= soon_cut:
            prior = priors.get((b["user_id"], t.date().isoformat()))
            expected.append({"booking_id": b["id"], "user_id": b["user_id"], "plate": b["plate_display"],
                             "at": db.local(b["start_at"]), "p": prior["p"] if prior else 0.8, "source": "booking"})
    for (user_id, day), p in priors.items():
        if day == t.date().isoformat() and user_id not in booked_users:
            ws = datetime.fromisoformat(p["window_start"])
            we = datetime.fromisoformat(p["window_end"])
            centre = ws + (we - ws) / 2
            if t <= centre <= t + timedelta(minutes=EXPECTED_WINDOW_MIN):
                expected.append({"booking_id": None, "user_id": user_id, "plate": None,
                                 "at": centre.isoformat(timespec="seconds"), "p": p["p"], "source": "prediction"})
    expected.sort(key=lambda e: e["at"])

    spaces = None
    if lot["has_spaces"]:
        occ = {r["space_id"] for r in conn.execute(
            f"SELECT space_id FROM stays WHERE lot_id = ? AND state IN {ACTIVE} AND space_id IS NOT NULL", (lot_id,))}
        res = {b["space_id"] for b in upcoming if b["space_id"]}
        spaces = [{"id": s["id"], "label": s["label"],
                   "state": "occupied" if s["id"] in occ else "reserved" if s["id"] in res else "free"}
                  for s in conn.execute("SELECT * FROM spaces WHERE lot_id = ? ORDER BY id", (lot_id,))]
    return {"lot": lot_out(lot), "now": t.isoformat(timespec="seconds"),
            "counts": {"capacity": lot["capacity"], "occupied": occupied, "reserved": reserved,
                       "free": max(0, lot["capacity"] - occupied - reserved)},
            "expected_soon": expected, "spaces": spaces}


# ---------- scan / check-in / check-out ----------
class ScanIn(BaseModel):
    lot_id: str
    device_id: str
    client_event_id: str | None = None
    booking_id: str | None = None
    plate: str | None = None
    qr: str | None = None
    observed_at: str | None = None


def _result(result, message, booking=None, stay=None, suggest=None) -> dict:
    return {"result": result, "message": message, "booking": booking_out(booking),
            "stay": stay_out(stay), "suggest": suggest}


def do_scan(conn, body: ScanIn) -> dict:
    if (seen := remembered(conn, body.client_event_id)) is not None:
        return seen
    _lot(conn, body.lot_id)
    t = db.now(conn)
    observed = db.parse(body.observed_at) if body.observed_at else t
    booking = None

    if body.qr:
        payload = qr_codes.verify_booking_qr(body.qr)
        if payload is None:
            return remember(conn, body.client_event_id, _result("invalid_qr", "The booking QR signature is invalid."))
        booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (payload["bid"],)).fetchone()
        if (booking is None or payload["lot"] != booking["lot_id"]
                or payload["ph"] != hashlib.sha256((booking["plate_norm"] or "").encode()).hexdigest()[:16]
                or payload["s"] != db.local(booking["start_at"]) or payload["e"] != db.local(booking["end_at"])):
            return remember(conn, body.client_event_id, _result("invalid_qr", "The booking QR does not match an active booking."))
        body = body.model_copy(update={"booking_id": booking["id"], "qr": None})

    if body.booking_id:
        booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (body.booking_id.strip().upper(),)).fetchone()
    elif body.plate:
        plate = db.normalize_plate(body.plate)
        # Prefer today's booking at this lot, closest to now.
        booking = conn.execute(
            "SELECT * FROM bookings WHERE plate_norm = ? AND status = 'booked' AND end_at > ? AND start_at <= ?"
            " ORDER BY (lot_id = ?) DESC, ABS(julianday(start_at) - julianday(?)) LIMIT 1",
            (plate, db.utc(t - timedelta(hours=12)), db.utc(t + timedelta(hours=12)), body.lot_id, db.utc(t))
        ).fetchone()
    if booking is None:
        return remember(conn, body.client_event_id,
                        _result("not_found", "No booking found. Register the car as a walk-in.",
                                suggest="register_walk_in"))

    plate_txt = booking["plate_display"] or booking["id"]
    stay = conn.execute(f"SELECT * FROM stays WHERE booking_id = ? AND state IN {ACTIVE}", (booking["id"],)).fetchone()
    if stay is not None:
        # A second scan soon after check-in is almost always a double scan, not a departure.
        if observed - db.parse(stay["in_at"]) < timedelta(minutes=DOUBLE_SCAN_MIN):
            return remember(conn, body.client_event_id,
                            _result("already_in", f"{plate_txt} was just checked in. Scan again on exit.",
                                    booking, stay))
        return remember(conn, body.client_event_id, _checkout(conn, stay, booking, observed, body.device_id,
                                                              body.client_event_id, "attendant"))
    if booking["lot_id"] != body.lot_id:
        return remember(conn, body.client_event_id,
                        _result("wrong_lot", f"{plate_txt} is booked at {booking['lot_id']}, not {body.lot_id}.",
                                booking))
    if booking["status"] in ("cancelled", "no_show") or t > db.parse(booking["end_at"]):
        return remember(conn, body.client_event_id,
                        _result("expired", f"Booking for {plate_txt} ended at "
                                           f"{(db.local(booking['end_at']) or '')[11:16]}.", booking))
    if t < db.parse(booking["start_at"]) - timedelta(minutes=EARLY_MIN):
        return remember(conn, body.client_event_id,
                        _result("too_early", f"{plate_txt} is booked from {(db.local(booking['start_at']) or '')[11:16]}.",
                                booking))
    if conn.execute("SELECT 1 FROM stays WHERE booking_id = ?", (booking["id"],)).fetchone():
        return remember(conn, body.client_event_id,
                        _result("already_in", f"{plate_txt} already checked in and out on this booking.", booking))

    stay_id = f"S-{booking['id']}"
    conn.execute("INSERT INTO stays VALUES (?,?,?,?,?, 'CHECKED_IN', ?, 'attendant', 1.0, NULL,NULL,NULL, 0, ?)",
                 (stay_id, booking["id"], booking["lot_id"], booking["space_id"], booking["plate_norm"],
                  db.utc(observed), db.utc(t)))
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
    _claim_gate(conn, booking["lot_id"], "enter", observed, booking["id"], stay_id)
    audit(conn, action="check_in", entity="stay", entity_id=stay_id, lot_id=booking["lot_id"], before=None,
          after=stay_out(stay), observed_at=observed, device_id=body.device_id,
          client_event_id=body.client_event_id)
    return remember(conn, body.client_event_id,
                    _result("checked_in", f"Checked in {plate_txt} at {booking['lot_id']} until "
                                          f"{(db.local(booking['end_at']) or '')[11:16]}.", booking, stay))


def _claim_gate(conn, lot_id, kind, at, booking_id, stay_id) -> None:
    from api.ops.fusion import resolve_gate
    resolve_gate(conn, lot_id, kind, at, booking_id, stay_id)


def _checkout(conn, stay, booking, observed: datetime, device_id, client_event_id, method) -> dict:
    before = stay_out(stay)
    _claim_gate(conn, stay["lot_id"], "exit", observed, stay["booking_id"], stay["id"])
    now_utc = db.utc(db.now(conn))
    conn.execute("UPDATE stays SET state = 'CHECKED_OUT', out_at = ?, out_method = ?, out_conf = 1.0,"
                 " updated_at = ? WHERE id = ?", (db.utc(observed), method, now_utc, stay["id"]))
    if booking is not None:
        conn.execute("UPDATE bookings SET status = 'completed', updated_at = ? WHERE id = ?",
                     (now_utc, booking["id"]))
    conn.execute("UPDATE alerts SET status = 'resolved', resolved_by = 'system', resolved_at = ?, updated_at = ? "
                 "WHERE stay_id = ? AND status = 'open'", (now_utc, now_utc, stay["id"]))
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay["id"],)).fetchone()
    audit(conn, action="check_out", entity="stay", entity_id=stay["id"], lot_id=stay["lot_id"], before=before,
          after=stay_out(stay), observed_at=observed, device_id=device_id, client_event_id=client_event_id)
    label = (booking["plate_display"] if booking is not None else None) or stay["plate_norm"] or stay["id"]
    return _result("checked_out", f"Checked out {label}.", booking, stay)


@router.post("/scan")
def scan(body: ScanIn, conn=Depends(get_conn)):
    return do_scan(conn, body)


class WalkInIn(BaseModel):
    lot_id: str
    plate: str
    device_id: str
    client_event_id: str
    observed_at: str | None = None
    space_id: str | None = None


@router.post("/walk_ins")
def walk_in(body: WalkInIn, conn=Depends(get_conn)):
    if (seen := remembered(conn, body.client_event_id)) is not None:
        return seen
    lot = _lot(conn, body.lot_id)
    plate_norm = db.normalize_plate(body.plate)
    if not plate_norm or not PLATE_PATTERN.fullmatch(plate_norm):
        raise HTTPException(422, "Plate must match the Lebanese format A123456")
    existing = conn.execute(
        f"SELECT * FROM stays WHERE plate_norm = ? AND state IN {ACTIVE} ORDER BY updated_at DESC LIMIT 1",
        (plate_norm,),
    ).fetchone()
    if existing:
        if existing["lot_id"] != body.lot_id:
            raise HTTPException(409, f"This vehicle is already active at {existing['lot_id']}")
        return remember(conn, body.client_event_id,
                        _result("already_in", f"{plate_norm} is already checked in.", stay=existing))

    now = db.now(conn)
    observed = db.parse(body.observed_at) if body.observed_at else now
    occupied = conn.execute(f"SELECT COUNT(*) FROM stays WHERE lot_id = ? AND state IN {ACTIVE}",
                             (body.lot_id,)).fetchone()[0]
    if occupied >= lot["capacity"]:
        raise HTTPException(409, "This lot is at capacity")
    if body.space_id:
        space = conn.execute("SELECT id FROM spaces WHERE id = ? AND lot_id = ?", (body.space_id, body.lot_id)).fetchone()
        if not space:
            raise HTTPException(422, "Space does not belong to this lot")
        if conn.execute(f"SELECT 1 FROM stays WHERE space_id = ? AND state IN {ACTIVE}",
                        (body.space_id,)).fetchone():
            raise HTTPException(409, "Space is already occupied")

    stay_id = "W-" + hashlib.sha256(body.client_event_id.encode("utf-8")).hexdigest()[:16]
    conn.execute(
        "INSERT INTO stays(id, booking_id, lot_id, space_id, plate_norm, state, in_at, in_method, in_conf, "
        "out_at, out_method, out_conf, inferred, updated_at) VALUES (?,NULL,?,?,?,'CHECKED_IN',?,'attendant',1.0,"
        "NULL,NULL,NULL,0,?)",
        (stay_id, body.lot_id, body.space_id, plate_norm, db.utc(observed), db.utc(now)),
    )
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
    _claim_gate(conn, body.lot_id, "enter", observed, None, stay_id)
    audit(conn, action="walk_in", entity="stay", entity_id=stay_id, lot_id=body.lot_id, before=None,
          after=stay_out(stay), observed_at=observed, device_id=body.device_id,
          client_event_id=body.client_event_id)
    return remember(conn, body.client_event_id,
                    _result("checked_in", f"Registered walk-in {plate_norm} at {body.lot_id}.", stay=stay))


class CheckoutIn(BaseModel):
    device_id: str
    client_event_id: str | None = None
    method: Literal["auto", "driver", "attendant", "inferred"] = "attendant"
    observed_at: str | None = None


def do_checkout(conn, stay_id: str, body: CheckoutIn) -> dict:
    if (seen := remembered(conn, body.client_event_id)) is not None:
        return seen
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
    if stay is None:
        raise HTTPException(404, f"Unknown stay {stay_id}")
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (stay["booking_id"],)).fetchone()
    if stay["state"] not in ACTIVE:
        return remember(conn, body.client_event_id, _result("checked_out", "Already checked out.", booking, stay))
    observed = db.parse(body.observed_at) if body.observed_at else db.now(conn)
    return remember(conn, body.client_event_id,
                    _checkout(conn, stay, booking, observed, body.device_id, body.client_event_id, body.method))


@router.post("/stays/{stay_id}/checkout")
def checkout(stay_id: str, body: CheckoutIn, conn=Depends(get_conn)):
    return do_checkout(conn, stay_id, body)


@router.get("/stays")
def stays(lot_id: str | None = None, state: str | None = None, conn=Depends(get_conn)):
    q, args = "SELECT * FROM stays WHERE 1=1", []
    if lot_id:
        q, args = q + " AND lot_id = ?", args + [lot_id]
    if state:
        q, args = q + " AND state = ?", args + [state]
    return [stay_out(r) for r in conn.execute(q + " ORDER BY in_at DESC", args)]


@router.get("/stays/{stay_id}/evidence")
def stay_evidence(stay_id: str, conn=Depends(get_conn)):
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
    if stay is None:
        raise HTTPException(404, "Unknown stay")
    prior = float(FUSION_CONFIG.get("default_prior", 0.3))
    if stay["booking_id"]:
        booking = conn.execute("SELECT user_id, start_at FROM bookings WHERE id = ?", (stay["booking_id"],)).fetchone()
        if booking:
            date = db.parse(booking["start_at"]).astimezone(db.TZ).date().isoformat()
            for candidate in latest_priors():
                if (candidate["user_id"] == booking["user_id"] and candidate["lot_id"] == stay["lot_id"]
                        and candidate["date"] == date):
                    prior = max(0.001, min(0.999, float(candidate["p"])))
                    break
    rows = conn.execute(
        "SELECT f.signal_id, f.direction, s.observed_at, s.source, s.kind, f.log_lr, "
        "f.weighted_log_lr, f.probability, f.prior FROM fusion_evidence f "
        "JOIN signals s ON s.id = f.signal_id WHERE f.stay_id = ? "
        "ORDER BY s.observed_at, f.id", (stay_id,),
    ).fetchall()
    if rows:
        prior = rows[0]["prior"]
    evidence = [{"signal_id": r["signal_id"], "direction": r["direction"],
                 "observed_at": db.local(r["observed_at"]), "source": r["source"], "kind": r["kind"],
                 "log_lr": r["log_lr"], "weighted_log_lr": r["weighted_log_lr"],
                 "probability": r["probability"]} for r in rows]
    return {"stay_id": stay_id, "prior": prior, "evidence": evidence}


@router.get("/audit")
def audit_log(lot_id: str | None = None, limit: int = 50, conn=Depends(get_conn)):
    q, args = "SELECT * FROM audit_log", []
    if lot_id:
        q, args = q + " WHERE lot_id = ?", [lot_id]
    rows = conn.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
    return [{**dict(r), "before": json.loads(r["before"] or "null"), "after": json.loads(r["after"] or "null"),
             "observed_at": db.local(r["observed_at"]), "received_at": db.local(r["received_at"])} for r in rows]


# ---------- signals ----------
class SignalIn(BaseModel):
    client_event_id: str
    device_id: str
    lot_id: str
    source: Literal["geofence", "motion", "ble", "nfc", "gate_qr", "booking_qr", "camera_plate",
                    "camera_spot", "gate_sensor", "car_bt", "attendant", "driver_confirm"]
    kind: Literal["enter", "exit", "park", "unpark", "scan", "occupied", "vacant", "confirm", "deny"]
    observed_at: str
    user_id: str | None = None
    booking_id: str | None = None
    payload: dict = {}


class SignalBatch(BaseModel):
    signals: list[SignalIn]


@router.post("/signals")
def signals(body: SignalIn | SignalBatch, conn=Depends(get_conn)):
    from api.ops import fusion
    items = body.signals if isinstance(body, SignalBatch) else [body]
    received = db.utc(db.now(conn))
    accepted = 0
    affected: set[str] = set()
    for s in items:
        if s.source == "gate_qr":
            gate = conn.execute("SELECT gate_secret FROM lots WHERE id = ?", (s.lot_id,)).fetchone()
            code = s.payload.get("code")
            if gate is None or not code or not qr_codes.verify_gate_code(
                    code, s.lot_id, gate["gate_secret"], db.now(conn)):
                raise HTTPException(422, "Invalid or expired rotating gate code")
        observed = db.parse(s.observed_at)
        payload, matched = dict(s.payload), None
        if s.source in ("camera_plate", "camera_spot"):
            if payload.get("plate_norm"):
                payload["plate_norm"] = db.normalize_plate(str(payload["plate_norm"]))
            # Cameras don't know the booking: attribute by plate or watched space (CONTRACT.md §6).
            matched = fusion.attribute_camera(conn, s.lot_id, s.source, s.kind, observed, payload)
        cur = conn.execute(
            "INSERT OR IGNORE INTO signals(client_event_id, device_id, user_id, lot_id, booking_id, source, kind,"
            " observed_at, received_at, payload, matched_booking_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (s.client_event_id, s.device_id, s.user_id, s.lot_id, s.booking_id, s.source, s.kind,
             db.utc(observed), received, db.dumps(payload), matched))
        accepted += cur.rowcount
        if cur.rowcount:
            affected |= {b for b in (s.booking_id, matched) if b}
            if s.source in ("gate_sensor", "camera_plate"):
                # Break-beam pair + lane camera: same lot and direction within seconds = one identified car.
                if paired := fusion.pair_gate(conn, cur.lastrowid):
                    affected.add(paired)
            if s.source == "camera_plate" and matched is None:
                affected |= fusion.plate_bookings_elsewhere(conn, payload.get("plate_norm"), s.lot_id, observed)
    conn.commit()
    for booking_id in sorted(affected):
        fusion.fuse_booking(conn, booking_id)
    return {"accepted": accepted, "duplicates": len(items) - accepted}


@router.get("/signals")
def list_signals(lot_id: str | None = None, source: str | None = None, since: str | None = None,
                 after_id: int | None = None, limit: int = 100, conn=Depends(get_conn)):
    """Stored signals, oldest first, for edge devices and debugging (CONTRACT.md §7).

    With `after_id` (the previous response's `cursor`): the next `limit` signals after it. Without it:
    the newest `limit` (so `limit=1` gives the latest id to start polling from). `since` filters on
    observed_at."""
    limit = max(1, min(limit, 1000))
    q, args = "SELECT * FROM signals WHERE 1=1", []
    if lot_id:
        q, args = q + " AND lot_id = ?", args + [lot_id]
    if source:
        q, args = q + " AND source = ?", args + [source]
    if since:
        try:
            q, args = q + " AND observed_at >= ?", args + [db.utc(db.parse(since))]
        except ValueError as exc:
            raise HTTPException(422, "since must be an ISO 8601 timestamp") from exc
    if after_id is not None:
        rows = conn.execute(q + " AND id > ? ORDER BY id LIMIT ?", args + [after_id, limit]).fetchall()
    else:
        rows = conn.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()[::-1]
    out = [{"id": r["id"], "client_event_id": r["client_event_id"], "device_id": r["device_id"],
            "user_id": r["user_id"], "lot_id": r["lot_id"], "booking_id": r["booking_id"],
            "matched_booking_id": r["matched_booking_id"], "source": r["source"], "kind": r["kind"],
            "observed_at": db.local(r["observed_at"]), "received_at": db.local(r["received_at"]),
            "payload": json.loads(r["payload"] or "{}")} for r in rows]
    return {"signals": out, "cursor": out[-1]["id"] if out else (after_id or 0)}


# ---------- sync (basic) ----------
class SyncEventIn(BaseModel):
    client_event_id: str
    type: Literal["scan", "checkout", "walk_in", "resolve_alert", "override"]
    lot_id: str
    observed_at: str
    body: dict


class SyncPushIn(BaseModel):
    device_id: str
    events: list[SyncEventIn]


@router.post("/sync/push")
def sync_push(body: SyncPushIn, conn=Depends(get_conn)):
    """Replay offline actions in observation order and report invalid state transitions."""
    acks, conflicts, results = [], [], {}
    for ev in sorted(body.events, key=lambda e: db.parse(e.observed_at).astimezone(timezone.utc)):
        try:
            if ev.type == "scan":
                booking_id = ev.body.get("booking_id")
                historical = conn.execute("SELECT status FROM bookings WHERE id = ?", (booking_id,)).fetchone() if booking_id else None
                if historical and historical["status"] in ("completed", "no_show"):
                    _upsert_alert(conn, alert_type="anomaly", severity="critical", lot_id=ev.lot_id,
                                  stay_id=None, booking_id=booking_id,
                                  message=f"Offline scan {ev.client_event_id} arrived for a {historical['status']} booking.",
                                  at=db.now(conn))
                    conflicts.append({"id": ev.client_event_id, "reason": "booking is already completed or marked no-show"})
                    continue
                res = do_scan(conn, ScanIn(**{**ev.body, "lot_id": ev.lot_id, "device_id": body.device_id,
                                              "client_event_id": ev.client_event_id, "observed_at": ev.observed_at}))
                if res["result"] == "already_in" and "already checked in and out" in res["message"]:
                    _upsert_alert(conn, alert_type="anomaly", severity="critical", lot_id=ev.lot_id,
                                  stay_id=None, booking_id=booking_id,
                                  message=f"Offline scan {ev.client_event_id} arrived after checkout.", at=db.now(conn))
                    conflicts.append({"id": ev.client_event_id, "reason": "booking was already checked out"})
                    continue
            elif ev.type == "checkout":
                stay_id = ev.body.get("stay_id")
                if not stay_id or conn.execute("SELECT 1 FROM stays WHERE id = ?", (stay_id,)).fetchone() is None:
                    _upsert_alert(conn, alert_type="anomaly", severity="critical", lot_id=ev.lot_id,
                                  stay_id=None, booking_id=ev.body.get("booking_id"),
                                  message=f"Offline checkout {ev.client_event_id} arrived before a matching check-in.",
                                  at=db.now(conn))
                    conflicts.append({"id": ev.client_event_id, "reason": "checkout arrived before check-in; review required"})
                    continue
                res = do_checkout(conn, stay_id,
                                  CheckoutIn(device_id=body.device_id, client_event_id=ev.client_event_id,
                                             observed_at=ev.observed_at))
            elif ev.type == "walk_in":
                res = walk_in(WalkInIn(**{**ev.body, "lot_id": ev.lot_id, "device_id": body.device_id,
                                          "client_event_id": ev.client_event_id, "observed_at": ev.observed_at}), conn)
            elif ev.type == "resolve_alert":
                alert_id = ev.body.get("alert_id")
                if not isinstance(alert_id, int):
                    conflicts.append({"id": ev.client_event_id, "reason": "resolve_alert requires an integer alert_id"})
                    continue
                res = resolve_alert(alert_id, ResolveAlertIn(device_id=body.device_id, note=ev.body.get("note")), conn)
            elif ev.type == "override":
                stay_id = ev.body.get("stay_id")
                state = ev.body.get("state")
                if state not in ("CHECKED_IN", "CHECKED_OUT") or not stay_id:
                    conflicts.append({"id": ev.client_event_id, "reason": "override requires stay_id and CHECKED_IN or CHECKED_OUT"})
                    continue
                stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
                if stay is None:
                    conflicts.append({"id": ev.client_event_id, "reason": "override references an unknown stay"})
                    continue
                before = stay_out(stay)
                observed = db.parse(ev.observed_at)
                if state == "CHECKED_IN":
                    conn.execute("UPDATE stays SET state = ?, in_at = ?, in_method = 'attendant', in_conf = 1.0, "
                                 "out_at = NULL, out_method = NULL, out_conf = NULL, inferred = 0, updated_at = ? WHERE id = ?",
                                 (state, db.utc(observed), db.utc(db.now(conn)), stay_id))
                else:
                    conn.execute("UPDATE stays SET state = ?, out_at = ?, out_method = 'attendant', out_conf = 1.0, "
                                 "updated_at = ? WHERE id = ?", (state, db.utc(observed), db.utc(db.now(conn)), stay_id))
                    if stay["booking_id"]:
                        conn.execute("UPDATE bookings SET status = 'completed', updated_at = ? WHERE id = ?",
                                     (db.utc(db.now(conn)), stay["booking_id"]))
                updated = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
                audit(conn, action="override", entity="stay", entity_id=stay_id, lot_id=stay["lot_id"],
                      before=before, after=stay_out(updated), observed_at=observed, device_id=body.device_id,
                      client_event_id=ev.client_event_id)
                res = _result("checked_in" if state == "CHECKED_IN" else "checked_out", "Stay state overridden.",
                              conn.execute("SELECT * FROM bookings WHERE id = ?", (stay["booking_id"],)).fetchone(), updated)
            else:
                conflicts.append({"id": ev.client_event_id, "reason": f"{ev.type} not supported yet"})
                continue
            acks.append(ev.client_event_id)
            results[ev.client_event_id] = res
        except HTTPException as e:
            conflicts.append({"id": ev.client_event_id, "reason": str(e.detail)})
        except (KeyError, TypeError, ValueError) as e:
            conflicts.append({"id": ev.client_event_id, "reason": f"invalid {ev.type} event: {e}"})
    conn.commit()
    return {"acks": acks, "conflicts": conflicts, "results": results}


@router.get("/sync/pull")
def sync_pull(lot_id: str, since: str | None = None, conn=Depends(get_conn)):
    """Return the initial lot cache or rows changed after the supplied UTC cursor."""
    _lot(conn, lot_id)
    t = db.now(conn)
    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    refresh_alerts(conn)
    if since:
        try:
            cursor = db.utc(db.parse(since))
        except ValueError as exc:
            raise HTTPException(422, "since must be an ISO 8601 timestamp") from exc
        bks = conn.execute("SELECT * FROM bookings WHERE lot_id = ? AND updated_at > ? ORDER BY updated_at",
                           (lot_id, cursor)).fetchall()
        sts = conn.execute("SELECT * FROM stays WHERE lot_id = ? AND updated_at > ? ORDER BY updated_at",
                           (lot_id, cursor)).fetchall()
        als = conn.execute("SELECT * FROM alerts WHERE lot_id = ? AND updated_at > ? ORDER BY updated_at",
                           (lot_id, cursor)).fetchall()
    else:
        bks = conn.execute("SELECT * FROM bookings WHERE lot_id = ? AND start_at >= ? AND start_at < ?",
                           (lot_id, db.utc(day_start), db.utc(day_start + timedelta(days=1)))).fetchall()
        sts = conn.execute("SELECT * FROM stays WHERE lot_id = ?", (lot_id,)).fetchall()
        als = conn.execute("SELECT * FROM alerts WHERE lot_id = ?", (lot_id,)).fetchall()
    changed = [r["updated_at"] for group in (bks, sts, als) for r in group if r["updated_at"]]
    next_cursor = max([db.utc(t), *changed])
    return {"cursor": db.local(next_cursor), "bookings": [booking_out(b) for b in bks],
            "stays": [stay_out(s) for s in sts], "alerts": [alert_out(a) for a in als]}


# ---------- persistent alerts ----------
def _upsert_alert(conn, *, alert_type, severity, lot_id, stay_id, booking_id, message,
                  estimated_fee=None, at=None):
    now_utc = db.utc(at or db.now(conn))
    current = conn.execute(
        "SELECT id FROM alerts WHERE type = ? AND stay_id IS ? AND booking_id IS ? AND status = 'open' "
        "ORDER BY id DESC LIMIT 1", (alert_type, stay_id, booking_id),
    ).fetchone()
    if current:
        conn.execute("UPDATE alerts SET severity = ?, message = ?, estimated_fee = ?, updated_at = ? WHERE id = ?",
                     (severity, message, estimated_fee, now_utc, current["id"]))
    else:
        conn.execute("INSERT INTO alerts(type, lot_id, stay_id, booking_id, severity, status, message, created_at, "
                     "updated_at, estimated_fee) VALUES (?,?,?,?,?,'open',?,?,?,?)",
                     (alert_type, lot_id, stay_id, booking_id, severity, message, now_utc, now_utc, estimated_fee))


def refresh_alerts(conn) -> None:
    """Reconcile persistent alert rows against the demo clock; safe to call repeatedly."""
    t = db.now(conn)
    now_utc = db.utc(t)
    soon = db.utc(t + timedelta(minutes=EXPECTED_WINDOW_MIN))

    upcoming = conn.execute(
        "SELECT b.* FROM bookings b WHERE b.status = 'booked' AND b.start_at BETWEEN ? AND ? "
        "AND NOT EXISTS (SELECT 1 FROM stays s WHERE s.booking_id = b.id AND s.state != 'EXPECTED') ORDER BY b.start_at",
        (now_utc, soon),
    ).fetchall()
    upcoming_ids = {b["id"] for b in upcoming}
    for b in upcoming:
        _upsert_alert(conn, alert_type="upcoming_arrival", severity="info", lot_id=b["lot_id"], stay_id=None,
                      booking_id=b["id"], message=f"{b['plate_display']} due at {(db.local(b['start_at']) or '')[11:16]}.", at=t)

    # Remove stale upcoming notifications from the open queue while retaining their history.
    for alert in conn.execute("SELECT id, booking_id FROM alerts WHERE type = 'upcoming_arrival' AND status = 'open'"):
        if alert["booking_id"] not in upcoming_ids:
            conn.execute("UPDATE alerts SET status = 'resolved', resolved_by = 'system', resolved_at = ?, "
                         "updated_at = ? WHERE id = ?", (now_utc, now_utc, alert["id"]))

    ended = conn.execute(
        "SELECT s.*, b.end_at, b.plate_display FROM stays s JOIN bookings b ON b.id = s.booking_id "
        f"WHERE s.state IN {('ARRIVING',) + ACTIVE} AND b.end_at < ?", (now_utc,),
    ).fetchall()
    for stay in ended:
        mins_past = max(0, int((t - db.parse(stay["end_at"])).total_seconds() // 60))
        if stay["state"] == "CHECKED_IN":
            conn.execute("UPDATE stays SET state = 'UNCONFIRMED_DEPARTURE', updated_at = ? WHERE id = ?",
                         (now_utc, stay["id"]))
            _upsert_alert(conn, alert_type="unconfirmed_departure", severity="warning", lot_id=stay["lot_id"],
                          stay_id=stay["id"], booking_id=stay["booking_id"],
                          message=f"{stay['plate_display']} booking ended; departure is unconfirmed.", at=t)
        if stay["state"] in ACTIVE and db.parse(stay["end_at"]) + timedelta(minutes=OVERSTAY_GRACE_MIN) < t:
            rate = float(FUSION_CONFIG.get("overstay_usd_per_15_min", 1.0))
            fee = round(math.ceil((mins_past - OVERSTAY_GRACE_MIN) / 15) * rate, 2)
            conn.execute("UPDATE stays SET state = 'OVERSTAY', updated_at = ? WHERE id = ? AND state = 'CHECKED_IN'",
                         (now_utc, stay["id"]))
            _upsert_alert(conn, alert_type="overstay", severity="warning", lot_id=stay["lot_id"],
                          stay_id=stay["id"], booking_id=stay["booking_id"],
                          message=f"{stay['plate_display']} is {mins_past} min past its booking end; estimated fee ${fee:.2f}.",
                          estimated_fee=fee, at=t)
        inferred_at = db.parse(stay["end_at"]) + timedelta(minutes=30)
        if t >= inferred_at:
            from api.ops.fusion import inferred_departure
            # Silent for 30 min after the end: check out at the last exit-like evidence, else the booking end,
            # unless the spot camera still sees the car.
            out_at = inferred_departure(conn, stay, t)
            if out_at is None:
                continue
            before = stay_out(stay)
            conn.execute(
                "UPDATE stays SET state='CHECKED_OUT', out_at=?, out_method='inferred', out_conf=0.6, "
                "inferred=1, updated_at=? WHERE id=?",
                (db.utc(out_at), now_utc, stay["id"]),
            )
            conn.execute("UPDATE bookings SET status='completed', updated_at=? WHERE id=?",
                         (now_utc, stay["booking_id"]))
            _claim_gate(conn, stay["lot_id"], "exit", out_at, stay["booking_id"], stay["id"])
            updated = conn.execute("SELECT * FROM stays WHERE id=?", (stay["id"],)).fetchone()
            audit(conn, action="inferred_check_out", entity="stay", entity_id=stay["id"], lot_id=stay["lot_id"],
                  before=before, after=stay_out(updated), observed_at=out_at, actor_type="system")
            conn.execute(
                "UPDATE alerts SET status='resolved', resolved_by='system', resolved_at=?, updated_at=? "
                "WHERE stay_id=? AND status='open'",
                (now_utc, now_utc, stay["id"]),
            )

    no_shows = conn.execute(
        "SELECT b.* FROM bookings b WHERE b.status = 'booked' AND b.end_at < ? "
        "AND NOT EXISTS (SELECT 1 FROM stays s WHERE s.booking_id = b.id AND s.state != 'EXPECTED')", (now_utc,),
    ).fetchall()
    for booking in no_shows:
        conn.execute("UPDATE bookings SET status = 'no_show', updated_at = ? WHERE id = ?",
                     (now_utc, booking["id"]))
        _upsert_alert(conn, alert_type="no_show", severity="warning", lot_id=booking["lot_id"], stay_id=None,
                      booking_id=booking["id"], message=f"{booking['plate_display']} did not arrive before the booking ended.",
                      at=t)
    conn.commit()


@router.get("/alerts")
def alerts(lot_id: str | None = None, conn=Depends(get_conn)):
    refresh_alerts(conn)
    q, args = "SELECT * FROM alerts WHERE status = 'open'", []
    if lot_id:
        q += " AND lot_id = ?"
        args.append(lot_id)
    rows = conn.execute(q + " ORDER BY created_at DESC, id DESC", args).fetchall()
    return [alert_out(r) for r in rows]


class ResolveAlertIn(BaseModel):
    device_id: str
    note: str | None = None


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, body: ResolveAlertIn, conn=Depends(get_conn)):
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Unknown alert")
    if row["status"] == "open":
        now = db.now(conn)
        conn.execute("UPDATE alerts SET status = 'resolved', resolved_by = ?, resolved_at = ?, updated_at = ? "
                     "WHERE id = ?", (body.device_id, db.utc(now), db.utc(now), alert_id))
        audit(conn, action="resolve_alert", entity="alert", entity_id=str(alert_id), lot_id=row["lot_id"],
              before={"status": "open"}, after={"status": "resolved", "note": body.note},
              observed_at=now, device_id=body.device_id, actor_type="attendant")
        conn.commit()
    return alert_out(conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone())


@router.get("/keys/qr")
def qr_key():
    return {"public_key": qr_codes.public_key_b64()}


@router.get("/dev/gate/{lot_id}")
def get_gate_code(lot_id: str, conn=Depends(get_conn)):
    lot = _lot(conn, lot_id)
    code, valid_until = qr_codes.gate_code(lot_id, lot["gate_secret"], db.now(conn))
    return {"code": code, "valid_until": valid_until.isoformat(timespec="seconds")}


# ---------- demo controls ----------
class ClockIn(BaseModel):
    now: str | None = None
    advance_minutes: int | None = None


@router.post("/dev/reset")
def dev_reset(prepark: bool = True, conn=Depends(get_conn)):
    """`prepark=false` starts with empty lots (simulator replays)."""
    return db.reset(conn, prepark=prepark)


@router.post("/dev/tick")
def dev_tick(conn=Depends(get_conn)):
    """Run the time-based rules now (alerts, silent departures, low confidence); the simulator calls
    this as it moves the demo clock, the API's own scheduler every 30 s."""
    from api.ops import fusion
    refresh_alerts(conn)
    return {"now": db.now(conn).isoformat(timespec="seconds"), "rescored": fusion.sweep(conn)}


@router.get("/dev/decisions")
def dev_decisions(since: str | None = None, until: str | None = None, conn=Depends(get_conn)):
    """What fusion decided per booking (start in [since, until)), for tools/simulator.

    Times are demo-clock times: `arriving_at`/`confirmed_at`/`checked_out_at` are when the system
    decided (audit received_at), the stay's `in_at`/`out_at` when it thinks the car moved.
    """
    q, args = "SELECT * FROM bookings WHERE 1=1", []
    if since:
        q, args = q + " AND start_at >= ?", args + [db.utc(db.parse(since))]
    if until:
        q, args = q + " AND start_at < ?", args + [db.utc(db.parse(until))]
    bookings = conn.execute(q + " ORDER BY id", args).fetchall()
    stays_by_booking = {r["booking_id"]: r for r in conn.execute(
        "SELECT * FROM stays WHERE booking_id IS NOT NULL ORDER BY updated_at")}
    decided: dict[tuple[str, str], str] = {}
    for r in conn.execute("SELECT entity_id, action, MIN(received_at) AS at FROM audit_log WHERE entity = 'stay' "
                          "GROUP BY entity_id, action"):
        decided[(r["entity_id"], r["action"])] = r["at"]
    alerts_by_booking: dict[str, set[str]] = {}
    for r in conn.execute("SELECT booking_id, type FROM alerts WHERE booking_id IS NOT NULL"):
        alerts_by_booking.setdefault(r["booking_id"], set()).add(r["type"])
    out = []
    for b in bookings:
        stay = stays_by_booking.get(b["id"])
        sid = stay["id"] if stay else None
        exit_action = next((a for a in ("auto_check_out", "closed_by_arrival_elsewhere", "inferred_check_out",
                                        "check_out") if (sid, a) in decided), None)
        out.append({"booking_id": b["id"], "lot_id": b["lot_id"], "status": b["status"], "stay": stay_out(stay),
                    "arriving_at": db.local(decided.get((sid, "arriving"))),
                    "confirmed_at": db.local(decided.get((sid, "auto_check_in")) or decided.get((sid, "check_in"))),
                    "checked_out_at": db.local(decided.get((sid, exit_action))) if exit_action else None,
                    "check_out_action": exit_action,
                    "alerts": sorted(alerts_by_booking.get(b["id"], ()))})
    return out


@router.get("/dev/clock")
def get_clock(conn=Depends(get_conn)):
    return {"now": db.now(conn).isoformat(timespec="seconds")}


@router.post("/dev/clock")
def set_clock(body: ClockIn, conn=Depends(get_conn)):
    t = db.parse(body.now) if body.now else db.now(conn)
    if body.advance_minutes:
        t += timedelta(minutes=body.advance_minutes)
    db.set_clock(conn, t)
    return {"now": db.now(conn).isoformat(timespec="seconds")}
