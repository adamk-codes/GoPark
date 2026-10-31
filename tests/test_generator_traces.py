"""Driver/camera traces: contract-valid signals, one trace per live booking, scenarios by lot kind."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import date, datetime, timedelta

import jsonschema
import pytest

from tests.conftest import ROOT, schema
from tools.generator.traces import CAMERA_KIT, SCENARIOS

CAMERA_SOURCES = {"camera_plate", "camera_spot"}


def generate(out, *extra):
    subprocess.run([sys.executable, "-m", "tools.generator", "--out", str(out), "--users", "200", "--weeks", "6",
                    "--seed", "17", *extra], cwd=ROOT, check=True, capture_output=True)
    with open(out / "traces" / "index.csv", newline="", encoding="utf-8") as f:
        index = list(csv.DictReader(f))
    traces = {r["booking_id"]: [json.loads(line) for line in
                                (out / "traces" / f"{r['booking_id']}.jsonl").read_text(encoding="utf-8").splitlines()]
              for r in index}
    return index, traces


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    out = tmp_path_factory.mktemp("traces")
    index, traces = generate(out)
    with open(out / "bookings.csv", newline="", encoding="utf-8") as f:
        bookings = {r["booking_id"]: r for r in csv.DictReader(f)}
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    return index, traces, bookings, truth


def test_every_line_matches_signal_schema(data):
    _, traces, _, _ = data
    validator = jsonschema.Draft202012Validator(schema("signal"), format_checker=jsonschema.FormatChecker())
    n = 0
    for lines in traces.values():
        for s in lines:
            validator.validate(s)
            assert isinstance(s["truth"], bool)
            assert datetime.fromisoformat(s["observed_at"]).utcoffset() is not None
            n += 1
    assert n > 500


def test_one_trace_per_live_booking(data):
    index, _, bookings, truth = data
    cutoff = date.fromisoformat(truth["generated_with"]["history_end"]) + timedelta(days=1)
    live = {b for b, r in bookings.items() if r["status"] == "booked"}
    assert live and set(r["booking_id"] for r in index) == live
    assert all(datetime.fromisoformat(bookings[b]["start_time"]).date() >= cutoff for b in live)
    assert sum(truth["generated_with"]["trace_scenarios"].values()) == len(index)


def test_lines_sorted_and_buffered_signals_sent_later(data):
    index, traces, _, _ = data
    buffered = 0
    for r in index:
        lines = traces[r["booking_id"]]
        assert [s["observed_at"] for s in lines] == sorted(s["observed_at"] for s in lines)
        assert int(r["signals"]) == len(lines)
        for s in lines:
            sent = (s.get("payload") or {}).get("sent_at")
            if sent:
                buffered += 1
                assert datetime.fromisoformat(sent) > datetime.fromisoformat(s["observed_at"])
        if r["scenario"] == "underground":
            ble_or_motion = [s for s in lines if s["source"] in ("ble", "motion")]
            assert ble_or_motion and all("sent_at" in s["payload"] for s in ble_or_motion)
    assert buffered > 0


def test_scenarios_follow_lot_kind(data):
    index, traces, _, _ = data
    seen = set()
    for r in index:
        assert r["scenario"] in SCENARIOS[r["lot_kind"]]
        seen.add(r["scenario"])
        lines = traces[r["booking_id"]]
        driver = [s for s in lines if s["device_id"].startswith("dev-")]
        if r["scenario"] == "phone_dead":
            assert not driver
        if r["scenario"] == "no_show":
            assert r["arrives"] == "False" and not any(s["truth"] for s in lines)
            assert not any(s["source"] in CAMERA_SOURCES for s in lines)
        else:
            assert r["arrives"] == "True"
            assert r["actual_arrival"] < r["actual_departure"]
        if r["scenario"] == "contradiction":
            wrong = [s for s in lines if s["source"] == "geofence"]
            assert wrong and all(not s["truth"] and s["lot_id"] != r["lot_id"] for s in wrong)
        if r["scenario"] == "weak_gps":
            assert all(s["payload"]["accuracy_m"] >= 120 for s in driver)
            assert not any(s["source"] == "motion" for s in driver)
        if r["lot_kind"] == "underground":
            assert r["scenario"] not in ("clean", "weak_gps")
    assert {"clean", "weak_gps", "underground", "phone_dead", "contradiction"} <= seen


def test_camera_signals_by_lot_kit(data):
    index, traces, bookings, _ = data
    plates_seen = 0
    for r in index:
        lines = traces[r["booking_id"]]
        cams = [s for s in lines if s["source"] in CAMERA_SOURCES]
        kit = CAMERA_KIT[r["lot_kind"]]
        if not kit["plate"]:
            assert not cams
        for s in cams:
            assert s["user_id"] is None and s["booking_id"] is None and s["device_id"].startswith("cam-")
            if s["source"] == "camera_plate":
                plates_seen += 1
                assert len(s["payload"]["plate_norm"]) == len(bookings[r["booking_id"]]["plate"].replace(" ", ""))
            else:
                assert kit["spot"] and s["payload"]["space_id"].startswith(r["lot_id"] + "-")
    assert plates_seen > 0


def test_no_cameras_flag(tmp_path):
    _, traces = generate(tmp_path, "--no-cameras")
    assert not any(s["source"] in CAMERA_SOURCES for lines in traces.values() for s in lines)
