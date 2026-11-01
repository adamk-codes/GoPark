"""Rhythm phase P2 on hand-built users: drift states, short histories, calibration, reminders."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import jsonschema
import numpy as np
import pandas as pd
import pytest

from rhythm.io import load_config
from rhythm.pipeline import analyse, fit_from_slots, pattern_rows, priors_and_forecast
from rhythm.stages import calibrate, drift, remind
from tests.conftest import schema
from tests.test_rhythm_stages import MON, NO_CALENDAR, frame, rows_for, weekly

TZ = "Asia/Beirut"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return load_config()


def run_engine(df, cutoff, cfg, bookings=None):
    an = analyse(df, cutoff, cfg, NO_CALENDAR)
    return an, pattern_rows(an, cfg, "test", bookings=bookings)


# ── drift ──────────────────────────────────────────────────────────────────────

def test_routine_switch_gets_ended_and_new_rows_with_combined_reason(cfg):
    """Mon/Wed/Fri at AUB for 14 weeks, then Tue/Thu at LAU for 5 weeks."""
    switch_day = MON + timedelta(weeks=14)
    rows = weekly("U_SW", "AUB", [0, 2, 4], "08:00", MON, 14) + weekly("U_SW", "LAU", [1, 3], "09:30", switch_day, 5)
    cutoff = switch_day + timedelta(weeks=5) - timedelta(days=1)
    an, out = run_engine(frame(rows), cutoff, cfg)
    mine = rows_for(out, "U_SW")
    new, old = mine[0], mine[1]
    assert (new["label"], new["location_id"], new["is_primary"]) == ("Tue / Thu", "LAU", True)
    assert new["state"] in ("emerging", "active") and new["next_booking"] is not None
    assert abs(date.fromisoformat(new["change_date"]) - switch_day).days <= 7
    assert (old["label"], old["location_id"], old["state"]) == ("Mon / Wed / Fri", "AUB", "ended")
    assert old["next_booking"] is None and old["confidence"] == 0
    assert abs(date.fromisoformat(old["change_date"]) - switch_day).days <= 7
    assert "Routine switch" in new["reason"] and "Routine switch" in old["reason"]
    priors, _ = priors_and_forecast(an)
    assert {p["lot_id"] for p in priors if p["user_id"] == "U_SW"} == {"LAU"}


def test_new_user_with_two_consistent_weeks_is_emerging(cfg):
    """Two weeks of Mon–Thu at 08:15: a lower-confidence emerging routine, not nothing."""
    rows = weekly("U_NEW", "BDD", [0, 1, 2, 3], "08:15", MON, 2) + weekly("U_OLD", "BDD", [0, 1, 2, 3], "08:15", MON - timedelta(weeks=10), 12)
    _, out = run_engine(frame(rows), MON + timedelta(weeks=2) - timedelta(days=1), cfg)
    (new,) = rows_for(out, "U_NEW")
    old = rows_for(out, "U_OLD")[0]
    assert new["pattern_type"] == "recurring" and new["state"] == "emerging"
    assert new["label"] == "Mon / Tue / Wed / Thu"
    assert new["confidence"] <= old["confidence"]
    assert "New since" in new["reason"]


def test_lapsed_user_routine_ends(cfg):
    rows = weekly("U_LAP", "VERDUN", [1, 3], "07:45", MON, 12)
    cutoff = MON + timedelta(weeks=18) - timedelta(days=1)
    an, out = run_engine(frame(rows), cutoff, cfg)
    (row,) = rows_for(out, "U_LAP")
    assert row["state"] == "ended" and row["next_booking"] is None and row["inactivity_detected"]
    assert "Ended" in row["reason"]
    priors, _ = priors_and_forecast(an)
    assert not [p for p in priors if p["user_id"] == "U_LAP"]


def test_change_points_find_a_real_change_and_ignore_noise(cfg):
    days = pd.date_range("2026-03-02", periods=20, freq="7D")
    rng = np.random.default_rng(0)
    steady = (rng.random(20) < 0.8).astype(float)
    t = pd.concat([pd.DataFrame({"user_id": "a", "location_id": "L", "cluster": 0, "weekday": 0, "day": days,
                                 "hit": [0.0] * 12 + [1.0] * 8}),
                   pd.DataFrame({"user_id": "b", "location_id": "L", "cluster": 0, "weekday": 0, "day": days,
                                 "hit": steady})])
    found = drift.change_points(t, cfg).set_index("user_id")
    assert list(found.index) == ["a"]
    assert found.loc["a", "regime_start"] == days[12] and found.loc["a", "direction"] == 1
    assert drift.change_points(t, cfg, enabled=False).empty


def test_switching_drift_off_keeps_the_old_routine(cfg):
    import copy
    off = copy.deepcopy(cfg)
    off["stages"]["drift"] = False
    rows = weekly("U_LAP", "VERDUN", [1, 3], "07:45", MON, 12)
    _, out = run_engine(frame(rows), MON + timedelta(weeks=18) - timedelta(days=1), off)
    assert rows_for(out, "U_LAP")[0]["state"] != "ended"


# ── calibration ────────────────────────────────────────────────────────────────

def test_isotonic_calibration_is_monotone_and_lowers_ece():
    rng = np.random.default_rng(1)
    score = rng.random(4000)
    y = (rng.random(4000) < score ** 2).astype(float)       # over-confident raw score
    m = calibrate.fit(score, y)
    assert m["fitted"] and np.all(np.diff(m["y"]) >= 0) and np.all(np.diff(m["x"]) >= 0)
    p = calibrate.apply(score, m)
    before, after = calibrate.reliability(score, y), calibrate.reliability(p, y)
    assert after["ece"] < before["ece"] and after["brier"] < before["brier"]
    assert sum(b["n"] for b in after["bins"]) == 4000
    off = calibrate.fit(score, y, enabled=False)
    assert not off["fitted"] and np.allclose(calibrate.apply([0.2, 0.7], off), [0.2, 0.7])


def test_tau_is_the_lowest_threshold_reaching_target_precision(cfg):
    p = np.repeat(np.linspace(0.05, 0.95, 19), 50)
    y = (np.tile(np.arange(50), 19) < p * 50).astype(float)  # precision ≈ p in each bucket
    curve = remind.pr_curve(p, y)
    tau = remind.choose_tau(curve, cfg)
    at = next(c for c in curve if c["tau"] == tau)
    assert at["precision"] >= cfg["reminder"]["target_precision"]
    lower = [c for c in curve if c["tau"] < tau]
    assert all(c["precision"] < cfg["reminder"]["target_precision"] for c in lower)
    assert all(a["recall"] >= b["recall"] for a, b in zip(curve, curve[1:]))
    s = pd.DataFrame({"score": p, "y": y, "cutoff": date(2026, 1, 4), "n_actual": int(y.sum())})
    fitted = fit_from_slots(s, cfg)
    assert fitted["fitted"] and 0 < fitted["tau"] < 1 and len(fitted["curve"]) == len(remind.GRID)


# ── reminders ──────────────────────────────────────────────────────────────────

def test_tiers(cfg):
    tau = 0.7
    assert remind.tier(0.95, "active", 3, tau, cfg) == "locked_in"
    assert remind.tier(0.8, "active", 8, tau, cfg) == "locked_in"            # long streak
    assert remind.tier(0.8, "active", 1, tau, cfg) == "at_risk"
    assert remind.tier(0.8, "fading", 9, tau, cfg) == "at_risk"
    assert remind.tier(0.5, "emerging", 2, tau, cfg) == "emerging"
    assert remind.tier(0.5, "active", 0, tau, cfg) == "none"
    assert remind.tier(0.99, "ended", 0, tau, cfg) == "none"


def test_reminders_skip_booked_slots_and_respect_cooldown(cfg):
    """Two at-risk routines of one user on consecutive days: one reminder (48 h cooldown).
    A second user already booked their next slot: no reminder."""
    cutoff = MON + timedelta(weeks=8) - timedelta(days=1)
    last = MON + timedelta(weeks=7)                      # each missed its latest slot: no streak, not locked in
    rows = weekly("U_A", "AUB", [0], "08:00", MON, 8, skip={last}) + \
        weekly("U_A", "BDD", [1], "17:00", MON, 8, skip={last + timedelta(days=1)}) + \
        weekly("U_B", "AUB", [0, 2], "09:00", MON, 8, skip={last + timedelta(days=2)}) + \
        [("U_B", "AUB", f"{cutoff + timedelta(days=1)} 09:00", "booked")]
    df = frame(rows)
    an = analyse(df, cutoff, cfg, NO_CALENDAR)
    an["calibration"] = {"tau": 0.3}
    an["slots"] = an["slots"].assign(p=0.6)              # every slot at-risk (τ ≤ p < 0.9)
    out = pattern_rows(an, cfg, "t", bookings=df)
    a = rows_for(out, "U_A")
    assert [r["reminder"]["tier"] for r in a] == ["at_risk", "at_risk"]
    assert sum(r["reminder"]["recommend"] for r in a) == 1
    sent = next(r for r in a if r["reminder"]["recommend"])
    assert datetime.fromisoformat(sent["reminder"]["at"]) < datetime.fromisoformat(sent["next_booking"]["at"])
    assert "within 48 h" in next(r for r in a if not r["reminder"]["recommend"])["reason"]
    (b,) = rows_for(out, "U_B")
    assert b["reminder"]["tier"] == "at_risk" and not b["reminder"]["recommend"] and "already booked" in b["reason"]
    for r in out:
        jsonschema.validate(r, schema("pattern_row"))


def test_reminder_timing_uses_created_at_lead(cfg):
    at = pd.Timestamp("2026-05-04 08:00", tz=TZ).to_pydatetime()
    now = pd.Timestamp("2026-05-03 12:00", tz=TZ).to_pydatetime()
    assert remind.when(at, 600, now, cfg) == at - timedelta(minutes=630)
    assert remind.when(at, None, now, cfg).hour == cfg["reminder"]["evening_before_hour"]   # morning fallback
    assert remind.when(at, 10_000, now, cfg) == now                                           # never before now
