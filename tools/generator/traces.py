"""Driver + camera signal traces for live-week bookings (CONTRACT.md §3, §6).

One file per booking, data/traces/<booking_id>.jsonl, one signal per line in the
signal shape plus "truth": whether the signal reflects the booked car really
arriving at / leaving that lot. Lines are sorted by observed_at.

Phones underground buffer signals and upload them later: those signals keep their
original observed_at and carry payload.sent_at (when the phone came back online).
A replayer should send each line at payload.sent_at if present, else observed_at.

traces/index.csv lists every trace with its scenario and what really happened
(arrives, actual_arrival, actual_departure), so a simulator or evaluation can
score detection without reading the lines. Scenario mix: tools/generator/README.md.
"""
from __future__ import annotations

import csv
import json
import math
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Scenario weights by lot kind.
SCENARIOS = {
    "open": {"clean": 0.55, "weak_gps": 0.20, "phone_dead": 0.10, "contradiction": 0.10, "no_show": 0.05},
    "gated": {"clean": 0.50, "weak_gps": 0.20, "phone_dead": 0.15, "contradiction": 0.10, "no_show": 0.05},
    "underground": {"underground": 0.75, "phone_dead": 0.12, "contradiction": 0.08, "no_show": 0.05},
    "valet": {"clean": 0.40, "weak_gps": 0.25, "phone_dead": 0.25, "contradiction": 0.05, "no_show": 0.05},
}
# Which lots have an entrance plate camera / overhead spot camera (old phones), by kind.
# Detection hardware is undecided; camera-first is likely, so cameras are on by default.
CAMERA_KIT = {
    "open": {"plate": True, "spot": False},
    "gated": {"plate": True, "spot": True},
    "underground": {"plate": True, "spot": False},
    "valet": {"plate": False, "spot": False},
}
CAMERA_MISS = 0.10       # car passes, camera reports nothing
CAMERA_MISREAD = 0.05    # camera reports a plate with one character wrong
OVERSTAY = 0.08

INDEX_COLUMNS = ["booking_id", "user_id", "lot_id", "lot_kind", "scenario", "variant", "arrives",
                 "actual_arrival", "actual_departure", "signals"]


def plate_norm(plate: str) -> str:
    return plate.replace(" ", "").replace("-", "").upper()


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _offset(lat: float, lng: float, meters: float, rng: random.Random) -> tuple[float, float]:
    a = rng.uniform(0, 2 * math.pi)
    dlat = meters * math.cos(a) / 111_320
    dlng = meters * math.sin(a) / (111_320 * math.cos(math.radians(lat)))
    return round(lat + dlat, 6), round(lng + dlng, 6)


def _dist_m(a: dict, b: dict) -> float:
    dlat = (a["lat"] - b["lat"]) * 111_320
    dlng = (a["lng"] - b["lng"]) * 111_320 * math.cos(math.radians(a["lat"]))
    return math.hypot(dlat, dlng)


def nearest_other_lot(lots: dict, lot_id: str) -> str:
    here = lots[lot_id]["center"]
    return min((lid for lid in lots if lid != lot_id), key=lambda lid: _dist_m(here, lots[lid]["center"]))


class TraceBuilder:
    def __init__(self, rng: random.Random, lots: dict, booking: dict, cameras: bool):
        self.rng, self.lots, self.b = rng, lots, booking
        self.lot = lots[booking["location_id"]]
        self.kit = CAMERA_KIT[self.lot["kind"]] if cameras else {"plate": False, "spot": False}
        self.device = f"dev-{booking['user_id']}"
        self.lines: list[dict] = []

    def _uuid(self) -> str:
        return str(uuid.UUID(int=self.rng.getrandbits(128), version=4))

    def driver(self, source: str, kind: str, at: datetime, truth: bool, payload: dict,
               lot_id: str | None = None, sent_at: datetime | None = None) -> None:
        if sent_at is not None:
            payload = {**payload, "sent_at": _iso(sent_at)}
        self.lines.append({"client_event_id": self._uuid(), "device_id": self.device,
                           "user_id": self.b["user_id"], "lot_id": lot_id or self.b["location_id"],
                           "booking_id": self.b["booking_id"], "source": source, "kind": kind,
                           "observed_at": _iso(at), "payload": payload, "truth": truth})

    def geofence(self, kind: str, at: datetime, accuracy: float, truth: bool = True, lot_id: str | None = None,
                 sent_at: datetime | None = None) -> None:
        lot = self.lots[lot_id or self.b["location_id"]]
        lat, lng = _offset(lot["center"]["lat"], lot["center"]["lng"],
                           self.rng.uniform(0, lot["radius_m"] * 0.8), self.rng)
        self.driver("geofence", kind, at, truth, {"lat": lat, "lng": lng, "accuracy_m": round(accuracy)},
                    lot_id=lot_id, sent_at=sent_at)

    def motion(self, kind: str, at: datetime, sent_at: datetime | None = None) -> None:
        walk = {"activity": "walking", "previous": "automotive"}
        drive = {"activity": "automotive", "previous": "walking"}
        payload = {**(walk if kind == "park" else drive), "confidence": round(self.rng.uniform(0.6, 0.95), 2)}
        self.driver("motion", kind, at, True, payload, sent_at=sent_at)

    def ble(self, kind: str, at: datetime, sent_at: datetime | None) -> None:
        beacon = self.lot.get("beacon") or {}
        self.driver("ble", kind, at, True, {"uuid": beacon.get("uuid"), "major": beacon.get("major"), "minor": 1,
                                            "rssi": self.rng.randint(-85, -58)}, sent_at=sent_at)

    def cameras(self, arrive: datetime, depart: datetime) -> None:
        lot_id = self.b["location_id"]
        cam = "ramp" if self.lot["kind"] == "underground" else "gate"
        for kind, at in (("enter", arrive), ("exit", depart)):
            if not self.kit["plate"] or self.rng.random() < CAMERA_MISS:
                continue
            plate = plate_norm(self.b["plate"])
            conf = self.rng.uniform(0.82, 0.99)
            if self.rng.random() < CAMERA_MISREAD:
                i = self.rng.randrange(1, len(plate))
                plate = plate[:i] + str((int(plate[i]) + self.rng.randint(1, 9)) % 10) + plate[i + 1:]
                conf = self.rng.uniform(0.45, 0.75)
            self.lines.append({"client_event_id": self._uuid(), "device_id": f"cam-{lot_id}-{cam}",
                               "user_id": None, "lot_id": lot_id, "booking_id": None,
                               "source": "camera_plate", "kind": kind,
                               "observed_at": _iso(at + timedelta(seconds=self.rng.randint(-20, 20))),
                               "payload": {"plate_norm": plate, "ocr_conf": round(conf, 2),
                                           "camera": cam, "direction": "in" if kind == "enter" else "out"},
                               "truth": True})
        if self.kit["spot"] and self.b.get("space_id"):
            for kind, at in (("occupied", arrive + timedelta(minutes=self.rng.uniform(1, 3))),
                             ("vacant", depart - timedelta(minutes=self.rng.uniform(0.5, 2)))):
                if self.rng.random() < CAMERA_MISS:
                    continue
                self.lines.append({"client_event_id": self._uuid(), "device_id": f"cam-{lot_id}-overhead",
                                   "user_id": None, "lot_id": lot_id, "booking_id": None,
                                   "source": "camera_spot", "kind": kind, "observed_at": _iso(at),
                                   "payload": {"space_id": self.b["space_id"]}, "truth": True})


def _times(rng: random.Random, b: dict) -> tuple[datetime, datetime]:
    arrive = b["start"] + timedelta(minutes=rng.gauss(-5, 8), seconds=rng.randint(0, 59))
    if rng.random() < OVERSTAY:
        depart = b["end"] + timedelta(minutes=rng.uniform(20, 60))
    else:
        depart = b["end"] + timedelta(minutes=rng.gauss(-5, 15))
    depart = max(depart, arrive + timedelta(minutes=10)) + timedelta(seconds=rng.randint(0, 59))
    return arrive, depart


def build_trace(rng: random.Random, lots: dict, b: dict, cameras: bool = True) -> tuple[dict, list[dict]]:
    """Return (index row, signal lines) for one live booking."""
    kind = lots[b["location_id"]]["kind"]
    weights = SCENARIOS[kind]
    scenario = rng.choices(list(weights), weights=list(weights.values()))[0]
    t = TraceBuilder(rng, lots, b, cameras)
    arrive, depart = _times(rng, b)
    m = lambda lo, hi: timedelta(minutes=rng.uniform(lo, hi))  # noqa: E731
    variant = ""

    if scenario == "no_show":
        if kind != "underground" and rng.random() < 0.5:
            variant = "drive_by"     # passes the lot, never parks
            at = b["start"] + m(-15, 10)
            t.geofence("enter", at, rng.uniform(10, 40), truth=False)
            t.geofence("exit", at + m(1, 4), rng.uniform(10, 40), truth=False)
        index = {"arrives": False, "actual_arrival": "", "actual_departure": ""}
    else:
        if scenario == "clean":
            t.geofence("enter", arrive - m(0.5, 3), rng.uniform(5, 30))
            t.motion("park", arrive + m(0.3, 2.5))
            t.motion("unpark", depart - m(0.2, 1))
            t.geofence("exit", depart + m(0.5, 3), rng.uniform(5, 30))
        elif scenario == "weak_gps":
            variant = "late_fence" if rng.random() < 0.5 else "coarse"
            delay = m(2, 12) if variant == "late_fence" else m(0, 2)   # OS delivers the fence late
            t.geofence("enter", arrive + delay, rng.uniform(120, 450))
            if rng.random() < 0.6:
                t.geofence("exit", depart + m(3, 15), rng.uniform(120, 450))
        elif scenario == "underground":
            variant = rng.choices(["ble", "no_ble"], weights=[0.8, 0.2])[0]
            if rng.random() < 0.5:   # GPS still works on the street, just before the ramp
                t.geofence("enter", arrive - m(0.5, 2), rng.uniform(15, 60))
            back_online = arrive + m(4, 20)      # walks out of the garage, phone reconnects
            if variant == "ble":
                t.ble("enter", arrive, sent_at=back_online)
            t.motion("park", arrive + m(0.3, 2), sent_at=back_online)
            surfaced = depart + m(0.5, 3)        # drives up the ramp, phone reconnects
            if variant == "ble":
                t.ble("exit", depart, sent_at=surfaced)
            t.motion("unpark", depart - m(0.2, 1), sent_at=surfaced)
        elif scenario == "contradiction":
            wrong = nearest_other_lot(lots, b["location_id"])
            variant = f"fence_at_{wrong}"
            t.geofence("enter", arrive - m(0.5, 3), rng.uniform(20, 80), truth=False, lot_id=wrong)
            t.motion("park", arrive + m(0.3, 2.5))
            t.motion("unpark", depart - m(0.2, 1))
        # phone_dead: no driver signals at all; only cameras (if any) see the car.
        t.cameras(arrive, depart)
        index = {"arrives": True, "actual_arrival": _iso(arrive), "actual_departure": _iso(depart)}

    t.lines.sort(key=lambda s: s["observed_at"])
    index.update({"booking_id": b["booking_id"], "user_id": b["user_id"], "lot_id": b["location_id"],
                  "lot_kind": kind, "scenario": scenario, "variant": variant, "signals": len(t.lines)})
    return index, t.lines


def _assign_spaces(lots: dict, bookings: list[dict]) -> None:
    """Give live bookings at spot-level lots a space, first free spot first (where the car parks)."""
    free_at: dict[str, datetime] = {}
    for b in sorted(bookings, key=lambda b: b["start"]):
        lot = lots[b["location_id"]]
        if not lot.get("has_spaces"):
            continue
        for n in range(1, lot["capacity"] + 1):
            sid = f"{lot['id']}-{n:02d}"
            if free_at.get(sid) is None or free_at[sid] <= b["start"]:
                free_at[sid] = b["end"]
                b["space_id"] = sid
                break


def write_traces(out: Path, lots: dict, bookings: list[dict], seed: int, cameras: bool = True) -> dict:
    """Write one trace per live booking plus traces/index.csv; return scenario counts."""
    rng = random.Random(seed * 7919 + 17)
    tdir = out / "traces"
    tdir.mkdir(parents=True, exist_ok=True)
    for old in tdir.glob("*.jsonl"):
        old.unlink()
    bookings = [dict(b) for b in bookings]
    _assign_spaces(lots, bookings)
    counts: dict[str, int] = {}
    with open(tdir / "index.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_COLUMNS)
        w.writeheader()
        for b in sorted(bookings, key=lambda b: b["booking_id"]):
            index, lines = build_trace(rng, lots, b, cameras)
            w.writerow(index)
            counts[index["scenario"]] = counts.get(index["scenario"], 0) + 1
            with open(tdir / f"{b['booking_id']}.jsonl", "w", encoding="utf-8", newline="\n") as tf:
                for line in lines:
                    tf.write(json.dumps(line, separators=(",", ":")) + "\n")
    return dict(sorted(counts.items()))
