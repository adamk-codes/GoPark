// Shapes P2 is adding to contract/types.ts in phase P1 (draft in the ops branch).
// TODO(P2): once the contract PR lands, run `npm run contract`, import these from
// ./contract instead and delete this file.
import type { ISOTime, ScanResult } from './contract';

export type AlertType =
  | "upcoming_arrival" | "overstay" | "unconfirmed_departure" | "no_show"
  | "unknown_vehicle" | "plate_mismatch" | "low_confidence" | "anomaly";

// GET /alerts?lot_id=. The P0 API returns these without id/status/fee/updated fields.
export interface Alert {
  id?: string;
  type: AlertType;
  severity: "info" | "warning" | "critical";
  status?: "open" | "resolved";
  lot_id: string;
  stay_id: string | null;
  booking_id: string | null;
  message: string;
  fee_usd?: number | null;
  created_at: ISOTime;
  updated_at?: ISOTime;
  resolved_at?: ISOTime | null;
  resolved_by?: string | null;
  note?: string | null;
}

// POST /walk_ins -> ScanResult
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
  client_event_id?: string | null;
  note?: string | null;
}

export interface QrPublicKey {
  format: "GP1";
  alg: "Ed25519";
  kid: string;
  public_key: string;
}

export interface BookingQr {
  booking_id: string;
  qr: string;
  valid_from: ISOTime;
  valid_until: ISOTime;
}

export interface Health {
  ok: boolean;
  now: ISOTime;
  version: string;
}

export interface Rewards {
  user_id: string;
  points: number;
  grace_minutes: number;
  tier: string;
}

export type WalkInResult = ScanResult;
