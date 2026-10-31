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
  tier TEXT NOT NULL DEFAULT 'standard'
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
  payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_signals_lot_time ON signals(lot_id, observed_at);

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

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
