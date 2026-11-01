"""Sensor-triggered plate reading: pair each `gate_sensor` event with the best plate read around it.

The plate reader keeps reading frames; instead of debouncing on its own it buffers recent reads and,
`window_s` after each gate event, posts the plate with the strongest evidence within +-`window_s` of the
event (summed OCR confidence over frames, so a plate seen in many frames beats one lucky read).
The `camera_plate` signal then carries the event's direction and `gate_id` / `lane`.

Times here are seconds on the reader's own monotonic clock: an event is stamped when it *arrives*, so a
sensor whose clock is off still pairs locally (the backend pairs on `observed_at`; see INTEGRATION.md).

Gate events reach the reader from:
  listen  a tiny local HTTP endpoint (`POST /gate`, same body as `POST /signals`); the sensor or
          `edge/sensor_sim.py` posts there as well as to the API;
  api     polling `GET {api}/signals?source=gate_sensor&lot_id=..&after_id=..` (needs that endpoint
          in the API; the reader exits with a hint if it is missing).
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class GateEvent:
    t: float                 # reader monotonic seconds when it arrived
    kind: str                # enter | exit
    lot_id: str
    gate_id: str | None = None
    lane: int | str | None = None
    client_event_id: str | None = None
    observed_at: str | None = None


@dataclass
class PlateHit:
    t: float
    plate: str
    conf: float
    observed_at: str         # reader clock (demo or field) when the frame was read


@dataclass
class Pairing:
    event: GateEvent
    plate: str | None
    conf: float = 0.0
    reads: int = 0
    observed_at: str | None = None
    candidates: dict = field(default_factory=dict)


def event_from_signal(sig: dict, t: float) -> GateEvent | None:
    if sig.get("source") != "gate_sensor" or sig.get("kind") not in ("enter", "exit"):
        return None
    p = sig.get("payload") or {}
    return GateEvent(t, sig["kind"], sig.get("lot_id", ""), p.get("gate_id"), p.get("lane"),
                     sig.get("client_event_id"), sig.get("observed_at"))


class GatePairer:
    """Buffer plate reads and gate events; `due(now)` returns the pairings whose window has closed."""

    def __init__(self, lot_id: str, window_s: float = 8.0, need: int = 2, kind: str = "auto",
                 gate_id: str | None = None, lane: str | None = None, keep_s: float = 30.0):
        self.lot_id, self.window_s, self.need, self.kind = lot_id, window_s, need, kind
        self.gate_id, self.lane, self.keep_s = gate_id, (str(lane) if lane is not None else None), keep_s
        self.hits: list[PlateHit] = []
        self.events: list[GateEvent] = []
        self.seen_ids: set[str] = set()

    def accepts(self, ev: GateEvent) -> bool:
        if ev.lot_id != self.lot_id:
            return False
        if self.kind in ("enter", "exit") and ev.kind != self.kind:
            return False  # a one-direction camera ignores the other lane's events
        if self.gate_id and ev.gate_id != self.gate_id:
            return False
        if self.lane is not None and str(ev.lane) != self.lane:
            return False
        if ev.client_event_id:
            if ev.client_event_id in self.seen_ids:
                return False  # same event retried by the sensor
            self.seen_ids.add(ev.client_event_id)
        return True

    def add_event(self, ev: GateEvent) -> bool:
        if not self.accepts(ev):
            return False
        self.events.append(ev)
        return True

    def add_hits(self, hits: list[PlateHit]) -> None:
        self.hits.extend(hits)

    def due(self, now: float, flush: bool = False) -> list[Pairing]:
        out = []
        for ev in sorted(self.events, key=lambda e: e.t):
            if not flush and now < ev.t + self.window_s:
                continue
            self.events.remove(ev)
            out.append(self._pair(ev))
        horizon = now - self.keep_s - self.window_s
        self.hits = [h for h in self.hits if h.t >= horizon]
        return out

    def _pair(self, ev: GateEvent) -> Pairing:
        near = [h for h in self.hits if abs(h.t - ev.t) <= self.window_s]
        score: dict[str, float] = {}
        count: dict[str, int] = {}
        for h in near:
            score[h.plate] = score.get(h.plate, 0.0) + h.conf
            count[h.plate] = count.get(h.plate, 0) + 1
        ranked = sorted(score, key=lambda p: (score[p], count[p]), reverse=True)
        best = next((p for p in ranked if count[p] >= self.need), None)
        if best is None:
            return Pairing(ev, None, candidates=count)
        mine = [h for h in near if h.plate == best]
        top = max(mine, key=lambda h: h.conf)
        # One car per read: the next event (e.g. a tailgater) must not get the same plate.
        self.hits = [h for h in self.hits if not (h.plate == best and abs(h.t - ev.t) <= self.window_s)]
        return Pairing(ev, best, top.conf, len(mine), top.observed_at, count)


# ---------- event sources ----------
class ListenSource:
    """`POST /gate` (or `/signals`) with a gate_sensor signal or {"signals": [...]}; `GET /health`."""

    def __init__(self, host: str, port: int):
        self.q: queue.Queue[GateEvent] = queue.Queue()
        src = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path.split("?")[0] not in ("/gate", "/signals"):
                    return self._reply(404, {"detail": "POST /gate"})
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                except ValueError:
                    return self._reply(422, {"detail": "invalid JSON"})
                items = body.get("signals", [body]) if isinstance(body, dict) else []
                n = 0
                for s in items:
                    ev = event_from_signal(s, time.monotonic()) if isinstance(s, dict) else None
                    if ev:
                        src.q.put(ev)
                        n += 1
                self._reply(200, {"accepted": n})

            def do_GET(self):
                self._reply(200, {"ok": True}) if self.path == "/health" else self._reply(404, {})

            def _reply(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        print(f"[trigger] listening for gate events on http://{host}:{self.port}/gate", file=sys.stderr)

    def poll(self) -> list[GateEvent]:
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out

    def close(self):
        self.server.shutdown()


class ApiPollSource:
    """Polls the API for new gate_sensor signals in a background thread."""

    def __init__(self, api: str, lot_id: str, every_s: float = 0.5):
        import urllib.error

        from edge.camera.common import http_json
        self.q: queue.Queue[GateEvent] = queue.Queue()
        url = f"{api}/signals"
        try:
            rows = http_json("GET", url, params={"source": "gate_sensor", "lot_id": lot_id, "limit": 1}, timeout=3)
        except urllib.error.HTTPError as e:
            if e.code in (404, 405):
                raise SystemExit(f"{url} has no GET (status {e.code}): the API does not list signals yet. "
                                 "Use --trigger listen and point the sensor (or edge/sensor_sim.py --also) at it.")
            raise
        rows = rows.get("signals", rows) if isinstance(rows, dict) else rows
        self.after = max((int(x.get("id", 0)) for x in rows), default=0)  # only events from now on
        self.stop = threading.Event()

        def loop():
            while not self.stop.is_set():
                try:
                    rows = http_json("GET", url, params={"source": "gate_sensor", "lot_id": lot_id,
                                                         "after_id": self.after}, timeout=3)
                    rows = rows.get("signals", rows) if isinstance(rows, dict) else rows
                    for s in rows:
                        self.after = max(self.after, int(s.get("id", 0)))
                        if ev := event_from_signal(s, time.monotonic()):
                            self.q.put(ev)
                except Exception as e:
                    print(f"[trigger] poll failed: {e}", file=sys.stderr)
                self.stop.wait(every_s)

        threading.Thread(target=loop, daemon=True).start()

    def poll(self) -> list[GateEvent]:
        out = []
        while not self.q.empty():
            out.append(self.q.get_nowait())
        return out

    def close(self):
        self.stop.set()
