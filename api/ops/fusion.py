"""Likelihood-ratio fusion for booking arrival and departure signals."""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import yaml

from api import db
from api.ops.routes import audit, stay_out
from api.rhythm_api.store import latest_priors

ROOT = Path(__file__).resolve().parents[2]
CONFIG = yaml.safe_load((ROOT / "config" / "fusion.yaml").read_text(encoding="utf-8"))
ARRIVAL_POSITIVE = {"enter", "park", "scan", "confirm"}
ARRIVAL_NEGATIVE = {"deny", "exit", "unpark"}
DEPARTURE_POSITIVE = {"exit", "unpark"}
DEPARTURE_NEGATIVE = {"enter", "park", "scan", "confirm", "deny"}
ACTIVE = ("CHECKED_IN", "OVERSTAY", "UNCONFIRMED_DEPARTURE")


def _clamp_probability(value: float) -> float:
    return min(0.999999, max(0.000001, value))


def _sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-max(-40.0, min(40.0, value))))


def _prior_for(booking, lot_id: str, now: datetime) -> float:
    priors = latest_priors()
    for item in priors:
        if item["user_id"] != booking["user_id"] or item["lot_id"] != lot_id:
            continue
        start = db.parse(item["window_start"])
        end = db.parse(item["window_end"])
        if start <= now <= end:
            return _clamp_probability(float(item["p"]))
    return float(CONFIG.get("default_prior", 0.3))


def _signals_for(conn, booking) -> list:
    return conn.execute(
        "SELECT * FROM signals WHERE booking_id = ? OR "
        "(source = 'camera_plate' AND json_extract(payload, '$.plate_norm') = ?) "
        "ORDER BY observed_at, id", (booking["id"], booking["plate_norm"]),
    ).fetchall()


def _direction(signal, direction: str):
    if signal["source"] == "camera_spot":
        return None
    if direction == "arrival":
        if signal["kind"] in ARRIVAL_POSITIVE:
            return 1
        if signal["kind"] in ARRIVAL_NEGATIVE:
            return -1
    else:
        if signal["kind"] in DEPARTURE_POSITIVE:
            return 1
        if signal["kind"] in DEPARTURE_NEGATIVE:
            return -1
    return None


def _score(signals: list, prior: float, now: datetime, direction: str) -> tuple[float, list[dict]]:
    half_life = max(0.001, float(CONFIG.get("freshness_half_life_min", 15)))
    running = math.log(prior / (1 - prior))
    points: list[dict] = []
    for signal in signals:
        sign = _direction(signal, direction)
        if sign is None:
            continue
        specification = CONFIG.get("signals", {}).get(signal["source"], {})
        if specification.get("definitive"):
            log_lr = 20.0 * sign
            weighted = log_lr
            running = log_lr
        else:
            magnitude = float(specification.get("log_lr", 0))
            log_lr = magnitude * sign
            age_min = max(0.0, (now - db.parse(signal["observed_at"])).total_seconds() / 60)
            weighted = log_lr * (0.5 ** (age_min / half_life))
            running += weighted
        points.append({"signal_id": signal["id"], "direction": direction,
                       "observed_at": db.local(signal["observed_at"]), "source": signal["source"],
                       "kind": signal["kind"], "log_lr": log_lr, "weighted_log_lr": weighted,
                       "probability": _sigmoid(running)})
    return _sigmoid(running), points


def _persist_points(conn, stay_id: str, prior: float, points: list[dict], now: datetime) -> None:
    conn.executemany(
        "INSERT INTO fusion_evidence(stay_id, signal_id, direction, prior, log_lr, weighted_log_lr, probability, created_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(stay_id, signal_id, direction) DO UPDATE SET "
        "prior=excluded.prior, log_lr=excluded.log_lr, weighted_log_lr=excluded.weighted_log_lr, "
        "probability=excluded.probability, created_at=excluded.created_at",
        [(stay_id, point["signal_id"], point["direction"], prior, point["log_lr"],
          point["weighted_log_lr"], point["probability"], db.utc(now)) for point in points],
    )


def _contradiction(conn, booking, signals: list, now: datetime) -> bool:
    strong = {"nfc", "camera_plate", "gate_qr", "booking_qr", "attendant", "driver_confirm"}
    for signal in signals:
        if signal["lot_id"] == booking["lot_id"]:
            continue
        if signal["source"] in strong and signal["kind"] in ARRIVAL_POSITIVE:
            message = f"Strong arrival evidence for {booking['id']} conflicts with lot {signal['lot_id']}."
            existing = conn.execute(
                "SELECT id FROM alerts WHERE type='anomaly' AND booking_id = ? AND status='open' LIMIT 1",
                (booking["id"],),
            ).fetchone()
            stamp = db.utc(now)
            if existing:
                conn.execute("UPDATE alerts SET message=?, updated_at=? WHERE id=?",
                             (message, stamp, existing["id"]))
            else:
                conn.execute("INSERT INTO alerts(type, lot_id, booking_id, severity, status, message, created_at, updated_at) "
                             "VALUES ('anomaly', ?, ?, 'critical', 'open', ?, ?, ?)",
                             (booking["lot_id"], booking["id"], message, stamp, stamp))
            conn.commit()
            return True
    return False


def fuse_booking(conn, booking_id: str) -> dict | None:
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if booking is None:
        return None
    now = db.now(conn)
    signals = _signals_for(conn, booking)
    if not signals:
        return None
    if _contradiction(conn, booking, signals, now):
        return {"booking_id": booking_id, "contradiction": True}
    signals = [signal for signal in signals if signal["lot_id"] == booking["lot_id"]]
    if not signals:
        return None

    arrival_prior = _prior_for(booking, booking["lot_id"], now)
    arrival_probability, arrival_points = _score(signals, arrival_prior, now, "arrival")
    departure_prior = float(CONFIG.get("default_prior", 0.3))
    departure_probability, departure_points = _score(signals, departure_prior, now, "departure")
    has_arrival_signal = any(_direction(signal, "arrival") == 1 for signal in signals)
    has_departure_signal = any(_direction(signal, "departure") == 1 for signal in signals)
    earliest = min((db.parse(s["observed_at"]) for s in signals), default=now)
    arrival_time = min((db.parse(s["observed_at"]) for s in signals if _direction(s, "arrival") == 1), default=now)
    stay_id = f"S-{booking_id}"
    stay = conn.execute("SELECT * FROM stays WHERE booking_id = ? ORDER BY updated_at DESC LIMIT 1",
                        (booking_id,)).fetchone()

    if has_arrival_signal and arrival_probability >= float(CONFIG["thresholds"]["ask_driver"]):
        if stay is None:
            initial_state = "CHECKED_IN" if arrival_probability >= float(CONFIG["thresholds"]["auto_confirm"]) else "ARRIVING"
            conn.execute(
                "INSERT INTO stays(id, booking_id, lot_id, space_id, plate_norm, state, in_at, in_method, in_conf, "
                "out_at, out_method, out_conf, inferred, updated_at) VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,0,?)",
                (stay_id, booking_id, booking["lot_id"], booking["space_id"], booking["plate_norm"], initial_state,
                 db.utc(arrival_time), "auto", arrival_probability, db.utc(now)),
            )
            stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
            audit(conn, action="auto_check_in" if initial_state == "CHECKED_IN" else "arriving",
                  entity="stay", entity_id=stay_id, lot_id=booking["lot_id"], before=None,
                  after=stay_out(stay), observed_at=arrival_time, actor_type="system")
        elif stay["state"] in ("EXPECTED", "ARRIVING"):
            before = stay_out(stay)
            state = "CHECKED_IN" if arrival_probability >= float(CONFIG["thresholds"]["auto_confirm"]) else "ARRIVING"
            conn.execute("UPDATE stays SET state = ?, in_conf = ?, in_method = 'auto', updated_at = ? WHERE id = ?",
                         (state, arrival_probability, db.utc(now), stay["id"]))
            stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay["id"],)).fetchone()
            if before["state"] != state:
                audit(conn, action="auto_check_in" if state == "CHECKED_IN" else "arriving",
                      entity="stay", entity_id=stay["id"], lot_id=stay["lot_id"], before=before,
                      after=stay_out(stay), observed_at=now, actor_type="system")
        _persist_points(conn, stay["id"], arrival_prior, arrival_points, now)

    if (has_departure_signal and stay is not None and stay["state"] in ACTIVE
            and departure_probability >= float(CONFIG["thresholds"]["auto_confirm"])):
        exit_time = max((db.parse(s["observed_at"]) for s in signals if _direction(s, "departure") == 1), default=now)
        before = stay_out(stay)
        conn.execute("UPDATE stays SET state='CHECKED_OUT', out_at=?, out_method='auto', out_conf=?, updated_at=? WHERE id=?",
                     (db.utc(exit_time), departure_probability, db.utc(now), stay["id"]))
        conn.execute("UPDATE bookings SET status='completed', updated_at=? WHERE id=?", (db.utc(now), booking_id))
        stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay["id"],)).fetchone()
        audit(conn, action="auto_check_out", entity="stay", entity_id=stay["id"], lot_id=stay["lot_id"],
              before=before, after=stay_out(stay), observed_at=exit_time, actor_type="system")
        _persist_points(conn, stay["id"], departure_prior, departure_points, now)

    if stay is None and (now - earliest).total_seconds() >= float(CONFIG["thresholds"].get("attendant_after_min", 10)) * 60:
        stamp = db.utc(now)
        if not conn.execute("SELECT 1 FROM alerts WHERE type='low_confidence' AND booking_id=? AND status='open'",
                            (booking_id,)).fetchone():
            conn.execute("INSERT INTO alerts(type, lot_id, booking_id, severity, status, message, created_at, updated_at) "
                         "VALUES ('low_confidence', ?, ?, 'warning', 'open', ?, ?, ?)",
                         (booking["lot_id"], booking_id, f"Weak evidence for booking {booking_id}; attendant review needed.",
                          stamp, stamp))
    conn.commit()
    return {"booking_id": booking_id, "arrival_probability": arrival_probability,
            "departure_probability": departure_probability, "stay_id": stay["id"] if stay else None}
