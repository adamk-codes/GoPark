"""Rhythm evaluation: metric definitions on hand-built cases + an end-to-end run on the test dataset."""
from __future__ import annotations

import json
from datetime import date

import pandas as pd

from rhythm.evaluate import MODELS, recovery, score_slots, score_top1, split_cutoffs


def _p(rows):
    return pd.DataFrame(rows, columns=["user_id", "day", "location_id", "minute"]).assign(
        day=lambda d: pd.to_datetime(d["day"]))


def test_hit_at_1_needs_same_day_lot_and_60_minutes():
    actual = _p([("a", "2026-11-16", "AUB", 480), ("a", "2026-11-18", "AUB", 480),   # first one counts
                 ("b", "2026-11-16", "AUB", 480), ("c", "2026-11-16", "AUB", 480),
                 ("d", "2026-11-17", "LAU", 600), ("e", "2026-11-16", "AUB", 10)])
    pred = _p([("a", "2026-11-16", "AUB", 535),          # 55 min off: hit
               ("b", "2026-11-16", "AUB", 545),          # 65 min off: miss, but time error counted
               ("c", "2026-11-16", "BDD", 480),          # wrong lot
               ("d", "2026-11-18", "LAU", 600),          # wrong day
               ("e", "2026-11-16", "AUB", 1430)])        # 20 min across midnight is still 20 min
    s = score_top1(pred, actual, 60).set_index("user_id")
    assert s["hit"].to_dict() == {"a": True, "b": False, "c": False, "d": False, "e": True}
    assert s.loc["b", "time_err"] == 65 and pd.isna(s.loc["c", "time_err"])
    missing = score_top1(pred[pred.user_id != "a"], actual, 60).set_index("user_id")
    assert not missing.loc["a", "hit"] and not missing.loc["a", "predicted"]


def test_slot_precision_recall():
    actual = _p([("a", "2026-11-16", "AUB", 480), ("a", "2026-11-18", "AUB", 480)])
    pred = _p([("a", "2026-11-16", "AUB", 500), ("a", "2026-11-20", "AUB", 480)])
    assert score_slots(pred, actual, 60) == {"tp_pred": 1, "n_pred": 2, "tp_actual": 1, "n_actual": 2}


def test_splits_count_back_from_the_last_week():
    cfg = {"splits": {"test_weeks": 4, "validation_weeks": 4}}
    s = split_cutoffs(date(2026, 11, 15), cfg)
    assert s["test"] == [date(2026, 10, 18), date(2026, 10, 25), date(2026, 11, 1), date(2026, 11, 8)]
    assert s["validation"][-1] == date(2026, 10, 11) and len(s["validation"]) == 4


def test_recovery_jaccard_and_false_habits():
    truth = {"users": {
        "a": {"persona": "mwf", "routines": [{"days": ["Mon", "Wed", "Fri"], "center": "08:00", "location_id": "AUB",
                                              "active_from": "2026-05-18", "active_to": None}]},
        "b": {"persona": "occ", "routines": []},
        "c": {"persona": "occ", "routines": []},
        "d": {"persona": "old", "routines": [{"days": ["Tue"], "center": "09:00", "location_id": "LAU",
                                              "active_from": "2026-05-18", "active_to": "2026-08-01"}]}}}
    pred = {"a": [{"days": ["Mon", "Fri"], "location_id": "AUB", "center": "08:10"}],
            "b": [{"days": ["Mon"], "location_id": "AUB", "center": "09:00"}]}
    r = recovery(pred, truth, date(2026, 11, 15))["by_persona"]
    assert r["mwf"]["day_jaccard"] == round(2 / 3, 4) and r["mwf"]["lot_accuracy"] == 1.0
    assert r["mwf"]["time_err_min"] == 10.0
    assert r["occ"]["false_habit_rate"] == 0.5
    assert r["old"]["false_habit_rate"] == 0.0                    # an ended routine isn't expected any more


def test_evaluate_writes_metrics_into_the_run(workdir):
    from rhythm.evaluate import evaluate
    m = evaluate(workdir / "bookings.csv")
    path = workdir / "runs" / m["run_id"] / "metrics.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved["next_booking"]) == {"validation", "test"}
    for split in ("validation", "test"):
        assert set(saved["next_booking"][split]) == set(MODELS)
        for s in saved["next_booking"][split].values():
            assert s["hit_at_1"] is None or 0 <= s["hit_at_1"] <= 1
    assert "engine" in saved["recovery"] and "by_persona" in saved["recovery"]["engine"]
    assert saved["by_persona"]["test"]["engine"]
