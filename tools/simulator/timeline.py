"""Load driver/camera traces (data/traces/*.jsonl, tools/generator/README.md § Traces) into a replay timeline.

A trace line is sent at `payload.sent_at` if present (a phone that was offline underground),
else at `observed_at`. Lines sent at the same moment go in one `/signals` batch.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path


# Same rules as api.db; not imported so GOPARK_DB can still be set before the API loads.
def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def normalize_plate(plate: str | None) -> str | None:
    return re.sub(r"[\s\-]", "", plate).upper() or None if plate else None


@dataclass
class Trace:
    booking_id: str
    user_id: str
    lot_id: str
    lot_kind: str
    scenario: str
    variant: str
    arrives: bool
    actual_arrival: datetime | None
    actual_departure: datetime | None
    start: datetime
    end: datetime
    plate_norm: str | None
    lines: list[dict] = field(default_factory=list)


def send_time(line: dict) -> datetime:
    sent = (line.get("payload") or {}).get("sent_at")
    return parse(sent or line["observed_at"])


def _when(value: str | None) -> datetime | None:
    return parse(value) if value else None


def load_traces(data_dir: Path, *, lots: list[str] | None = None, scenarios: list[str] | None = None,
                bookings: list[str] | None = None, since: datetime | None = None, until: datetime | None = None,
                limit: int | None = None) -> list[Trace]:
    """Traces listed in traces/index.csv, joined with bookings.csv, filtered and in booking order.

    Ground truth comes from the index (`arrives`, `actual_arrival/departure`); data/stays.csv fills
    actual times for any booking the index leaves blank.
    """
    traces_dir = data_dir / "traces"
    booked = {}
    with open(data_dir / "bookings.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            booked[r["booking_id"]] = r
    stays = {}
    if (data_dir / "stays.csv").exists():
        with open(data_dir / "stays.csv", newline="", encoding="utf-8") as f:
            stays = {r["booking_id"]: r for r in csv.DictReader(f)}
    out: list[Trace] = []
    with open(traces_dir / "index.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            b = booked.get(r["booking_id"])
            if b is None:
                continue
            start = parse(b["start_time"])
            if lots and r["lot_id"] not in lots or scenarios and r["scenario"] not in scenarios:
                continue
            if bookings and r["booking_id"] not in bookings:
                continue
            if since and start < since or until and start >= until:
                continue
            truth = stays.get(r["booking_id"], {})
            out.append(Trace(
                booking_id=r["booking_id"], user_id=r["user_id"], lot_id=r["lot_id"], lot_kind=r["lot_kind"],
                scenario=r["scenario"], variant=r["variant"], arrives=r["arrives"] == "True",
                actual_arrival=_when(r["actual_arrival"] or truth.get("actual_arrival")),
                actual_departure=_when(r["actual_departure"] or truth.get("actual_departure")),
                start=start, end=parse(b["end_time"]), plate_norm=normalize_plate(b.get("plate"))))
    out.sort(key=lambda t: t.booking_id)
    if limit:
        out = out[:limit]
    for t in out:
        path = traces_dir / f"{t.booking_id}.jsonl"
        t.lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


GATE_SENSOR_MISS = 0.02
# Lot kinds with a gate lane (break-beam pair + plate camera); valet has none (tools/generator CAMERA_KIT).
GATE_KINDS = {"open", "gated", "underground"}


def add_gate_sensors(traces: list[Trace], seed: int = 1) -> None:
    """Add `gate_sensor` enter/exit lines until the generator emits them: one per real pass through the gate,
    a few seconds from the plate read when the camera saw the car, else at the true arrival/departure."""
    import random
    rng = random.Random(seed)
    for t in traces:
        if t.lot_kind not in GATE_KINDS or not t.arrives:
            continue
        reads = {line["kind"]: parse(line["observed_at"]) for line in t.lines if line["source"] == "camera_plate"}
        for kind, actual in (("enter", t.actual_arrival), ("exit", t.actual_departure)):
            if actual is None or rng.random() < GATE_SENSOR_MISS:
                continue
            at = reads[kind] + timedelta(seconds=rng.uniform(-3, 3)) if kind in reads \
                else actual + timedelta(seconds=rng.uniform(-20, 20))
            t.lines.append({"client_event_id": f"gate-{t.booking_id}-{kind}", "device_id": f"gate-{t.lot_id}-1",
                            "user_id": None, "lot_id": t.lot_id, "booking_id": None, "source": "gate_sensor",
                            "kind": kind, "observed_at": at.isoformat(timespec="seconds"),
                            "payload": {"gate_id": f"{t.lot_id}-1", "lane": 1}, "truth": True})
        t.lines.sort(key=lambda line: line["observed_at"])


@dataclass
class Step:
    at: datetime
    signals: list[dict]      # empty = a tick (POST /dev/tick)


def build_timeline(traces: list[Trace], tick_min: float = 2.0, settle_min: float = 45.0) -> list[Step]:
    """Signal batches by send time, plus ticks every `tick_min` while any booking is in play
    (first signal .. booking end + `settle_min`, so silent departures get inferred)."""
    batches: dict[datetime, list[dict]] = {}
    spans = []
    for t in traces:
        times = []
        for line in t.lines:
            at = send_time(line)
            times.append(at)
            batches.setdefault(at, []).append({k: v for k, v in line.items() if k != "truth"})
        first = min(times + [t.start - timedelta(minutes=30)])
        spans.append((first, max(times + [t.end]) + timedelta(minutes=settle_min)))
    steps = [Step(at, sigs) for at, sigs in batches.items()]
    if tick_min > 0:
        merged: list[list[datetime]] = []
        for lo, hi in sorted(spans):
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        step = timedelta(minutes=tick_min)
        for lo, hi in merged:
            at = lo.replace(second=0, microsecond=0)
            while at <= hi:
                steps.append(Step(at, []))
                at += step
    # Signals before a tick at the same moment, so the tick sees them.
    steps.sort(key=lambda s: (s.at, not s.signals))
    return steps
