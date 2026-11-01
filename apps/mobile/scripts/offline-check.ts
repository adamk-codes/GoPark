/// <reference types="node" />
// End-to-end check of the offline path against a running API (default http://localhost:8004):
//   1. Ed25519 check of real booking QRs from GET /bookings/{id}/qr with the key from GET /keys/qr
//      (valid, tampered payload, tampered signature, wrong lot);
//   2. the outbox: scans and a walk-in queued while "offline", then replayed through POST /sync/push,
//      landing in /audit with their original observed_at; a bad event surfaces as a conflict.
// Run: npm run test:offline [-- http://localhost:8004]. Resets the API's demo DB first (POST /dev/reset).
import { webcrypto } from 'node:crypto';
import { createApi } from '../src/api';
import type { AuditEntry, Booking } from '../src/contract';
import { decideScan } from '../src/core/decide';
import { uuidv7 } from '../src/core/ids';
import { verifyBookingQr } from '../src/core/qr';
import { MemoryStore } from '../src/core/store';
import { SyncEngine } from '../src/core/sync';

const BASE = process.argv[2] ?? 'http://localhost:8004';
const DEAD = 'http://127.0.0.1:9'; // nothing listens here: stands in for airplane mode
const newId = () => uuidv7((n) => webcrypto.getRandomValues(new Uint8Array(n)));

let failed = 0;
function check(name: string, ok: boolean, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
  if (!ok) failed += 1;
}

const b64urlJson = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url');

async function main() {
  const api = createApi({ baseUrl: BASE, deviceId: 'dev-offline-check', devMocks: false });
  await fetch(`${BASE}/dev/reset`, { method: 'POST' });
  const health = await api.health();
  const now = Date.parse(health.now);
  console.log(`API ${BASE} · demo clock ${health.now}`);

  // Bookings that can check in right now: in the [s − 30 min, e] window, no stay yet.
  const pull = await api.syncPull('AUB');
  const withStay = new Set(pull.stays.map((s) => s.booking_id));
  const open = (b: Booking) => b.status === 'booked' && !withStay.has(b.booking_id)
    && Date.parse(b.start_time) - 30 * 60_000 <= now && now <= Date.parse(b.end_time);
  const ready = pull.bookings.filter(open);
  if (ready.length < 2) throw new Error(`need 2 AUB bookings open for check-in, found ${ready.length}`);
  const other = (await api.syncPull('BDD')).bookings.find((b) => b.status === 'booked');
  if (!other) throw new Error('need a booked BDD booking');

  // ---------- 1. signature ----------
  const key = (await api.qrKey()).public_key;
  const { qr } = await api.bookingQr(ready[0].booking_id);
  const payload = verifyBookingQr(qr, key);
  check('valid QR verifies', payload?.bid === ready[0].booking_id, qr.slice(0, 40) + '…');

  const [prefix, enc, sig] = qr.split('.');
  const forged = { ...JSON.parse(Buffer.from(enc, 'base64url').toString()), lot: 'BDD' };
  check('tampered payload rejected', verifyBookingQr(`${prefix}.${b64urlJson(forged)}.${sig}`, key) === null);
  const flipped = sig.slice(0, 10) + (sig[10] === 'A' ? 'B' : 'A') + sig.slice(11);
  check('tampered signature rejected', verifyBookingQr(`${prefix}.${enc}.${flipped}`, key) === null);
  const otherKey = Buffer.from(webcrypto.getRandomValues(new Uint8Array(32))).toString('base64url');
  check('QR rejected under another key', verifyBookingQr(qr, otherKey) === null);

  const ctx = { lotId: 'AUB', nowMs: now, nowIso: health.now, qrKey: key, used: new Set<string>() };
  const otherQr = (await api.bookingQr(other.booking_id)).qr;
  const wrong = decideScan({ bookings: [], stays: [], alerts: [] }, { qr: otherQr }, ctx);
  check('BDD booking QR at AUB → wrong_lot', wrong.result.result === 'wrong_lot',
    wrong.result.message);
  const right = decideScan({ bookings: [], stays: [], alerts: [] }, { qr }, ctx);
  check('AUB booking QR at AUB (empty cache) → checked_in', right.result.result === 'checked_in', right.result.message);
  const early = decideScan({ bookings: [], stays: [], alerts: [] }, { qr }, { ...ctx, nowMs: now - 6 * 3600_000 });
  check('same QR six hours early → too_early', early.result.result === 'too_early', early.result.message);

  // ---------- 2. outbox replay ----------
  const store = new MemoryStore();
  const engine = new SyncEngine({ store, api, lotId: 'AUB', newId });
  await engine.init();
  await engine.pull(true); // online once: cache + QR key
  check('cache filled from /sync/pull', engine.view().bookings.length > 0 && engine.qrKey() === key,
    `${engine.view().bookings.length} bookings`);

  engine.setApi(createApi({ baseUrl: DEAD, deviceId: 'dev-offline-check', devMocks: false, timeoutMs: 1000 }));
  const qr2 = (await api.bookingQr(ready[1].booking_id)).qr;
  const a1 = engine.scan({ qr });
  const a2 = engine.scan({ qr: qr2 });
  const again = engine.scan({ qr });
  const walk = engine.walkIn('Z 99881');
  const badResolve = engine.resolveAlert(999_999);
  check('offline scan 1 → checked_in', a1.decision.result?.result === 'checked_in', a1.decision.result?.message);
  check('offline scan 2 → checked_in', a2.decision.result?.result === 'checked_in', a2.decision.result?.message);
  check('rescan within 5 min → already_in, not queued', again.decision.result?.result === 'already_in' && again.id === null);
  check('offline walk-in → checked_in', typeof walk !== 'string' && walk.decision.result?.result === 'checked_in');
  const walkStay = typeof walk === 'string' ? null : walk.decision.result!.stay!;
  // Car leaves by plate: checkout of a stay that only exists on this phone so far.
  const leave = engine.scan({ plate: 'Z 99881' });
  check('walk-in exits by plate → checked_out (queued as checkout)',
    leave.decision.result?.result === 'checked_out' && leave.decision.event?.type === 'checkout');
  await engine.flush();
  check('offline: push fails, events stay queued', engine.status().online === false && engine.status().pending === 5,
    `pending ${engine.status().pending}`);
  check('offline: view shows the optimistic check-ins',
    engine.view().stays.some((s) => s.booking_id === ready[0].booking_id && s.state === 'CHECKED_IN'));
  const queued = engine.pendingItems().map((p) => p.event);

  // 20 minutes pass in airplane mode before the network comes back.
  await fetch(`${BASE}/dev/clock`, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ advance_minutes: 20 }) });
  engine.setApi(api); // network back
  await engine.tick();
  await engine.settle();
  const st = engine.status();
  check('online: outbox drained', st.online === true && st.pending === 0, `pending ${st.pending}`);
  check('online: both scans acked', engine.outcome(a1.id)?.status === 'synced' && engine.outcome(a2.id)?.status === 'synced');
  check('online: server agrees on scan 1', engine.outcome(a1.id)?.server?.result === 'checked_in',
    engine.outcome(a1.id)?.server?.message);
  check('online: bad resolve surfaces as conflict', engine.outcome(badResolve.id)?.status === 'conflict'
    && engine.conflictItems().length === 1, engine.outcome(badResolve.id)?.reason);
  check('store persisted the drained outbox', store.snap.outbox.length === 0 && store.snap.conflicts.length === 1);

  const audit = await api.audit('AUB', 50);
  for (const ev of queued.filter((e) => e.type !== 'resolve_alert')) {
    const row = audit.find((r: AuditEntry) => r.client_event_id === ev.client_event_id);
    const late = row ? (Date.parse(row.received_at) - Date.parse(row.observed_at)) / 60_000 : 0;
    check(`audit has ${ev.type} ${ev.client_event_id.slice(0, 13)}… at its offline time, not the sync time`,
      !!row && Date.parse(row.observed_at) === Date.parse(ev.observed_at) && late >= 19,
      row ? `${row.action} observed ${row.observed_at.slice(11, 19)}, received ${row.received_at.slice(11, 19)}` : 'missing');
  }
  const walkRow = audit.find((r) => r.action === 'walk_in' && r.entity_id === walkStay?.id);
  check('phone-computed walk-in stay id matches the server', !!walkRow, walkStay?.id);

  // Replaying the same events again is harmless: the server remembers client_event_ids.
  const replay = await api.syncPush(queued.filter((e) => e.type === 'walk_in'));
  check('re-push of an acked event is acked again (idempotent)', replay.acks.length === 1);
}

main().then(() => {
  console.log(failed ? `\n${failed} check(s) failed` : '\nall checks passed');
  process.exit(failed ? 1 : 0);
}, (e) => {
  console.error(e);
  process.exit(1);
});
