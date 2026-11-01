"""Gate launcher, beam rules, and the offline queue (no camera models)."""
from __future__ import annotations

import pytest

from edge.gate_logic import (
    BeamDecoder, OfflineQueue, Passage, SensorClock, build_gate_signal,
)
from edge.run_gate import ctrl_c_should_stop, load_gates, lot_settings, main, stream_url


def _passages(samples, **kw):
    decoder = BeamDecoder(**kw)
    out = []
    for a, b, t in samples:
        passage = decoder.sample(a, b, t)
        if passage is not None:
            out.append(passage)
    return out


def test_passage_is_second_beam_and_rejects_person_and_backout():
    assert _passages([
        (True, False, 0),
        (True, True, 100),
        (False, False, 400),
    ], debounce_ms=0, clear_ms=0) == [Passage("enter", 100)]
    assert _passages([
        (False, True, 0),
        (True, True, 150),
        (False, False, 300),
    ], debounce_ms=0, clear_ms=0) == [Passage("exit", 150)]
    assert _passages([(True, False, 0), (False, False, 50)], debounce_ms=0, clear_ms=0) == []
    assert _passages([
        (True, False, 0),
        (True, True, 100),
        (False, True, 200),
        (True, True, 300),
        (False, False, 400),
    ], debounce_ms=0, clear_ms=0) == []
    assert _passages([(True, False, 0), (True, False, 10), (False, False, 20)],
                     debounce_ms=30, clear_ms=0) == []


def test_clear_hold_and_stuck_beam():
    assert _passages([
        (True, False, 0),
        (True, True, 100),
        (False, False, 200),
        (False, False, 600),
    ], debounce_ms=0, clear_ms=400) == [Passage("enter", 100)]
    decoder = BeamDecoder(debounce_ms=0, clear_ms=0, max_passage_ms=1000)
    assert decoder.sample(True, False, 0) is None
    assert decoder.sample(True, True, 100) is None
    assert decoder.sample(True, True, 1200) is None
    assert decoder.sample(False, False, 1300) is None


def test_gate_signal_matches_the_integration_shape():
    signal = build_gate_signal(lot_id="AUB", gate=1, lane=1, kind="enter",
                               observed_at="2026-11-16T08:01:10+02:00",
                               client_event_id="6f0d8a4e-3c1b-4f7e-9a59-2f1e0c9b7d11")
    assert set(signal) == {"client_event_id", "device_id", "lot_id", "source", "kind", "observed_at", "payload"}
    assert signal["device_id"] == "gate-AUB-1"
    assert signal["source"] == "gate_sensor" and signal["kind"] == "enter"
    assert signal["payload"] == {"gate_id": "AUB-1", "lane": 1}
    assert "user_id" not in signal and "booking_id" not in signal
    with pytest.raises(ValueError):
        build_gate_signal(lot_id="AUB", gate=1, lane=1, kind="park", observed_at="2026-11-16T08:01:10+02:00")


def test_queue_retries_the_same_id_and_drops_422():
    signal = build_gate_signal(lot_id="AUB", gate=1, lane=1, kind="exit",
                               observed_at="2026-11-16T09:00:00+02:00")
    queue = OfflineQueue(backoff_s=(1, 2, 5, 10))
    queue.push(signal)
    seen = []

    def post(body):
        seen.append(body["client_event_id"])
        if len(seen) == 1:
            raise TimeoutError("wifi unplugged")
        return 200, {"accepted": 1, "duplicates": 0}

    assert queue.pump(0, post)["result"] == "retry"
    assert queue.pump(0.5, post) is None
    sent = queue.pump(1, post)
    assert sent["result"] == "sent" and sent["body"]["accepted"] == 1
    assert seen[0] == seen[1] == signal["client_event_id"]
    assert list(queue.items) == []

    bad = build_gate_signal(lot_id="AUB", gate=1, lane=1, kind="enter",
                            observed_at="2026-11-16T09:00:01+02:00")
    good = build_gate_signal(lot_id="AUB", gate=1, lane=1, kind="enter",
                             observed_at="2026-11-16T09:00:02+02:00")
    queue.push(bad)
    queue.push(good)
    assert queue.pump(1, lambda _body: (422, {"detail": "no"}))["result"] == "dropped"
    assert queue.pump(1, lambda _body: (200, {"accepted": 1, "duplicates": 0}))["result"] == "sent"
    assert [item["client_event_id"] for item in queue.dropped] == [bad["client_event_id"]]


def test_queue_backoff_and_drops_oldest():
    queue = OfflineQueue(maxlen=2, backoff_s=(1, 2, 5, 10))
    ids = []
    for i in range(3):
        signal = build_gate_signal(lot_id="AUB", gate=1, lane=1, kind="enter",
                                   observed_at=f"2026-11-16T09:00:0{i}+02:00",
                                   client_event_id=f"gate-event-{i:04d}")
        ids.append(signal["client_event_id"])
        queue.push(signal)
    assert [item["client_event_id"] for item in queue.items] == ids[1:]
    assert queue.pump(0, lambda _body: (500, None))["result"] == "retry"
    assert queue.next_at == 1
    assert queue.pump(1, lambda _body: (503, None))["result"] == "retry"
    assert queue.next_at == 3
    assert queue.pump(3, lambda _body: (500, None))["result"] == "retry"
    assert queue.next_at == 8
    assert queue.pump(8, lambda _body: (500, None))["result"] == "retry"
    assert queue.next_at == 18
    assert queue.pump(18, lambda _body: (500, None))["result"] == "retry"
    assert queue.next_at == 28


def test_demo_clock_resyncs_every_minute():
    clock = SensorClock("demo")
    assert clock.needs_sync(0)
    clock.sync("2026-11-16T07:50:00+02:00", 10)
    assert clock.iso(12.8) == "2026-11-16T07:50:02+02:00"
    assert not clock.needs_sync(69) and clock.needs_sync(70)
    assert SensorClock("field").needs_sync(10_000) is False


def test_run_gate_prints_the_agreed_phone_command():
    argv = main(["--lot-id", "AUB", "--phone", "http://10.0.0.8:8080", "--print-only"])
    assert argv[argv.index("--source") + 1] == "http://10.0.0.8:8080/video"
    assert argv[argv.index("--kind") + 1] == "auto"
    assert argv[argv.index("--trigger") + 1] == "api"
    assert "--cars" in argv and "--vehicle-match" in argv
    assert argv[argv.index("--fps") + 1] == "4"
    assert argv[argv.index("--min-conf") + 1] == "0.85"
    assert argv[argv.index("--need") + 1] == "2"
    assert argv[argv.index("--roi") + 1] == "160,240,960,440"
    assert argv[argv.index("--gate-id") + 1] == "AUB-1"
    assert argv[argv.index("--clock") + 1] == "demo"
    kept = main(["--lot-id", "AUB", "--phone", "http://10.0.0.8:8080/video", "--clock", "field",
                 "--source", "clip.mp4", "--print-only"])
    assert kept[kept.index("--source") + 1] == "clip.mp4"
    assert kept[kept.index("--clock") + 1] == "field"
    with pytest.raises(SystemExit):
        main(["--lot-id", "AUB", "--print-only"])
    with pytest.raises(SystemExit):
        main(["--lot-id", "NO-SUCH", "--phone", "http://10.0.0.8:8080", "--print-only"])


def test_aub_roi_covers_the_synthetic_gate_plate():
    # Last approach frame of edge.tests.synth.make_gate_video: plate_w = 90+39*6, shift x = (20-39)*4.
    settings = lot_settings(load_gates(), "AUB")
    x, y, w, h = settings["roi"]
    plate_w = 324
    plate_h = int(plate_w * 120 / 520)
    cx, cy = 1280 // 2 + (20 - 39) * 4, int(720 * 0.62)
    px, py = cx - plate_w // 2, cy - plate_h // 2
    assert plate_w >= 200
    assert x <= px and px + plate_w <= x + w
    assert y <= py and py + plate_h <= y + h
    assert stream_url("http://10.1.2.3:8080") == "http://10.1.2.3:8080/video"
    assert ctrl_c_should_stop(0) and ctrl_c_should_stop(1) and not ctrl_c_should_stop(2)
