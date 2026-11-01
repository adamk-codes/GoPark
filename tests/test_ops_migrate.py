"""An API started on a DB from an older schema adds the missing columns before creating triggers on them."""
from __future__ import annotations

import sqlite3


def test_old_db_is_migrated_before_schema_triggers(tmp_path):
    from api import db
    conn = sqlite3.connect(tmp_path / "old.sqlite")
    conn.row_factory = sqlite3.Row
    # Phase-1 era tables: alerts without estimated_fee/updated_at, bookings without updated_at, signals unmatched.
    conn.executescript("""
        CREATE TABLE bookings (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, lot_id TEXT NOT NULL, space_id TEXT,
          plate_norm TEXT, plate_display TEXT, start_at TEXT NOT NULL, end_at TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT);
        INSERT INTO bookings VALUES ('B1','U1','AUB',NULL,'B123456','B 123456','2026-11-16T06:00:00+00:00',
          '2026-11-16T08:00:00+00:00','booked',NULL);
        CREATE TABLE alerts (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, lot_id TEXT NOT NULL,
          stay_id TEXT, booking_id TEXT, severity TEXT NOT NULL DEFAULT 'info', status TEXT NOT NULL DEFAULT 'open',
          message TEXT, created_at TEXT NOT NULL);
        INSERT INTO alerts(type, lot_id, message, created_at) VALUES ('overstay','AUB','old','2026-11-16T06:00:00+00:00');
        CREATE TABLE signals (id INTEGER PRIMARY KEY AUTOINCREMENT, client_event_id TEXT NOT NULL UNIQUE,
          device_id TEXT NOT NULL, user_id TEXT, lot_id TEXT NOT NULL, booking_id TEXT, source TEXT NOT NULL,
          kind TEXT NOT NULL, observed_at TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}');
    """)
    db.ensure_seeded(conn)
    assert {"estimated_fee", "updated_at", "resolved_at"} <= {r["name"] for r in conn.execute("PRAGMA table_info(alerts)")}
    assert "matched_booking_id" in {r["name"] for r in conn.execute("PRAGMA table_info(signals)")}
    conn.execute("UPDATE alerts SET estimated_fee = 2.0, status = 'resolved'")   # fires alerts_changed_update
    assert conn.execute("SELECT COUNT(*) FROM changes WHERE entity = 'alert'").fetchone()[0] == 1
    conn.close()
