// What a scan, walk-in or checkout does, decided on the phone from the local cache so the attendant gets an
// answer at once, online or not. Mirrors do_scan / walk_in in api/ops/routes.py; the server replays the same
// action from the outbox and its answer wins when it comes back.
import { sha256 } from '@noble/hashes/sha2.js';
import type { Alert, Booking, ScanResult, Stay, SyncEvent } from '../contract';
import { t } from '../strings';
import { normPlate, PLATE_RE, walkInStayId } from './ids';
import { verifyBookingQr, type QrPayload } from './qr';

export const EARLY_MIN = 30; // CONTRACT §5: valid from s − 30 min
export const DOUBLE_SCAN_MIN = 5; // a repeat scan this soon after check-in is not a departure
const ACTIVE = new Set<Stay['state']>(['CHECKED_IN', 'OVERSTAY', 'UNCONFIRMED_DEPARTURE']);
const MIN = 60_000;

export interface LotView {
  bookings: Booking[];
  stays: Stay[];
  alerts: Alert[];
}

export interface DecideCtx {
  lotId: string;
  nowMs: number; // demo clock
  nowIso: string;
  qrKey: string | null; // base64url Ed25519 key from GET /keys/qr
  used: ReadonlySet<string>; // booking ids already used for a check-in on this device
}

// The local answer plus the outbox event that makes it real on the server (null: nothing to send).
export interface Decision {
  result: ScanResult;
  event: Pick<SyncEvent, 'type' | 'body'> | null;
  usedBid?: string; // remember this booking as used on this device
  unverified?: boolean; // no QR key cached yet, so the signature couldn't be checked here
}

export type DecideInput = { qr: string } | { booking_id: string } | { plate: string };

const res = (result: ScanResult['result'], message: string, booking: Booking | null = null, stay: Stay | null = null,
  suggest: ScanResult['suggest'] = null): ScanResult => ({ result, message, booking, stay, suggest });

const hm = (iso: string | null | undefined) => (iso ? iso.slice(11, 16) : '');
const isActive = (s: Stay) => ACTIVE.has(s.state);
const plateHash = (plateNorm: string) =>
  Array.from(sha256(Uint8Array.from(plateNorm, (ch) => ch.charCodeAt(0))), (b) => b.toString(16).padStart(2, '0'))
    .join('').slice(0, 16);

function bookingFromPayload(p: QrPayload): Booking {
  return { booking_id: p.bid, user_id: '', location_id: p.lot, start_time: p.s, end_time: p.e, status: 'booked',
    plate: null };
}

export function decideScan(view: LotView, input: DecideInput, ctx: DecideCtx): Decision {
  if ('qr' in input) {
    const qr = input.qr.trim();
    if (!ctx.qrKey) {
      // Can't check the signature yet: let the server decide when the event syncs.
      return { result: res('invalid_qr', t.local.noKey), event: { type: 'scan', body: { qr } }, unverified: true };
    }
    const payload = verifyBookingQr(qr, ctx.qrKey);
    if (!payload) return { result: res('invalid_qr', t.local.badSignature), event: null };
    const cached = view.bookings.find((b) => b.booking_id === payload.bid);
    if (cached?.plate && plateHash(normPlate(cached.plate)) !== payload.ph) {
      return { result: res('invalid_qr', t.local.qrMismatch, cached), event: null };
    }
    // booking_id rides along so the server can flag offline scans of already-completed bookings.
    return decideBooking(view, cached ?? bookingFromPayload(payload), { qr, booking_id: payload.bid }, ctx);
  }

  if ('booking_id' in input) {
    const id = input.booking_id.trim().toUpperCase();
    const booking = view.bookings.find((b) => b.booking_id === id);
    // Not in today's cache for this lot: the server may still know it (another lot, a new booking).
    if (!booking) return { result: res('not_found', t.local.notInCache, null, null, 'register_walk_in'),
      event: { type: 'scan', body: { booking_id: id } } };
    return decideBooking(view, booking, { booking_id: id }, ctx);
  }

  const plate = normPlate(input.plate);
  const booking = findByPlate(view, plate, ctx);
  if (booking) return decideBooking(view, booking, { plate: input.plate.trim() }, ctx);
  // A car parked without a booking (walk-in) leaves by plate: check its stay out directly.
  const stay = view.stays.find((s) => isActive(s) && s.lot_id === ctx.lotId && s.plate_norm === plate);
  if (stay) return decideCheckout(stay, null, ctx);
  return { result: res('not_found', t.local.notInCache, null, null, 'register_walk_in'),
    event: { type: 'scan', body: { plate: input.plate.trim() } } };
}

// Today's booked booking for this plate, this lot first, closest to now (like the server's plate lookup).
function findByPlate(view: LotView, plate: string, ctx: DecideCtx): Booking | null {
  const near = view.bookings.filter((b) => b.plate && normPlate(b.plate) === plate && b.status === 'booked'
    && Date.parse(b.end_time) > ctx.nowMs - 12 * 60 * MIN && Date.parse(b.start_time) <= ctx.nowMs + 12 * 60 * MIN);
  near.sort((a, b) => Number(b.location_id === ctx.lotId) - Number(a.location_id === ctx.lotId)
    || Math.abs(Date.parse(a.start_time) - ctx.nowMs) - Math.abs(Date.parse(b.start_time) - ctx.nowMs));
  return near[0] ?? null;
}

function decideBooking(view: LotView, b: Booking, body: Record<string, unknown>, ctx: DecideCtx): Decision {
  const label = b.plate ?? b.booking_id;
  const event = { type: 'scan' as const, body };
  const active = view.stays.find((s) => s.booking_id === b.booking_id && isActive(s));
  if (active) {
    // Exit scan. Not part of the §5 entry checks, and comes first, as on the server.
    if (active.in_at && ctx.nowMs - Date.parse(active.in_at) < DOUBLE_SCAN_MIN * MIN) {
      return { result: res('already_in', t.local.justIn(label), b, active), event: null };
    }
    return decideCheckout(active, b, ctx, event);
  }
  // Entry checks in CONTRACT §5 order (signature was checked above): lot → time window → used on this device.
  if (b.location_id !== ctx.lotId) {
    return { result: res('wrong_lot', t.local.wrongLot(label, b.location_id, ctx.lotId), b), event: null };
  }
  if (b.status === 'cancelled' || b.status === 'no_show' || ctx.nowMs > Date.parse(b.end_time)) {
    return { result: res('expired', t.local.expired(label, hm(b.end_time)), b), event: null };
  }
  if (ctx.nowMs < Date.parse(b.start_time) - EARLY_MIN * MIN) {
    return { result: res('too_early', t.local.tooEarly(label, hm(b.start_time)), b), event: null };
  }
  if (ctx.used.has(b.booking_id) || view.stays.some((s) => s.booking_id === b.booking_id)) {
    return { result: res('already_in', t.local.used(label), b), event: null };
  }
  const stay: Stay = { id: `S-${b.booking_id}`, booking_id: b.booking_id, lot_id: b.location_id,
    plate_norm: b.plate ? normPlate(b.plate) : null, state: 'CHECKED_IN', in_at: ctx.nowIso, in_method: 'attendant',
    in_conf: 1, out_at: null, out_method: null, out_conf: null, inferred: false };
  return { result: res('checked_in', t.local.checkedIn(label, b.location_id, hm(b.end_time)), b, stay), event,
    usedBid: b.booking_id };
}

export function decideCheckout(stay: Stay, booking: Booking | null, ctx: DecideCtx,
  event: Decision['event'] = { type: 'checkout', body: { stay_id: stay.id, booking_id: stay.booking_id } }): Decision {
  const out: Stay = { ...stay, state: 'CHECKED_OUT', out_at: ctx.nowIso, out_method: 'attendant', out_conf: 1 };
  const b = booking ? { ...booking, status: 'completed' as const } : null;
  return { result: res('checked_out', t.local.checkedOut(booking?.plate ?? stay.plate_norm ?? stay.id), b, out), event };
}

// Walk-in: a car with no booking. Returns an error string for a plate the server would refuse.
export function decideWalkIn(view: LotView, plateRaw: string, clientEventId: string, ctx: DecideCtx): Decision | string {
  const plate = normPlate(plateRaw);
  if (!PLATE_RE.test(plate)) return t.local.badPlate;
  const active = view.stays.find((s) => isActive(s) && s.plate_norm === plate);
  if (active && active.lot_id !== ctx.lotId) return t.local.activeElsewhere(active.lot_id);
  if (active) return { result: res('already_in', t.local.walkInAlready(plate), null, active), event: null };
  const stay: Stay = { id: walkInStayId(clientEventId), booking_id: null, lot_id: ctx.lotId, plate_norm: plate,
    state: 'CHECKED_IN', in_at: ctx.nowIso, in_method: 'attendant', in_conf: 1, out_at: null, out_method: null,
    out_conf: null, inferred: false };
  return { result: res('checked_in', t.local.walkIn(plate, ctx.lotId), null, stay),
    event: { type: 'walk_in', body: { plate: plateRaw.trim(), space_id: null } } };
}

// The cache as the attendant should see it: server rows with this phone's unsynced actions applied on top.
export function overlay(server: LotView, pending: { event: SyncEvent; local: ScanResult | null }[]): LotView {
  if (!pending.length) return server;
  const stays = new Map(server.stays.map((s) => [s.id, s]));
  const bookings = new Map(server.bookings.map((b) => [b.booking_id, b]));
  const resolved = new Set<number>();
  for (const p of pending) {
    if (p.local?.stay) stays.set(p.local.stay.id, p.local.stay);
    if (p.local?.booking && bookings.has(p.local.booking.booking_id)) bookings.set(p.local.booking.booking_id, p.local.booking);
    if (p.event.type === 'resolve_alert') resolved.add(Number(p.event.body.alert_id));
  }
  return { bookings: [...bookings.values()], stays: [...stays.values()],
    alerts: server.alerts.filter((a) => !resolved.has(a.id)) };
}
