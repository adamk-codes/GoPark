// Mirrors contract/schemas/*.schema.json and CONTRACT.md. Apps copy this file
// with `npm run contract`; edit only here, through a contract PR.

export type ISOTime = string; // ISO 8601 with offset, e.g. 2026-11-16T08:00:00+02:00
export type ISODate = string; // YYYY-MM-DD

export type BookingStatus = "booked" | "completed" | "cancelled" | "no_show";
export type LotKind = "open" | "underground" | "gated" | "valet";
export type StayState =
  | "EXPECTED" | "ARRIVING" | "CHECKED_IN" | "OVERSTAY" | "CHECKED_OUT"
  | "UNCONFIRMED_DEPARTURE" | "NO_SHOW" | "CANCELLED";
export type Method = "auto" | "driver" | "attendant" | "inferred";
export type SignalSource =
  | "geofence" | "motion" | "ble" | "nfc" | "gate_qr" | "booking_qr"
  | "camera_plate" | "camera_spot" | "camera_vehicle" | "gate_sensor" | "car_bt" | "attendant" | "driver_confirm";
export type SignalKind =
  | "enter" | "exit" | "park" | "unpark" | "scan" | "occupied" | "vacant" | "confirm" | "deny";
export type PatternState = "emerging" | "active" | "fading" | "ended";
export type ReminderTier = "locked_in" | "at_risk" | "emerging" | "none";

export interface Lot {
  id: string;
  name: string;
  kind: LotKind;
  capacity: number;
  has_spaces: boolean;
  center_lat: number;
  center_lng: number;
  radius_m: number;
}

export interface Booking {
  booking_id: string;
  user_id: string;
  location_id: string;
  start_time: ISOTime;
  end_time: ISOTime;
  status: BookingStatus;
  created_at?: ISOTime | null;
  plate?: string | null;
  vehicle_color?: VehicleColor | null;
  vehicle_body?: VehicleBody | null;
  vehicle_make?: VehicleMake | null;
  vehicle_model?: string | null;
}

export interface Stay {
  id: string;
  booking_id: string | null;
  lot_id: string;
  plate_norm: string | null;
  state: StayState;
  in_at: ISOTime | null;
  in_method: Method | null;
  in_conf: number | null;
  out_at: ISOTime | null;
  out_method: Method | null;
  out_conf: number | null;
  inferred: boolean;
}

export interface Signal {
  client_event_id: string;
  device_id: string;
  user_id?: string | null;
  lot_id: string;
  booking_id?: string | null;
  source: SignalSource;
  kind: SignalKind;
  observed_at: ISOTime;
  payload?: Record<string, unknown>;
}

export type VehicleColor = "white" | "silver" | "black" | "grey" | "blue" | "red" | "green" | "beige";
export type VehicleBody = "sedan" | "hatchback" | "suv" | "pickup" | "van";
export type VehicleMake = "Kia" | "Toyota" | "Hyundai" | "Nissan" | "Mercedes" | "BMW";

export interface VehicleProfile {
  color: VehicleColor;
  body: VehicleBody;
  make: VehicleMake;
  model?: string | null;
}

export interface ExpectedVehicle {
  booking_id: string;
  user_id: string;
  vehicle: VehicleProfile;
  p: number;
}

export interface CameraVehicleCandidate {
  booking_id: string;
  score: number;
  vehicle: VehicleProfile;
}

export interface CameraVehiclePayload {
  candidates: CameraVehicleCandidate[];
  top_margin: number;
  expected_count?: number;
  appearance_count?: number;
  model?: string;
}

export interface SyncEvent {
  client_event_id: string;
  type: "scan" | "checkout" | "walk_in" | "resolve_alert" | "override";
  lot_id: string;
  observed_at: ISOTime;
  body: Record<string, unknown>;
}

export interface BookingQr {
  qr: string;
}

export interface QrPublicKey {
  public_key: string;
}

export interface WalkInRequest {
  lot_id: string;
  plate: string;
  device_id: string;
  client_event_id: string;
  observed_at?: ISOTime;
  space_id?: string | null;
}

export interface ResolveAlertRequest {
  device_id: string;
  note?: string;
}

export type AlertType =
  | "upcoming_arrival" | "overstay" | "unconfirmed_departure" | "no_show"
  | "unknown_vehicle" | "plate_mismatch" | "low_confidence" | "anomaly";

export interface Alert {
  id: number;
  type: AlertType;
  severity: "info" | "warning" | "critical";
  lot_id: string;
  stay_id: string | null;
  booking_id: string | null;
  message: string;
  estimated_fee: number | null;
  status: "open" | "resolved";
  created_at: ISOTime;
  resolved_by: string | null;
  resolved_at: ISOTime | null;
}

export interface GateCode {
  code: string;
  valid_until: ISOTime;
}

export interface StayEvidencePoint {
  signal_id: number;
  direction: "arrival" | "departure";
  observed_at: ISOTime;
  source: SignalSource;
  kind: SignalKind;
  log_lr: number;
  weighted_log_lr: number;
  probability: number;
}

export interface StayEvidence {
  stay_id: string;
  prior: number;
  evidence: StayEvidencePoint[];
}

export type ScanResultCode =
  | "checked_in" | "checked_out" | "already_in" | "wrong_lot"
  | "too_early" | "expired" | "not_found" | "invalid_qr";

export interface ScanResult {
  result: ScanResultCode;
  message: string;
  booking: Booking | null;
  stay: Stay | null;
  suggest: "register_walk_in" | null;
}

export interface ExpectedArrival {
  booking_id: string | null;
  user_id: string;
  plate: string | null;
  at: ISOTime;
  p: number;
  source: "booking" | "prediction";
}

export interface LotOverview {
  lot: Lot;
  now: ISOTime;
  counts: { capacity: number; occupied: number; reserved: number; free: number };
  expected_soon: ExpectedArrival[];
  spaces: { id: string; label: string; state: "free" | "reserved" | "occupied" }[] | null;
}

export interface AuditEntry {
  id: number;
  client_event_id: string | null;
  actor_type: "attendant" | "driver" | "system";
  actor_id: string | null;
  action: string;
  entity: string;
  entity_id: string;
  before: unknown;
  after: unknown;
  observed_at: ISOTime;
  received_at: ISOTime;
  device_id: string | null;
}

export interface PatternRow {
  user_id: string;
  pattern_id: string;
  is_primary: boolean;
  pattern_type: "recurring" | "occasional" | "insufficient_history";
  state: PatternState | null;
  days: ("Mon" | "Tue" | "Wed" | "Thu" | "Fri" | "Sat" | "Sun")[];
  label: string;
  time_window: { center: string; spread_min: number } | null;
  location_id: string | null;
  support: number;
  active_weeks: number;
  recurrence_prob: number | null;
  next_booking: { at: ISOTime; location_id: string } | null;
  confidence: number;
  reminder: { recommend: boolean; tier: ReminderTier; at: ISOTime | null };
  last_booking_date: ISODate | null;
  frequency_per_week: number;
  inactivity_detected: boolean;
  cancel_rate: number;
  change_date: ISODate | null;
  reason: string;
  run_id: string;
  model_version: string;
}

export interface ArrivalPrior {
  user_id: string;
  lot_id: string;
  date: ISODate;
  window_start: ISOTime;
  window_end: ISOTime;
  p: number;
  pattern_id?: string | null;
}

export interface LotForecast {
  lot_id: string;
  date: ISODate;
  hour: number;
  expected_arrivals: number;
}

export interface RunInfo {
  run_id: string;
  created_at: ISOTime;
  cutoff: ISODate;
  input: string;
  model_version: string;
  params: Record<string, unknown>;
}

export interface UserRhythm {
  user_id: string;
  patterns: PatternRow[];
  history: Booking[];
  heatmap: number[][]; // [weekday 0=Mon..6][hour 0..23] booking counts
}

// Driver mode (P3): one-tap confirm, pending confirmations, rewards (CONTRACT.md §7).
export type ConfirmAnswer = "arrived" | "not_me" | "left";

export interface ConfirmRequest {
  device_id: string;
  client_event_id: string;
  answer: ConfirmAnswer;
  observed_at?: ISOTime;
}

export interface ConfirmResult {
  result: "checked_in" | "checked_out" | "rejected" | "disputed" | "already";
  message: string;
  stay: Stay | null;
  points_awarded: number;
}

export interface PendingConfirmation {
  stay: Stay;
  booking: Booking;
  question: "arrived" | "left";
  p: number | null; // fused arrival probability of an ARRIVING stay
  asked_at: ISOTime;
}

export interface Perk {
  id: string;
  name: string;
  description: string;
  unlocked: boolean;
}

export interface RewardEntry {
  at: ISOTime;
  points: number;
  reason: "confirm_arrival" | "confirm_departure" | "on_time_arrival" | "clean_week";
  label: string;
}

export interface Rewards {
  user_id: string;
  points: number;
  streak_weeks: number;
  grace_minutes: number;
  tier: "standard" | "verified";
  perks: Perk[];
  history: RewardEntry[]; // newest first, up to 50
}

// GET /signals?lot_id=&source=&since=&after_id=&limit= (oldest first; poll with after_id = cursor).
export interface StoredSignal {
  id: number;
  client_event_id: string;
  device_id: string;
  user_id: string | null;
  lot_id: string;
  booking_id: string | null;
  matched_booking_id: string | null; // booking a camera / paired gate event was attributed to
  source: SignalSource;
  kind: SignalKind;
  observed_at: ISOTime;
  received_at: ISOTime;
  payload: Record<string, unknown>;
}

export interface SignalList {
  signals: StoredSignal[];
  cursor: number; // id of the last signal returned (or the after_id sent); pass it back as after_id
}
