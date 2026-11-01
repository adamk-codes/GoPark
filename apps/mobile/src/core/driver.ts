// Pure driver-side helpers (node-testable): which booking a geofence event belongs to, and the signal it becomes.
import type { Signal } from '../contract';

export interface DriverBooking {
  booking_id: string;
  lot_id: string;
  start: string; // ISO with offset, from the booking QR payload
  end: string;
}

const MIN = 60_000;
// A geofence event belongs to a booking from an hour before its start to three hours after its end (overstays).
const BEFORE_MIN = 60;
const AFTER_MIN = 180;

// The booking at this lot around now, closest start first.
export function bookingAt(bookings: DriverBooking[], lotId: string, nowMs: number): DriverBooking | null {
  const near = bookings.filter((b) => b.lot_id === lotId && Date.parse(b.start) - BEFORE_MIN * MIN <= nowMs
    && nowMs <= Date.parse(b.end) + AFTER_MIN * MIN);
  near.sort((a, b) => Math.abs(Date.parse(a.start) - nowMs) - Math.abs(Date.parse(b.start) - nowMs));
  return near[0] ?? null;
}

// Geofence enter/exit as a CONTRACT §6 signal. null when no booking of this driver is around now at that lot:
// such events would only be noise for fusion.
export function geofenceSignal(o: { id: string; deviceId: string; userId: string; lotId: string; kind: 'enter' | 'exit';
  observedAt: string; nowMs: number; bookings: DriverBooking[]; radiusM?: number }): Signal | null {
  const booking = bookingAt(o.bookings, o.lotId, o.nowMs);
  if (!booking) return null;
  return { client_event_id: o.id, device_id: o.deviceId, user_id: o.userId, lot_id: o.lotId,
    booking_id: booking.booking_id, source: 'geofence', kind: o.kind, observed_at: o.observedAt,
    payload: o.radiusM ? { radius_m: o.radiusM } : {} };
}
