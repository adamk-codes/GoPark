"""Replay traces into POST /signals on the demo clock and record what fusion decided per booking.

The simulator owns the demo clock while it runs: before each step it sets `/dev/clock` to the
step's time, posts the batch (or `/dev/tick` for the time-based rules), and sleeps
`Δdemo / speed` real seconds (`speed = 0`: as fast as possible).
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from tools.simulator.timeline import Step, Trace, build_timeline, driver_answer, parse


class HttpClient:
    """A running API (`uv run uvicorn api.main:app --port 8001`)."""

    def __init__(self, url: str):
        import httpx
        self._client = httpx.Client(base_url=url.rstrip("/"), timeout=60)

    def post(self, path, **kwargs):
        return self._client.post(path, **kwargs)

    def get(self, path, **kwargs):
        return self._client.get(path, **kwargs)


@contextmanager
def local_client(db_path: Path, data_dir: Path):
    """The API in-process on its own SQLite file, with the 30 s scheduler off (ticks drive time)."""
    os.environ["GOPARK_DB"] = str(db_path)
    os.environ["GOPARK_DATA"] = str(data_dir)
    os.environ["GOPARK_SCHEDULER"] = "0"
    from api import db
    if db.DB_PATH != db_path:
        raise RuntimeError(f"api.db was imported earlier with GOPARK_DB={db.DB_PATH}; use a fresh process")
    from fastapi.testclient import TestClient
    from api.main import app
    with TestClient(app) as client:
        yield client


def _ok(response):
    if response.status_code >= 400:
        raise RuntimeError(f"{response.request.method} {response.request.url} -> {response.status_code}: "
                           f"{response.text[:300]}")
    return response.json()


def replay(client, traces: list[Trace], *, speed: float = 0.0, tick_min: float = 2.0, reset: bool = True,
           progress=None, driver_confirm: float = 0.0) -> list[dict]:
    """Play `traces` into the API and return one decision record per booking (see `record`).

    `driver_confirm` = share of drivers who answer an "arrived?" prompt (ARRIVING stay) 1-5 min later,
    truthfully ("arrived", or "not_me" for a no-show), through POST /stays/{id}/confirm."""
    steps = build_timeline(traces, tick_min=tick_min)
    if reset:
        _ok(client.post("/dev/reset", params={"prepark": "false"}))
    arrives = {t.booking_id: t.arrives for t in traces}
    asked: set[str] = set()
    answers: list[tuple] = []   # (when, stay_id, answer), sorted
    last = None
    for i, step in enumerate(steps):
        if speed > 0 and last is not None:
            time.sleep(max(0.0, (step.at - last).total_seconds() / speed))
        last = step.at
        while answers and answers[0][0] <= step.at:
            when, stay_id, answer = answers.pop(0)
            _ok(client.post("/dev/clock", json={"now": when.isoformat()}))
            if stay_id not in {s["id"] for s in _ok(client.get("/stays", params={"state": "ARRIVING"}))}:
                continue   # settled before the prompt was answered
            _ok(client.post(f"/stays/{stay_id}/confirm", json={
                "device_id": "sim-driver", "client_event_id": f"sim-confirm-{stay_id}", "answer": answer}))
        _ok(client.post("/dev/clock", json={"now": step.at.isoformat()}))
        if step.signals:
            _ok(client.post("/signals", json={"signals": step.signals}))
            if driver_confirm > 0:
                for stay in _ok(client.get("/stays", params={"state": "ARRIVING"})):
                    if stay["booking_id"] in arrives and stay["id"] not in asked:
                        asked.add(stay["id"])
                        if due := driver_answer(stay["booking_id"], arrives[stay["booking_id"]], step.at, driver_confirm):
                            answers = sorted(answers + [(due[0], stay["id"], due[1])], key=lambda a: a[0])
        else:
            _ok(client.post("/dev/tick"))
        if progress and i % 500 == 0:
            progress(i, len(steps), step)
    return decisions(client, traces)


def decisions(client, traces: list[Trace]) -> list[dict]:
    """Join `/dev/decisions` with each trace's ground truth."""
    if not traces:
        return []
    since = min(t.start for t in traces)
    until = max(t.start for t in traces) + timedelta(seconds=1)
    rows = {r["booking_id"]: r for r in _ok(client.get("/dev/decisions", params={
        "since": since.isoformat(), "until": until.isoformat()}))}
    return [record(t, rows.get(t.booking_id)) for t in traces]


def record(trace: Trace, row: dict | None) -> dict:
    """One booking: ground truth + what the API decided (times as ISO strings)."""
    stay = (row or {}).get("stay") or {}
    iso = lambda v: v.isoformat() if v else None  # noqa: E731
    return {"booking_id": trace.booking_id, "lot_id": trace.lot_id, "lot_kind": trace.lot_kind,
            "scenario": trace.scenario, "variant": trace.variant, "arrives": trace.arrives,
            "actual_arrival": iso(trace.actual_arrival), "actual_departure": iso(trace.actual_departure),
            "start": iso(trace.start), "end": iso(trace.end),
            "state": stay.get("state"), "in_at": stay.get("in_at"), "in_method": stay.get("in_method"),
            "in_conf": stay.get("in_conf"), "out_at": stay.get("out_at"), "out_method": stay.get("out_method"),
            "inferred": stay.get("inferred"), "arriving_at": (row or {}).get("arriving_at"),
            "confirmed_at": (row or {}).get("confirmed_at"), "checked_out_at": (row or {}).get("checked_out_at"),
            "check_out_action": (row or {}).get("check_out_action"), "alerts": (row or {}).get("alerts", [])}


def describe(steps: list[Step], traces: list[Trace], show: int = 15) -> str:
    """Dry-run text: what would be sent, without touching the API."""
    signals = sum(len(s.signals) for s in steps)
    ticks = sum(1 for s in steps if not s.signals)
    by_scenario: dict[str, int] = {}
    for t in traces:
        by_scenario[t.scenario] = by_scenario.get(t.scenario, 0) + 1
    lines = [f"{len(traces)} bookings {dict(sorted(by_scenario.items()))}",
             f"{signals} signals in {len(steps) - ticks} batches, {ticks} ticks"]
    if steps:
        lines.append(f"demo clock {steps[0].at.isoformat()} -> {steps[-1].at.isoformat()}")
    for s in [s for s in steps if s.signals][:show]:
        for sig in s.signals:
            observed = parse(sig["observed_at"])
            late = f" (observed {observed.isoformat()})" if observed != s.at else ""
            lines.append(f"  {s.at.isoformat()}  {sig['lot_id']:<9} {sig.get('booking_id') or '-':<8} "
                         f"{sig['source']:<12} {sig['kind']:<8}{late}")
    return "\n".join(lines)
