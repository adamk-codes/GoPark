// Offline-first sync (docs/briefs/P1-mobile.md §P2, CONTRACT §7). Every attendant action is decided locally,
// applied to the cache at once and queued as a sync_event; the outbox drains through POST /sync/push with
// backoff, and the cache refreshes from GET /sync/pull. The UI reads only from here and never waits on the network.
import type { Api } from '../api';
import type { Alert, Booking, ScanResult, Stay, SyncEvent } from '../contract';
import { clockFrom, demoNowIso, demoNowMs, PHONE_CLOCK, type DemoClock } from './clock';
import { decideCheckout, decideScan, decideWalkIn, overlay, type DecideInput, type Decision, type LotView } from './decide';
import type { ConflictItem, OutboxItem, Rows, Store } from './store';

const TICK_ONLINE_MS = 10_000; // health ping / outbox drain while online
const BACKOFF_MS = [2_000, 4_000, 8_000, 15_000, 30_000]; // retry delays while offline
const PULL_EVERY_MS = 2 * 60_000; // incremental /sync/pull
const FULL_PULL_EVERY_MS = 30 * 60_000; // full reload (drops yesterday's rows)
const PUSH_BATCH = 25;

export interface SyncStatus {
  online: boolean | null; // null until the first answer (or failure)
  pending: number; // outbox events for this lot
  conflicts: number;
  lastPullAt: number | null; // phone ms
  lastError: string | null;
}

// What became of one queued action, for the scan result card and History.
export interface EventOutcome {
  status: 'pending' | 'synced' | 'conflict';
  server?: ScanResult; // the server's own answer once synced
  reason?: string; // conflict reason
}

// A decision, or a plain queued action with no scan answer (resolve alert).
type Queued = Omit<Decision, 'result'> & { result: ScanResult | null };

export interface Action {
  id: string | null; // client_event_id, or null when nothing was queued
  decision: Queued;
}

export interface SyncDeps {
  store: Store;
  api: Api;
  lotId: string;
  newId: () => string;
  phoneNow?: () => number;
  log?: (msg: string) => void;
}

const isScanResult = (x: unknown): x is ScanResult => !!x && typeof x === 'object' && 'result' in x;

export class SyncEngine {
  private store: Store;
  private api: Api;
  private lotId: string;
  private newId: () => string;
  private phoneNow: () => number;
  private log: (msg: string) => void;

  private bookings = new Map<string, Booking>();
  private stays = new Map<string, Stay>();
  private alerts = new Map<number, Alert>();
  private outbox: OutboxItem[] = [];
  private conflicts: ConflictItem[] = [];
  private used = new Set<string>();
  private meta: Record<string, string> = {};
  private outcomes = new Map<string, EventOutcome>();

  private online: boolean | null = null;
  private lastError: string | null = null;
  private failures = 0;
  private flushing: Promise<void> | null = null;
  private pulling: Promise<void> | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private running = false;
  private writes: Promise<unknown> = Promise.resolve();
  private listeners = new Set<() => void>();
  private cachedView: LotView | null = null;
  version = 0; // bumps on every change; React re-renders on it

  constructor(deps: SyncDeps) {
    this.store = deps.store;
    this.api = deps.api;
    this.lotId = deps.lotId;
    this.newId = deps.newId;
    this.phoneNow = deps.phoneNow ?? Date.now;
    this.log = deps.log ?? (() => undefined);
  }

  async init() {
    const snap = await this.store.load();
    this.meta = snap.meta;
    snap.bookings.forEach((b) => this.bookings.set(b.booking_id, b));
    snap.stays.forEach((s) => this.stays.set(s.id, s));
    snap.alerts.forEach((a) => this.alerts.set(a.id, a));
    this.outbox = snap.outbox;
    this.conflicts = snap.conflicts;
    snap.used.forEach((b) => this.used.add(b));
    this.outbox.forEach((o) => this.outcomes.set(o.event.client_event_id, { status: 'pending' }));
    this.changed();
  }

  // ---------- reading ----------
  subscribe(fn: () => void) {
    this.listeners.add(fn);
    return () => void this.listeners.delete(fn);
  }

  status(): SyncStatus {
    return { online: this.online, pending: this.pendingItems().length, conflicts: this.conflicts.length,
      lastPullAt: this.meta[`pulled:${this.lotId}`] ? Number(this.meta[`pulled:${this.lotId}`]) : null,
      lastError: this.lastError };
  }

  clock(): DemoClock {
    return this.meta.clock ? (JSON.parse(this.meta.clock) as DemoClock) : PHONE_CLOCK;
  }

  nowIso() {
    return demoNowIso(this.clock(), this.phoneNow());
  }

  qrKey(): string | null {
    return this.meta.qr_key ?? null;
  }

  // This lot's bookings, stays and open alerts, with unsynced actions applied.
  view(): LotView {
    if (!this.cachedView) {
      const lot = this.lotId;
      this.cachedView = overlay({
        bookings: [...this.bookings.values()].filter((b) => b.location_id === lot),
        stays: [...this.stays.values()].filter((s) => s.lot_id === lot),
        alerts: [...this.alerts.values()].filter((a) => a.lot_id === lot && a.status === 'open'),
      }, this.pendingItems());
    }
    return this.cachedView;
  }

  pendingItems(): OutboxItem[] {
    return this.outbox.filter((o) => o.event.lot_id === this.lotId);
  }

  conflictItems(): ConflictItem[] {
    return this.conflicts.filter((c) => c.event.lot_id === this.lotId);
  }

  // Last good copy of a screen's server data (overview, audit), so screens still show something offline.
  saved<T>(key: string): { at: number; data: T } | null {
    const raw = this.meta[`saved:${key}`];
    return raw ? JSON.parse(raw) : null;
  }

  save(key: string, data: unknown) {
    this.setMeta(`saved:${key}`, JSON.stringify({ at: this.phoneNow(), data }));
  }

  outcome(id: string | null): EventOutcome | null {
    return id ? this.outcomes.get(id) ?? null : null;
  }

  // ---------- attendant actions (synchronous: the answer never waits on the network) ----------
  scan(input: DecideInput): Action {
    return this.act(decideScan(this.view(), input, this.ctx()));
  }

  checkout(stayId: string): Action | null {
    const stay = this.view().stays.find((s) => s.id === stayId);
    if (!stay) return null;
    const booking = stay.booking_id ? this.bookings.get(stay.booking_id) ?? null : null;
    return this.act(decideCheckout(stay, booking, this.ctx()));
  }

  walkIn(plate: string): Action | string {
    const id = this.newId();
    const d = decideWalkIn(this.view(), plate, id, this.ctx());
    return typeof d === 'string' ? d : this.act(d, id);
  }

  resolveAlert(alertId: number, note?: string): Action {
    return this.act({ result: null, event: { type: 'resolve_alert', body: { alert_id: alertId, note: note ?? null } } });
  }

  dismissConflict(id: string) {
    this.conflicts = this.conflicts.filter((c) => c.id !== id);
    this.persist(() => this.store.removeConflict(id));
    this.changed();
  }

  private ctx() {
    const clock = this.clock();
    return { lotId: this.lotId, nowMs: demoNowMs(clock, this.phoneNow()), nowIso: demoNowIso(clock, this.phoneNow()),
      qrKey: this.qrKey(), used: this.used };
  }

  private act(d: Queued, id = this.newId()): Action {
    if (d.usedBid && !this.used.has(d.usedBid)) {
      this.used.add(d.usedBid);
      const bid = d.usedBid;
      this.persist(() => this.store.addUsed(bid, this.nowIso()));
    }
    if (!d.event) {
      this.changed();
      return { id: null, decision: d };
    }
    const event: SyncEvent = { client_event_id: id, type: d.event.type, lot_id: this.lotId, observed_at: this.nowIso(),
      body: d.event.body };
    const item: OutboxItem = { event, local: d.result, attempts: 0, lastError: null };
    this.outbox.push(item);
    this.outcomes.set(id, { status: 'pending' });
    this.persist(() => this.store.putOutbox(item));
    this.changed();
    this.kick();
    return { id, decision: d };
  }

  // ---------- network ----------
  setApi(api: Api) {
    this.api = api;
    this.online = null;
    this.kick();
  }

  setLot(lotId: string) {
    if (lotId === this.lotId) return;
    this.lotId = lotId;
    this.changed();
    this.kick(); // no full pull for this lot yet, so the next cycle does one
  }

  start() {
    if (this.running) return;
    this.running = true;
    this.schedule(0);
  }

  stop() {
    this.running = false;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  // Run a sync cycle now (app came to the foreground, attendant pulled to refresh, new action queued).
  kick() {
    if (this.running) this.schedule(0);
  }

  // One cycle: drain the outbox, then pull if due, else just ping /health so the header stays honest.
  async tick() {
    const wasOnline = this.online;
    await this.flush();
    const pulledAt = Number(this.meta[`pulled:${this.lotId}`] ?? 0);
    const fullAt = Number(this.meta[`full:${this.lotId}`] ?? 0);
    const now = this.phoneNow();
    if (this.online !== false || this.pendingItems().length === 0) {
      if (now - fullAt > FULL_PULL_EVERY_MS) await this.pull(true);
      else if (wasOnline !== true || now - pulledAt > PULL_EVERY_MS) await this.pull(); // reconnect or due
      else await this.ping();
    }
    if (wasOnline === false && this.online) await this.flush(); // came back: drain what queued meanwhile
  }

  flush(): Promise<void> {
    this.flushing ??= this.doFlush().finally(() => { this.flushing = null; });
    return this.flushing;
  }

  pull(full = false): Promise<void> {
    this.pulling ??= this.doPull(full).finally(() => { this.pulling = null; });
    return this.pulling;
  }

  // Wait for queued storage writes (tests, and before the app is backgrounded).
  async settle() {
    await this.writes;
  }

  private schedule(ms: number) {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(async () => {
      this.timer = null;
      try {
        await this.tick();
      } catch (e) {
        this.log(`sync tick failed: ${String(e)}`);
      }
      if (!this.running || this.timer) return;
      this.schedule(this.online === false ? BACKOFF_MS[Math.min(this.failures - 1, BACKOFF_MS.length - 1)] ?? 2_000
        : TICK_ONLINE_MS);
    }, ms);
  }

  private async doFlush() {
    for (;;) {
      const batch = this.outbox.slice(0, PUSH_BATCH);
      if (!batch.length) return;
      let r;
      try {
        r = await this.api.syncPush(batch.map((b) => b.event));
      } catch (e) {
        this.wentOffline(e);
        for (const b of batch) {
          b.attempts += 1;
          b.lastError = String(e instanceof Error ? e.message : e);
          this.persist(() => this.store.putOutbox(b));
        }
        this.changed();
        return;
      }
      this.cameOnline();
      const done: string[] = [];
      const rows: Required<Rows> = { bookings: [], stays: [], alerts: [] };
      for (const id of r.acks) {
        const answer = r.results?.[id];
        if (isScanResult(answer)) {
          if (answer.booking) rows.bookings.push(answer.booking);
          if (answer.stay) rows.stays.push(answer.stay);
        } else if (answer && typeof answer === 'object' && 'id' in answer) {
          rows.alerts.push(answer as Alert);
        }
        this.outcomes.set(id, { status: 'synced', server: isScanResult(answer) ? answer : undefined });
        done.push(id);
      }
      const at = this.nowIso();
      for (const c of r.conflicts) {
        const item = batch.find((b) => b.event.client_event_id === c.id);
        if (!item) continue;
        const conflict: ConflictItem = { id: c.id, event: item.event, reason: c.reason, at };
        this.conflicts = [...this.conflicts.filter((x) => x.id !== c.id), conflict];
        this.outcomes.set(c.id, { status: 'conflict', reason: c.reason });
        this.persist(() => this.store.putConflict(conflict));
        done.push(c.id);
      }
      this.applyRows(rows);
      this.outbox = this.outbox.filter((o) => !done.includes(o.event.client_event_id));
      this.persist(() => this.store.removeOutbox(done));
      this.changed();
      this.log(`push: ${r.acks.length} acked, ${r.conflicts.length} conflicts`);
      if (!done.length) return; // the server answered neither way; try again next cycle
    }
  }

  private async doPull(full: boolean) {
    const lot = this.lotId;
    try {
      const h = await this.api.health();
      this.setMeta('clock', JSON.stringify(clockFrom(h.now, this.phoneNow())));
      let since = full ? null : this.meta[`cursor:${lot}`] ?? null;
      // A cursor ahead of the server clock means the demo clock was reset: start over.
      if (since && Date.parse(since) > Date.parse(h.now) + 60_000) since = null;
      if (!since || !this.qrKey()) {
        try {
          this.setMeta('qr_key', (await this.api.qrKey()).public_key);
        } catch (e) {
          this.log(`qr key: ${String(e)}`);
        }
      }
      const d = await this.api.syncPull(lot, since);
      if (lot !== this.lotId) return; // lot changed mid-request
      const rows = { bookings: d.bookings, stays: d.stays, alerts: d.alerts };
      if (since) {
        this.applyRows(rows);
      } else {
        for (const [id, b] of this.bookings) if (b.location_id === lot) this.bookings.delete(id);
        for (const [id, s] of this.stays) if (s.lot_id === lot) this.stays.delete(id);
        for (const [id, a] of this.alerts) if (a.lot_id === lot) this.alerts.delete(id);
        this.applyRows(rows, false);
        this.persist(() => this.store.replaceLot(lot, rows));
        this.setMeta(`full:${lot}`, String(this.phoneNow()));
      }
      this.setMeta(`cursor:${lot}`, d.cursor);
      this.setMeta(`pulled:${lot}`, String(this.phoneNow()));
      this.cameOnline();
      this.log(`pull${since ? '' : ' (full)'}: ${d.bookings.length} bookings, ${d.stays.length} stays, ${d.alerts.length} alerts`);
    } catch (e) {
      this.wentOffline(e);
    }
    this.changed();
  }

  private async ping() {
    try {
      const h = await this.api.health();
      this.setMeta('clock', JSON.stringify(clockFrom(h.now, this.phoneNow())));
      this.cameOnline();
    } catch (e) {
      this.wentOffline(e);
    }
    this.changed();
  }

  private applyRows(rows: Rows, save = true) {
    rows.bookings?.forEach((b) => this.bookings.set(b.booking_id, b));
    rows.stays?.forEach((s) => this.stays.set(s.id, s));
    rows.alerts?.forEach((a) => this.alerts.set(a.id, a));
    if (save && (rows.bookings?.length || rows.stays?.length || rows.alerts?.length)) {
      this.persist(() => this.store.upsert(rows));
    }
  }

  private setMeta(k: string, v: string) {
    if (this.meta[k] === v) return;
    this.meta[k] = v;
    this.persist(() => this.store.setMeta(k, v));
  }

  private cameOnline() {
    this.online = true;
    this.failures = 0;
    this.lastError = null;
  }

  private wentOffline(e: unknown) {
    this.online = false;
    this.failures += 1;
    this.lastError = e instanceof Error ? e.message : String(e);
  }

  // Storage writes run one after another in call order; a failed write is logged, never thrown at the UI.
  private persist(fn: () => Promise<unknown>) {
    this.writes = this.writes.then(fn).catch((e) => this.log(`store write failed: ${String(e)}`));
  }

  private changed() {
    this.cachedView = null;
    this.version += 1;
    this.listeners.forEach((fn) => fn());
  }
}
