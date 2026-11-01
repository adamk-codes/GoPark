// Driver-side state shared by the Driver screens and the background geofence task. The task runs with no React
// mounted, so everything it needs (API URL, user, demo clock, the driver's bookings) lives in AsyncStorage.
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { ConfirmAnswer, ConfirmRequest } from '../contract';
import { demoNowIso, demoNowMs, PHONE_CLOCK, type DemoClock } from '../core/clock';
import { geofenceSignal, type DriverBooking } from '../core/driver';
import { RequestOutbox, type KV, type OutboxState, type Send } from '../core/requests';
import { uuidv7 } from './ids';

export type { DriverBooking };

export interface DriverLot {
  id: string;
  name: string;
  lat: number;
  lng: number;
  radius_m: number;
}

export interface DriverConfig {
  apiUrl: string;
  deviceId: string;
  userId: string;
  clock: DemoClock;
  bookings: DriverBooking[];
  lots: DriverLot[];
  sensing: boolean; // the driver switched background arrival sensing on
}

const KEY = 'gopark.driver.v1';
const kv: KV = { get: (k) => AsyncStorage.getItem(k), set: (k, v) => AsyncStorage.setItem(k, v) };
export const driverOutbox = new RequestOutbox(kv);

export async function loadDriver(): Promise<DriverConfig | null> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as DriverConfig) : null;
  } catch {
    return null;
  }
}

export async function saveDriver(patch: Partial<DriverConfig>, base: DriverConfig): Promise<DriverConfig> {
  const next = { ...base, ...(await loadDriver()), ...patch };
  await AsyncStorage.setItem(KEY, JSON.stringify(next));
  return next;
}

export function sender(apiUrl: string, timeoutMs = 8000): Send {
  const base = apiUrl.replace(/\/+$/, '');
  return async (path, body) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(`${base}${path}`, { method: 'POST', signal: ctrl.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      return { status: res.status, text: await res.text() };
    } finally {
      clearTimeout(timer);
    }
  };
}

export function flushDriver(cfg: DriverConfig): Promise<OutboxState> {
  return driverOutbox.flush(sender(cfg.apiUrl));
}

// One-tap answer to "Did you just arrive?" / "Did you leave?". Queued first, so it survives no signal.
export async function queueConfirm(cfg: DriverConfig, stayId: string, answer: ConfirmAnswer, label: string) {
  const id = uuidv7();
  const observed = demoNowIso(cfg.clock ?? PHONE_CLOCK);
  const body: ConfirmRequest = { device_id: cfg.deviceId, client_event_id: id, answer, observed_at: observed };
  await driverOutbox.enqueue({ id, kind: 'confirm', path: `/stays/${encodeURIComponent(stayId)}/confirm`,
    body, label, queuedAt: observed });
  return flushDriver(cfg);
}

// Geofence enter/exit → a queued `geofence` signal with the booking it belongs to (or nothing, see geofenceSignal).
export async function queueGeofence(cfg: DriverConfig, lotId: string, kind: 'enter' | 'exit', radiusM?: number) {
  const clock = cfg.clock ?? PHONE_CLOCK;
  const id = uuidv7();
  const signal = geofenceSignal({ id, deviceId: cfg.deviceId, userId: cfg.userId, lotId, kind,
    observedAt: demoNowIso(clock), nowMs: demoNowMs(clock), bookings: cfg.bookings, radiusM });
  if (!signal) return null;
  await driverOutbox.enqueue({ id, kind: 'signal', path: '/signals', body: signal,
    label: `${kind === 'enter' ? 'Entered' : 'Left'} ${lotId} (${signal.booking_id})`, queuedAt: signal.observed_at });
  return flushDriver(cfg);
}
