"""Generator personas, persona mix, capacity and determinism (tools/generator)."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from tests.conftest import ROOT
from tools.generator.personas import PERSONAS

HIDDEN = ROOT / "tools" / "generator" / "hidden_personas.py"


def generate(out: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "tools.generator", "--out", str(out), *args],
                          cwd=ROOT, check=True, capture_output=True, text=True)


def load(out: Path):
    truth = json.loads((out / "truth.json").read_text(encoding="utf-8"))
    with open(out / "bookings.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    by_user = defaultdict(list)
    for r in rows:
        by_user[r["user_id"]].append(r)
    return truth, rows, by_user


@pytest.fixture(scope="module")
def mixed(tmp_path_factory):
    """Every persona, equal weights, enough users to see each several times."""
    out = tmp_path_factory.mktemp("gen_mixed")
    generate(out, "--users", "240", "--weeks", "26", "--seed", "11", "--no-traces",
             "--mix", ",".join(PERSONAS))
    return load(out)


def users_of(truth, persona):
    return [(u, v) for u, v in truth["users"].items() if v["persona"] == persona]


def d(iso: str) -> date:
    return datetime.fromisoformat(iso).date()


def test_every_persona_appears_with_valid_routines(mixed):
    truth, rows, _ = mixed
    assert set(truth["generated_with"]["personas"]) == set(PERSONAS)
    for u, v in truth["users"].items():
        vehicle = v["vehicle"]
        assert vehicle["color"] in ("white", "silver", "black", "grey", "blue", "red", "green", "beige")
        assert vehicle["body"] in ("sedan", "hatchback", "suv", "pickup", "van")
        assert vehicle["make"] in ("Kia", "Toyota", "Hyundai", "Nissan", "Mercedes", "BMW")
        assert all(r["vehicle_color"] == vehicle["color"] and r["vehicle_body"] == vehicle["body"]
                   and r["vehicle_make"] == vehicle["make"] for r in rows if r["user_id"] == u)
        for r in v["routines"]:
            assert {"days", "center", "location_id", "active_from", "active_to"} <= set(r), u
            assert r["days"] and all(x in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") for x in r["days"])
            if r["active_to"]:
                assert r["active_from"] <= r["active_to"], u


def test_routine_switcher_has_two_routines_split_at_switch(mixed):
    truth, _, by_user = mixed
    for u, v in users_of(truth, "routine_switcher"):
        a, b = v["routines"]
        assert date.fromisoformat(a["active_to"]) + timedelta(days=1) == date.fromisoformat(b["active_from"])
        assert v["switch"]["date"] == b["active_from"]
        assert (a["days"], a["location_id"]) != (b["days"], b["location_id"])
        after = [r for r in by_user[u] if d(r["start_time"]) >= date.fromisoformat(b["active_from"])]
        assert after and all(r["location_id"] == b["location_id"] for r in after)


def test_lapsed_stops_and_new_user_starts_late(mixed):
    truth, _, by_user = mixed
    end = date.fromisoformat(truth["generated_with"]["history_end"])
    for u, v in users_of(truth, "lapsed"):
        stop = date.fromisoformat(v["routines"][0]["active_to"])
        assert stop < end - timedelta(weeks=4)
        assert all(d(r["start_time"]) <= stop for r in by_user[u])
    for u, v in users_of(truth, "new_user"):
        joined = date.fromisoformat(v["routines"][0]["active_from"])
        assert timedelta(days=7) <= end - joined + timedelta(days=1) <= timedelta(days=21)
        assert by_user[u] and all(d(r["start_time"]) >= joined for r in by_user[u])


def test_fader_thins_out(mixed):
    truth, _, by_user = mixed
    start = date.fromisoformat(truth["generated_with"]["history_start"])
    for u, v in users_of(truth, "fader"):
        fade_from = date.fromisoformat(v["routines"][0]["attendance"]["fade_from"])
        before = sum(start <= d(r["start_time"]) < fade_from for r in by_user[u]) / max(1, (fade_from - start).days)
        late = [r for r in by_user[u] if d(r["start_time"]) >= fade_from + timedelta(weeks=8)]
        assert len(late) / (8 * 7) < before


def test_noisy_timer_jitter_and_heavy_canceller(mixed):
    truth, _, by_user = mixed
    for u, v in users_of(truth, "noisy_timer"):
        assert 40 <= v["routines"][0]["jitter_min"] <= 60
        minutes = [datetime.fromisoformat(r["start_time"]) for r in by_user[u]]
        minutes = [t.hour * 60 + t.minute for t in minutes]
        assert max(minutes) - min(minutes) >= 60
    for u, v in users_of(truth, "heavy_canceller"):
        statuses = Counter(r["status"] for r in by_user[u] if r["status"] != "booked")
        assert statuses["cancelled"] / sum(statuses.values()) >= 0.2


def test_multi_routine_and_coincidence_trap(mixed):
    truth, _, by_user = mixed
    for u, v in users_of(truth, "multi_routine"):
        assert len(v["routines"]) == 2 and v["routines"][1]["days"] == ["Sat"]
        assert any(datetime.fromisoformat(r["start_time"]).weekday() == 5 and r["location_id"] == "HAMRA"
                   for r in by_user[u])
    for u, v in users_of(truth, "coincidence_trap"):
        assert v["routines"] == []
        c = v["coincidence"]
        hits = [r for r in by_user[u] if datetime.fromisoformat(r["start_time"]).strftime("%a") == c["day"]
                and r["location_id"] == c["location_id"]]
        assert len(hits) >= c["count"]


def test_capacity_respected_at_booking_start(tmp_path):
    # Everybody is an MWF student at AUB/LAU around 08:00: far more than 60 spaces each.
    out = generate(tmp_path, "--users", "400", "--weeks", "3", "--seed", "5", "--no-traces", "--mix", "mwf_student")
    truth, rows, _ = load(tmp_path)
    cap = truth["generated_with"]["capacity"]
    assert cap["dropped"] > 0 and f"{cap['dropped']} dropped" in out.stdout
    live = [(datetime.fromisoformat(r["start_time"]), datetime.fromisoformat(r["end_time"]), r["location_id"])
            for r in rows if r["status"] != "cancelled"]
    by_lot = defaultdict(list)
    for s, e, lot in live:
        by_lot[lot].append((s, e))
    for lot, spans in by_lot.items():
        for s, _ in spans:
            assert sum(a <= s < b for a, b in spans) <= 60, (lot, s)


def test_full_output_is_deterministic(tmp_path):
    args = ("--users", "80", "--weeks", "8", "--seed", "9", "--messy")
    generate(tmp_path / "a", *args)
    generate(tmp_path / "b", *args)
    for name in ("bookings.csv", "truth.json", "stays.csv", "messy_log.json", "traces/index.csv"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    traces = sorted((tmp_path / "a" / "traces").glob("*.jsonl"))
    assert traces
    for t in traces[:20]:
        assert t.read_bytes() == (tmp_path / "b" / "traces" / t.name).read_bytes()


def test_unknown_persona_in_mix_fails(tmp_path):
    r = subprocess.run([sys.executable, "-m", "tools.generator", "--out", str(tmp_path), "--mix", "nobody"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode != 0 and "Unknown persona" in r.stderr


def test_hidden_personas_only_with_flag(mixed, tmp_path):
    truth, _, _ = mixed
    if HIDDEN.exists():
        from tools.generator.hidden_personas import HIDDEN_PERSONAS
        assert not set(HIDDEN_PERSONAS) & set(truth["generated_with"]["personas"])
        generate(tmp_path, "--users", "40", "--weeks", "8", "--seed", "2", "--no-traces", "--include-hidden",
                 "--mix", ",".join(HIDDEN_PERSONAS))
        hidden_truth, _, _ = load(tmp_path)
        assert set(hidden_truth["generated_with"]["personas"]) <= set(HIDDEN_PERSONAS)
        assert hidden_truth["generated_with"]["include_hidden"] is True
    else:
        r = subprocess.run([sys.executable, "-m", "tools.generator", "--out", str(tmp_path), "--include-hidden"],
                           cwd=ROOT, capture_output=True, text=True)
        assert r.returncode != 0
