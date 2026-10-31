"""SQLite access, demo clock and seeding from the generator's files."""
from __future__ import annotations

import csv
import json
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("GOPARK_DB", ROOT / "data" / "gopark.sqlite"))
DATA_DIR = Path(os.environ.get("GOPARK_DATA", ROOT / "data"))
TZ = ZoneInfo("Asia/Beirut")
# The synthetic data's "live" week starts Monday 2026-11-16. The demo clock starts
# here and keeps ticking in real time; POST /dev/clock moves it (e.g. fast-forward).
DEFAULT_DEMO_NOW = os.environ.get("GOPARK_DEMO_NOW", "2026-11-16T07:50:00+02:00")

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


# ---------- time helpers ----------
def utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def local(ts: str | None) -> str | None:
    return parse(ts).astimezone(TZ).isoformat(timespec="seconds") if ts else None


def normalize_plate(plate: str | None) -> str | None:
    if not plate:
        return None
    return re.sub(r"[\s\-]", "", plate.translate(ARABIC_DIGITS)).upper() or None


# ---------- connection ----------
def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_meta(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


# ---------- demo clock ----------
def now(conn) -> datetime:
    anchor = get_meta(conn, "clock_demo_anchor", DEFAULT_DEMO_NOW)
    real = get_meta(conn, "clock_real_anchor")
    demo = parse(anchor)
    if real:
        demo += datetime.now(timezone.utc) - parse(real)
    return demo.astimezone(TZ)


def set_clock(conn, demo_now: datetime) -> None:
    set_meta(conn, "clock_demo_anchor", demo_now.isoformat())
    set_meta(conn, "clock_real_anchor", datetime.now(timezone.utc).isoformat())
    conn.commit()


# ---------- seeding ----------
def reset(conn, data_dir: Path = DATA_DIR) -> dict:
    """Drop everything and reload lots + bookings from config/ and data/."""
    conn.executescript(";".join(f"DROP TABLE IF EXISTS {t}" for t in (
        "points_ledger", "alerts", "processed_events", "audit_log", "stays", "signals",
        "bookings", "vehicles", "users", "spaces", "lots", "meta")))
    conn.executescript((ROOT / "api" / "schema.sql").read_text(encoding="utf-8"))

    lots_cfg = yaml.safe_load((ROOT / "config" / "lots.yaml").read_text(encoding="utf-8"))["lots"]
    rng = random.Random(7)
    for lot in lots_cfg:
        beacon = lot.get("beacon") or {}
        conn.execute(
            "INSERT INTO lots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (lot["id"], lot["name"], lot["kind"], lot["capacity"], int(lot.get("has_spaces", False)),
             lot["center"]["lat"], lot["center"]["lng"], lot["radius_m"],
             beacon.get("uuid"), beacon.get("major"), f"{rng.getrandbits(128):032x}"))
        if lot.get("has_spaces"):
            conn.executemany("INSERT INTO spaces VALUES (?,?,?)",
                             [(f"{lot['id']}-{n:02d}", lot["id"], f"{n:02d}") for n in range(1, lot["capacity"] + 1)])

    spaces_by_lot: dict[str, list[str]] = {}
    for row in conn.execute("SELECT id, lot_id FROM spaces ORDER BY id"):
        spaces_by_lot.setdefault(row["lot_id"], []).append(row["id"])
    space_free_at: dict[str, str] = {}

    users, vehicles, bookings = set(), set(), []
    with open(data_dir / "bookings.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            plate_norm = normalize_plate(r.get("plate"))
            users.add(r["user_id"])
            if plate_norm:
                vehicles.add((r["user_id"], plate_norm, r["plate"]))
            start, end = utc(parse(r["start_time"])), utc(parse(r["end_time"]))
            space = None
            if r["status"] == "booked" and r["location_id"] in spaces_by_lot:
                # Greedy space assignment for upcoming bookings at spot-level lots.
                for sid in spaces_by_lot[r["location_id"]]:
                    if space_free_at.get(sid, "") <= start:
                        space, space_free_at[sid] = sid, end
                        break
            bookings.append((r["booking_id"], r["user_id"], r["location_id"], space, plate_norm, r.get("plate"),
                             start, end, r["status"], utc(parse(r["created_at"])) if r.get("created_at") else None))

    conn.executemany("INSERT INTO users(id) VALUES (?)", [(u,) for u in sorted(users)])
    conn.executemany("INSERT INTO vehicles(user_id, plate_norm, plate_display) VALUES (?,?,?)", sorted(vehicles))
    conn.executemany("INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?,?,?)", bookings)

    # Cars that should already be parked when the demo clock starts.
    t0 = parse(DEFAULT_DEMO_NOW)
    set_clock(conn, t0)
    rows = conn.execute("SELECT * FROM bookings WHERE status = 'booked' AND start_at <= ? AND end_at > ?",
                        (utc(t0), utc(t0))).fetchall()
    for b in rows:
        method = rng.choice(["auto", "auto", "driver", "attendant"])
        in_at = parse(b["start_at"]) + timedelta(minutes=rng.randint(-8, 6))
        conn.execute("INSERT INTO stays VALUES (?,?,?,?,?, 'CHECKED_IN', ?,?,?, NULL,NULL,NULL, 0, ?)",
                     (f"S-{b['id']}", b["id"], b["lot_id"], b["space_id"], b["plate_norm"],
                      utc(min(in_at, t0)), method, 1.0 if method != "auto" else 0.97, utc(t0)))
    conn.commit()
    return {"lots": len(lots_cfg), "users": len(users), "bookings": len(bookings), "parked_at_start": len(rows),
            "demo_now": now(conn).isoformat()}


def ensure_seeded(conn) -> None:
    has = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='bookings'").fetchone()
    if not has or not conn.execute("SELECT 1 FROM bookings LIMIT 1").fetchone():
        if not (DATA_DIR / "bookings.csv").exists():
            raise RuntimeError("data/bookings.csv missing; run `uv run python -m tools.generator` first")
        reset(conn)


def row_json(row) -> dict:
    return dict(row) if row is not None else None


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
