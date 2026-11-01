"""Scripted gate passes against the API. No phone and no ESP32: the JSON is what they post.

    uv run --group edge python -m edge.scripted_passes

Prints one JSON object per pass. `ms` is how long the request that should decide the stay took
on this machine, not a driveway measurement. Plate OCR and CLIP are not run here.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta

LOT = "AUB"


def _db():
    from api import db
    return db


def _at(ts: str, minutes: float = 0, seconds: float = 0) -> str:
    db = _db()
    return (db.parse(ts) + timedelta(minutes=minutes, seconds=seconds)).astimezone(db.TZ).isoformat(timespec="seconds")


def _covers(booking: dict, when: str) -> bool:
    db = _db()
    observed = db.parse(when)
    start, end = db.parse(booking["start_at"]), db.parse(booking["end_at"])
    return start <= observed + timedelta(minutes=90) and end >= observed - timedelta(minutes=180)


def _bookings(lot: str, n: int, *, min_hours: float = 2) -> list[dict]:
    db = _db()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM bookings WHERE status = 'booked' AND plate_norm IS NOT NULL AND lot_id = ? "
            "ORDER BY start_at, id", (lot,)).fetchall()
    finally:
        conn.close()
    out, seen = [], set()
    for row in rows:
        booking = dict(row)
        if booking["plate_norm"] in seen:
            continue
        if db.parse(booking["end_at"]) - db.parse(booking["start_at"]) < timedelta(hours=min_hours):
            continue
        seen.add(booking["plate_norm"])
        out.append(booking)
        if len(out) == n:
            return out
    raise RuntimeError(f"need {n} bookings at {lot} lasting >= {min_hours} h, found {len(out)}")


def _reset(client) -> None:
    response = client.post("/dev/reset", params={"prepark": "false"})
    assert response.status_code == 200, response.text


def _paint(user_id: str) -> None:
    db = _db()
    conn = db.connect()
    try:
        conn.execute("UPDATE users SET vehicle_color='white', vehicle_body='sedan', vehicle_make='Kia' "
                     "WHERE id != ?", (user_id,))
        conn.execute("UPDATE users SET vehicle_color='beige', vehicle_body='van', vehicle_make='BMW' WHERE id = ?",
                     (user_id,))
        conn.commit()
    finally:
        conn.close()


def _post(client, lot: str, source: str, kind: str, when: str, payload: dict, *, clock: str | None = None):
    signal = {
        "client_event_id": str(uuid.uuid4()),
        "device_id": f"gate-{lot}-1" if source == "gate_sensor" else "cam-gate-1",
        "lot_id": lot,
        "source": source,
        "kind": kind,
        "observed_at": when,
        "payload": payload,
    }
    client.post("/dev/clock", json={"now": clock or when})
    started = time.perf_counter()
    response = client.post("/signals", json=signal)
    elapsed = (time.perf_counter() - started) * 1000
    assert response.status_code == 200, response.text
    return signal, elapsed, response.json()


def _sensor(client, lot, kind, when, *, clock=None):
    return _post(client, lot, "gate_sensor", kind, when, {"gate_id": f"{lot}-1", "lane": 1}, clock=clock)


def _plate(client, booking, kind, when, *, conf=0.93, clock=None, plate=None):
    return _post(client, booking["lot_id"], "camera_plate", kind, when, {
        "plate_norm": plate or booking["plate_norm"], "ocr_conf": conf,
        "gate_id": f"{booking['lot_id']}-1", "lane": 1,
    }, clock=clock)


def _vehicle(client, booking, when):
    return _post(client, booking["lot_id"], "camera_vehicle", "enter", when, {
        "candidates": [{"booking_id": booking["id"], "score": 0.95,
                        "vehicle": {"color": "beige", "body": "van", "make": "BMW", "model": None}}],
        "top_margin": 0.75, "model": "stub",
    })


def _tick(client, when: str) -> None:
    client.post("/dev/clock", json={"now": when})
    client.post("/dev/tick")


def _stays(client, lot: str) -> list[dict]:
    return client.get("/stays", params={"lot_id": lot}).json()


def _stay_for(client, booking) -> dict | None:
    rows = [s for s in _stays(client, booking["lot_id"]) if s["booking_id"] == booking["id"]]
    return rows[0] if rows else None


def _unknown(client, lot: str) -> list[dict]:
    return [s for s in _stays(client, lot) if s["id"].startswith("G-") and s["state"] != "CANCELLED"]


def _alerts(client, lot: str) -> list[dict]:
    return client.get("/alerts", params={"lot_id": lot}).json()


def _occupied(client, lot: str) -> int:
    return client.get(f"/lots/{lot}/overview").json()["counts"]["occupied"]


def _gate_lr(client, stay_id: str):
    evidence = client.get(f"/stays/{stay_id}/evidence").json()["evidence"]
    rows = [row for row in evidence if row["source"] == "gate_sensor"]
    return rows[0]["log_lr"] if rows else None


def _row(name, expected, stay, *, method, plate_correct, ms, ok, note, gate_log_lr=None):
    return {
        "name": name,
        "expected_state": expected,
        "actual_state": None if stay is None else stay["state"],
        "expected_method": method,
        "actual_method": (None if stay is None else
                          stay.get("out_method") if expected == "CHECKED_OUT" else stay.get("in_method")),
        "plate_correct": plate_correct,
        "ms": None if ms is None else round(ms, 1),
        "gate_log_lr": gate_log_lr,
        "ok": bool(ok),
        "note": note,
        "stay_id": None if stay is None else stay["id"],
        "out_at": None if stay is None else stay.get("out_at"),
        "plate_norm": None if stay is None else stay.get("plate_norm"),
    }


def _round_trip(client) -> list[dict]:
    _reset(client)
    booking = _bookings(LOT, 1, min_hours=3)[0]
    entered = _at(booking["start_at"], minutes=-3)
    _sensor(client, LOT, "enter", entered)
    _sig, ms, _body = _plate(client, booking, "enter", _at(entered, seconds=1))
    stay = _stay_for(client, booking)
    lr = _gate_lr(client, stay["id"]) if stay else None
    rows = [_row("booked_in", "CHECKED_IN", stay, method="auto",
                 plate_correct=bool(stay and stay["plate_norm"] == booking["plate_norm"]), ms=ms,
                 ok=bool(stay and stay["state"] == "CHECKED_IN" and stay["in_method"] == "auto"
                         and stay["plate_norm"] == booking["plate_norm"] and lr == 2.0),
                 note="sensor, then the plate 1 s later", gate_log_lr=lr)]
    left = _at(entered, minutes=30)
    _sensor(client, LOT, "exit", left)
    _sig, ms, _body = _plate(client, booking, "exit", left)
    stay = _stay_for(client, booking)
    rows.append(_row("booked_out", "CHECKED_OUT", stay, method="auto",
                     plate_correct=bool(stay and stay["plate_norm"] == booking["plate_norm"]), ms=ms,
                     ok=bool(stay and stay["state"] == "CHECKED_OUT" and stay["out_method"] == "auto"
                             and stay["out_at"] == left),
                     note=f"out_at should be the exit second {left}"))
    return rows


def _walk_in(client) -> dict:
    _reset(client)
    booking = _bookings(LOT, 1)[0]
    when = _at(booking["start_at"], minutes=-3)
    _sensor(client, LOT, "enter", when)
    _sig, ms, _body = _plate(client, booking, "enter", when, plate="Z900001")
    unknown = _unknown(client, LOT)
    stay = unknown[0] if len(unknown) == 1 else None
    alerts = [a for a in _alerts(client, LOT) if a["type"] == "unknown_vehicle"]
    return _row("walk_in", "CHECKED_IN", stay, method="auto",
                plate_correct=bool(stay and stay["plate_norm"] == "Z900001"), ms=ms,
                ok=bool(stay and stay["booking_id"] is None and stay["state"] == "CHECKED_IN"
                        and stay["in_method"] == "auto" and stay["inferred"] and stay["plate_norm"] == "Z900001"
                        and alerts),
                note="plate matches no booking: booking-less stay plus unknown_vehicle, not a registered walk-in")


def _covered_match(client) -> dict:
    _reset(client)
    booking = _bookings(LOT, 1)[0]
    _paint(booking["user_id"])
    when = _at(booking["start_at"], minutes=-3)
    _sensor(client, LOT, "enter", when)
    _sig, ms, _body = _vehicle(client, booking, _at(when, seconds=1))
    _tick(client, _at(when, seconds=30))
    stay = _stay_for(client, booking)
    unknown = _unknown(client, LOT)
    texts = " ".join(a["message"] for a in _alerts(client, LOT))
    return _row("plate_covered_match", "ARRIVING", stay, method="suggestion",
                plate_correct=None, ms=ms,
                ok=bool(stay and stay["state"] == "ARRIVING" and stay["state"] != "CHECKED_IN"
                        and not unknown and "Car-look suggests" in texts and _occupied(client, LOT) == 0),
                note="no auto check-in; occupied stays 0 until the driver confirms. Confirmation message is T3")


def _covered_unknown(client) -> dict:
    _reset(client)
    booking = _bookings(LOT, 1)[0]
    when = _at(booking["start_at"], minutes=-3)
    _sensor(client, LOT, "enter", when)
    _tick(client, _at(when, seconds=30))
    unknown = _unknown(client, LOT)
    stay = unknown[0] if len(unknown) == 1 else None
    texts = " ".join(a["message"] for a in _alerts(client, LOT))
    return _row("plate_covered_no_match", "CHECKED_IN", stay, method="auto",
                plate_correct=None, ms=None,
                ok=bool(stay and stay["booking_id"] is None and stay["state"] == "CHECKED_IN"
                        and stay["plate_norm"] is None and stay["inferred"]
                        and "identity unknown" in texts and _occupied(client, LOT) == 1),
                note="no plate and no car-look: occupancy counts one identity-unknown car")


def _reentry(client) -> dict:
    _reset(client)
    booking = _bookings(LOT, 1, min_hours=3)[0]
    entered = _at(booking["start_at"], minutes=-3)
    _sensor(client, LOT, "enter", entered)
    _plate(client, booking, "enter", entered)
    left = _at(entered, minutes=30)
    _sensor(client, LOT, "exit", left)
    _plate(client, booking, "exit", left)
    again = _at(left, minutes=10)
    _sensor(client, LOT, "enter", again)
    _sig, ms, _body = _plate(client, booking, "enter", again)
    stay = _stay_for(client, booking)
    fresh = [s for s in _stays(client, LOT) if s["booking_id"] == booking["id"] and s["state"] == "CHECKED_IN"]
    return _row("reentry", "CHECKED_IN", stay, method="auto",
                plate_correct=bool(stay and stay["plate_norm"] == booking["plate_norm"]), ms=ms,
                ok=bool(fresh),
                note="expected a new CHECKED_IN after the car left and came back. "
                     "Fusion keeps the completed stay CHECKED_OUT and does not open another")


def _two_cars(client) -> list[dict]:
    _reset(client)
    found = None
    pool = _bookings(LOT, 8, min_hours=2)
    for i, first in enumerate(pool):
        for second in pool[i + 1:]:
            when = _at(max(first["start_at"], second["start_at"]), minutes=-3)
            if _covers(first, when) and _covers(second, when):
                found = (first, second, when)
                break
        if found:
            break
    if found is None:
        return [_row("two_cars_a", "CHECKED_IN", None, method="auto", plate_correct=None, ms=None,
                     ok=False, note="no two AUB bookings share a time")]
    first, second, when = found
    rows = []
    ms = None
    for index, (booking, delay) in enumerate(((first, 0), (second, 3))):
        at = _at(when, seconds=delay)
        _sensor(client, LOT, "enter", at)
        _sig, ms, _body = _plate(client, booking, "enter", _at(at, seconds=1))
        stay = _stay_for(client, booking)
        name = "two_cars_a" if index == 0 else "two_cars_b"
        rows.append(_row(name, "CHECKED_IN", stay, method="auto",
                         plate_correct=bool(stay and stay["plate_norm"] == booking["plate_norm"]), ms=ms,
                         ok=bool(stay and stay["state"] == "CHECKED_IN" and stay["in_method"] == "auto"
                                 and stay["plate_norm"] == booking["plate_norm"]),
                         note=f"back to back, {delay} s after the first sensor"))
    return rows


def _wifi(client) -> dict:
    from edge.gate_logic import OfflineQueue, build_gate_signal
    _reset(client)
    booking = _bookings(LOT, 1)[0]
    when = _at(booking["start_at"], minutes=-3)
    signal = build_gate_signal(lot_id=LOT, gate=1, lane=1, kind="enter", observed_at=when)
    queue = OfflineQueue()
    queue.push(signal)
    calls = {"n": 0}

    def post(body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("wifi unplugged")
        client.post("/dev/clock", json={"now": when})
        response = client.post("/signals", json=body)
        return response.status_code, response.json()

    started = time.perf_counter()
    first = queue.pump(0.0, post)
    assert first["result"] == "retry" and queue.pump(0.5, post) is None
    second = queue.pump(1.0, post)
    assert second["result"] == "sent" and second["body"]["accepted"] == 1
    again = client.post("/signals", json=signal)
    duplicate = again.json()
    _plate(client, booking, "enter", _at(when, seconds=1))
    elapsed = (time.perf_counter() - started) * 1000
    stored = [row for row in client.get("/signals", params={"lot_id": LOT, "limit": 1000}).json()["signals"]
              if row["client_event_id"] == signal["client_event_id"]]
    stay = _stay_for(client, booking)
    lr = _gate_lr(client, stay["id"]) if stay else None
    return _row("wifi_unplugged", "CHECKED_IN", stay, method="auto",
                plate_correct=bool(stay and stay["plate_norm"] == booking["plate_norm"]), ms=elapsed,
                ok=bool(stay and stay["state"] == "CHECKED_IN" and len(stored) == 1
                        and duplicate.get("duplicates") == 1 and duplicate.get("accepted") == 0 and lr == 2.0),
                note=f"timeout, retry of {signal['client_event_id']}, then a duplicate post; stored {len(stored)} time(s)",
                gate_log_lr=lr)


def _night(client) -> dict:
    _reset(client)
    booking = _bookings(LOT, 1)[0]
    when = _at(booking["start_at"], minutes=-3)
    _sensor(client, LOT, "enter", when)
    _sig, ms, _body = _plate(client, booking, "enter", when, conf=0.42)
    stay = _stay_for(client, booking)
    return _row("night_dim", "CHECKED_IN", stay, method="auto", plate_correct=None, ms=ms,
                ok=bool(stay and stay["state"] == "CHECKED_IN" and stay["in_method"] == "auto"
                        and stay["plate_norm"] == booking["plate_norm"]),
                note="no photograph. An exact plate posted with ocr_conf 0.42 still checks in; OCR in dim light was not run")


def _late_plate(client) -> dict:
    _reset(client)
    booking = _bookings(LOT, 1)[0]
    when = _at(booking["start_at"], minutes=-3)
    _sensor(client, LOT, "enter", when)
    _tick(client, _at(when, seconds=30))
    assert _unknown(client, LOT), "sensor should be identity unknown before the late plate"
    _sig, ms, _body = _plate(client, booking, "enter", _at(when, seconds=2), clock=_at(when, seconds=90))
    stay = _stay_for(client, booking)
    cancelled = [s for s in _stays(client, LOT) if s["id"].startswith("G-") and s["state"] == "CANCELLED"]
    return _row("late_plate", "CHECKED_IN", stay, method="auto",
                plate_correct=bool(stay and stay["plate_norm"] == booking["plate_norm"]), ms=ms,
                ok=bool(stay and stay["state"] == "CHECKED_IN" and cancelled and not _unknown(client, LOT)),
                note="plate observed_at is 2 s after the sensor but the upload arrives after the unknown stay; placeholder cancels")


def _outside_window(client) -> dict:
    _reset(client)
    booking = _bookings(LOT, 1)[0]
    when = _at(booking["start_at"], minutes=-3)
    _sensor(client, LOT, "enter", when)
    _sig, ms, _body = _plate(client, booking, "enter", _at(when, seconds=9))
    _tick(client, _at(when, seconds=30))
    stay = _stay_for(client, booking)
    lr = _gate_lr(client, stay["id"]) if stay else None
    unknown = _unknown(client, LOT)
    return _row("outside_8s", "CHECKED_IN", stay, method="auto",
                plate_correct=bool(stay and stay["plate_norm"] == booking["plate_norm"]), ms=ms,
                ok=bool(stay and stay["state"] == "CHECKED_IN" and lr is None and len(unknown) == 1),
                note="9 s apart does not pair (gate log-LR absent). The plate still checks the booking in, "
                     "and the sensor is a second identity-unknown car. Clocks have to agree inside 8 s",
                gate_log_lr=lr)


def run_passes(client) -> list[dict]:
    rows = []
    rows += _round_trip(client)
    rows.append(_walk_in(client))
    rows.append(_covered_match(client))
    rows.append(_covered_unknown(client))
    rows.append(_reentry(client))
    rows += _two_cars(client)
    rows.append(_wifi(client))
    rows.append(_night(client))
    rows.append(_late_plate(client))
    rows.append(_outside_window(client))
    return rows


def main() -> None:
    import os
    import subprocess
    import sys
    import tempfile
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    work = Path(tempfile.mkdtemp(prefix="gopark-passes-"))
    (work / "runs").mkdir()
    os.environ["GOPARK_DATA"] = str(work)
    os.environ["GOPARK_DB"] = str(work / "passes.sqlite")
    os.environ["GOPARK_RUNS"] = str(work / "runs")
    os.environ["GOPARK_SCHEDULER"] = "0"
    subprocess.run([sys.executable, "-m", "tools.generator", "--users", "60", "--weeks", "12",
                    "--seed", "3", "--out", str(work)], cwd=root, check=True)
    from fastapi.testclient import TestClient

    from api.main import app
    from api.ops import fusion
    fusion.latest_priors = lambda: []
    with TestClient(app) as client:
        client.post("/dev/reset", params={"prepark": "false"})
        rows = run_passes(client)
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
