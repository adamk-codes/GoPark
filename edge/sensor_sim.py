"""Fake gate presence sensor: posts `gate_sensor` enter/exit events like the ESP32 does (CONTRACT §6).

    uv run python -m edge.sensor_sim --lot-id AUB                       # type e / x + Enter per car, q quits
    uv run python -m edge.sensor_sim --lot-id AUB --every 20 --pattern enter,exit --count 6
    uv run python -m edge.sensor_sim --lot-id AUB --also http://localhost:8090/gate   # also wake the plate reader

Each event is validated against contract/schemas/signal.schema.json. `observed_at` comes from the API's
demo clock (`GET /dev/clock`, default) or real local time (`--clock field`). A failed post stays on an
offline queue with the same `client_event_id` and is retried with backoff (INTEGRATION.md §4). The
ESP32 sketch uses the same rules (`edge/gate_logic.py`). Needs only the default install (no edge group).
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
import urllib.error

from edge.camera.common import Clock, http_json
from edge.gate_logic import OfflineQueue, build_gate_signal


def gate_signal(clock: Clock, kind: str, lot_id: str, gate: int, lane: int) -> dict:
    return build_gate_signal(lot_id=lot_id, gate=gate, lane=lane, kind=kind, observed_at=clock.iso())


def main(argv: list[str] | None = None) -> list[dict]:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lot-id", required=True)
    ap.add_argument("--gate", type=int, default=1, help="gate number: device gate-<LOT>-<n>, gate_id <LOT>-<n>")
    ap.add_argument("--lane", type=int, default=1)
    ap.add_argument("--api", default="http://localhost:8005")
    ap.add_argument("--clock", choices=["demo", "field"], default="demo")
    ap.add_argument("--dry-run", action="store_true", help="print instead of posting to the API")
    ap.add_argument("--also", action="append", default=[], help="extra URL(s) to POST each event to "
                                                                "(e.g. the plate reader's /gate)")
    ap.add_argument("--every", type=float, help="seconds between events (no keyboard)")
    ap.add_argument("--pattern", default="enter,exit", help="--every: kinds to cycle through")
    ap.add_argument("--count", type=int, default=0, help="--every: stop after N events (0 = forever)")
    a = ap.parse_args(argv)

    clock = Clock(a.clock, a.api)
    queue = OfflineQueue()

    def post(body: dict):
        try:
            return 200, http_json("POST", f"{a.api}/signals", body, timeout=5)
        except urllib.error.HTTPError as exc:
            return exc.code, None

    def drain() -> None:
        if a.dry_run:
            return
        while True:
            result = queue.pump(time.monotonic(), post)
            if result is None or result["result"] == "retry":
                if result and result["result"] == "retry":
                    print(f"[sim] queued {result['client_event_id']}, retry with the same id", file=sys.stderr)
                return
            if result["result"] == "dropped":
                print(f"[sim] dropped {result['client_event_id']} (HTTP {result['status']})", file=sys.stderr)

    def fire(kind: str) -> dict:
        sig = gate_signal(clock, kind, a.lot_id, a.gate, a.lane)
        if a.dry_run:
            print(json.dumps(sig, ensure_ascii=False), flush=True)
        else:
            queue.push(sig)
            drain()
        for url in a.also:
            try:
                http_json("POST", url, sig, timeout=1)
            except Exception as e:
                print(f"[also] {url}: {e}", file=sys.stderr)
        print(f"[sim] {kind} {sig['payload']['gate_id']} lane {a.lane} at {sig['observed_at']}", file=sys.stderr)
        return sig

    sent = []
    try:
        if a.every:
            kinds = itertools.cycle([k.strip() for k in a.pattern.split(",") if k.strip()])
            for i in itertools.count():
                if a.count and i >= a.count:
                    break
                if i:
                    time.sleep(a.every)
                sent.append(fire(next(kinds)))
        else:
            print("e = car in, x = car out, q = quit (then Enter)", file=sys.stderr)
            for line in sys.stdin:
                c = line.strip().lower()[:1]
                if c == "q":
                    break
                if c in ("e", "x"):
                    sent.append(fire("enter" if c == "e" else "exit"))
    except KeyboardInterrupt:
        pass
    drain()
    return sent


if __name__ == "__main__":
    main()
