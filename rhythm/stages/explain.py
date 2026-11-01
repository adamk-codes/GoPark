"""Stage 9 — explain (ARCHITECTURE_PLAN.md §4.2 step 9): template reasons with the real numbers.

One template per row type (active / emerging / fading / ended routine, routine switch,
occasional, insufficient history), plus optional inactivity and reminder sentences.
"""
from __future__ import annotations

import pandas as pd

from rhythm import baselines
from rhythm.stages.common import DAY_FULL, DAY_NAMES, hhmm


def day_text(d) -> str:
    d = pd.Timestamp(d)
    return f"{d:%b} {d.day}"


def slots_label(days: tuple[int, ...]) -> str:
    return f"{DAY_FULL[days[0]]}s" if len(days) == 1 else f"{baselines.day_label([DAY_NAMES[d] for d in days])} slots"


def routine_name(r) -> str:
    return f"{baselines.day_label([DAY_NAMES[d] for d in r.days])} at {r.location_id}"


def routine(r, u, partner=None) -> str:
    """`r`: a routine row (habit + drift columns); `u`: the user's activity summary;
    `partner`: the routine it switched with, if any."""
    lo, hi = hhmm(r.center - r.spread), hhmm(r.center + r.spread)
    usual = f"usually {lo}–{hi}"
    what = slots_label(r.days)
    if r.state == "ended":
        text = (f"Ended {day_text(r.change_date)}: {r.post_hits:g} {r.location_id} bookings on its "
                f"{what} since, down from {r.regime_hits} of {r.regime_slots} before ({usual}).")
        if partner is not None:
            text += f" Routine switch: replaced by {routine_name(partner)} from {day_text(partner.change_date)}."
        return text
    if r.state == "emerging":
        text = (f"New since {day_text(r.change_date)}: booked {r.location_id} on {r.regime_hits} of "
                f"{r.regime_slots} {what}, {usual}.")
        if partner is not None:
            text = (f"Routine switch: {routine_name(r)} since {day_text(r.change_date)}, replacing "
                    f"{routine_name(partner)} (ended {day_text(partner.change_date)}). Booked {r.regime_hits} of "
                    f"{r.regime_slots} {what} since, {usual}.")
        return text
    if r.recent_slots:
        text = f"Booked {r.location_id} on {r.recent_hits} of the last {r.recent_slots} {what}, {usual}."
    else:
        text = f"Booked {r.location_id} {r.support} times on {what}, {usual}."
    if r.state == "fading":
        if r.pre_slots:
            text += (f" Fading since {day_text(r.change_date)}: down from {r.pre_hits:g} of {r.pre_slots} "
                     f"before.")
        else:
            text += (f" Fading: {r.recent_rate:.0%} of its slots in the last 4 weeks vs "
                     f"{r.regime_rate:.0%} before.")
    elif partner is not None:
        text = (f"Routine switch: {routine_name(r)} since {day_text(r.change_date)}, replacing "
                f"{routine_name(partner)} (ended {day_text(partner.change_date)}). " + text)
    return text


def inactivity(u, factor: float) -> str:
    return (f" Inactive {u.days_since_last} days (usual gap {u.median_gap_days:g}); "
            f"confidence lowered ×{factor:.2f}.")


def occasional(n: int, weeks: int, gap, guess=None) -> str:
    text = f"No steady routine: {n} bookings over {weeks} active weeks."
    if gap is not None and gap.bookings > 0:
        text += f" Likely to book within 7 days at {gap.gap_lot} (~{gap.p_week:.0%})."
    if guess is not None:
        text += f" Best single guess {guess['at']:%a} ~{guess['at']:%H:%M} at {guess['location_id']} ({guess['p']:.0%})."
    return text


def insufficient(n: int) -> str:
    return f"Only {n} booking{'s' if n != 1 else ''} so far; not enough history for a routine."


def reminder(note: str | None) -> str:
    return f" Reminder: {note}." if note else ""
