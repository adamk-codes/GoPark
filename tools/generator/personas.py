"""Persona definitions for the synthetic booking generator.

Each persona turns a random generator + calendar into a list of planned bookings
for one user, plus the ground-truth routines that produced them. Add new personas
by writing a function with the same signature and registering it in PERSONAS.
v0 ships three personas; the full set is listed in ARCHITECTURE_PLAN.md §5.1.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Beirut")
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class Calendar:
    start: date                     # first day of history (a Monday)
    history_end: date               # last day of history (a Sunday)
    end: date                       # last generated day (history_end + future days)
    holidays: set[date]
    term_breaks: list[tuple[date, date]]

    def days(self):
        d = self.start
        while d <= self.end:
            yield d
            d += timedelta(days=1)

    def in_term_break(self, d: date) -> bool:
        return any(a <= d <= b for a, b in self.term_breaks)


@dataclass
class Planned:
    location_id: str
    start: datetime
    end: datetime
    lead_hours: float               # how long before start the booking was created


@dataclass
class UserPlan:
    persona: str
    routines: list[dict]
    bookings: list[Planned] = field(default_factory=list)
    cancel_rate: float = 0.05
    no_show_rate: float = 0.02


def _at(d: date, minute_of_day: float) -> datetime:
    """Local time on day d, rounded to 5 minutes like slots picked in the app."""
    minute_of_day = max(0, min(23 * 60 + 55, 5 * round(minute_of_day / 5)))
    return datetime.combine(d, time(minute_of_day // 60, minute_of_day % 60), TZ)


def _duration(hours: float) -> timedelta:
    """Booking length rounded to 15 minutes."""
    return timedelta(minutes=max(15, 15 * round(hours * 4)))


def _hhmm(minute_of_day: float) -> str:
    m = round(minute_of_day)
    return f"{m // 60:02d}:{m % 60:02d}"


def _routine_user(rng: random.Random, cal: Calendar, persona: str, weekdays: list[int],
                  locations: list[str], center_mean: float, center_sd: float,
                  attend_p: float, jitter_sd: float, dur_range: tuple[float, float],
                  lead_median_h: float, skips_term_breaks: bool) -> UserPlan:
    location = rng.choice(locations)
    center = rng.gauss(center_mean, center_sd)
    plan = UserPlan(
        persona=persona,
        routines=[{"days": [DAY_NAMES[w] for w in weekdays], "center": _hhmm(center),
                   "location_id": location, "active_from": cal.start.isoformat(), "active_to": None}],
        cancel_rate=rng.uniform(0.02, 0.08),
    )
    for d in cal.days():
        if d.weekday() not in weekdays:
            continue
        if d in cal.holidays and rng.random() < 0.9:
            continue
        if skips_term_breaks and cal.in_term_break(d) and rng.random() < 0.9:
            continue
        if rng.random() > attend_p:
            continue
        start = _at(d, rng.gauss(center, jitter_sd))
        lead = rng.lognormvariate(0, 0.6) * lead_median_h
        plan.bookings.append(Planned(location, start, start + _duration(rng.uniform(*dur_range)), lead))
    return plan


def weekday_commuter(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    pool = [lid for lid, lot in lots.items() if lot["kind"] in ("underground", "open") and lid != "AUB"]
    return _routine_user(rng, cal, "weekday_commuter", [0, 1, 2, 3, 4], pool,
                         center_mean=9 * 60, center_sd=30, attend_p=0.85, jitter_sd=10,
                         dur_range=(8, 9.5), lead_median_h=12, skips_term_breaks=False)


def mwf_student(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    pool = [lid for lid in ("AUB", "LAU") if lid in lots]
    return _routine_user(rng, cal, "mwf_student", [0, 2, 4], pool,
                         center_mean=8 * 60, center_sd=20, attend_p=0.85, jitter_sd=12,
                         dur_range=(3, 6), lead_median_h=10, skips_term_breaks=True)


def occasional(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    plan = UserPlan(persona="occasional", routines=[], cancel_rate=rng.uniform(0.05, 0.15))
    weekly_rate = rng.uniform(0.15, 0.6)
    locations = list(lots)
    for d in cal.days():
        if rng.random() < weekly_rate / 7:
            start = _at(d, rng.uniform(9 * 60, 20 * 60))
            plan.bookings.append(Planned(rng.choice(locations), start,
                                         start + _duration(rng.uniform(1, 3)),
                                         rng.lognormvariate(0, 0.8) * 2))
    return plan


# name -> (builder, default share of users)
PERSONAS = {
    "weekday_commuter": (weekday_commuter, 0.35),
    "mwf_student": (mwf_student, 0.35),
    "occasional": (occasional, 0.30),
}
