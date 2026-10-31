"""Rhythm engine stages on hand-built users (no generator): day sets, coincidences, repairs."""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta

import jsonschema
import numpy as np
import pandas as pd
import pytest

from rhythm import baselines
from rhythm.io import load_config
from rhythm.pipeline import analyse, pattern_rows, priors_and_forecast
from rhythm.stages import activity, clean
from rhythm.stages.common import circ_stats
from rhythm.stages.discover import dbscan_circular
from tests.conftest import schema

TZ = "Asia/Beirut"
MON = date(2026, 3, 2)   # a Monday well before any holiday in config/holidays.yaml
NO_CALENDAR = {"public_holidays": [], "term_breaks": []}


def frame(rows: list[tuple]) -> pd.DataFrame:
    """rows: (user_id, location_id, 'YYYY-MM-DD HH:MM', status) → a frame shaped like `load_bookings`."""
    df = pd.DataFrame(rows, columns=["user_id", "location_id", "start", "status"])
    start = pd.to_datetime(df.pop("start")).dt.tz_localize(TZ)
    df.insert(0, "booking_id", [f"T{i:05d}" for i in range(len(df))])
    df["start_time"], df["end_time"] = start, start + pd.Timedelta(hours=2)
    return df.sort_values("start_time").reset_index(drop=True)


def weekly(user: str, loc: str, weekdays: list[int], hhmm: str, first_monday: date, weeks: int,
           skip: set[date] = frozenset(), status: str = "completed") -> list[tuple]:
    return [(user, loc, f"{d} {hhmm}", status)
            for w in range(weeks) for wd in weekdays
            if (d := first_monday + timedelta(weeks=w, days=wd)) not in skip]


@pytest.fixture(scope="module")
def cfg() -> dict:
    return load_config()


def run_engine(df: pd.DataFrame, cutoff: date, cfg: dict, calendar=NO_CALENDAR):
    an = analyse(df, cutoff, cfg, calendar)
    return an, pattern_rows(an, cfg, "test")


def rows_for(rows: list[dict], user: str) -> list[dict]:
    return [r for r in rows if r["user_id"] == user]


# ── the two headline cases ─────────────────────────────────────────────────────

def test_mwf_user_that_b2_gets_wrong(cfg):
    """MWF student: 12 weeks, a 4-week break, then 4 weeks with one Wednesday missed.
    B2's 8-week window counts the break as misses and drops Wednesday; the engine skips the
    inactive gap and keeps the full Mon / Wed / Fri."""
    back = MON + timedelta(weeks=16)
    rows = weekly("U_MWF", "AUB", [0, 2, 4], "08:00", MON, 12) + \
        weekly("U_MWF", "AUB", [0, 2, 4], "08:05", back, 4, skip={back + timedelta(weeks=1, days=2)})
    df = frame(rows)
    cutoff = back + timedelta(weeks=4) - timedelta(days=1)       # the Sunday after the last week

    b2 = baselines.user_patterns("U_MWF", df, cutoff, cfg, "b2")
    assert b2["days"] == ["Mon", "Fri"]

    an, rows = run_engine(df, cutoff, cfg)
    (row,) = rows_for(rows, "U_MWF")
    assert row["pattern_type"] == "recurring" and row["days"] == ["Mon", "Wed", "Fri"]
    assert row["label"] == "Mon / Wed / Fri" and row["location_id"] == "AUB"
    assert abs(int(row["time_window"]["center"][3:]) - 3) <= 5 and row["time_window"]["center"][:2] == "08"
    assert row["next_booking"]["at"].startswith((cutoff + timedelta(days=1)).isoformat())
    u = an["users"].set_index("user_id").loc["U_MWF"]
    assert u.exposed_days < (cutoff - MON).days + 1 - 21          # the break is not exposure


def test_coincidence_user_is_occasional(cfg):
    """Random bookings on Tue–Sun at random times, plus four Mondays at 09:00 in the last 8 weeks.
    B2 sees 4/8 Mondays and calls it a routine; the engine's chance test and posterior don't."""
    rng = np.random.default_rng(7)
    rows = []
    for w in range(20):
        d = MON + timedelta(weeks=w, days=int(rng.integers(1, 7)))
        m = int(rng.integers(7 * 60, 20 * 60))
        rows.append(("U_COIN", "HAMRA", f"{d} {m // 60:02d}:{m % 60:02d}", "completed"))
    for w in (12, 14, 16, 18):
        rows.append(("U_COIN", "HAMRA", f"{MON + timedelta(weeks=w)} 09:00", "completed"))
    df = frame(rows)
    cutoff = MON + timedelta(weeks=20) - timedelta(days=1)

    b2 = baselines.user_patterns("U_COIN", df, cutoff, cfg, "b2")
    assert b2["pattern_type"] == "recurring" and b2["days"] == ["Mon"]

    an, rows = run_engine(df, cutoff, cfg)
    (row,) = rows_for(rows, "U_COIN")
    assert row["pattern_type"] == "occasional" and row["days"] == [] and row["is_primary"]
    mon = an["day_stats"].query("user_id == 'U_COIN' and weekday == 0")
    assert not mon["is_habit"].any()


# ── more routine shapes ────────────────────────────────────────────────────────

def test_multi_routine_user_gets_primary_plus_extra_row(cfg):
    rows = weekly("U_MULTI", "BDD", [0, 1, 2, 3, 4], "09:00", MON, 10) + \
        weekly("U_MULTI", "HAMRA", [5], "11:30", MON, 10)
    _, out = run_engine(frame(rows), MON + timedelta(weeks=10) - timedelta(days=1), cfg)
    mine = rows_for(out, "U_MULTI")
    assert [r["pattern_id"] for r in mine] == ["U_MULTI-P1", "U_MULTI-P2"]
    assert [r["is_primary"] for r in mine] == [True, False]
    assert (mine[0]["label"], mine[0]["location_id"]) == ("Weekdays", "BDD")
    assert (mine[1]["label"], mine[1]["location_id"], mine[1]["time_window"]["center"]) == ("Sat", "HAMRA", "11:30")


def test_daily_and_weekend_labels(cfg):
    rows = weekly("U_DAILY", "VERDUN", list(range(7)), "18:00", MON, 8) + \
        weekly("U_WKND", "HAMRA", [5, 6], "13:00", MON, 8)
    _, out = run_engine(frame(rows), MON + timedelta(weeks=8) - timedelta(days=1), cfg)
    assert rows_for(out, "U_DAILY")[0]["label"] == "Daily"      # chance test doesn't eat daily users
    assert rows_for(out, "U_WKND")[0]["label"] == "Weekends"


def test_two_time_clusters_at_one_lot_are_two_routines(cfg):
    rows = weekly("U_TWO", "AUB", [0, 2, 4], "08:00", MON, 8) + weekly("U_TWO", "AUB", [1, 3], "14:00", MON, 8)
    _, out = run_engine(frame(rows), MON + timedelta(weeks=8) - timedelta(days=1), cfg)
    assert {(r["label"], r["time_window"]["center"]) for r in rows_for(out, "U_TWO")} == \
        {("Mon / Wed / Fri", "08:00"), ("Tue / Thu", "14:00")}


def test_new_user_with_two_bookings_is_insufficient_history(cfg):
    rows = [("U_NEW", "LAU", f"{MON} 10:00", "completed"), ("U_NEW", "LAU", f"{MON + timedelta(days=2)} 10:00", "completed")]
    _, out = run_engine(frame(rows), MON + timedelta(days=6), cfg)
    assert rows_for(out, "U_NEW")[0]["pattern_type"] == "insufficient_history"


def test_rows_and_feeds_match_contract(cfg):
    rows = weekly("U_A", "AUB", [0, 2, 4], "08:00", MON, 8) + weekly("U_B", "BDD", [1, 3], "10:00", MON, 8, status="no_show") + \
        [("U_C", "HAMRA", f"{MON + timedelta(days=k * 9)} 12:00", "completed") for k in range(5)]
    an, out = run_engine(frame(rows), MON + timedelta(weeks=8) - timedelta(days=1), cfg)
    v = jsonschema.Draft202012Validator(schema("pattern_row"))
    for r in out:
        v.validate(r)
    priors, forecast = priors_and_forecast(an)
    assert priors and forecast
    for p in priors:
        jsonschema.validate(p, schema("arrival_prior"))
    for f in forecast:
        jsonschema.validate(f, schema("lot_forecast"))
    ids = {r["pattern_id"] for r in out}
    assert {p["pattern_id"] for p in priors} <= ids
    assert {p["date"] for p in priors if p["user_id"] == "U_A"} == \
        {(MON + timedelta(weeks=8, days=d)).isoformat() for d in (0, 2, 4)}


def test_every_stage_can_be_switched_off(cfg):
    off = copy.deepcopy(cfg)
    off["stages"] = {k: False for k in cfg["stages"]}
    rows = weekly("U_A", "AUB", [0, 2, 4], "08:00", MON, 8) + weekly("U_B", "BDD", [1, 3], "10:00", MON, 8)
    _, out = run_engine(frame(rows), MON + timedelta(weeks=8) - timedelta(days=1), off)
    v = jsonschema.Draft202012Validator(schema("pattern_row"))
    for r in out:
        v.validate(r)
    assert {r["label"] for r in out} == {"Mon / Wed / Fri", "Tue / Thu"}


def test_holiday_is_not_a_miss(cfg):
    """A Monday holiday inside the history doesn't count against a Monday routine."""
    hol = MON + timedelta(weeks=3)
    rows = weekly("U_H", "AUB", [0], "08:00", MON, 6, skip={hol})
    df = frame(rows)
    cutoff = MON + timedelta(weeks=6) - timedelta(days=1)
    with_hol, _ = run_engine(df, cutoff, cfg, {"public_holidays": [hol], "term_breaks": []})
    without, _ = run_engine(df, cutoff, cfg)
    p = lambda an: an["day_stats"].query("weekday == 0")["post_mean"].iat[0]
    assert p(with_hol) > p(without)
    assert hol not in set(with_hol["exposure"]["day"].dt.date)


# ── clean / activity / discover units ──────────────────────────────────────────

def test_clean_repairs_messy_rows(cfg):
    rows = [("U", "AUB", f"{MON} 08:00", " Completed "), ("U", "AUB", f"{MON} 08:00", "completed"),  # duplicate
            ("U", "AUB", f"{MON + timedelta(days=2)} 08:00", "No-Show"),
            ("U", "AUB", f"{MON + timedelta(days=4)} 08:00", "CANCELED"),
            ("U", "AUB", f"{MON + timedelta(days=4)} 17:00", "completed"),                            # rebook same day
            ("U", "AUB", f"{MON + timedelta(days=7)} 08:00", "cancelled"),                            # plain cancel
            ("U", "AUB", f"{MON + timedelta(days=9)} 08:00", "weird")]                                # dropped
    df = frame(rows)
    df.loc[2, "end_time"] = pd.NaT
    df.loc[3, "end_time"] = df.loc[3, "start_time"] - pd.Timedelta(hours=1)
    out = clean(df, cfg)
    assert out["status"].tolist() == ["completed", "no_show", "completed", "cancelled"]
    assert (out["end_time"] > out["start_time"]).all()
    assert out["weight"].tolist() == [1.0, 0.6, 1.0, 0.4]
    assert out["cancel_rate"].iat[0] == pytest.approx(2 / 6)      # before merging, unknown status excluded
    assert len(clean(df, cfg, merge_rebook=False)) == 5


def test_activity_drops_long_gaps_and_flags_inactivity(cfg):
    rows = weekly("U", "AUB", [0, 3], "08:00", MON, 4) + weekly("U", "AUB", [0, 3], "08:00", MON + timedelta(weeks=10), 2)
    df = clean(frame(rows), cfg)
    cutoff = MON + timedelta(weeks=20)
    exposure, users = activity(df, cutoff, cfg, NO_CALENDAR)
    u = users.iloc[0]
    assert u.gap_threshold_days == 21 and u.inactive and u.days_since_last > 21
    gap_day = pd.Timestamp(MON + timedelta(weeks=6))
    assert gap_day not in set(exposure["day"])
    assert pd.Timestamp(cutoff) in set(exposure["day"])           # the trailing gap still counts (P2: drift)
    _, users_off = activity(df, cutoff, cfg, NO_CALENDAR, enabled=False)
    assert users_off.iloc[0].exposed_days == (cutoff - MON).days + 1


def test_term_break_only_excluded_for_users_who_sit_it_out(cfg):
    brk = {"public_holidays": [], "term_breaks": [{"start": MON + timedelta(weeks=4), "end": MON + timedelta(weeks=7, days=-1),
                                                   "name": "break"}]}
    student = weekly("S", "AUB", [0, 2, 4], "08:00", MON, 4) + weekly("S", "AUB", [0, 2, 4], "08:00", MON + timedelta(weeks=7), 3)
    worker = weekly("W", "BDD", [0, 1, 2, 3, 4], "09:00", MON, 10)
    _, users = activity(clean(frame(student + worker), cfg), MON + timedelta(weeks=10, days=-1), cfg, brk)
    assert users.set_index("user_id")["skips_breaks"].to_dict() == {"S": True, "W": False}


def test_dbscan_is_circular():
    m = np.array([1430, 1435, 5, 10, 600, 605, 900])
    labels = dbscan_circular(m, np.ones(len(m)), eps=45, min_samples=2)
    assert labels[0] == labels[1] == labels[2] == labels[3] != labels[4] == labels[5]
    assert labels[6] == -1


def test_circular_center_wraps_midnight():
    f = pd.DataFrame({"k": 1, "minute": [1430, 1440 - 20, 10, 20], "w": 1.0})
    out = circ_stats(f, ["k"])
    assert min(out["center"].iat[0], 1440 - out["center"].iat[0]) <= 15
    assert out["spread"].iat[0] < 20
