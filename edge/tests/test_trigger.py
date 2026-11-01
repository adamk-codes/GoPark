"""Sensor-triggered plate reading (edge/camera/trigger.py) and the gate sensor simulator. No models needed."""
import io
import json
import time
import urllib.request
from contextlib import redirect_stdout

from edge import sensor_sim
from edge.camera.common import validate_signal
from edge.camera.trigger import GateEvent, GatePairer, ListenSource, PlateHit, event_from_signal


def hit(t, plate, conf=0.9):
    return PlateHit(t, plate, conf, f"2026-11-16T08:00:{int(t):02d}+02:00")


def ev(t, kind="enter", lot="AUB", gate="AUB-1", lane=1, cid=None):
    return GateEvent(t, kind, lot, gate, lane, cid)


def test_best_read_in_window_is_posted_after_window():
    p = GatePairer("AUB", window_s=8, need=2)
    p.add_hits([hit(1, "B123456", 0.7), hit(2, "B123456", 0.95), hit(3, "B128456", 0.99), hit(4, "B123456", 0.8)])
    assert p.add_event(ev(5))
    assert p.due(12.9) == []                       # window still open: reads may still come
    p.add_hits([hit(12, "B123456", 0.9)])          # inside +8 s
    (pr,) = p.due(13.0)
    assert pr.plate == "B123456" and pr.reads == 4 and pr.conf == 0.95
    assert pr.observed_at.endswith("08:00:02+02:00")  # time of the best frame
    assert pr.candidates == {"B123456": 4, "B128456": 1}


def test_reads_outside_window_and_single_misreads_are_ignored():
    p = GatePairer("AUB", window_s=8, need=2)
    p.add_hits([hit(0, "Z777"), hit(1, "Z777"),     # 20 s before the event: an earlier car
                hit(20, "M42")])                    # read once only
    p.add_event(ev(20))
    (pr,) = p.due(28)
    assert pr.plate is None and pr.candidates == {"M42": 1}
    p2 = GatePairer("AUB", window_s=8, need=1)
    p2.add_hits([hit(20, "M42")])
    p2.add_event(ev(20))
    assert p2.due(28)[0].plate == "M42"


def test_direction_lane_lot_filters_and_retries():
    auto = GatePairer("AUB", kind="auto")
    assert auto.add_event(ev(1, "enter")) and auto.add_event(ev(2, "exit"))
    exit_cam = GatePairer("AUB", kind="exit")
    assert not exit_cam.add_event(ev(1, "enter")) and exit_cam.add_event(ev(1, "exit"))
    lane2 = GatePairer("AUB", lane="2")
    assert not lane2.add_event(ev(1, lane=1)) and lane2.add_event(ev(1, lane=2))
    assert not GatePairer("AUB").add_event(ev(1, lot="LAU"))
    assert not GatePairer("AUB", gate_id="AUB-2").add_event(ev(1))
    p = GatePairer("AUB")
    assert p.add_event(ev(1, cid="abc")) and not p.add_event(ev(1.5, cid="abc"))  # sensor retried the same post


def test_auto_kind_takes_direction_from_event_and_plate_used_once():
    p = GatePairer("AUB", kind="auto", window_s=8)
    p.add_hits([hit(10, "B1"), hit(11, "B1"), hit(12, "G2"), hit(13, "G2")])
    p.add_event(ev(11, "exit"))
    p.add_event(ev(12, "exit"))                    # second car right behind
    a, b = p.due(30)
    assert (a.event.kind, a.plate) == ("exit", "B1") or (a.event.kind, a.plate) == ("exit", "G2")
    assert {a.plate, b.plate} == {"B1", "G2"}      # the first car's plate is not reused for the second


def test_flush_pairs_pending_events_at_end_of_file():
    p = GatePairer("AUB")
    p.add_hits([hit(1, "B5"), hit(2, "B5")])
    p.add_event(ev(2))
    assert p.due(3) == [] and p.due(3, flush=True)[0].plate == "B5"


def test_event_from_signal():
    sig = {"client_event_id": "x" * 8, "device_id": "gate-AUB-1", "lot_id": "AUB", "source": "gate_sensor",
           "kind": "exit", "observed_at": "2026-11-16T08:00:00+02:00", "payload": {"gate_id": "AUB-1", "lane": 2}}
    e = event_from_signal(sig, 5.0)
    assert (e.kind, e.lot_id, e.gate_id, e.lane, e.t) == ("exit", "AUB", "AUB-1", 2, 5.0)
    assert event_from_signal({**sig, "source": "camera_plate"}, 0) is None
    assert event_from_signal({**sig, "kind": "park"}, 0) is None


def test_listen_endpoint_accepts_single_and_batch():
    src = ListenSource("127.0.0.1", 0)
    try:
        sig = {"source": "gate_sensor", "kind": "enter", "lot_id": "AUB", "payload": {"gate_id": "AUB-1"}}
        for body in (sig, {"signals": [sig, {**sig, "kind": "exit"}, {"source": "camera_plate", "kind": "enter"}]}):
            req = urllib.request.Request(f"http://127.0.0.1:{src.port}/gate", json.dumps(body).encode(),
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3).read()
        time.sleep(0.05)
        kinds = [e.kind for e in src.poll()]
        assert kinds == ["enter", "enter", "exit"]
    finally:
        src.close()


def test_sensor_sim_dry_run_signals_validate():
    buf = io.StringIO()
    with redirect_stdout(buf):
        sent = sensor_sim.main(["--lot-id", "AUB", "--gate", "2", "--lane", "3", "--every", "0.01",
                                "--count", "3", "--clock", "field", "--dry-run"])
    printed = [json.loads(line) for line in buf.getvalue().splitlines()]
    assert printed == sent and [s["kind"] for s in sent] == ["enter", "exit", "enter"]
    for s in sent:
        validate_signal(s)
        assert s["source"] == "gate_sensor" and s["device_id"] == "gate-AUB-2"
        assert s["payload"] == {"gate_id": "AUB-2", "lane": 3}
