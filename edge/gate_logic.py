"""Gate-sensor rules the ESP32 sketch follows (edge/firmware/gate_sensor/gate_sensor.ino).

Two beams, about 1 m apart. A is the street side, B is the lot side. A passage is one car:
A then B is `enter`, B then A is `exit`, and `observed_at` is when the second beam broke.
One beam (a person) or A-B-A (the car backed out) is not an event. INTEGRATION.md §2.

The offline queue keeps that JSON, including its UUID, until the API accepts it. A `422` is
dropped. A timeout or `5xx` is retried with the same id. INTEGRATION.md §4.
"""
from __future__ import annotations

import copy
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from edge.camera.common import validate_signal

BACKOFF_S = (1, 2, 5, 10)
QUEUE_MAX = 50
# A beam must stay broken this long before it counts (contact bounce).
DEBOUNCE_MS = 30
# Both beams clear this long: the car has left the pair, so the passage can be judged.
CLEAR_MS = 800
# Still blocking a beam this long: the car stopped on the sensor. Drop the partial passage.
MAX_PASSAGE_MS = 20_000


@dataclass(frozen=True)
class Passage:
    kind: str
    observed_ms: int


def collapse_edges(edges: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Drop a repeated break of the same beam (bounce). Keep the first time it broke."""
    out: list[tuple[str, int]] = []
    for beam, at in edges:
        if not out or out[-1][0] != beam:
            out.append((beam, at))
    return out


def passage_from_edges(edges: list[tuple[str, int]]) -> Passage | None:
    """`enter` / `exit` only for exactly A-then-B or B-then-A. The time is the second beam."""
    beams = collapse_edges(edges)
    if len(beams) != 2:
        return None
    first, second = beams
    if (first[0], second[0]) == ("A", "B"):
        return Passage("enter", second[1])
    if (first[0], second[0]) == ("B", "A"):
        return Passage("exit", second[1])
    return None


class BeamDecoder:
    """Debounced samples in, at most one `Passage` when both beams have cleared.

    `a_broken` / `b_broken` are the raw levels. Time is milliseconds on the board's clock
    (`millis()`), and it only has to be monotonic. The passage time is when the second
    break was accepted, not when the HTTP post happens.
    """

    def __init__(self, debounce_ms: int = DEBOUNCE_MS, clear_ms: int = CLEAR_MS,
                 max_passage_ms: int = MAX_PASSAGE_MS):
        self.debounce_ms, self.clear_ms, self.max_passage_ms = debounce_ms, clear_ms, max_passage_ms
        self._raw = {"A": None, "B": None}
        self._stable = {"A": False, "B": False}
        self._since = {"A": 0, "B": 0}
        self._edges: list[tuple[str, int]] = []
        self._clear_since: int | None = None

    def sample(self, a_broken: bool, b_broken: bool, t_ms: int) -> Passage | None:
        self._debounce("A", bool(a_broken), t_ms)
        self._debounce("B", bool(b_broken), t_ms)
        if self._stable["A"] or self._stable["B"]:
            if self._edges and t_ms - self._edges[0][1] > self.max_passage_ms:
                self._edges.clear()
            self._clear_since = None
            return None
        if not self._edges:
            self._clear_since = None
            return None
        if self._clear_since is None:
            self._clear_since = t_ms
        if t_ms - self._clear_since < self.clear_ms:
            return None
        edges, self._edges, self._clear_since = self._edges, [], None
        return passage_from_edges(edges)

    def _debounce(self, beam: str, broken: bool, t_ms: int) -> None:
        if self._raw[beam] is None or broken != self._raw[beam]:
            self._raw[beam] = broken
            self._since[beam] = t_ms
        if t_ms - self._since[beam] < self.debounce_ms or self._stable[beam] == broken:
            return
        self._stable[beam] = broken
        if broken:
            self._edges.append((beam, t_ms))


def build_gate_signal(*, lot_id: str, gate: int, lane: int, kind: str, observed_at: str,
                      client_event_id: str | None = None) -> dict:
    """The JSON in INTEGRATION.md §2. No user, booking, or plate. UUID is fixed by the caller on retry."""
    if kind not in ("enter", "exit"):
        raise ValueError("kind must be enter or exit")
    signal = {
        "client_event_id": client_event_id or str(uuid.uuid4()),
        "device_id": f"gate-{lot_id}-{gate}",
        "lot_id": lot_id,
        "source": "gate_sensor",
        "kind": kind,
        "observed_at": observed_at,
        "payload": {"gate_id": f"{lot_id}-{gate}", "lane": int(lane)},
    }
    validate_signal(signal)
    return signal


class OfflineQueue:
    """RAM queue of gate posts. Head only, so a `422` drops that event and nothing else.

    `post(signal)` returns `(status, body)` or raises `OSError` / `TimeoutError` when the
    network fails. `2xx` removes the head (`duplicates` in the body is fine). `422` drops it.
    `5xx`, `408`, and `429` keep it and wait 1, 2, 5, then 10 seconds.
    """

    def __init__(self, maxlen: int = QUEUE_MAX, backoff_s: tuple[float, ...] = BACKOFF_S):
        if maxlen < 1:
            raise ValueError("queue must hold at least one event")
        if not backoff_s:
            raise ValueError("backoff schedule is empty")
        self.maxlen, self.backoff_s = maxlen, backoff_s
        self.items: deque[dict] = deque()
        self.dropped: list[dict] = []
        self.next_at = 0.0
        self._attempt = 0

    def push(self, signal: dict) -> None:
        if len(self.items) >= self.maxlen:
            self.items.popleft()
        self.items.append(copy.deepcopy(signal))

    def pump(self, now: float, post) -> dict | None:
        """Try the head once. `None` when the queue is empty or the backoff has not elapsed."""
        if not self.items or now < self.next_at:
            return None
        signal = self.items[0]
        try:
            status, body = post(signal)
        except (OSError, TimeoutError) as exc:
            self._schedule(now)
            return {"result": "retry", "error": str(exc), "client_event_id": signal["client_event_id"]}
        event_id = signal["client_event_id"]
        if 200 <= int(status) < 300:
            self.items.popleft()
            self._attempt = 0
            self.next_at = now
            return {"result": "sent", "status": int(status), "body": body, "client_event_id": event_id}
        if int(status) == 422 or (400 <= int(status) < 500 and int(status) not in (408, 429)):
            self.dropped.append(self.items.popleft())
            self._attempt = 0
            self.next_at = now
            return {"result": "dropped", "status": int(status), "client_event_id": event_id}
        self._schedule(now)
        return {"result": "retry", "status": int(status), "client_event_id": event_id}

    def _schedule(self, now: float) -> None:
        delay = self.backoff_s[min(self._attempt, len(self.backoff_s) - 1)]
        self._attempt += 1
        self.next_at = now + delay


class SensorClock:
    """Demo: `GET /dev/clock` every 60 s, then add elapsed seconds. Field: the machine's local time."""

    RESYNC_S = 60.0

    def __init__(self, mode: str = "demo"):
        if mode not in ("demo", "field"):
            raise ValueError("clock mode must be demo or field")
        self.mode = mode
        self._server: datetime | None = None
        self._mono = 0.0
        self._synced_at = float("-inf")

    def needs_sync(self, mono: float) -> bool:
        return self.mode == "demo" and mono - self._synced_at >= self.RESYNC_S

    def sync(self, server_iso: str, mono: float) -> None:
        self._server = datetime.fromisoformat(server_iso)
        self._mono = mono
        self._synced_at = mono

    def iso(self, mono: float) -> str:
        if self.mode == "field":
            return datetime.now().astimezone().isoformat(timespec="seconds")
        if self._server is None:
            raise RuntimeError("demo clock has not read GET /dev/clock")
        return (self._server + timedelta(seconds=mono - self._mono)).isoformat(timespec="seconds")
