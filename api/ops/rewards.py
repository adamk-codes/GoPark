"""Driver rewards ledger (ARCHITECTURE_PLAN.md §3.4, config/rewards.yaml).

Every award is a `points_ledger` row with a unique `ref_event_id` per user, so awarding is
idempotent: `sync` can rescan stays and bookings any time and only adds what is missing.
Points come from confirmations (`award_confirmation`, anti-fraud: other evidence must agree),
on-time arrivals and clean weeks (no cancellation / no-show). A daily cap applies.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import yaml

from api import db

CONFIG = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "rewards.yaml").read_text(encoding="utf-8"))
REASONS = {"confirm_arrival": "Confirmed your arrival", "confirm_departure": "Confirmed your departure",
           "on_time_arrival": "Arrived on time", "clean_week": "A week with no cancellation or no-show"}


def _award(conn, user_id: str, reason: str, ref: str, at: datetime, base: int) -> int:
    """Add one ledger row (once per ref), trimmed to what is left of that day's cap; returns the points given."""
    if conn.execute("SELECT 1 FROM points_ledger WHERE user_id = ? AND ref_event_id = ?", (user_id, ref)).fetchone():
        return 0
    local = at.astimezone(db.TZ)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    used = conn.execute("SELECT COALESCE(SUM(delta), 0) FROM points_ledger WHERE user_id = ? AND created_at >= ? "
                        "AND created_at < ?", (user_id, db.utc(day), db.utc(day + timedelta(days=1)))).fetchone()[0]
    points = max(0, min(base, int(CONFIG["daily_cap"]) - used))
    conn.execute("INSERT INTO points_ledger(user_id, delta, reason, ref_event_id, created_at) VALUES (?,?,?,?,?)",
                 (user_id, points, reason, ref, db.utc(at)))
    return points


def _week_start(t: datetime) -> datetime:
    local = t.astimezone(db.TZ)
    return (local - timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def streak_weeks(conn, user_id: str, now: datetime) -> int:
    """Finished weeks in a row (back from last week) with a completed booking and no cancellation / no-show."""
    weeks: dict[datetime, set[str]] = {}
    for r in conn.execute("SELECT start_at, status FROM bookings WHERE user_id = ? AND start_at < ?",
                          (user_id, db.utc(_week_start(now)))):
        weeks.setdefault(_week_start(db.parse(r["start_at"])), set()).add(r["status"])
    streak, week = 0, _week_start(now) - timedelta(days=7)
    while week in weeks and "completed" in weeks[week] and not weeks[week] & {"cancelled", "no_show"}:
        streak, week = streak + 1, week - timedelta(days=7)
    return streak


def sync(conn, user_id: str, now: datetime) -> None:
    """Add any missing on-time and clean-week awards."""
    early = timedelta(minutes=CONFIG["on_time_window_min"]["early"])
    late = timedelta(minutes=CONFIG["on_time_window_min"]["late"])
    for r in conn.execute(
            "SELECT s.in_at, b.id, b.start_at FROM stays s JOIN bookings b ON b.id = s.booking_id WHERE b.user_id = ? "
            "AND s.in_at IS NOT NULL AND s.state NOT IN ('EXPECTED', 'ARRIVING', 'CANCELLED')", (user_id,)).fetchall():
        start, arrived = db.parse(r["start_at"]), db.parse(r["in_at"])
        if start - early <= arrived <= start + late:
            _award(conn, user_id, "on_time_arrival", f"on_time:{r['id']}", arrived, CONFIG["points"]["on_time_arrival"])
    weeks: dict[datetime, set[str]] = {}
    for r in conn.execute("SELECT start_at, status FROM bookings WHERE user_id = ? AND start_at < ?",
                          (user_id, db.utc(_week_start(now)))):
        weeks.setdefault(_week_start(db.parse(r["start_at"])), set()).add(r["status"])
    for week, statuses in sorted(weeks.items()):
        if "completed" in statuses and not statuses & {"cancelled", "no_show"}:
            _award(conn, user_id, "clean_week", f"clean_week:{week.date().isoformat()}",
                   week + timedelta(days=6, hours=23, minutes=59), CONFIG["points"]["clean_week"])
    conn.commit()


def award_confirmation(conn, user_id: str, booking_id: str, answer: str, agreed: bool, at: datetime) -> int:
    """Points for a driver confirmation, only when other evidence agreed (anti-fraud), scaled by the streak."""
    if not agreed or answer not in ("arrived", "left"):
        return 0
    reason = "confirm_arrival" if answer == "arrived" else "confirm_departure"
    m = CONFIG["streak_multiplier"]
    factor = min(float(m["max"]), 1 + float(m["per_week"]) * streak_weeks(conn, user_id, at))
    return _award(conn, user_id, reason, f"{reason}:{booking_id}", at, round(CONFIG["points"][reason] * factor))


def summary(conn, user_id: str, now: datetime) -> dict:
    """`GET /users/{id}/rewards` (CONTRACT.md §7)."""
    sync(conn, user_id, now)
    rows = conn.execute("SELECT * FROM points_ledger WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                        (user_id,)).fetchall()
    points = sum(r["delta"] for r in rows)
    streak = streak_weeks(conn, user_id, now)
    confirmations = sum(r["reason"].startswith("confirm_") for r in rows)
    departures = sum(r["reason"] == "confirm_departure" for r in rows)
    have = {"points": points, "streak_weeks": streak, "confirmations": confirmations}
    perks = [{"id": p["id"], "name": p["name"], "description": p["description"],
              "unlocked": all(have[k] >= v for k, v in p["requires"].items())} for p in CONFIG["perks"]]
    grace = CONFIG["grace"]
    return {"user_id": user_id, "points": points, "streak_weeks": streak,
            "grace_minutes": min(int(grace["cap_min"]), departures * int(grace["per_confirmed_departure_min"])),
            "tier": "verified" if any(p["unlocked"] for p in perks if p["id"] == "verified") else "standard",
            "perks": perks,
            "history": [{"at": db.local(r["created_at"]), "points": r["delta"], "reason": r["reason"],
                         "label": REASONS.get(r["reason"], r["reason"])} for r in rows[:50]]}
