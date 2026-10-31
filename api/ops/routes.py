"""Ops API for Challenge 1 (CONTRACT.md §7). Owner: P2.

P0 implements scan/check-in/out, overview, stays, audit, signal storage and
simple on-the-fly alerts. Fusion, QR signatures, rewards and live push are P2+.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api import db
from api.deps import get_conn
from api.rhythm_api.store import latest_priors

router = APIRouter()
EARLY_MIN = 30           # scan accepted this many minutes before start
OVERSTAY_GRACE_MIN = 10
EXPECTED_WINDOW_MIN = 30
DOUBLE_SCAN_MIN = 5      # a repeat scan within this window is not treated as a check-out
ACTIVE = ("CHECKED_IN", "OVERSTAY")


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
    return {"ok": True, "now": db.now(conn).isoformat(timespec="seconds"), "version": "p0"}


@router.get("/lots")
def lots(conn=Depends(get_conn)):
    return [lot_out(r) for r in conn.execute("SELECT * FROM lots ORDER BY id")]


def _lot(conn, lot_id: str):
    row = conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Unknown lot {lot_id}")
    return row


@router.get("/lots/{lot_id}/overview")
def overview(lot_id: str, conn=Depends(get_conn)):
    lot = _lot(conn, lot_id)
    t = db.now(conn)
    occupied = conn.execute(f"SELECT COUNT(*) FROM stays WHERE lot_id = ? AND state IN {ACTIVE}",
                            (lot_id,)).fetchone()[0]
    upcoming = conn.execute(
        "SELECT b.* FROM bookings b WHERE b.lot_id = ? AND b.status = 'booked' AND b.end_at > ? AND b.start_at <= ?"
        " AND NOT EXISTS (SELECT 1 FROM stays s WHERE s.booking_id = b.id) ORDER BY b.start_at",
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

    if body.qr:
        return remember(conn, body.client_event_id,
                        _result("invalid_qr", "QR codes are not supported yet. Enter the booking ID or plate."))

    booking = None
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
                                           f"{db.local(booking['end_at'])[11:16]}.", booking))
    if t < db.parse(booking["start_at"]) - timedelta(minutes=EARLY_MIN):
        return remember(conn, body.client_event_id,
                        _result("too_early", f"{plate_txt} is booked from {db.local(booking['start_at'])[11:16]}.",
                                booking))
    if conn.execute("SELECT 1 FROM stays WHERE booking_id = ?", (booking["id"],)).fetchone():
        return remember(conn, body.client_event_id,
                        _result("already_in", f"{plate_txt} already checked in and out on this booking.", booking))

    stay_id = f"S-{booking['id']}"
    conn.execute("INSERT INTO stays VALUES (?,?,?,?,?, 'CHECKED_IN', ?, 'attendant', 1.0, NULL,NULL,NULL, 0, ?)",
                 (stay_id, booking["id"], booking["lot_id"], booking["space_id"], booking["plate_norm"],
                  db.utc(observed), db.utc(t)))
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
    audit(conn, action="check_in", entity="stay", entity_id=stay_id, lot_id=booking["lot_id"], before=None,
          after=stay_out(stay), observed_at=observed, device_id=body.device_id,
          client_event_id=body.client_event_id)
    return remember(conn, body.client_event_id,
                    _result("checked_in", f"Checked in {plate_txt} at {booking['lot_id']} until "
                                          f"{db.local(booking['end_at'])[11:16]}.", booking, stay))


def _checkout(conn, stay, booking, observed: datetime, device_id, client_event_id, method) -> dict:
    before = stay_out(stay)
    conn.execute("UPDATE stays SET state = 'CHECKED_OUT', out_at = ?, out_method = ?, out_conf = 1.0,"
                 " updated_at = ? WHERE id = ?", (db.utc(observed), method, db.utc(db.now(conn)), stay["id"]))
    if booking is not None:
        conn.execute("UPDATE bookings SET status = 'completed' WHERE id = ?", (booking["id"],))
    stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay["id"],)).fetchone()
    audit(conn, action="check_out", entity="stay", entity_id=stay["id"], lot_id=stay["lot_id"], before=before,
          after=stay_out(stay), observed_at=observed, device_id=device_id, client_event_id=client_event_id)
    label = (booking["plate_display"] if booking is not None else None) or stay["plate_norm"] or stay["id"]
    return _result("checked_out", f"Checked out {label}.", booking, stay)


@router.post("/scan")
def scan(body: ScanIn, conn=Depends(get_conn)):
    return do_scan(conn, body)


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
                    "camera_spot", "car_bt", "attendant", "driver_confirm"]
    kind: Literal["enter", "exit", "park", "unpark", "scan", "occupied", "vacant", "confirm", "deny"]
    observed_at: str
    user_id: str | None = None
    booking_id: str | None = None
    payload: dict = {}


class SignalBatch(BaseModel):
    signals: list[SignalIn]


@router.post("/signals")
def signals(body: SignalIn | SignalBatch, conn=Depends(get_conn)):
    items = body.signals if isinstance(body, SignalBatch) else [body]
    received = db.utc(db.now(conn))
    accepted = 0
    for s in items:
        cur = conn.execute(
            "INSERT OR IGNORE INTO signals(client_event_id, device_id, user_id, lot_id, booking_id, source, kind,"
            " observed_at, received_at, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (s.client_event_id, s.device_id, s.user_id, s.lot_id, s.booking_id, s.source, s.kind,
             db.utc(db.parse(s.observed_at)), received, db.dumps(s.payload)))
        accepted += cur.rowcount
    conn.commit()
    # TODO(P2): run fusion for the affected bookings and update stays.
    return {"accepted": accepted, "duplicates": len(items) - accepted}


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
    """Basic replay of offline actions in observed order. TODO(P2): anomaly queue for impossible orderings."""
    acks, conflicts, results = [], [], {}
    for ev in sorted(body.events, key=lambda e: e.observed_at):
        try:
            if ev.type == "scan":
                res = do_scan(conn, ScanIn(**{**ev.body, "lot_id": ev.lot_id, "device_id": body.device_id,
                                              "client_event_id": ev.client_event_id, "observed_at": ev.observed_at}))
            elif ev.type == "checkout":
                res = do_checkout(conn, ev.body["stay_id"],
                                  CheckoutIn(device_id=body.device_id, client_event_id=ev.client_event_id,
                                             observed_at=ev.observed_at))
            else:
                conflicts.append({"id": ev.client_event_id, "reason": f"{ev.type} not supported yet"})
                continue
            acks.append(ev.client_event_id)
            results[ev.client_event_id] = res
        except HTTPException as e:
            conflicts.append({"id": ev.client_event_id, "reason": str(e.detail)})
    return {"acks": acks, "conflicts": conflicts, "results": results}


@router.get("/sync/pull")
def sync_pull(lot_id: str, since: str | None = None, conn=Depends(get_conn)):
    """Today's bookings and stays for a lot (the attendant's offline cache). TODO(P2): real cursor."""
    t = db.now(conn)
    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    bks = conn.execute("SELECT * FROM bookings WHERE lot_id = ? AND start_at >= ? AND start_at < ?",
                       (lot_id, db.utc(day_start), db.utc(day_start + timedelta(days=1)))).fetchall()
    sts = conn.execute("SELECT * FROM stays WHERE lot_id = ?", (lot_id,)).fetchall()
    return {"cursor": t.isoformat(timespec="seconds"), "bookings": [booking_out(b) for b in bks],
            "stays": [stay_out(s) for s in sts]}


# ---------- alerts (computed on the fly in P0) ----------
@router.get("/alerts")
def alerts(lot_id: str | None = None, conn=Depends(get_conn)):
    t = db.now(conn)
    lot_filter, args = ("AND s.lot_id = ?", [lot_id]) if lot_id else ("", [])
    out = []
    for r in conn.execute(
            f"SELECT s.*, b.end_at, b.plate_display FROM stays s JOIN bookings b ON b.id = s.booking_id"
            f" WHERE s.state IN {ACTIVE} AND b.end_at < ? {lot_filter}",
            [db.utc(t - timedelta(minutes=OVERSTAY_GRACE_MIN))] + args):
        mins = int((t - db.parse(r["end_at"])).total_seconds() // 60)
        out.append({"type": "overstay", "severity": "warning", "lot_id": r["lot_id"], "stay_id": r["id"],
                    "booking_id": r["booking_id"],
                    "message": f"{r['plate_display']} is {mins} min past its booking end.",
                    "created_at": db.local(r["end_at"])})
    b_filter = "AND b.lot_id = ?" if lot_id else ""
    for r in conn.execute(
            f"SELECT b.* FROM bookings b WHERE b.status = 'booked' AND b.start_at BETWEEN ? AND ? {b_filter}"
            f" AND NOT EXISTS (SELECT 1 FROM stays s WHERE s.booking_id = b.id) ORDER BY b.start_at",
            [db.utc(t), db.utc(t + timedelta(minutes=EXPECTED_WINDOW_MIN))] + args):
        out.append({"type": "upcoming_arrival", "severity": "info", "lot_id": r["lot_id"], "stay_id": None,
                    "booking_id": r["id"], "message": f"{r['plate_display']} due at {db.local(r['start_at'])[11:16]}.",
                    "created_at": t.isoformat(timespec="seconds")})
    return out


# ---------- not built yet ----------
@router.post("/stays/{stay_id}/confirm")
def confirm(stay_id: str):
    raise HTTPException(501, "Driver confirmation is not built yet (phase P3).")


@router.get("/users/{user_id}/rewards")
def rewards(user_id: str, conn=Depends(get_conn)):
    pts = conn.execute("SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE user_id = ?", (user_id,)).fetchone()[0]
    tier = conn.execute("SELECT tier FROM users WHERE id = ?", (user_id,)).fetchone()
    if tier is None:
        raise HTTPException(404, f"Unknown user {user_id}")
    return {"user_id": user_id, "points": pts, "grace_minutes": 0, "tier": tier["tier"]}


@router.get("/keys/qr")
def qr_key():
    raise HTTPException(501, "QR signing keys are not built yet (phase P2).")


# ---------- demo controls ----------
class ClockIn(BaseModel):
    now: str | None = None
    advance_minutes: int | None = None


@router.post("/dev/reset")
def dev_reset(conn=Depends(get_conn)):
    return db.reset(conn)


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
