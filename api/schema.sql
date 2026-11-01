-- Ops database (CONTRACT.md §4). Owner: P2.
-- All timestamps are stored as UTC ISO strings ("YYYY-MM-DDTHH:MM:SS+00:00") so they
-- compare correctly as text; the API converts them to Asia/Beirut on the way out.

CREATE TABLE IF NOT EXISTS lots (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('open','underground','gated','valet')),
  capacity INTEGER NOT NULL,
  has_spaces INTEGER NOT NULL DEFAULT 0,
  center_lat REAL, center_lng REAL, radius_m REAL,
  beacon_uuid TEXT, beacon_major INTEGER,
  gate_secret TEXT
);

CREATE TABLE IF NOT EXISTS spaces (
  id TEXT PRIMARY KEY,
  lot_id TEXT NOT NULL REFERENCES lots(id),
  label TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  reliability_score REAL NOT NULL DEFAULT 0.5,
  tier TEXT NOT NULL DEFAULT 'standard',
  vehicle_color TEXT,
  vehicle_body TEXT,
  vehicle_make TEXT,
  vehicle_model TEXT
);

CREATE TABLE IF NOT EXISTS vehicles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(id),
  plate_norm TEXT NOT NULL,
  plate_display TEXT NOT NULL,
  UNIQUE (user_id, plate_norm)
);
CREATE INDEX IF NOT EXISTS idx_vehicles_plate ON vehicles(plate_norm);

CREATE TABLE IF NOT EXISTS bookings (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  lot_id TEXT NOT NULL REFERENCES lots(id),
  space_id TEXT REFERENCES spaces(id),
  plate_norm TEXT,
  plate_display TEXT,
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('booked','completed','cancelled','no_show')),
  created_at TEXT,
  updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_bookings_lot_start ON bookings(lot_id, start_at);
CREATE INDEX IF NOT EXISTS idx_bookings_plate ON bookings(plate_norm);

-- Raw evidence, append-only.
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT NOT NULL UNIQUE,
  device_id TEXT NOT NULL,
  user_id TEXT,
  lot_id TEXT NOT NULL,
  booking_id TEXT,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  -- Booking a camera signal was attributed to on arrival (plate or space match); driver signals use booking_id.
  matched_booking_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_lot_time ON signals(lot_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_signals_booking ON signals(booking_id);

CREATE TABLE IF NOT EXISTS fusion_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  stay_id TEXT NOT NULL REFERENCES stays(id),
  signal_id INTEGER NOT NULL REFERENCES signals(id),
  direction TEXT NOT NULL DEFAULT 'arrival' CHECK (direction IN ('arrival','departure')),
  prior REAL NOT NULL,
  log_lr REAL NOT NULL,
  weighted_log_lr REAL NOT NULL,
  probability REAL NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (stay_id, signal_id, direction)
);

-- Projection of what is physically in the lot.
CREATE TABLE IF NOT EXISTS stays (
  id TEXT PRIMARY KEY,
  booking_id TEXT REFERENCES bookings(id),
  lot_id TEXT NOT NULL REFERENCES lots(id),
  space_id TEXT,
  plate_norm TEXT,
  state TEXT NOT NULL,
  in_at TEXT, in_method TEXT, in_conf REAL,
  out_at TEXT, out_method TEXT, out_conf REAL,
  inferred INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stays_lot_state ON stays(lot_id, state);
CREATE INDEX IF NOT EXISTS idx_stays_booking ON stays(booking_id);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  client_event_id TEXT,
  actor_type TEXT NOT NULL,
  actor_id TEXT,
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  lot_id TEXT,
  before TEXT,
  after TEXT,
  observed_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  device_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_lot ON audit_log(lot_id, id);

-- Remembers the response to every client_event_id so retries get the same answer.
CREATE TABLE IF NOT EXISTS processed_events (
  client_event_id TEXT PRIMARY KEY,
  response TEXT NOT NULL,
  received_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  lot_id TEXT NOT NULL,
  stay_id TEXT,
  booking_id TEXT,
  severity TEXT NOT NULL DEFAULT 'info',
  status TEXT NOT NULL DEFAULT 'open',
  message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT '',
  estimated_fee REAL,
  resolved_by TEXT,
  resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS points_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(id),
  delta INTEGER NOT NULL,
  reason TEXT NOT NULL,
  ref_event_id TEXT,
  created_at TEXT NOT NULL
);
-- One award per event (api/ops/rewards.py): rescanning never double-counts.
CREATE UNIQUE INDEX IF NOT EXISTS idx_points_ref ON points_ledger(user_id, ref_event_id);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

-- Gate presence sensors (gate_sensor signals, CONTRACT.md §6): each event is paired with a plate read
-- (same lot and direction within gate_pairing.window_s), or becomes "car in/out, identity unknown".
CREATE TABLE IF NOT EXISTS gate_events (
  signal_id INTEGER PRIMARY KEY REFERENCES signals(id),
  lot_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('enter','exit')),
  observed_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','paired','unidentified','resolved')),
  plate_signal_id INTEGER,
  booking_id TEXT,
  stay_id TEXT,
  alert_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_gate_events_lot ON gate_events(lot_id, status, observed_at);

-- Change feed for WS /live: one row per stay/alert insert or meaningful update (filled by triggers).
CREATE TABLE IF NOT EXISTS changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity TEXT NOT NULL CHECK (entity IN ('stay','alert')),
  entity_id TEXT NOT NULL,
  lot_id TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS stays_changed_insert AFTER INSERT ON stays BEGIN
  INSERT INTO changes(entity, entity_id, lot_id) VALUES ('stay', NEW.id, NEW.lot_id);
END;
CREATE TRIGGER IF NOT EXISTS stays_changed_update AFTER UPDATE ON stays
WHEN OLD.state IS NOT NEW.state OR OLD.in_at IS NOT NEW.in_at OR OLD.in_conf IS NOT NEW.in_conf
  OR OLD.out_at IS NOT NEW.out_at OR OLD.out_method IS NOT NEW.out_method OR OLD.inferred IS NOT NEW.inferred
BEGIN
  INSERT INTO changes(entity, entity_id, lot_id) VALUES ('stay', NEW.id, NEW.lot_id);
END;
CREATE TRIGGER IF NOT EXISTS alerts_changed_insert AFTER INSERT ON alerts BEGIN
  INSERT INTO changes(entity, entity_id, lot_id) VALUES ('alert', CAST(NEW.id AS TEXT), NEW.lot_id);
END;
CREATE TRIGGER IF NOT EXISTS alerts_changed_update AFTER UPDATE ON alerts
WHEN OLD.status IS NOT NEW.status OR OLD.severity IS NOT NEW.severity OR OLD.message IS NOT NEW.message
  OR OLD.estimated_fee IS NOT NEW.estimated_fee
BEGIN
  INSERT INTO changes(entity, entity_id, lot_id) VALUES ('alert', CAST(NEW.id AS TEXT), NEW.lot_id);
END;
