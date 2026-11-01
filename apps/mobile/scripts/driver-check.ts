/// <reference types="node" />
// Driver path against a running API (default http://localhost:8004): geofence signals tied to the right booking,
// the driver outbox holding confirms/signals while offline and replaying them on reconnect, server refusals
// kept for the driver, and the rewards / confirmations normalizers on today's server.
// Run: npm run test:driver [-- http://localhost:8004]. Resets the API's demo DB first (POST /dev/reset).
import { webcrypto } from 'node:crypto';
import { createApi, normalizeConfirmations, normalizeRewards } from '../src/api';
import { geofenceSignal, type DriverBooking } from '../src/core/driver';
import { uuidv7 } from '../src/core/ids';
import { peekBookingQr } from '../src/core/qr';
import { RequestOutbox, type KV, type Send } from '../src/core/requests';

const BASE = process.argv[2] ?? 'http://localhost:8004';
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

async function main() {
  const api = createApi({ baseUrl: BASE, deviceId: 'dev-driver-check', devMocks: false });
  await fetch(`${BASE}/dev/reset`, { method: 'POST' });
  const health = await api.health();
  const now = Date.parse(health.now);

  // The driver adds a booking by ID: lot and times come from its signed QR.
  const pull = await api.syncPull('AUB');
  const b = pull.bookings.find((x) => x.status === 'booked' && Date.parse(x.start_time) - 60 * 60_000 <= now
    && now <= Date.parse(x.end_time));
  if (!b) throw new Error('need an AUB booking around now');
  const p = peekBookingQr((await api.bookingQr(b.booking_id)).qr);
  const mine: DriverBooking[] = [{ booking_id: p!.bid!, lot_id: p!.lot!, start: p!.s!, end: p!.e! }];
  check('booking details read from its QR', mine[0].booking_id === b.booking_id && mine[0].lot_id === 'AUB'
    && Date.parse(mine[0].end) === Date.parse(b.end_time), `${mine[0].start} – ${mine[0].end}`);

  const base = { deviceId: 'dev-driver-check', userId: b.user_id, kind: 'enter' as const, observedAt: health.now,
    bookings: mine };
  const sig = geofenceSignal({ ...base, id: newId(), lotId: 'AUB', nowMs: now, radiusM: 150 });
  check('geofence enter at AUB carries the booking', sig?.booking_id === b.booking_id && sig.source === 'geofence');
  check('geofence at a lot without a booking → no signal', geofenceSignal({ ...base, id: newId(), lotId: 'BDD', nowMs: now }) === null);
  check('geofence 5 h after the booking ended → no signal',
    geofenceSignal({ ...base, id: newId(), lotId: 'AUB', nowMs: Date.parse(b.end_time) + 5 * 3600_000 }) === null);

  // Driver outbox: queued offline, replayed on reconnect.
  const mem = new Map<string, string>();
  const kv: KV = { get: async (k) => mem.get(k) ?? null, set: async (k, v) => void mem.set(k, v) };
  const outbox = new RequestOutbox(kv);
  const confirmId = newId();
  await outbox.enqueue({ id: sig!.client_event_id, kind: 'signal', path: '/signals', body: sig as never,
    label: 'Entered AUB', queuedAt: health.now });
  await outbox.enqueue({ id: confirmId, kind: 'confirm', path: `/stays/S-${b.booking_id}/confirm`,
    body: { device_id: 'dev-driver-check', client_event_id: confirmId, answer: 'arrived', observed_at: health.now },
    label: 'Arrived at AUB', queuedAt: health.now });
  let st = await outbox.flush(sender('http://127.0.0.1:9'));
  check('offline: both kept, outbox says offline', st.online === false && st.pending.length === 2);

  st = await outbox.flush(sender(BASE));
  check('online: geofence signal delivered', !st.pending.some((r) => r.kind === 'signal') && st.online === true);
  const confirm = st.pending.find((r) => r.id === confirmId);
  check('online: confirm stays queued while the endpoint is a stub', !!confirm, confirm?.lastError ?? 'delivered');
  st = await outbox.flush(sender(BASE), { devMocks: true });
  check('dev mocks: stub confirm counts as delivered', st.pending.length === 0);

  const again = await fetch(`${BASE}/signals`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(sig) }).then((r) => r.json());
  check('server stored the replayed signal once (re-send is a duplicate)', again.duplicates === 1, JSON.stringify(again));

  await outbox.enqueue({ id: 'bad', kind: 'signal', path: '/signals', body: { ...sig!, client_event_id: newId(), kind: 'teleport' },
    label: 'bad', queuedAt: health.now });
  st = await outbox.flush(sender(BASE));
  check('server refusal (422) is kept for the driver, not retried', st.pending.length === 0 && st.failed[0]?.status === 422,
    st.failed[0]?.reason.slice(0, 60));

  // Normalizers: today's rewards shape, and likely confirmation shapes.
  const rw = normalizeRewards(b.user_id, await (await fetch(`${BASE}/users/${b.user_id}/rewards`)).json());
  check('rewards normalizer reads today\'s server shape', typeof rw.points === 'number' && rw.streak_weeks === 0
    && Array.isArray(rw.perks), JSON.stringify(rw));
  const pc = normalizeConfirmations([{ stay_id: 'S-1', booking_id: 'B1', lot_id: 'AUB', kind: 'departure' },
    { id: 'S-2', lot_id: 'AUB', question: 'arrival', asked_at: health.now }, { nothing: true }]);
  check('confirmations normalizer', pc.length === 2 && pc[0].question === 'departure' && pc[1].stay_id === 'S-2');
}

main().then(() => {
  console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
  process.exit(failed ? 1 : 0);
}, (e) => {
  console.error(e);
  process.exit(1);
});
