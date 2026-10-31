"""--messy injector: each kind of dirt is present, logged with the right count, and nothing else changes."""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime

import pytest

from tests.conftest import ROOT

ARGS = ("--users", "150", "--weeks", "26", "--seed", "21", "--no-traces")


def rows_of(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))[1:]


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    clean, dirty = tmp_path_factory.mktemp("clean"), tmp_path_factory.mktemp("messy")
    for out, extra in ((clean, ()), (dirty, ("--messy",))):
        r = subprocess.run([sys.executable, "-m", "tools.generator", "--out", str(out), *ARGS, *extra],
                           cwd=ROOT, check=True, capture_output=True, text=True)
    log = json.loads((dirty / "messy_log.json").read_text(encoding="utf-8"))
    return rows_of(clean / "bookings.csv"), rows_of(dirty / "bookings.csv"), log, r.stdout


def test_every_kind_is_injected_and_printed(pair):
    _, _, log, stdout = pair
    for kind in ("status_noise", "missing_end_time", "end_before_start", "no_offset", "duplicate"):
        assert log[kind]["count"] > 0, kind
        assert f"{kind} {log[kind]['count']}" in stdout


def test_logged_counts_match_the_file(pair):
    _, dirty, log, _ = pair
    statuses = {"booked", "completed", "cancelled", "no_show"}
    ids = Counter(r[0] for r in dirty)
    uniq = {r[0]: r for r in dirty}.values()
    assert sum(n - 1 for n in ids.values()) == log["duplicate"]["count"]
    assert sum(r[5] not in statuses for r in uniq) == log["status_noise"]["count"]
    assert sum(r[4] == "" for r in uniq) == log["missing_end_time"]["count"]
    assert sum(len(r[3]) == 19 for r in uniq) == log["no_offset"]["count"]
    swapped = [r for r in uniq if r[4] and len(r[3]) > 19
               and datetime.fromisoformat(r[4]) < datetime.fromisoformat(r[3])]
    assert len(swapped) == log["end_before_start"]["count"]
    noisy = {r[5].strip().lower().replace("canceled", "cancelled") for r in uniq}
    assert noisy <= statuses


def test_untouched_rows_equal_the_clean_run(pair):
    clean, dirty, log, _ = pair
    damaged = {b for k, v in log.items() if k != "duplicate" for b in v["booking_ids"]}
    dirty_by_id = {r[0]: r for r in dirty}
    assert set(dirty_by_id) == {r[0] for r in clean}
    for r in clean:
        if r[0] not in damaged:
            assert dirty_by_id[r[0]] == r
        if r[5] == "booked":
            assert r[0] not in damaged and r[0] not in log["duplicate"]["booking_ids"]
