"""Unit tests for the camera edge logic (no models needed). Run: uv run pytest edge/tests"""
import io
import json

import jsonschema
import numpy as np
import pytest

from api.db import normalize_plate as api_normalize
from edge.camera.common import PlateDebouncer, SignalSink, make_signal, normalize_plate, plate_from_ocr, validate_signal
from edge.camera.spot_watcher import SpotGeometry, SpotTracker


@pytest.mark.parametrize("raw, want", [
    ("B 123456", "B123456"),
    ("b-12 34", "B1234"),
    ("B ١٢٣٤٥٦", "B123456"),
    ("M ۷۸۹", "M789"),          # Persian digits too
    ("8 123456", "B123456"),    # letter read as digit
    ("B 12O4S6", "B120456"),    # digits read as letters
    ("|B 123456", "B123456"),   # border noise
    ("B1234567", "B123456"),    # stray trailing char trimmed
    ("123456", None),
    ("", None),
    (None, None),
    ("B", None),
    ("IB123456", "B123456"),  # border read as I
])
def test_plate_from_ocr(raw, want):
    assert plate_from_ocr(raw) == want


def test_normalize_matches_api():
    for raw in ["B 123456", "b-1234", "B ١٢٣٤٥٦", " z 9 "]:
        assert normalize_plate(raw) == api_normalize(raw)


def test_debounce_needs_two_of_five_and_cooldown():
    d = PlateDebouncer(window=5, need=2, cooldown_s=60)
    assert d.push({"B123"}, 0.0) == []
    assert d.push(set(), 0.1) == []
    assert d.push({"B123"}, 0.2) == ["B123"]      # 2 of last 3
    assert d.push({"B123"}, 0.3) == []            # cooldown
    for t in range(5):
        d.push(set(), 1 + t)
    assert d.push({"B123"}, 70.0) == []           # window was cleared by empty frames
    assert d.push({"B123"}, 70.5) == ["B123"]     # after cooldown
    # a one-off misread never fires
    d2 = PlateDebouncer()
    assert [d2.push(s, i) for i, s in enumerate([{"B1"}, {"B2"}, {"B3"}, {"B4"}, {"B5"}])] == [[]] * 5
    # outside the window: hits 5 frames apart do not count
    d3 = PlateDebouncer()
    out = [d3.push({"Z9"} if i in (0, 5) else set(), i) for i in range(6)]
    assert out == [[]] * 6


def test_signals_validate_against_contract():
    plate = make_signal(device_id="cam-entrance-1", lot_id="AUB", source="camera_plate", kind="enter",
                        observed_at="2026-11-16T08:01:10+02:00", payload={"plate_norm": "B123456", "ocr_conf": 0.9})
    spot = make_signal(device_id="cam-overhead-1", lot_id="LAU", source="camera_spot", kind="occupied",
                       observed_at="2026-11-16T08:01:10+02:00", payload={"space_id": "LAU-07"})
    for s in (plate, spot):
        validate_signal(s)
        assert len(s["client_event_id"]) >= 8
    with pytest.raises(jsonschema.ValidationError):
        validate_signal({**plate, "kind": "arrive"})
    with pytest.raises(jsonschema.ValidationError):
        validate_signal({**plate, "image": "..."})  # additionalProperties: false


def test_dry_run_prints_json_lines():
    buf = io.StringIO()
    sink = SignalSink(api=None, dry_run=True, out=buf)
    sig = make_signal(device_id="d", lot_id="AUB", source="camera_plate", kind="exit",
                      observed_at="2026-11-16T08:01:10+02:00", payload={"plate_norm": "B1"})
    sink.send(sig)
    assert json.loads(buf.getvalue()) == sig


def test_spot_tracker_votes_and_only_sends_changes():
    tr = SpotTracker(["S1"], frames=5, agree=4)
    seq = [True, True, True, True, False, True, False, False, False, False, False]
    changes = [tr.push({"S1": v}) for v in seq]
    flat = [c for cs in changes for c in cs]
    assert flat == [("S1", "vacant")]          # initial occupied not sent; then vacant once
    tr2 = SpotTracker(["S1"], frames=5, agree=4, emit_initial=True)
    flat2 = [c for v in seq for c in tr2.push({"S1": v})]
    assert flat2 == [("S1", "occupied"), ("S1", "vacant")]


def test_spot_geometry_coverage_and_scaling():
    cfg = {"frame_size": [100, 100], "spots": [{"space_id": "A", "polygon": [[0, 0], [50, 0], [50, 100], [0, 100]]},
                                               {"space_id": "B", "polygon": [[50, 0], [100, 0], [100, 100], [50, 100]]}]}
    g = SpotGeometry(cfg, (200, 200, 3))  # frame twice the JSON size
    cov = g.coverage_from_boxes(np.array([[0, 0, 100, 200]]))
    assert cov[0] == pytest.approx(1.0, abs=0.03) and cov[1] < 0.05
    assert g.coverage_from_boxes(np.zeros((0, 4))) == [0.0, 0.0]
