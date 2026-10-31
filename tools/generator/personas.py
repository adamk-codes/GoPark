"""Persona definitions for the synthetic booking generator.

Each persona turns a random generator + calendar into a list of planned bookings
for one user, plus the ground-truth routines that produced them. Add new personas
by writing a function with the same signature and registering it in PERSONAS.
The full set is listed in ARCHITECTURE_PLAN.md §5.1. Two more live in the
gitignored hidden_personas.py and are only used with --include-hidden.

Truth routines always carry days, center, location_id, active_from and active_to
(CONTRACT.md §3). Some add optional keys: jitter_min, attendance (fader).
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Beirut")
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
OFFICE_LOTS = ("BDD", "ACHRAFIEH", "VERDUN")
DAY_SETS = [[0, 1, 2, 3, 4], [0, 2, 4], [1, 3], [0, 1, 2, 3], [1, 2, 3]]


@dataclass
class Calendar:
    start: date                     # first day of history (a Monday)
    history_end: date               # last day of history (a Sunday)
    end: date                       # last generated day (history_end + future days)
    holidays: set[date]
    term_breaks: list[tuple[date, date]]

    def days(self, start: date | None = None, end: date | None = None):
        d = max(start or self.start, self.start)
        last = min(end or self.end, self.end)
        while d <= last:
            yield d
            d += timedelta(days=1)

    def in_term_break(self, d: date) -> bool:
        return any(a <= d <= b for a, b in self.term_breaks)

    def at_fraction(self, f: float) -> date:
        """The day f of the way through the history (0 = start, 1 = history_end)."""
        return self.start + timedelta(days=round((self.history_end - self.start).days * f))


@dataclass
class Planned:
    location_id: str
    start: datetime
    end: datetime
    lead_hours: float               # how long before start the booking was created
    status: str | None = None       # forced history status (e.g. the cancelled half of a rebook)


@dataclass
class UserPlan:
    persona: str
    routines: list[dict]
    bookings: list[Planned] = field(default_factory=list)
    cancel_rate: float = 0.05
    no_show_rate: float = 0.02
    extra: dict = field(default_factory=dict)   # extra truth keys for this user


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


def _pool(lots: dict, ids=None, kinds=None, exclude=()) -> list[str]:
    out = [lid for lid, lot in lots.items()
           if (ids is None or lid in ids) and (kinds is None or lot["kind"] in kinds) and lid not in exclude]
    return out or list(lots)


def _add_routine(rng: random.Random, cal: Calendar, plan: UserPlan, *, weekdays: list[int], location: str,
                 center: float, attend: float | Callable[[date], float] = 0.85, jitter_sd: float = 10,
                 uniform_jitter: float | None = None, dur_range: tuple[float, float] = (8, 9.5),
                 lead_median_h: float = 12, skips_term_breaks: bool = False,
                 active_from: date | None = None, active_to: date | None = None,
                 last_booking: date | None = None) -> dict:
    """Append one weekly routine's bookings to plan and return its truth record.

    active_to is the truth end date; last_booking (default active_to) is when the
    user really stops booking (a fader can keep a few stray bookings after).
    """
    active_from = active_from or cal.start
    stop = last_booking or active_to
    for d in cal.days(active_from, stop):
        if d.weekday() not in weekdays:
            continue
        if d in cal.holidays and rng.random() < 0.9:
            continue
        if skips_term_breaks and cal.in_term_break(d) and rng.random() < 0.9:
            continue
        p = attend(d) if callable(attend) else attend
        if rng.random() > p:
            continue
        offset = rng.uniform(-uniform_jitter, uniform_jitter) if uniform_jitter else rng.gauss(0, jitter_sd)
        start = _at(d, center + offset)
        lead = rng.lognormvariate(0, 0.6) * lead_median_h
        plan.bookings.append(Planned(location, start, start + _duration(rng.uniform(*dur_range)), lead))
    routine = {"days": [DAY_NAMES[w] for w in weekdays], "center": _hhmm(center), "location_id": location,
               "active_from": active_from.isoformat(), "active_to": active_to.isoformat() if active_to else None,
               "jitter_min": round(uniform_jitter if uniform_jitter else jitter_sd)}
    plan.routines.append(routine)
    return routine


def _random_bookings(rng: random.Random, cal: Calendar, plan: UserPlan, lots: dict, weekly_rate: float,
                     hours: tuple[float, float] = (9, 20)) -> None:
    locations = list(lots)
    for d in cal.days():
        if rng.random() < weekly_rate / 7:
            start = _at(d, rng.uniform(hours[0] * 60, hours[1] * 60))
            plan.bookings.append(Planned(rng.choice(locations), start, start + _duration(rng.uniform(1, 3)),
                                         rng.lognormvariate(0, 0.8) * 2))


# ---------------------------------------------------------------- v0 personas

def weekday_commuter(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    plan = UserPlan("weekday_commuter", [], cancel_rate=rng.uniform(0.02, 0.08))
    _add_routine(rng, cal, plan, weekdays=[0, 1, 2, 3, 4],
                 location=rng.choice(_pool(lots, kinds=("underground", "open"), exclude=("AUB",))),
                 center=rng.gauss(9 * 60, 30), jitter_sd=10, dur_range=(8, 9.5), lead_median_h=12)
    return plan


def mwf_student(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    plan = UserPlan("mwf_student", [], cancel_rate=rng.uniform(0.02, 0.08))
    _add_routine(rng, cal, plan, weekdays=[0, 2, 4], location=rng.choice(_pool(lots, ids=("AUB", "LAU"))),
                 center=rng.gauss(8 * 60, 20), jitter_sd=12, dur_range=(3, 6), lead_median_h=10,
                 skips_term_breaks=True)
    return plan


def tue_thu(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    plan = UserPlan("tue_thu", [], cancel_rate=rng.uniform(0.02, 0.08))
    _add_routine(rng, cal, plan, weekdays=[1, 3], location=rng.choice(_pool(lots, ids=("LAU",))),
                 center=rng.gauss(10 * 60, 20), jitter_sd=12, dur_range=(2, 4), lead_median_h=18,
                 skips_term_breaks=True)
    return plan


def occasional(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    plan = UserPlan("occasional", [], cancel_rate=rng.uniform(0.05, 0.15))
    _random_bookings(rng, cal, plan, lots, weekly_rate=rng.uniform(0.15, 0.6))
    return plan


# ------------------------------------------------------------- P1 personas

def routine_switcher(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    """Changes days, lot or both on a switch date; truth has two routines."""
    plan = UserPlan("routine_switcher", [], cancel_rate=rng.uniform(0.02, 0.08))
    switch = cal.at_fraction(rng.uniform(0.4, 0.75))
    switch = min(switch, cal.history_end - timedelta(weeks=4))
    days_a = rng.choice(DAY_SETS)
    lot_a = rng.choice(_pool(lots, exclude=("HAMRA",)))
    center_a = rng.gauss(8.5 * 60, 40)
    change = rng.choice(["days", "lot", "both"])
    days_b = rng.choice([s for s in DAY_SETS if s != days_a]) if change in ("days", "both") else days_a
    lot_b = rng.choice(_pool(lots, exclude=("HAMRA", lot_a))) if change in ("lot", "both") else lot_a
    center_b = center_a + rng.choice([-1, 1]) * rng.uniform(30, 90) if rng.random() < 0.5 else center_a
    dur = (3, 8)
    _add_routine(rng, cal, plan, weekdays=days_a, location=lot_a, center=center_a, dur_range=dur,
                 active_to=switch - timedelta(days=1))
    _add_routine(rng, cal, plan, weekdays=days_b, location=lot_b, center=center_b, dur_range=dur,
                 active_from=switch)
    plan.extra["switch"] = {"date": switch.isoformat(), "changed": change}
    return plan


def fader(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    """Attendance decays week by week after a fade start; truth ends when it drops below 0.25."""
    plan = UserPlan("fader", [], cancel_rate=rng.uniform(0.03, 0.1))
    fade_from = cal.at_fraction(rng.uniform(0.25, 0.55))
    p0, half_life_w = rng.uniform(0.8, 0.92), rng.uniform(2.5, 5)

    def attend(d: date) -> float:
        weeks = max(0.0, (d - fade_from).days / 7)
        return p0 * 0.5 ** (weeks / half_life_w)

    weeks_to_quarter = half_life_w * math.log2(p0 / 0.25)
    active_to = fade_from + timedelta(weeks=weeks_to_quarter)
    r = _add_routine(rng, cal, plan, weekdays=rng.choice(DAY_SETS), location=rng.choice(_pool(lots)),
                     center=rng.gauss(9 * 60, 45), attend=attend, dur_range=(2, 8),
                     active_to=active_to if active_to <= cal.history_end else None, last_booking=cal.end)
    r["attendance"] = {"p_start": round(p0, 2), "fade_from": fade_from.isoformat(), "half_life_weeks": round(half_life_w, 1)}
    return plan


def noisy_timer(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    """Real routine, but start times wander ±40–60 min."""
    plan = UserPlan("noisy_timer", [], cancel_rate=rng.uniform(0.02, 0.08))
    _add_routine(rng, cal, plan, weekdays=rng.choice(DAY_SETS), location=rng.choice(_pool(lots, exclude=("HAMRA",))),
                 center=rng.gauss(10 * 60, 60), uniform_jitter=rng.uniform(40, 60), dur_range=(2, 7))
    return plan


def heavy_canceller(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    """Real routine with 30–50% cancels; some cancels are rebooked a little later the same day."""
    plan = UserPlan("heavy_canceller", [], cancel_rate=0.0)
    _add_routine(rng, cal, plan, weekdays=rng.choice(DAY_SETS), location=rng.choice(_pool(lots, exclude=("HAMRA",))),
                 center=rng.gauss(9 * 60, 40), dur_range=(3, 8), attend=0.9)
    cancel_p, rebook_p = rng.uniform(0.3, 0.5), rng.uniform(0.2, 0.5)
    out = []
    for b in plan.bookings:
        if b.start.date() > cal.history_end or rng.random() >= cancel_p:
            out.append(b)
            continue
        out.append(Planned(b.location_id, b.start, b.end, b.lead_hours, status="cancelled"))
        if rng.random() < rebook_p:
            shift = timedelta(minutes=rng.choice([15, 30, 45, 60, 90]))
            lead = max(0.25, b.lead_hours * rng.uniform(0.05, 0.5))   # rebooked closer to the time
            out.append(Planned(b.location_id, b.start + shift, b.end + shift, lead))
    plan.bookings = out
    plan.extra["cancel_rate"] = round(cancel_p, 2)
    return plan


def multi_routine(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    """Weekdays at an office lot plus Saturday late mornings in Hamra."""
    plan = UserPlan("multi_routine", [], cancel_rate=rng.uniform(0.02, 0.08))
    _add_routine(rng, cal, plan, weekdays=rng.choice([[0, 1, 2, 3, 4], [0, 1, 2, 3]]),
                 location=rng.choice(_pool(lots, ids=OFFICE_LOTS)), center=rng.gauss(8.75 * 60, 25),
                 dur_range=(8, 9.5))
    _add_routine(rng, cal, plan, weekdays=[5], location=rng.choice(_pool(lots, ids=("HAMRA",))),
                 center=rng.gauss(11.5 * 60, 30), jitter_sd=15, attend=0.7, dur_range=(2, 4), lead_median_h=20)
    return plan


def coincidence_trap(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    """Random booker who happens to land on the same weekday/time/lot 3–4 times. No routine."""
    plan = UserPlan("coincidence_trap", [], cancel_rate=rng.uniform(0.05, 0.12))
    _random_bookings(rng, cal, plan, lots, weekly_rate=rng.uniform(0.1, 0.25))
    day, lot = rng.randrange(5), rng.choice(list(lots))
    minute = rng.uniform(8 * 60, 17 * 60)
    weeks_total = (cal.history_end - cal.start).days // 7 + 1
    # Non-consecutive weeks, so they look like chance rather than a run (fewer if the history is short).
    count = min(rng.choice([3, 4]), (weeks_total + 1) // 2)
    weeks = sorted(rng.sample(range(weeks_total - count + 1), count))
    weeks = [w + i for i, w in enumerate(weeks)]    # spread the sample out: gaps of at least one week
    for w in sorted(weeks):
        d = cal.start + timedelta(weeks=w, days=day)
        start = _at(d, minute + rng.gauss(0, 10))
        plan.bookings.append(Planned(lot, start, start + _duration(rng.uniform(1, 3)), rng.lognormvariate(0, 0.8) * 4))
    plan.extra["coincidence"] = {"day": DAY_NAMES[day], "location_id": lot, "count": count}
    return plan


def new_user(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    """Signs up 1–3 weeks before history end, already on a routine."""
    plan = UserPlan("new_user", [], cancel_rate=rng.uniform(0.02, 0.08))
    joined = cal.history_end - timedelta(days=rng.randint(7, 21) - 1)
    _add_routine(rng, cal, plan, weekdays=rng.choice(DAY_SETS), location=rng.choice(_pool(lots, exclude=("HAMRA",))),
                 center=rng.gauss(9 * 60, 40), dur_range=(3, 8), active_from=joined, attend=0.9)
    plan.extra["joined"] = joined.isoformat()
    return plan


def lapsed(rng: random.Random, cal: Calendar, lots: dict) -> UserPlan:
    """Strong routine, then stops booking for good."""
    plan = UserPlan("lapsed", [], cancel_rate=rng.uniform(0.02, 0.06))
    last = cal.at_fraction(rng.uniform(0.4, 0.75))
    _add_routine(rng, cal, plan, weekdays=rng.choice(DAY_SETS), location=rng.choice(_pool(lots, exclude=("HAMRA",))),
                 center=rng.gauss(8.5 * 60, 30), attend=0.92, dur_range=(3, 9), active_to=last)
    return plan


# name -> (builder, default share of users). Shares are weights; they need not sum to 1.
PERSONAS = {
    "weekday_commuter": (weekday_commuter, 0.14),
    "mwf_student": (mwf_student, 0.12),
    "tue_thu": (tue_thu, 0.05),
    "occasional": (occasional, 0.13),
    "routine_switcher": (routine_switcher, 0.08),
    "fader": (fader, 0.07),
    "noisy_timer": (noisy_timer, 0.07),
    "heavy_canceller": (heavy_canceller, 0.07),
    "multi_routine": (multi_routine, 0.07),
    "coincidence_trap": (coincidence_trap, 0.07),
    "new_user": (new_user, 0.06),
    "lapsed": (lapsed, 0.07),
}
