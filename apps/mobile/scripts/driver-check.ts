/// <reference types="node" />
// Driver path against a running API (default http://localhost:8004), on the contract types:
//   geofence signal tied to the right booking → spot camera → stay ARRIVING → "Did you just arrive?" in
//   GET /users/{id}/pending_confirmations → the driver taps Arrived while offline → the driver outbox replays
//   POST /stays/{id}/confirm on reconnect → ConfirmResult with points_awarded, visible in GET /users/{id}/rewards.
//   Plus refusals kept for the driver (404 unknown stay, 409 stay without booking, 422 bad signal).
// Run: npm run test:driver [-- http://localhost:8004]. Resets the API's demo DB first (POST /dev/reset).
import { webcrypto } from 'node:crypto';
import { createApi } from '../src/api';
import type { ConfirmRequest, ConfirmResult, Signal } from '../src/contract';
import { geofenceSignal, type DriverBooking } from '../src/core/driver';
import { uuidv7 } from '../src/core/ids';
import { peekBookingQr } from '../src/core/qr';
import { RequestOutbox, type KV, type Send } from '../src/core/requests';

const BASE = process.argv[2] ?? 'http://localhost:8004';
const DEVICE = 'dev-driver-check';
const newId = () => uuidv7((n) => webcrypto.getRandomValues(new Uint8Array(n)));

let failed = 0;
function check(name: string, ok: boolean, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) failed += 1;
}

const sender = (base: string): Send => async (path, body) => {
  const res = await fetch(`${base}${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal: AbortSignal.timeout(3000) });
  return { status: res.status, text: await res.text() };
};
const OFFLINE = sender('http://127.0.0.1:9');
const ONLINE = sender(BASE);

function confirmReq(stayId: string, answer: ConfirmRequest['answer'], observedAt: string) {
  const id = newId();
  const body: ConfirmRequest = { device_id: DEVICE, client_event_id: id, answer, observed_at: observedAt };
  return { id, kind: 'confirm' as const, path: `/stays/${stayId}/confirm`, body, label: `${answer} ${stayId}`,
    queuedAt: observedAt };
}

async function main() {
  const api = createApi({ baseUrl: BASE, deviceId: DEVICE });
  await fetch(`${BASE}/dev/reset`, { method: 'POST' });
  const health = await api.health();
  const now = Date.parse(health.now);

  // A booking open for check-in with no stay yet; the driver adds it by ID (lot and times from its QR).
  const pull = await api.syncPull('AUB');
  const withStay = new Set(pull.stays.map((s) => s.booking_id));
  const b = pull.bookings.find((x) => x.status === 'booked' && !withStay.has(x.booking_id)
    && Date.parse(x.start_time) - 30 * 60_000 <= now && now <= Date.parse(x.end_time));
  if (!b) throw new Error('need an AUB booking open for check-in');
  const p = peekBookingQr((await api.bookingQr(b.booking_id)).qr);
  const mine: DriverBooking[] = [{ booking_id: p!.bid!, lot_id: p!.lot!, start: p!.s!, end: p!.e! }];
  check('booking details read from its QR', mine[0].booking_id === b.booking_id && mine[0].lot_id === 'AUB'
    && Date.parse(mine[0].end) === Date.parse(b.end_time), `${mine[0].start} – ${mine[0].end}`);

  const geo = { deviceId: DEVICE, userId: b.user_id, kind: 'enter' as const, observedAt: health.now, bookings: mine };
  const sig = geofenceSignal({ ...geo, id: newId(), lotId: 'AUB', nowMs: now, radiusM: 150 });
  check('geofence enter at AUB carries the booking', sig?.booking_id === b.booking_id && sig.source === 'geofence');
  check('geofence at a lot without a booking → no signal', geofenceSignal({ ...geo, id: newId(), lotId: 'BDD', nowMs: now }) === null);
  check('geofence 5 h after the booking ended → no signal',
    geofenceSignal({ ...geo, id: newId(), lotId: 'AUB', nowMs: Date.parse(b.end_time) + 5 * 3600_000 }) === null);

  // Geofence signal queued underground, delivered on reconnect.
  const mem = new Map<string, string>();
  const kv: KV = { get: async (k) => mem.get(k) ?? null, set: async (k, v) => void mem.set(k, v) };
  const outbox = new RequestOutbox(kv);
  await outbox.enqueue({ id: sig!.client_event_id, kind: 'signal', path: '/signals', body: sig!, label: 'Entered AUB',
    queuedAt: health.now });
  let st = await outbox.flush(OFFLINE);
  check('offline: signal kept, outbox says offline', st.online === false && st.pending.length === 1);
  st = await outbox.flush(ONLINE);
  check('online: geofence signal delivered', st.pending.length === 0 && st.done[0]?.id === sig!.client_event_id);

  // The spot camera sees a car: with the geofence that is "probably them" → ARRIVING → the driver is asked.
  const spot: Signal = { client_event_id: newId(), device_id: 'cam-check', lot_id: 'AUB', source: 'camera_spot',
    kind: 'occupied', observed_at: health.now, booking_id: b.booking_id, payload: {} };
  await ONLINE('/signals', spot);
  const prompts = await api.pendingConfirmations(b.user_id);
  const prompt = prompts.find((x) => x.booking.booking_id === b.booking_id);
  check('pending_confirmations asks "arrived" for the ARRIVING stay', prompt?.question === 'arrived'
    && prompt.stay.state === 'ARRIVING', prompt ? `p=${prompt.p?.toFixed(2)} stay ${prompt.stay.id}` : 'none');
  if (!prompt) throw new Error('no prompt to answer');

  // The driver taps "Arrived" with no signal; it goes out on reconnect.
  const before = await api.rewards(b.user_id);
  const tap = confirmReq(prompt.stay.id, 'arrived', health.now);
  await outbox.enqueue(tap);
  st = await outbox.flush(OFFLINE);
  check('offline tap: confirm kept', st.pending.some((r) => r.id === tap.id));
  st = await outbox.flush(ONLINE);
  const result = st.done.find((r) => r.id === tap.id)?.response as ConfirmResult | undefined;
  check('online: confirm → checked_in', result?.result === 'checked_in' && result.stay?.in_method === 'driver',
    result?.message);
  check('points_awarded > 0 (camera agreed with the driver)', (result?.points_awarded ?? 0) > 0,
    `+${result?.points_awarded}`);
  const after = await api.rewards(b.user_id);
  // The server may add other awards for the same arrival (e.g. on_time_arrival), so at least points_awarded.
  check('rewards: points went up by at least points_awarded', after.points - before.points >= (result?.points_awarded ?? 1),
    `${before.points} → ${after.points}`);
  const fresh = after.history.slice(0, after.history.length - before.history.length);
  check('rewards: history has the confirmation', fresh.some((h) => h.reason === 'confirm_arrival'),
    fresh.map((h) => `+${h.points} ${h.label}`).join(', '));
  check('rewards: grace_minutes and tier present', typeof after.grace_minutes === 'number' && !!after.tier,
    `${after.grace_minutes} min · ${after.tier} · ${after.perks.length} perks`);
  check('prompt gone once answered', !(await api.pendingConfirmations(b.user_id)).some((x) => x.stay.id === prompt.stay.id));

  const replay = await ONLINE(tap.path, tap.body);
  check('re-sending the same confirm is harmless (same answer)', replay.status === 200
    && (JSON.parse(replay.text) as ConfirmResult).points_awarded === result?.points_awarded);

  // Refusals are kept for the driver, not retried.
  const walk = await api.syncPush([{ client_event_id: newId(), type: 'walk_in', lot_id: 'AUB', observed_at: health.now,
    body: { plate: 'Z 99882' } }]);
  const walkStay = (Object.values(walk.results ?? {})[0] as { stay?: { id: string } } | undefined)?.stay?.id;
  await outbox.enqueue(confirmReq('S-NOPE', 'arrived', health.now));
  if (walkStay) await outbox.enqueue(confirmReq(walkStay, 'arrived', health.now));
  await outbox.enqueue({ id: newId(), kind: 'signal', path: '/signals', label: 'bad', queuedAt: health.now,
    body: { ...sig!, client_event_id: newId(), kind: 'teleport' } });
  st = await outbox.flush(ONLINE);
  const codes = st.failed.map((f) => f.status).sort();
  check('404 unknown stay, 409 walk-in stay, 422 bad signal → refused, nothing left queued',
    st.pending.length === 0 && JSON.stringify(codes) === JSON.stringify([404, 409, 422]),
    st.failed.map((f) => `${f.status} ${f.reason.slice(0, 40)}`).join(' | '));
}

main().then(() => {
  console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
  process.exit(failed ? 1 : 0);
}, (e) => {
  console.error(e);
  process.exit(1);
});
