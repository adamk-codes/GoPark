"""tools/simulator: timeline, replay through the API, offline emulation, metrics, config rewrite."""
from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import yaml

from tests.conftest import FIXED_FUSION, ROOT
from tools.simulator import evaluate as ev
from tools.simulator.replay import replay
from tools.simulator.timeline import Trace, build_timeline, load_traces

T0 = datetime(2026, 11, 16, 8, 0, tzinfo=timezone(timedelta(hours=2)))


def _line(source, kind, minutes, sent_minutes=None, **extra):
    payload = {"sent_at": (T0 + timedelta(minutes=sent_minutes)).isoformat()} if sent_minutes is not None else {}
    return {"client_event_id": f"{source}-{kind}-{minutes}", "device_id": "dev-U1", "user_id": "U1", "lot_id": "BDD",
            "booking_id": "B1", "source": source, "kind": kind,
            "observed_at": (T0 + timedelta(minutes=minutes)).isoformat(), "payload": payload, "truth": True, **extra}


def _trace(lines) -> Trace:
    return Trace(booking_id="B1", user_id="U1", lot_id="BDD", lot_kind="underground", scenario="underground",
                 variant="ble", arrives=True, actual_arrival=T0, actual_departure=T0 + timedelta(hours=2),
                 start=T0, end=T0 + timedelta(hours=2), plate_norm="B123456", lines=lines)


def test_timeline_sends_buffered_signals_when_the_phone_reconnects():
    trace = _trace([_line("geofence", "enter", -1), _line("ble", "enter", 0, sent_minutes=12),
                    _line("motion", "park", 1, sent_minutes=12)])
    steps = build_timeline([trace], tick_min=5)
    batches = [s for s in steps if s.signals]
    assert [s.at for s in batches] == [T0 - timedelta(minutes=1), T0 + timedelta(minutes=12)]
    assert [x["source"] for x in batches[1].signals] == ["ble", "motion"]
    assert all("truth" not in x for s in batches for x in s.signals)
    ticks = [s.at for s in steps if not s.signals]
    assert ticks == sorted(ticks) and ticks[-1] >= trace.end + timedelta(minutes=40)
    # A tick at the same moment as a batch comes after it.
    same = [i for i, s in enumerate(steps) if s.at == T0 + timedelta(minutes=12)]
    assert all(steps[i].signals for i in same[:1])


def test_replay_records_decisions_and_matches_offline_emulation(ops, workdir):
    everything = load_traces(workdir)
    picked: list[Trace] = []
    for scenario in ("clean", "underground", "weak_gps", "phone_dead", "contradiction", "no_show"):
        picked += [t for t in everything if t.scenario == scenario][:2]
    assert len(picked) >= 6
    records = replay(ops, picked, speed=0, tick_min=10)
    assert [r["booking_id"] for r in records] == [t.booking_id for t in picked]
    by_id = {r["booking_id"]: r for r in records}
    for t in picked:
        rec = by_id[t.booking_id]
        offline = ev.emulate(t, FIXED_FUSION, [])
        assert bool(rec["confirmed_at"]) == bool(offline["confirmed_at"]), t.booking_id
        assert rec["in_at"] == offline["in_at"], t.booking_id
        if not t.arrives:
            assert not rec["confirmed_at"]
        if rec["confirmed_at"]:
            assert rec["state"] == "CHECKED_OUT"   # every replayed stay ends, by evidence or inference
            assert rec["check_out_action"] in ("auto_check_out", "inferred_check_out")
    m = ev.metrics(records)
    assert m["bookings"] == len(picked) and m["false_confirm_rate"] in (0.0, None)


def test_metrics_rates_and_time_to_confirm():
    base = {"lot_id": "AUB", "lot_kind": "open", "scenario": "clean", "variant": "", "alerts": [],
            "start": None, "end": None, "state": None, "in_method": None, "in_conf": None, "out_method": None,
            "inferred": None, "in_at": None, "out_at": None, "arriving_at": None, "confirmed_at": None,
            "checked_out_at": None, "check_out_action": None}
    t = lambda m: (T0 + timedelta(minutes=m)).isoformat()  # noqa: E731
    records = [
        {**base, "booking_id": "A", "arrives": True, "actual_arrival": t(0), "actual_departure": t(60),
         "confirmed_at": t(2), "in_method": "auto", "in_at": t(0), "out_at": t(61), "checked_out_at": t(61),
         "check_out_action": "auto_check_out"},
        {**base, "booking_id": "B", "arrives": True, "actual_arrival": t(0), "actual_departure": t(60),
         "arriving_at": t(1), "in_method": "auto", "in_at": t(1), "out_at": t(70), "checked_out_at": t(90),
         "check_out_action": "inferred_check_out", "alerts": ["unconfirmed_departure"]},
        {**base, "booking_id": "C", "arrives": True, "actual_arrival": t(0), "actual_departure": t(60),
         "alerts": ["low_confidence"]},
        {**base, "booking_id": "D", "arrives": False, "actual_arrival": None, "actual_departure": None,
         "confirmed_at": t(5), "in_method": "auto", "in_at": t(5)},
    ]
    m = ev.metrics(records)
    assert (m["arrivals"], m["no_shows"]) == (3, 1)
    assert m["auto_confirm_rate"] == round(1 / 3, 4) and m["ask_driver_rate"] == round(1 / 3, 4)
    assert m["missed_arrival_rate"] == round(1 / 3, 4) and m["false_confirm_rate"] == 1.0
    assert m["median_time_to_confirm_min"] == 2.0 and m["low_confidence_alerts"] == 1
    d = m["departure"]
    assert d["stays"] == 2 and d["auto_checkout_rate"] == 0.5 and d["inferred_checkout_rate"] == 0.5
    assert d["median_out_at_error_min"] == 5.5   # |61-60| and |70-60|
    assert ev.split_of("B016522") in ("tune", "test") and ev.split_of("B016522") == ev.split_of("B016522")


def test_write_config_keeps_comments_and_changes_only_tuned_values(tmp_path):
    path = tmp_path / "fusion.yaml"
    shutil.copy(ROOT / "config" / "fusion.yaml", path)
    before = yaml.safe_load(path.read_text(encoding="utf-8"))
    ev.write_config(path, {"signals.motion.log_lr": [None, 3.5], "signals.camera_plate.fuzzy_log_lr": [None, 2.25],
                           "freshness_half_life_min": [None, 60]})
    text = path.read_text(encoding="utf-8")
    after = yaml.safe_load(text)
    assert after["signals"]["motion"]["log_lr"] == 3.5 and after["freshness_half_life_min"] == 60
    assert after["signals"]["camera_plate"]["fuzzy_log_lr"] == 2.25
    assert after["signals"]["camera_plate"]["log_lr"] == before["signals"]["camera_plate"]["log_lr"]
    assert after["thresholds"] == before["thresholds"] and "# Evidence fusion settings" in text


def test_cli_dry_run_prints_the_schedule(workdir):
    out = subprocess.run([sys.executable, "-m", "tools.simulator", "replay", "--dry-run", "--data", str(workdir),
                          "--scenario", "underground", "--limit", "2"],
                         cwd=ROOT, check=True, capture_output=True, text=True).stdout
    assert out.startswith("2 bookings") and "signals in" in out and "ticks" in out


def test_driver_confirm_resolves_asks_the_same_way_in_replay_and_emulation(ops, workdir):
    everything = load_traces(workdir)
    picked = [t for t in everything if t.scenario in ("weak_gps", "no_show")][:6]
    assert picked
    offline = {t.booking_id: ev.emulate(t, FIXED_FUSION, [], driver_confirm=1.0) for t in picked}
    assert any(r["in_method"] == "driver" for r in offline.values()), "no weak_gps ask in the test data"
    records = replay(ops, picked, speed=0, tick_min=10, driver_confirm=1.0)
    for rec in records:
        emulated = offline[rec["booking_id"]]
        assert (rec["in_method"], bool(rec["confirmed_at"])) == (emulated["in_method"], bool(emulated["confirmed_at"]))
        if not rec["arrives"]:
            assert not rec["confirmed_at"]   # no-show drivers answer "not me"
    m = ev.metrics(records)
    assert m["ask_driver_rate"] in (0.0, None) and m["driver_confirm_rate"] is not None
