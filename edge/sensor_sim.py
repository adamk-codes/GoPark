"""Fake gate presence sensor: posts `gate_sensor` enter/exit events like the ESP32 does (CONTRACT §6).

    uv run python -m edge.sensor_sim --lot-id AUB                       # type e / x + Enter per car, q quits
    uv run python -m edge.sensor_sim --lot-id AUB --every 20 --pattern enter,exit --count 6
    uv run python -m edge.sensor_sim --lot-id AUB --also http://localhost:8090/gate   # also wake the plate reader

Each event is validated against contract/schemas/signal.schema.json. `observed_at` comes from the API's
demo clock (`GET /dev/clock`, default) or real local time (`--clock field`). Failed posts are queued and
retried with the next event (same `client_event_id`, so the API ignores duplicates). Needs only the
default install (no edge group).
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time

from edge.camera.common import Clock, SignalSink, http_json, make_signal


def gate_signal(clock: Clock, kind: str, lot_id: str, gate: int, lane: int) -> dict:
    return make_signal(device_id=f"gate-{lot_id}-{gate}", lot_id=lot_id, source="gate_sensor", kind=kind,
                       observed_at=clock.iso(), payload={"gate_id": f"{lot_id}-{gate}", "lane": lane})


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
    sink = SignalSink(a.api, a.dry_run)

    def fire(kind: str) -> dict:
        sig = gate_signal(clock, kind, a.lot_id, a.gate, a.lane)
        sink.send(sig)
        for url in a.also:
            try:
                http_json("POST", url, sig, timeout=2)
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
    sink.flush()
    return sent


if __name__ == "__main__":
    main()
