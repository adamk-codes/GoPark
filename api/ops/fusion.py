"""Likelihood-ratio fusion for booking arrival and departure signals (ARCHITECTURE_PLAN.md §3.1).

    logit P = logit(prior) + Σ log_lr(source) · 0.5^(age_min / freshness_half_life)

`score` and `decide` are pure (no database), so tools/simulator can replay traces offline
with other settings. `fuse_booking` loads one booking's signals, calls `decide` and writes the
stay, evidence, audit and alerts. `attribute_camera` links camera signals (no booking_id) to a
booking by plate or watched space; `pair_gate` pairs gate presence sensor events with plate reads.
`sweep` runs the time-based rules (unidentified gate events, low confidence).
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from api import db
from api.ops.routes import ACTIVE, _upsert_alert, audit, stay_out
from api.rhythm_api.store import latest_priors

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("GOPARK_FUSION_CONFIG", ROOT / "config" / "fusion.yaml"))
DEFINITIVE_LOG_LR = 20.0
ARRIVAL_POSITIVE = {"enter", "park", "scan", "confirm", "occupied"}
ARRIVAL_NEGATIVE = {"deny", "exit", "unpark"}
DEPARTURE_POSITIVE = {"exit", "unpark", "vacant"}
DEPARTURE_NEGATIVE = {"enter", "park", "occupied"}
# Sources that identify the car (or the driver in person), so seeing them at two lots is a contradiction.
STRONG = {"nfc", "camera_plate", "gate_qr", "booking_qr", "attendant", "driver_confirm"}
OPEN = ("ARRIVING",) + ACTIVE


def load_config(path: Path | str = CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


CONFIG = load_config()


def use_config(cfg: dict) -> None:
    """Swap the settings used by the API (the simulator compares configs in one process)."""
    global CONFIG
    CONFIG = cfg


def _logit(p: float) -> float:
    p = min(0.999999, max(0.000001, p))
    return math.log(p / (1 - p))


def _sigmoid(value: float) -> float:
    return 1 / (1 + math.exp(-max(-40.0, min(40.0, value))))


def _minutes(cfg: dict, key: str, default: float) -> timedelta:
    return timedelta(minutes=float(cfg.get(key, default)))


# ---------- pure scoring ----------
def signal_log_lr(sig: dict, direction: str, cfg: dict) -> float | None:
    """Signed log-LR of one signal for arrival/departure at the booked lot; None = not evidence.

    `sig` has source, kind, observed_at (datetime), and optional `elsewhere` (seen at another
    lot) and `fuzzy` (camera plate matched with one misread character).
    """
    kind = sig["kind"]
    if direction == "arrival":
        sign = 1 if kind in ARRIVAL_POSITIVE else -1 if kind in ARRIVAL_NEGATIVE else 0
    else:
        sign = 1 if kind in DEPARTURE_POSITIVE else -1 if kind in DEPARTURE_NEGATIVE else 0
    if sign == 0:
        return None
    spec = cfg.get("signals", {}).get(sig["source"], {})
    if sig["source"] == "camera_vehicle":
        payload = sig.get("payload") or {}
        candidates = payload.get("candidates") or []
        if not candidates or candidates[0].get("booking_id") != sig.get("booking_id"):
            return None
        top_score = max(0.0, float(candidates[0].get("score", 0)))
        if top_score < float(spec.get("min_score", 0)):
            return None
        top_vehicle = candidates[0].get("vehicle") or {}
        same_appearance = sum(
            1 for candidate in candidates
            if (candidate.get("vehicle") or {}).get("color") == top_vehicle.get("color")
            and (candidate.get("vehicle") or {}).get("body") == top_vehicle.get("body")
        )
        same_appearance = max(1, int(payload.get("appearance_count", same_appearance)))
        margin = max(0.0, float(payload.get("top_margin", 0)))
        margin_scale = max(0.001, float(spec.get("margin_scale", 0.08)))
        margin_factor = min(1.0, margin / margin_scale)
        uniqueness = 1.0 / max(1, same_appearance)
        evidence = float(spec.get("log_lr", 0)) * margin_factor * uniqueness
        return min(float(spec.get("max_log_lr", evidence)), evidence) * sign
    if sig.get("elsewhere"):
        # Arriving at another lot is evidence against arriving here; nothing else from there counts.
        if direction != "arrival" or sign < 0:
            return None
        if sig["source"] in STRONG:
            return -float(cfg.get("strong_elsewhere_log_lr", 4.0))
        return -float(spec.get("log_lr", 0)) * float(cfg.get("elsewhere_factor", 1.0))
    if spec.get("definitive"):
        if direction == "departure" and sign < 0:
            return None  # an arrival scan says nothing about the car still being there hours later
        return DEFINITIVE_LOG_LR * sign
    if sig.get("fuzzy"):
        return float(spec.get("fuzzy_log_lr", 0)) * sign
    return float(spec.get("log_lr", 0)) * sign


def score(signals: list[dict], prior: float, now: datetime, direction: str,
          cfg: dict | None = None) -> tuple[float, list[dict]]:
    """Fused probability at `now` plus one evidence point per signal that counted."""
    cfg = cfg or CONFIG
    half_life = max(0.001, float(cfg.get("freshness_half_life_min", 15)))
    running = _logit(prior)
    points: list[dict] = []
    for sig in sorted(signals, key=lambda s: s["observed_at"]):
        log_lr = signal_log_lr(sig, direction, cfg)
        if log_lr is None:
            continue
        if log_lr <= -DEFINITIVE_LOG_LR and direction == "arrival":
            # A definitive "no" (driver: not me) discards the evidence so far; later evidence starts from the prior.
            weighted, running = _logit(prior) - running, _logit(prior)
        elif abs(log_lr) >= DEFINITIVE_LOG_LR:
            weighted = running = log_lr  # definitive sources settle it outright
        else:
            age_min = max(0.0, (now - sig["observed_at"]).total_seconds() / 60)
            weighted = log_lr * 0.5 ** (age_min / half_life)
            running += weighted
        points.append({"signal_id": sig.get("id"), "direction": direction, "observed_at": sig["observed_at"],
                       "source": sig["source"], "kind": sig["kind"], "log_lr": log_lr,
                       "weighted_log_lr": weighted, "probability": _sigmoid(running)})
    return _sigmoid(running), points


def _is_positive(sig: dict, direction: str, cfg: dict) -> bool:
    value = signal_log_lr(sig, direction, cfg)
    return value is not None and value > 0


def decide(stay: dict | None, signals: list[dict], now: datetime, prior: float, cfg: dict | None = None) -> dict:
    """Next state of one booking's stay given every signal received so far.

    `stay` is None or {"state", "in_at" (datetime), "inferred" (bool)}. Returns the scores, the new
    `state`, and `arrival` in {None, "arriving", "check_in"} / `departure` in {None, "check_out",
    "moved", "correct_out"} with `in_at` / `out_at`. `contradiction` = strong arrival evidence here
    and at another lot; such a stay is never auto-confirmed.
    """
    cfg = cfg or CONFIG
    th = cfg["thresholds"]
    auto, ask = float(th["auto_confirm"]), float(th["ask_driver"])
    here = [s for s in signals if not s.get("elsewhere")]
    strong_elsewhere = [s for s in signals if s.get("elsewhere") and s["source"] in STRONG
                        and s["kind"] in ARRIVAL_POSITIVE]
    state = stay["state"] if stay else None
    in_at = stay["in_at"] if stay else None
    out = {"state": state, "arrival": None, "departure": None, "in_at": None, "out_at": None,
           "arrival_p": None, "arrival_points": [], "departure_p": None, "departure_points": [],
           "contradiction": False, "vehicle_suggestion": None}

    if state in (None, "ARRIVING"):
        p, points = score(signals, prior, now, "arrival", cfg)
        denied = max((s["observed_at"] for s in here
                      if (signal_log_lr(s, "arrival", cfg) or 0) <= -DEFINITIVE_LOG_LR), default=None)
        arrivals = [s for s in here if _is_positive(s, "arrival", cfg) and (denied is None or s["observed_at"] > denied)]
        definitive = any(cfg["signals"].get(s["source"], {}).get("definitive") for s in arrivals)
        vehicle_only = bool(arrivals) and all(s["source"] == "camera_vehicle" for s in arrivals)
        if vehicle_only:
            p = min(p, float(cfg.get("camera_vehicle_max_probability", 0.94)))
            for point in points:
                point["probability"] = min(point["probability"], float(cfg.get("camera_vehicle_max_probability", 0.94)))
            vehicle_signal = next((s for s in reversed(arrivals) if s["source"] == "camera_vehicle"), None)
            if vehicle_signal:
                candidates = vehicle_signal.get("payload", {}).get("candidates", [])
                if candidates:
                    out["vehicle_suggestion"] = {
                        **candidates[0], "top_margin": vehicle_signal.get("payload", {}).get("top_margin", 0)}
        out.update(arrival_p=p, arrival_points=points)
        contradiction = bool(strong_elsewhere) and any(s["source"] in STRONG for s in arrivals)
        out["contradiction"] = contradiction
        if arrivals and p >= ask:
            strength = _logit(p) - _logit(prior)
            confident = not vehicle_only and p >= auto and (definitive or (
                not contradiction and strength >= float(cfg.get("min_evidence_log_lr", 0))))
            new_state = "CHECKED_IN" if confident else "ARRIVING"
            if new_state != state:
                in_at = in_at or min(s["observed_at"] for s in arrivals)
                out.update(state=new_state, in_at=in_at,
                           arrival="check_in" if new_state == "CHECKED_IN" else "arriving")

    reopen = state == "CHECKED_OUT" and stay and stay.get("inferred")
    if (out["state"] in OPEN or reopen) and in_at is not None:
        settled = in_at + _minutes(cfg, "min_stay_min", 5)
        after = [s for s in here if s["observed_at"] > settled]
        prior_out = float(cfg.get("departure_prior", cfg.get("default_prior", 0.3)))
        dp, dpoints = score(after, prior_out, now, "departure", cfg)
        out.update(departure_p=dp, departure_points=dpoints)
        exit_signals = [s for s in after if _is_positive(s, "departure", cfg)]
        vehicle_only_exit = bool(exit_signals) and all(s["source"] == "camera_vehicle" for s in exit_signals)
        if vehicle_only_exit:
            dp = min(dp, float(cfg.get("camera_vehicle_max_probability", 0.94)))
            for point in dpoints:
                point["probability"] = min(point["probability"], float(cfg.get("camera_vehicle_max_probability", 0.94)))
            out["departure_p"] = dp
        exits = sorted(s["observed_at"] for s in exit_signals)
        moved = sorted(s["observed_at"] for s in strong_elsewhere if s["observed_at"] > settled)
        if exits and not vehicle_only_exit and dp >= auto:
            # The departure is the first exit-like signal of the last burst (unpark, gate camera, fence).
            burst = [t for t in exits if t >= exits[-1] - timedelta(minutes=30)]
            out.update(state="CHECKED_OUT", out_at=burst[0], departure="correct_out" if reopen else "check_out")
        elif moved and not reopen:
            out.update(state="CHECKED_OUT", out_at=moved[0], departure="moved")
    return out


def prior_for(user_id: str, lot_id: str, at: datetime, priors: list[dict], cfg: dict | None = None) -> float:
    """C2 arrival prior for the window containing `at`, else `default_prior`."""
    cfg = cfg or CONFIG
    for item in priors:
        if item["user_id"] != user_id or item["lot_id"] != lot_id:
            continue
        if db.parse(item["window_start"]) <= at <= db.parse(item["window_end"]):
            return min(float(cfg.get("max_prior", 0.99)), max(0.01, float(item["p"])))
    return float(cfg.get("default_prior", 0.3))


# ---------- camera attribution ----------
def _one_char_apart(a: str | None, b: str | None) -> bool:
    return bool(a and b) and len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def attribute_camera(conn, lot_id: str, source: str, kind: str, observed: datetime, payload: dict) -> str | None:
    """Booking a camera signal belongs to, or None.

    `camera_plate` matches `payload.plate_norm` exactly, or (when `ocr_conf` is low) with one misread
    character if only one booked plate fits. `camera_spot` matches the booking holding `payload.space_id`.
    Among candidates, enter/occupied prefer a booking without a stay and the nearest start;
    exit/vacant prefer one with an open stay and the nearest end.
    """
    match = CONFIG.get("matching", {})
    lo = db.utc(observed - _minutes(match, "after_end_min", 180))
    hi = db.utc(observed + _minutes(match, "before_start_min", 90))
    rows = conn.execute(
        "SELECT b.id, b.plate_norm, b.space_id, b.start_at, b.end_at, "
        "(SELECT s.state FROM stays s WHERE s.booking_id = b.id ORDER BY s.updated_at DESC LIMIT 1) AS stay_state "
        "FROM bookings b WHERE b.lot_id = ? AND b.status != 'cancelled' AND b.start_at <= ? AND b.end_at >= ?",
        (lot_id, hi, lo)).fetchall()
    if source == "camera_plate":
        plate = db.normalize_plate(payload.get("plate_norm"))
        candidates = [r for r in rows if r["plate_norm"] == plate]
        if not candidates and float(payload.get("ocr_conf", 1.0)) < float(match.get("fuzzy_below_conf", 0.8)):
            near = [r for r in rows if _one_char_apart(r["plate_norm"], plate)]
            if len({r["plate_norm"] for r in near}) == 1:
                candidates = near
    elif source == "camera_spot":
        candidates = [r for r in rows if r["space_id"] and r["space_id"] == payload.get("space_id")]
    else:
        return None
    if not candidates:
        return None
    arriving = kind in ("enter", "occupied")

    def rank(r):
        fits = r["stay_state"] in (None, "ARRIVING") if arriving else r["stay_state"] in OPEN
        anchor = db.parse(r["start_at"] if arriving else r["end_at"])
        return (0 if fits else 1, abs((anchor - observed).total_seconds()))
    return min(candidates, key=rank)["id"]


def plate_bookings_elsewhere(conn, plate: str | None, lot_id: str, observed: datetime) -> set[str]:
    """Bookings for this plate at other lots around `observed` (for contradictions and moves)."""
    if not plate:
        return set()
    match = CONFIG.get("matching", {})
    rows = conn.execute(
        "SELECT id FROM bookings WHERE plate_norm = ? AND lot_id != ? AND status != 'cancelled' "
        "AND start_at <= ? AND end_at >= ?",
        (plate, lot_id, db.utc(observed + _minutes(match, "before_start_min", 90)),
         db.utc(observed - _minutes(match, "after_end_min", 180)))).fetchall()
    return {r["id"] for r in rows}


# ---------- database wrapper ----------
def _signals_for(conn, booking) -> list[dict]:
    """The booking's own signals, camera signals attributed to it, and unattributed sightings of its plate
    at other lots inside the booking window."""
    match = CONFIG.get("matching", {})
    lo = db.utc(db.parse(booking["start_at"]) - _minutes(match, "before_start_min", 90))
    hi = db.utc(db.parse(booking["end_at"]) + _minutes(match, "after_end_min", 180))
    rows = conn.execute(
        "SELECT * FROM signals WHERE booking_id = ? OR matched_booking_id = ? OR "
        "(source = 'camera_plate' AND matched_booking_id IS NULL AND lot_id != ? "
        " AND json_extract(payload, '$.plate_norm') = ? AND observed_at BETWEEN ? AND ?) "
        "ORDER BY observed_at, id",
        (booking["id"], booking["id"], booking["lot_id"], booking["plate_norm"], lo, hi)).fetchall()
    out = []
    for r in rows:
        payload = json.loads(r["payload"] or "{}")
        out.append({"id": r["id"], "source": r["source"], "kind": r["kind"], "lot_id": r["lot_id"],
                    "booking_id": r["booking_id"] or r["matched_booking_id"], "payload": payload,
                    "observed_at": db.parse(r["observed_at"]), "elsewhere": r["lot_id"] != booking["lot_id"],
                    "fuzzy": r["source"] == "camera_plate"
                    and db.normalize_plate(payload.get("plate_norm")) != booking["plate_norm"]})
    return out


def _persist_points(conn, stay_id: str, prior: float, points: list[dict], now: datetime) -> None:
    conn.executemany(
        "INSERT INTO fusion_evidence(stay_id, signal_id, direction, prior, log_lr, weighted_log_lr, probability, created_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(stay_id, signal_id, direction) DO UPDATE SET "
        "prior=excluded.prior, log_lr=excluded.log_lr, weighted_log_lr=excluded.weighted_log_lr, "
        "probability=excluded.probability, created_at=excluded.created_at",
        [(stay_id, p["signal_id"], p["direction"], prior, p["log_lr"], p["weighted_log_lr"], p["probability"],
          db.utc(now)) for p in points],
    )


def _resolve_alerts(conn, now: datetime, *, stay_id: str | None = None, booking_id: str | None = None,
                    alert_type: str | None = None) -> None:
    q = "UPDATE alerts SET status='resolved', resolved_by='system', resolved_at=?, updated_at=? WHERE status='open'"
    args: list = [db.utc(now), db.utc(now)]
    if stay_id:
        q, args = q + " AND stay_id = ?", args + [stay_id]
    if booking_id:
        q, args = q + " AND booking_id = ?", args + [booking_id]
    if alert_type:
        q, args = q + " AND type = ?", args + [alert_type]
    conn.execute(q, args)


def _check_out(conn, stay, *, out_at: datetime, method: str, conf: float | None, inferred: bool,
               action: str, now: datetime) -> None:
    before = stay_out(stay)
    conn.execute("UPDATE stays SET state='CHECKED_OUT', out_at=?, out_method=?, out_conf=?, inferred=?, updated_at=? "
                 "WHERE id=?", (db.utc(out_at), method, conf, int(inferred), db.utc(now), stay["id"]))
    if stay["booking_id"]:
        conn.execute("UPDATE bookings SET status='completed', updated_at=? WHERE id=?", (db.utc(now), stay["booking_id"]))
    _resolve_alerts(conn, now, stay_id=stay["id"])
    if stay["booking_id"]:
        resolve_gate(conn, stay["lot_id"], "exit", out_at, stay["booking_id"], stay["id"])
    after = conn.execute("SELECT * FROM stays WHERE id = ?", (stay["id"],)).fetchone()
    audit(conn, action=action, entity="stay", entity_id=stay["id"], lot_id=stay["lot_id"], before=before,
          after=stay_out(after), observed_at=out_at, actor_type="system")


def _close_elsewhere(conn, booking, arrived_at: datetime, conf: float, now: datetime) -> None:
    """Arrival at this lot ends any open stay of the same car at another lot."""
    if not booking["plate_norm"]:
        return
    for other in conn.execute(f"SELECT * FROM stays WHERE plate_norm = ? AND lot_id != ? AND state IN {OPEN} "
                              "AND in_at < ?", (booking["plate_norm"], booking["lot_id"], db.utc(arrived_at))).fetchall():
        _check_out(conn, other, out_at=arrived_at, method="inferred", conf=conf, inferred=True,
                   action="closed_by_arrival_elsewhere", now=now)


def fuse_booking(conn, booking_id: str) -> dict | None:
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
    if booking is None:
        return None
    signals = _signals_for(conn, booking)
    if not signals:
        return None
    now = db.now(conn)
    stay = conn.execute("SELECT * FROM stays WHERE booking_id = ? ORDER BY updated_at DESC LIMIT 1",
                        (booking_id,)).fetchone()
    # EXPECTED = a driver said "not me" to an earlier ARRIVING; fuse it as if there were no stay yet.
    current = None if stay is None or stay["state"] == "EXPECTED" else {
        "state": stay["state"], "in_at": db.parse(stay["in_at"]) if stay["in_at"] else now,
        "inferred": bool(stay["inferred"])}
    prior = prior_for(booking["user_id"], booking["lot_id"], now, latest_priors())
    d = decide(current, signals, now, prior)
    stay_id = stay["id"] if stay else f"S-{booking_id}"

    if d["arrival"]:
        before = stay_out(stay)
        if stay is None:
            conn.execute(
                "INSERT INTO stays(id, booking_id, lot_id, space_id, plate_norm, state, in_at, in_method, in_conf, "
                "out_at, out_method, out_conf, inferred, updated_at) VALUES (?,?,?,?,?,?,?,'auto',?,NULL,NULL,NULL,0,?)",
                (stay_id, booking_id, booking["lot_id"], booking["space_id"], booking["plate_norm"], d["state"],
                 db.utc(d["in_at"]), d["arrival_p"], db.utc(now)))
        else:
            conn.execute("UPDATE stays SET state=?, in_at=?, in_conf=?, in_method='auto', updated_at=? WHERE id=?",
                         (d["state"], db.utc(d["in_at"]), d["arrival_p"], db.utc(now), stay_id))
        stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
        audit(conn, action="auto_check_in" if d["arrival"] == "check_in" else "arriving", entity="stay",
              entity_id=stay_id, lot_id=booking["lot_id"], before=before, after=stay_out(stay),
              observed_at=d["in_at"], actor_type="system")
        suggestion = d.get("vehicle_suggestion")
        if suggestion:
            vehicle = suggestion.get("vehicle") or {}
            description = " ".join(str(vehicle.get(field, "")) for field in ("color", "make", "body")).strip()
            message = (f"Car-look suggests booking {booking_id} ({description}); verify with the driver or attendant. "
                       f"Visual score {float(suggestion.get('score', 0)):.2f}, margin "
                       f"{float(suggestion.get('top_margin', 0)):.2f}.")
            _upsert_alert(conn, alert_type="low_confidence", severity="warning", lot_id=booking["lot_id"],
                          stay_id=stay_id, booking_id=booking_id, at=now, message=message)
        else:
            _resolve_alerts(conn, now, booking_id=booking_id, alert_type="low_confidence")
        if before is None or before["state"] == "EXPECTED":
            resolve_gate(conn, booking["lot_id"], "enter", d["in_at"], booking_id, stay_id)
        if d["arrival"] == "check_in":
            _close_elsewhere(conn, booking, d["in_at"], d["arrival_p"], now)
    elif stay is not None and stay["state"] == "ARRIVING" and d["arrival_p"] is not None:
        conn.execute("UPDATE stays SET in_conf=?, updated_at=? WHERE id=?", (d["arrival_p"], db.utc(now), stay_id))
    if stay is not None and d["arrival_points"]:
        _persist_points(conn, stay_id, prior, d["arrival_points"], now)

    if d["departure"] and stay is not None:
        prior_out = float(CONFIG.get("departure_prior", CONFIG.get("default_prior", 0.3)))
        if d["departure"] == "moved":
            _check_out(conn, stay, out_at=d["out_at"], method="inferred", conf=None, inferred=True,
                       action="closed_by_arrival_elsewhere", now=now)
        else:
            _check_out(conn, stay, out_at=d["out_at"], method="auto", conf=d["departure_p"], inferred=False,
                       action="corrected_check_out" if d["departure"] == "correct_out" else "auto_check_out", now=now)
        _persist_points(conn, stay_id, prior_out, d["departure_points"], now)
        stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()

    if d["contradiction"]:
        others = sorted({s["lot_id"] for s in signals if s.get("elsewhere") and s["source"] in STRONG})
        _upsert_alert(conn, alert_type="anomaly", severity="critical", lot_id=booking["lot_id"], stay_id=None,
                      booking_id=booking_id, at=now,
                      message=f"{booking['plate_display'] or booking_id} has strong arrival evidence at "
                              f"{booking['lot_id']} and at {', '.join(others)}; check before confirming.")
    th = CONFIG["thresholds"]
    first_here = min((s["observed_at"] for s in signals
                      if not s.get("elsewhere") and _is_positive(s, "arrival", CONFIG)), default=None)
    if (stay is None and first_here is not None and booking["status"] == "booked"
            and now - first_here >= _minutes(th, "attendant_after_min", 10)
            and not conn.execute("SELECT 1 FROM alerts WHERE type='low_confidence' AND booking_id=?",
                                 (booking_id,)).fetchone()):
        suggestion = d.get("vehicle_suggestion")
        if suggestion:
            vehicle = suggestion.get("vehicle") or {}
            appearance = " ".join(str(vehicle.get(field, "")) for field in ("color", "make", "body")).strip()
            reason = f" Car-look suggests {booking_id} ({appearance}); verify manually."
        else:
            reason = ""
        _upsert_alert(conn, alert_type="low_confidence", severity="warning", lot_id=booking["lot_id"], stay_id=None,
                      booking_id=booking_id, at=now,
                      message=f"Weak evidence for {booking['plate_display'] or booking_id} "
                              f"(P={d['arrival_p']:.2f}); attendant review needed.{reason}")
    conn.commit()
    return {"booking_id": booking_id, "arrival_probability": d["arrival_p"],
            "departure_probability": d["departure_p"], "stay_id": stay["id"] if stay else None,
            "state": stay["state"] if stay else None, "contradiction": d["contradiction"],
            "vehicle_suggestion": d.get("vehicle_suggestion")}


def inferred_departure(conn, stay, now: datetime) -> datetime | None:
    """Best guess for a silent departure: the last exit-like signal, else the booking end.

    None while the spot camera's last word is still `occupied` (the car is visibly there), up to
    `still_parked_max_min` past the booking end.
    """
    booking = conn.execute("SELECT * FROM bookings WHERE id = ?", (stay["booking_id"],)).fetchone()
    end = db.parse(booking["end_at"])
    since = db.parse(stay["in_at"]) if stay["in_at"] else end
    signals = [s for s in _signals_for(conn, booking) if not s["elsewhere"] and since < s["observed_at"] <= now]
    spot = [s for s in signals if s["source"] == "camera_spot"]
    if spot and spot[-1]["kind"] == "occupied" and now < end + _minutes(CONFIG, "still_parked_max_min", 180):
        return None
    exits = [s["observed_at"] for s in signals if s["kind"] in DEPARTURE_POSITIVE]
    if exits:
        return exits[-1]
    # A car left through the gate unidentified near the booking end: most likely this one.
    window = _minutes(CONFIG.get("gate_pairing", {}), "resolve_window_min", 15) * 2
    gate = conn.execute(
        "SELECT observed_at FROM gate_events WHERE lot_id = ? AND kind = 'exit' AND status = 'unidentified' "
        "AND stay_id IS NULL AND observed_at BETWEEN ? AND ? "
        "ORDER BY ABS(julianday(observed_at) - julianday(?)) LIMIT 1",
        (stay["lot_id"], db.utc(max(since, end - window)), db.utc(min(now, end + window)), db.utc(end))).fetchone()
    return db.parse(gate["observed_at"]) if gate else end


# ---------- gate presence sensors ----------
def pair_gate(conn, signal_id: int) -> str | None:
    """Pair a new `gate_sensor` or `camera_plate` signal with its counterpart: same lot, same direction,
    within `gate_pairing.window_s`. The pair is one identified car; returns the plate read's booking."""
    sig = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    if sig is None or sig["kind"] not in ("enter", "exit"):
        return None
    window = timedelta(seconds=float(CONFIG.get("gate_pairing", {}).get("window_s", 8)))
    observed = db.parse(sig["observed_at"])
    span = (sig["lot_id"], sig["kind"], db.utc(observed - window), db.utc(observed + window), sig["observed_at"])
    if sig["source"] == "gate_sensor":
        conn.execute("INSERT OR IGNORE INTO gate_events(signal_id, lot_id, kind, observed_at) VALUES (?,?,?,?)",
                     (signal_id, sig["lot_id"], sig["kind"], sig["observed_at"]))
        plate = conn.execute(
            "SELECT s.* FROM signals s WHERE s.source = 'camera_plate' AND s.lot_id = ? AND s.kind = ? "
            "AND s.observed_at BETWEEN ? AND ? AND NOT EXISTS (SELECT 1 FROM gate_events g WHERE g.plate_signal_id = s.id) "
            "ORDER BY ABS(julianday(s.observed_at) - julianday(?)) LIMIT 1", span).fetchone()
        gate_id = signal_id
    elif sig["source"] == "camera_plate":
        plate = sig
        gate = conn.execute(
            "SELECT signal_id FROM gate_events WHERE lot_id = ? AND kind = ? AND status IN ('pending', 'unidentified') "
            "AND plate_signal_id IS NULL AND observed_at BETWEEN ? AND ? "
            "ORDER BY ABS(julianday(observed_at) - julianday(?)) LIMIT 1", span).fetchone()
        gate_id = gate["signal_id"] if gate else None
    else:
        return None
    if plate is None or gate_id is None:
        return None
    event = conn.execute("SELECT * FROM gate_events WHERE signal_id = ?", (gate_id,)).fetchone()
    booking_id = plate["matched_booking_id"]
    now = db.now(conn)
    if event["status"] == "unidentified":
        _undo_unidentified(conn, event, now)   # the plate upload was late: a known car after all
    conn.execute("UPDATE gate_events SET status = 'paired', plate_signal_id = ?, booking_id = ? WHERE signal_id = ?",
                 (plate["id"], booking_id, gate_id))
    if booking_id:
        conn.execute("UPDATE signals SET matched_booking_id = ? WHERE id = ?", (booking_id, gate_id))
    else:
        # Plate read but nothing booked for it: an identified car without a booking.
        _unidentified(conn, event, now, plate_norm=json.loads(plate["payload"] or "{}").get("plate_norm"))
        conn.execute("UPDATE gate_events SET status = 'paired' WHERE signal_id = ?", (gate_id,))
    return booking_id


def _unidentified(conn, event, now: datetime, plate_norm: str | None = None) -> None:
    """A gate event no booking claimed. Entry: a booking-less stay G-<signal id> (counted in occupancy) + alert.
    Exit: closes the oldest open booking-less stay at the lot, else an alert (occupancy counts the car as gone)."""
    at = db.parse(event["observed_at"])
    hhmm = at.astimezone(db.TZ).strftime("%H:%M")
    payload = conn.execute("SELECT payload FROM signals WHERE id = ?", (event["signal_id"],)).fetchone()["payload"]
    gate = json.loads(payload or "{}").get("gate_id", "gate")
    who = plate_norm or "identity unknown"
    stay_id = None
    if event["kind"] == "enter":
        stay_id = f"G-{event['signal_id']}"
        conn.execute("INSERT OR IGNORE INTO stays(id, booking_id, lot_id, space_id, plate_norm, state, in_at, in_method, "
                     "in_conf, out_at, out_method, out_conf, inferred, updated_at) "
                     "VALUES (?, NULL, ?, NULL, ?, 'CHECKED_IN', ?, 'auto', NULL, NULL, NULL, NULL, 1, ?)",
                     (stay_id, event["lot_id"], plate_norm, event["observed_at"], db.utc(now)))
        stay = conn.execute("SELECT * FROM stays WHERE id = ?", (stay_id,)).fetchone()
        audit(conn, action="gate_unidentified_entry", entity="stay", entity_id=stay_id, lot_id=event["lot_id"],
              before=None, after=stay_out(stay), observed_at=at, actor_type="system")
        message = f"Car in at {event['lot_id']} {gate} {hhmm}, {who}, no booking matched; scan it or wait for the phone."
    else:
        q = ("SELECT * FROM stays WHERE lot_id = ? AND booking_id IS NULL AND state = 'CHECKED_IN' AND in_at <= ? "
             + ("AND plate_norm = ? " if plate_norm else "AND plate_norm IS NULL ") + "ORDER BY in_at LIMIT 1")
        unknown = conn.execute(q, (event["lot_id"], event["observed_at"]) + ((plate_norm,) if plate_norm else ())
                               ).fetchone()
        if unknown:
            _check_out(conn, unknown, out_at=at, method="inferred", conf=None, inferred=True,
                       action="gate_unidentified_exit", now=now)
            conn.execute("UPDATE gate_events SET status = 'unidentified', stay_id = ? WHERE signal_id = ?",
                         (unknown["id"], event["signal_id"]))
            return
        message = f"Car out at {event['lot_id']} {gate} {hhmm}, {who}; no open stay matched."
    stamp = db.utc(now)
    alert_id = conn.execute(
        "INSERT INTO alerts(type, lot_id, stay_id, booking_id, severity, status, message, created_at, updated_at) "
        "VALUES (?, ?, ?, NULL, 'warning', 'open', ?, ?, ?)",
        ("unknown_vehicle" if plate_norm else "low_confidence", event["lot_id"], stay_id, message, stamp, stamp)
    ).lastrowid
    conn.execute("UPDATE gate_events SET status = 'unidentified', stay_id = ?, alert_id = ? WHERE signal_id = ?",
                 (stay_id, alert_id, event["signal_id"]))


def _undo_unidentified(conn, event, now: datetime) -> None:
    """An unidentified gate event turned out to be a known car: drop its placeholder stay and alert."""
    if event["alert_id"]:
        conn.execute("UPDATE alerts SET status='resolved', resolved_by='system', resolved_at=?, updated_at=? "
                     "WHERE id = ? AND status = 'open'", (db.utc(now), db.utc(now), event["alert_id"]))
    if event["kind"] == "enter" and event["stay_id"]:
        stay = conn.execute("SELECT * FROM stays WHERE id = ?", (event["stay_id"],)).fetchone()
        if stay and stay["booking_id"] is None and stay["state"] == "CHECKED_IN":
            conn.execute("UPDATE stays SET state = 'CANCELLED', updated_at = ? WHERE id = ?", (db.utc(now), stay["id"]))
            audit(conn, action="gate_identified", entity="stay", entity_id=stay["id"], lot_id=stay["lot_id"],
                  before=stay_out(stay), after={**stay_out(stay), "state": "CANCELLED"}, observed_at=now,
                  actor_type="system")


def resolve_gate(conn, lot_id: str, kind: str, at: datetime, booking_id: str | None, stay_id: str) -> None:
    """A booked stay started (`enter`) or ended (`exit`) at `at`: claim the nearest unidentified gate event."""
    window = _minutes(CONFIG.get("gate_pairing", {}), "resolve_window_min", 15)
    claimable = "stay_id IS NOT NULL" if kind == "enter" else "stay_id IS NULL"
    event = conn.execute(
        f"SELECT * FROM gate_events WHERE lot_id = ? AND kind = ? AND status = 'unidentified' AND {claimable} "
        "AND observed_at BETWEEN ? AND ? ORDER BY ABS(julianday(observed_at) - julianday(?)) LIMIT 1",
        (lot_id, kind, db.utc(at - window), db.utc(at + window), db.utc(at))).fetchone()
    if event is None:
        return
    if kind == "enter":
        placeholder = conn.execute("SELECT state, plate_norm FROM stays WHERE id = ?", (event["stay_id"],)).fetchone()
        if placeholder is None or placeholder["state"] != "CHECKED_IN" or placeholder["plate_norm"]:
            return   # already gone, or a plate we read that is not this booking's
    _undo_unidentified(conn, event, db.now(conn))
    conn.execute("UPDATE gate_events SET status = 'resolved', booking_id = ?, stay_id = ? WHERE signal_id = ?",
                 (booking_id, stay_id, event["signal_id"]))


def settle_gates(conn) -> int:
    """Gate events still unpaired after the pairing window become unidentified."""
    now = db.now(conn)
    window = timedelta(seconds=float(CONFIG.get("gate_pairing", {}).get("window_s", 8)))
    events = conn.execute("SELECT * FROM gate_events WHERE status = 'pending' AND observed_at < ? ORDER BY observed_at",
                          (db.utc(now - window),)).fetchall()
    for event in events:
        _unidentified(conn, event, now)
    conn.commit()
    return len(events)


def unidentified_exits(conn, lot_id: str, now: datetime) -> int:
    """Cars seen leaving in the last 24 h that no stay has claimed (subtracted from occupancy)."""
    return conn.execute("SELECT COUNT(*) FROM gate_events WHERE lot_id = ? AND kind = 'exit' AND status = 'unidentified' "
                        "AND stay_id IS NULL AND observed_at > ?",
                        (lot_id, db.utc(now - timedelta(hours=24)))).fetchone()[0]


def sweep(conn) -> int:
    """Settle gate events, re-fuse bookings whose first evidence is old enough for the low-confidence rule,
    and prune the change feed."""
    settle_gates(conn)
    now = db.now(conn)
    cutoff = db.utc(now - _minutes(CONFIG["thresholds"], "attendant_after_min", 10))
    ids = [r["id"] for r in conn.execute(
        "SELECT b.id FROM bookings b WHERE b.status = 'booked' AND b.end_at > ? "
        "AND NOT EXISTS (SELECT 1 FROM stays s WHERE s.booking_id = b.id AND s.state != 'EXPECTED') "
        "AND NOT EXISTS (SELECT 1 FROM alerts a WHERE a.booking_id = b.id AND a.type = 'low_confidence') "
        "AND EXISTS (SELECT 1 FROM signals g WHERE (g.booking_id = b.id OR g.matched_booking_id = b.id) "
        "            AND g.lot_id = b.lot_id AND g.observed_at <= ?)",
        (db.utc(now), cutoff))]
    for booking_id in ids:
        fuse_booking(conn, booking_id)
    conn.execute("DELETE FROM changes WHERE id < (SELECT MAX(id) - 50000 FROM changes)")
    conn.commit()
    return len(ids)
